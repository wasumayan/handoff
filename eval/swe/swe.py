"""SWE-bench handoff experiment: an agent runs out of usage mid-task; does a smart handoff help the
next one finish, and what does it cost in context? Every agent command runs in Docker.

    uv run python eval/swe/swe.py run [--instances 10051,7490] [--out eval/out/swe/NAME]
    uv run python eval/swe/swe.py report eval/out/swe/NAME

Per instance (eval/swe/instances.json, from SWE-bench Verified):
  1. source: Codex works on the issue to the end (whether it solves it alone is recorded). Then it is
     rewound to the moment just before its first file edit, as if its usage ran out there: its
     session file is cut at that point and the repo reset to match. What it had found out by then
     exists only in that session.
  2. Each condition starts from that state, at the same path:
       plain  a fresh Codex gets the issue and "another agent ran out of usage; take over"
       smart  the same prompt, plus `handoff create --brief` written by a local model (Ollama) in
              AGENTS.md, which Codex reads on start
  3. Grading: SWE-bench's test patch is applied to a copy; resolved means every FAIL_TO_PASS and
     PASS_TO_PASS test passes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "eval"))
from harness.agents import codex_peak_context, parse_codex_events  # noqa: E402

BASE_IMAGE = "handoff-swe-base"
# Where containers work. Not under ~/Documents: Docker Desktop on macOS needs a privacy permission
# to mount it, and without one the container hangs at start and wedges the engine.
WORK_ROOT = Path("/tmp/handoff-swe").resolve()
TIMEOUT = 1500  # seconds per agent run
CONDITIONS = ("plain", "smart")
SOURCE_PROMPT = ("Resolve the GitHub issue below in this repository so that the project's tests pass."
                 "\n\nIssue:\n{issue}\n")
TAKEOVER_PROMPT = ("You are taking over a coding task from another agent that ran out of usage partway "
                   "through. Any changes it made are uncommitted in this repository. Resolve the GitHub "
                   "issue below so that the project's tests pass. No one is available to answer "
                   "questions; use your judgment and finish.\n\nIssue:\n{issue}\n")
# Editable install of the repo under test, deps already in the image.
INSTALL = "pip install -q --no-deps --no-build-isolation -e . >/dev/null 2>&1"


def api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "")
    auth = Path.home() / ".codex" / "auth.json"
    if not key and auth.is_file():
        key = json.loads(auth.read_text()).get("OPENAI_API_KEY") or ""
    if not key:
        raise SystemExit("set OPENAI_API_KEY (or log Codex in with an API key)")
    return key


def build_images(instances: list[dict]) -> None:
    subprocess.run(["docker", "build", "-q", "-t", BASE_IMAGE, str(HERE)], check=True, capture_output=True)
    for inst in instances:
        tag = image(inst)
        if subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode == 0:
            continue
        dockerfile = (f"FROM {BASE_IMAGE}\n"
                      f'RUN pip install -q "setuptools_scm[toml]>=6.2.3" wheel \\\n'
                      f" && git clone -q https://github.com/{inst['repo']} /tmp/repo && cd /tmp/repo \\\n"
                      f" && git checkout -q {inst['base_commit']} && pip install -q -e '.[testing]' \\\n"
                      f" && rm -rf /tmp/repo\n")
        print(f"building {tag}", file=sys.stderr)
        subprocess.run(["docker", "build", "-q", "-t", tag, "-"], input=dockerfile, text=True, check=True,
                       capture_output=True)


def image(inst: dict) -> str:
    return "handoff-swe-" + inst["instance_id"].split("__")[1].lower()


def docker(tag: str, mounts: list[Path], script: str, env: dict[str, str] | None = None,
           name: str = "") -> list[str]:
    """A throwaway container that sees only `mounts`, each at its host path. Env values named here
    are passed by name, so secrets stay out of the command line."""
    cmd = ["docker", "run", "--rm", *(["--name", name] if name else [])]
    for m in mounts:
        cmd += ["-v", f"{m}:{m}"]
    for k in env or {}:
        cmd += ["-e", k]
    return cmd + [tag, "bash", "-c", script]


def run_codex(tag: str, repo: Path, home: Path, prompt: str) -> dict:
    """Codex in a container on `repo`, CODEX_HOME=`home`, killed after TIMEOUT."""
    home.mkdir(parents=True, exist_ok=True)
    (home / "prompt.txt").write_text(prompt)
    name = f"hswe-{uuid4().hex[:8]}"
    script = (f"cd {repo} && {INSTALL}; printenv OPENAI_API_KEY | codex login --with-api-key >/dev/null; "
              f"codex exec --json --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check "
              f'"$(cat {home}/prompt.txt)"')
    env = {"OPENAI_API_KEY": api_key(), "CODEX_HOME": str(home)}
    t0 = time.time()
    proc = subprocess.Popen(docker(tag, [repo, home], script, env, name), stdout=subprocess.PIPE,
                            stderr=open(home / "stderr.log", "w"), text=True, env={**os.environ, **env})
    timer = threading.Timer(TIMEOUT, lambda: subprocess.run(["docker", "kill", name], capture_output=True))
    timer.start()
    lines = list(proc.stdout)
    proc.wait()
    timer.cancel()
    (home / "auth.json").unlink(missing_ok=True)  # the API key, written by `codex login`
    events = "".join(lines)
    (home / "events.jsonl").write_text(events)
    run = parse_codex_events(events)
    rollout = "\n".join(p.read_text(errors="replace") for p in (home / "sessions").rglob("*.jsonl"))
    return {
        "seconds": round(time.time() - t0, 1),
        "turns": run.turns,
        "tool_calls": run.tool_calls,
        "input_tokens": (run.input_tokens or 0) + (run.cache_read_tokens or 0),
        "cached_input_tokens": run.cache_read_tokens,
        "output_tokens": run.output_tokens,
        "first_call_context": first_call_context(rollout),
        "peak_context": codex_peak_context(rollout),
        "timed_out": time.time() - t0 >= TIMEOUT,
        "agent_model": next(iter(re.findall(r'"model":"([^"]+)"', rollout)), ""),
        "error": run.error,
    }


def is_edit(rec: dict) -> bool:
    """A file edit in a Codex rollout: an apply_patch call, standalone or inside the `exec` tool."""
    p = rec.get("payload") or {}
    if rec.get("type") == "event_msg":
        return p.get("type") == "patch_apply_begin"
    return (rec.get("type") == "response_item" and p.get("type") in ("custom_tool_call", "function_call")
            and "*** Begin Patch" in str(p.get("input") or p.get("arguments") or ""))


def rewind(home: Path, repo: Path) -> dict:
    """Cut the session just before its first edit and reset the repo to match: the state at the
    moment usage would have run out. The full session is kept as rollout.full.jsonl."""
    path = next((home / "sessions").rglob("*.jsonl"))
    lines = path.read_text().splitlines(keepends=True)
    recs = [json.loads(ln) for ln in lines]
    calls = [i for i, r in enumerate(recs) if r.get("type") == "response_item"
             and (r.get("payload") or {}).get("type") in ("custom_tool_call", "function_call")]
    cut = next((i for i, r in enumerate(recs) if is_edit(r)), None)
    if cut is None:  # never edited through apply_patch: cut halfway through its tool calls
        cut = calls[len(calls) // 2] if calls else len(lines)
    (home / "rollout.full.jsonl").write_text("".join(lines))
    path.write_text("".join(lines[:cut]))
    subprocess.run([*GIT, "checkout", "-q", "--", "."], cwd=repo, check=True, env=GIT_ENV)
    # -x: ignored files go too, so no agent-made symlink survives for host tools to follow; the build
    # files it removes are recreated by the editable install at each container start
    subprocess.run([*GIT, "clean", "-fdxq"], cwd=repo, check=True, env=GIT_ENV)
    kept = "".join(lines[:cut])
    return {"tool_calls_before_cut": sum(1 for i in calls if i < cut),
            "tool_calls_total": len(calls), "context_at_cut": codex_peak_context(kept)}


def first_call_context(rollout: str) -> int | None:
    """Input tokens of the first model call: system prompt, AGENTS.md and the prompt, before any
    exploration."""
    for line in rollout.splitlines():
        try:
            p = json.loads(line).get("payload") or {}
        except json.JSONDecodeError:
            continue
        if p.get("type") == "token_count":
            n = ((p.get("info") or {}).get("last_token_usage") or {}).get("input_tokens")
            if isinstance(n, int):
                return n
    return None


def grade(tag: str, inst: dict, repo: Path, dest: Path) -> dict:
    """SWE-bench's check, on a copy: reset the test files the test patch touches to the base commit
    (agents often add their own tests to them), apply the patch, run those files, and require every
    FAIL_TO_PASS and PASS_TO_PASS test to pass (XFAIL counts as a pass, as in SWE-bench)."""
    g = dest / "grade"
    shutil.rmtree(g, ignore_errors=True)
    shutil.copytree(repo, g, symlinks=True)
    (dest / "test.patch").write_text(inst["test_patch"])
    files = sorted({ln[6:] for ln in inst["test_patch"].splitlines() if ln.startswith("+++ b/")})
    reset = " ".join(f"(git checkout -q {inst['base_commit']} -- {f} 2>/dev/null || rm -f {f});" for f in files)
    script = (f"cd {g} && {INSTALL}; {reset} git apply {dest}/test.patch && "
              f"python -m pytest -rA -p no:cacheprovider {' '.join(files)}")
    r = subprocess.run(docker(tag, [dest], script), capture_output=True, text=True, timeout=1200)
    out = r.stdout + r.stderr
    (dest / "grade.log").write_text(out)
    shutil.rmtree(g, ignore_errors=True)
    status: dict[str, str] = {}
    for line in out.splitlines():
        word, _, rest = line.partition(" ")
        if word in ("PASSED", "FAILED", "ERROR", "XFAIL", "XPASS", "SKIPPED") and rest.split():
            status[rest.split()[0]] = word  # SWE-bench's ids end at the first space, so must ours
    passed = lambda t: status.get(t) in ("PASSED", "XFAIL")  # noqa: E731
    return {"resolved": all(map(passed, inst["FAIL_TO_PASS"] + inst["PASS_TO_PASS"])),
            "fail_to_pass": f"{sum(map(passed, inst['FAIL_TO_PASS']))}/{len(inst['FAIL_TO_PASS'])}",
            "pass_to_pass": f"{sum(map(passed, inst['PASS_TO_PASS']))}/{len(inst['PASS_TO_PASS'])}"}


def build_handoff(repo: Path, source_home: Path, dest: Path, model: str) -> dict:
    """`handoff create --brief` with a local model, installed into the repo's AGENTS.md. --keep-pii
    keeps absolute paths: the agent runs in a container where ~ is not the host's home."""
    env = {**os.environ, "PYTHONPATH": str(ROOT), "HANDOFF_HOME_OVERRIDE": str(source_home),
           "HANDOFF_BASE_URL": "http://localhost:11434/v1", "HANDOFF_MODEL": model}
    t0 = time.time()
    r = subprocess.run([sys.executable, "-m", "handoff.cli", "create", "--to", "codex", "--from", "codex",
                        "--path", str(repo), "--install", "--brief", "--keep-pii", "--out", str(dest)],
                       capture_output=True, text=True, env=env, timeout=900)
    (dest.parent / "handoff.stderr").write_text(r.stderr)
    if r.returncode:
        raise RuntimeError(f"handoff create failed: {r.stderr[-500:]}")
    block = (repo / "AGENTS.md").read_text()
    return {"seconds": round(time.time() - t0, 1), "chars": len(block),
            "model_failed": "model step failed" in block or "model step failed" in r.stderr}


# Host git on a repo an agent could write to. Its .git is rebuilt first (see sanitize); hooks,
# fsmonitor and external diff tools are also off here, and no system or global config is read.
GIT = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
GIT_ENV = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}


def sanitize(repo: Path, pristine: Path) -> None:
    """After a container ran in `repo`: replace its .git with the copy taken before any agent ran,
    kept outside every mount. Whatever the agent put in .git (config with fsmonitor or drivers,
    hooks, commondir, attributes) is gone, so host git cannot be made to run it. The diff is taken
    from the working tree against the base commit, so even an agent's commits are still counted."""
    git = repo / ".git"
    if git.is_symlink() or git.is_file():
        git.unlink()
    elif git.exists():
        shutil.rmtree(git)
    shutil.copytree(pristine, git, symlinks=True)


def git_diff(repo: Path, base: str) -> str:
    subprocess.run([*GIT, "add", "-A"], cwd=repo, capture_output=True, env=GIT_ENV)
    return subprocess.run([*GIT, "diff", "--cached", "--no-ext-diff", "--no-textconv", base], cwd=repo,
                          capture_output=True, text=True, env=GIT_ENV).stdout


def run_instance(inst: dict, out: Path, model: str) -> dict:
    """Runs in WORK_ROOT; results and logs (not the repo copies) end up in out/<instance>."""
    done = out / inst["instance_id"] / "result.json"
    if done.is_file():
        return json.loads(done.read_text())
    work = WORK_ROOT / out.name / inst["instance_id"]
    if (work / "result.json").is_file():  # finished, but its results were not collected
        return collect(work, out / inst["instance_id"])
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    tag, repo, base = image(inst), work / "repo", inst["base_commit"]
    cache = ROOT / "eval" / ".cache" / "swe" / inst["repo"].replace("/", "__")
    if not cache.is_dir():
        subprocess.run(["git", "clone", "-q", f"https://github.com/{inst['repo']}", str(cache)], check=True)
    subprocess.run(["git", "clone", "-q", str(cache), str(repo)], check=True)
    subprocess.run(["git", "checkout", "-q", base], cwd=repo, check=True)
    pristine = work / "git-pristine"  # .git before any agent ran; never mounted into a container
    shutil.copytree(repo / ".git", pristine, symlinks=True)
    res: dict = {"instance": inst["instance_id"], "difficulty": inst["difficulty"], "model": model}

    print(f"{inst['instance_id']}: source run", file=sys.stderr)
    source_home = work / "source-home"
    res["source"] = run_codex(tag, repo, source_home / ".codex", SOURCE_PROMPT.format(issue=inst["problem_statement"]))
    sanitize(repo, pristine)
    res["source"]["grade"] = grade(tag, inst, repo, work / "source")  # had it not been cut
    (work / "source" / "diff.patch").write_text(git_diff(repo, base))
    subprocess.run([*GIT, "reset", "-q", "--hard", base], cwd=repo, check=True, env=GIT_ENV)
    res["source"].update(rewind(source_home / ".codex", repo))
    snap = work / "snapshot"
    shutil.copytree(repo, snap, symlinks=True)

    for cond in CONDITIONS:
        print(f"{inst['instance_id']}: {cond} takeover", file=sys.stderr)
        shutil.rmtree(repo)
        shutil.copytree(snap, repo, symlinks=True)
        d = work / cond
        d.mkdir(exist_ok=True)
        r: dict = {}
        if cond == "smart":
            r["handoff"] = build_handoff(repo, source_home, d / "handoff", model)
        r.update(run_codex(tag, repo, d / "home", TAKEOVER_PROMPT.format(issue=inst["problem_statement"])))
        sanitize(repo, pristine)
        r["grade"] = grade(tag, inst, repo, d)
        (d / "diff.patch").write_text(git_diff(repo, base))
        res[cond] = r
    shutil.rmtree(repo)
    (work / "result.json").write_text(json.dumps(res, indent=2))
    return collect(work, out / inst["instance_id"])


def collect(work: Path, dest: Path) -> dict:
    """Keep results and logs, not the repo copies or Codex's temp files (dangling symlinks)."""
    shutil.copytree(work, dest, symlinks=True, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("repo", "snapshot", "grade", "tmp", "git-pristine"))
    shutil.rmtree(work, ignore_errors=True)
    return json.loads((dest / "result.json").read_text())


def regrade(out: Path) -> None:
    """Grade every saved diff.patch again, e.g. after a grader fix, without rerunning any agent."""
    insts = {i["instance_id"]: i for i in json.loads((HERE / "instances.json").read_text())}
    for path in sorted(out.glob("*/result.json")):
        res, inst = json.loads(path.read_text()), insts[path.parent.name]
        cache = ROOT / "eval" / ".cache" / "swe" / inst["repo"].replace("/", "__")
        for cond in ("source", *CONDITIONS):
            work = WORK_ROOT / f"regrade-{uuid4().hex[:6]}"
            repo = work / "repo"
            subprocess.run(["git", "clone", "-q", str(cache), str(repo)], check=True)
            subprocess.run(["git", "checkout", "-q", inst["base_commit"]], cwd=repo, check=True)
            diff = path.parent / cond / "diff.patch"
            if diff.stat().st_size:
                subprocess.run(["git", "apply", str(diff)], cwd=repo, check=True)
            res[cond]["grade"] = grade(image(inst), inst, repo, work)
            shutil.copy(work / "grade.log", path.parent / cond / "grade.log")
            shutil.rmtree(work, ignore_errors=True)
            print(path.parent.name, cond, res[cond]["grade"], file=sys.stderr)
        path.write_text(json.dumps(res, indent=2))


def report(out: Path) -> str:
    rows = [json.loads(p.read_text()) for p in sorted(out.glob("*/result.json"))]
    k = lambda n: "n/a" if n is None else f"{n / 1000:.1f}k"  # noqa: E731
    lines = ["# SWE-bench handoff experiment", "",
             f"{len(rows)} instances. Source: Codex (`{rows[0]['source'].get('agent_model', '?') if rows else '?'}`), "
             "rewound to just before its first edit (simulated out of usage). Takeover: a fresh Codex, "
             "plain prompt vs. the same prompt plus a `--brief` handoff written by "
             f"`{rows[0]['model'] if rows else '?'}` via Ollama. Tokens are model input tokens.", "",
             "| instance | source: solved uncut / calls before cut / ctx at cut | condition | resolved | "
             "handoff chars | ctx at start | peak ctx | total input | output | turns | tool calls | seconds |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        s = r["source"]
        src = (f"{'yes' if s['grade']['resolved'] else 'no'} / {s['tool_calls_before_cut']} of "
               f"{s['tool_calls_total']} / {k(s['context_at_cut'])}")
        for i, c in enumerate(CONDITIONS):
            x = r[c]
            lines.append(f"| {r['instance'] if i == 0 else ''} | {src if i == 0 else ''} | {c} | "
                         f"{'yes' if x['grade']['resolved'] else 'no'} "
                         f"(F2P {x['grade']['fail_to_pass']}) | {x.get('handoff', {}).get('chars', 0):,} | "
                         f"{k(x['first_call_context'])} | {k(x['peak_context'])} | {k(x['input_tokens'])} | "
                         f"{k(x['output_tokens'])} | {x['turns']} | {x['tool_calls']} | {x['seconds']} |")
    lines += ["", "| condition | resolved | mean ctx at start | mean peak ctx | mean total input | mean tool calls |",
              "|---|---|---|---|---|---|"]
    for c in CONDITIONS:
        xs = [r[c] for r in rows]
        mean = lambda f, xs=xs: sum(x[f] or 0 for x in xs) / max(1, len(xs))  # noqa: E731
        lines.append(f"| {c} | {sum(x['grade']['resolved'] for x in xs)}/{len(xs)} | {k(mean('first_call_context'))} | "
                     f"{k(mean('peak_context'))} | {k(mean('input_tokens'))} | {mean('tool_calls'):.0f} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--instances", help="comma-separated ids or number suffixes (default: all)")
    r.add_argument("--out", default=str(ROOT / "eval" / "out" / "swe" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")))
    r.add_argument("--model", default="qwen3.5", help="Ollama model that writes the handoff")
    for name in ("report", "regrade"):
        sub.add_parser(name).add_argument("out")
    a = ap.parse_args()
    if a.cmd == "regrade":
        regrade(Path(a.out).resolve())
    if a.cmd in ("report", "regrade"):
        text = report(Path(a.out))
        (Path(a.out) / "results.md").write_text(text)
        print(text)
        return 0
    instances = json.loads((HERE / "instances.json").read_text())
    if a.instances:
        want = a.instances.split(",")
        instances = [i for i in instances if any(i["instance_id"].endswith(w) for w in want)]
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    api_key()
    build_images(instances)
    for inst in instances:
        try:
            run_instance(inst, out, a.model)
        except Exception as e:  # noqa: BLE001 - one broken instance should not stop the others
            print(f"{inst['instance_id']}: {type(e).__name__}: {e}", file=sys.stderr)
    text = report(out)
    (out / "results.md").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import __version__, card, state
from .backend import Backend, BackendError
from .compact import compact
from .compose import MODEL_INPUT_CHARS, compose, messages_for
from .model import Bundle, Doc, Session
from .recipients import RECIPIENTS, get_recipient
from .redact import home_to_tilde, redact, redact_bundle
from .sources import SOURCES, detect_sources, get_source
from .sources import git as gitsrc


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _when(s: Session) -> str:
    """Last activity, as `YYYY-MM-DD HH:MM`."""
    return s.ended[:16].replace("T", " ") or "no timestamp"


def build_bundle(cwd: str, source_name: str | None, recipient_name: str, *, sessions: int,
                 session_id: str | None, include_git: bool = True) -> Bundle:
    if source_name:
        src = get_source(source_name)
    else:
        found = detect_sources(cwd)
        if not found:
            raise SystemExit(
                f"no source agent state found for {cwd}. Known sources: {', '.join(SOURCES)}. "
                "Pass --from explicitly or run from the project directory the agent worked in."
            )
        src = found[0]
        if len(found) > 1:
            _log(f"multiple sources detected ({', '.join(s.name for s in found)}); using {src.name}. "
                 "Pass --from to choose.")
    b = Bundle(source=src.name, recipient=recipient_name, cwd=str(Path(cwd).resolve()))
    b.identity = src.identity_docs()
    b.project = src.project_docs(cwd)
    if include_git:
        snap = gitsrc.snapshot(cwd)
        if snap:
            b.project.append(Doc(path="(git)", content=snap, origin="git"))
    try:
        b.sessions = src.sessions(cwd, limit=sessions, session_id=session_id)
    except FileNotFoundError as e:
        raise SystemExit(str(e)) from e
    if not b.sessions:
        b.notes.append(f"no {src.display} sessions found for this directory; handoff has instruction layers only")
    for s in b.sessions:
        _log(f"using {src.display} session {s.id[:8]} ({_when(s)}, {len(s.messages)} messages)")
    if b.sessions and not session_id:
        _log("  (`handoff sources -v` lists the others; `--session <id>` picks one)")
    # delta
    base = state.baseline(cwd, recipient_name)
    d = gitsrc.delta(cwd, base.get("last_sha", ""), base.get("at", ""))
    if base and b.sessions:
        last_uuid = base.get("last_message_uuid", "")
        newest = b.sessions[0]
        if base.get("last_session_id") == newest.id and last_uuid:
            idx = next((i for i, m in enumerate(newest.messages) if m.uuid == last_uuid), None)
            d.new_messages = len(newest.messages) - idx - 1 if idx is not None else len(newest.messages)
        else:
            d.new_messages = sum(len(s.messages) for s in b.sessions)
    b.delta = d if gitsrc.is_repo(cwd) else None
    return b


def cmd_create(a: argparse.Namespace) -> int:
    cwd = str(Path(a.path).resolve())
    try:
        rcp = get_recipient(a.to)
    except KeyError as e:
        raise SystemExit(str(e)) from e
    bundle = build_bundle(cwd, a.source, rcp.name, sessions=a.sessions, session_id=a.session)
    n = redact_bundle(bundle, pii=not a.keep_pii)
    if n:
        what = "secret-looking" if a.keep_pii else "secret-looking or personal (email, phone)"
        bundle.notes.append(f"{n} {what} value(s) redacted")
    bundle.card = card.build(bundle.sessions[0] if bundle.sessions else None, with_user=a.brief)
    rcp.brief = a.brief
    budget = a.budget or rcp.profile.context_budget
    full = bundle
    bundle = compact(full, budget)
    if full.chars != bundle.chars:
        _log(f"compacted {full.chars:,} → {bundle.chars:,} chars (budget {budget:,})")

    backend = None
    if not a.no_llm:
        try:
            backend = Backend.from_env(a.backend, a.model, a.base_url)
        except BackendError as e:
            raise SystemExit(str(e)) from e
        if backend is None:
            _log("no backend configured (HANDOFF_BASE_URL / --backend); producing --no-llm handoff")

    # The model reads more than the recipient will: it condenses, the recipient only reads.
    model_bundle = compact(full, max(budget, MODEL_INPUT_CHARS))
    if a.dry_run:
        msgs = messages_for(model_bundle, rcp.profile)
        dest = f"{backend.model} at {backend.host}" if backend else "(no backend)"
        print(f"# would send {sum(len(m['content']) for m in msgs):,} chars to {dest}\n")
        for m in msgs:
            print(f"===== {m['role']} =====\n{m['content']}\n")
        return 0

    composed = None
    if backend is not None:
        _log(f"sending {model_bundle.chars:,} chars to {backend.model} at {backend.host}")
        composed, warn = compose(model_bundle, rcp.profile, backend)
        if composed is None:
            _log(f"model step failed: {warn}. Falling back to --no-llm output.")
            bundle.notes.append("model step failed; next.md is not synthesized")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(a.out) if a.out else Path(cwd) / ".handoff" / f"{stamp}-{rcp.name}"
    layers_dir = str(out_dir.resolve().relative_to(Path(cwd).resolve())
                     if out_dir.resolve().is_relative_to(Path(cwd).resolve()) else out_dir.resolve())
    if not a.keep_pii:
        layers_dir = home_to_tilde(layers_dir)  # added after redaction ran, so scrub it here
    files = rcp.render(bundle, composed, layers_dir)
    # The budget is on what the recipient reads: headers, next.md and delta.md count too. Squeezing
    # further never drops a human turn; if those alone do not fit, the budget gives.
    for _ in range(4):
        over = len(rcp.install_block(files)) - budget
        if over <= 0 or bundle.chars <= 500:
            break
        # rendering adds markup per message that bundle.chars does not count: overshoot the cut
        bundle = compact(bundle, max(500, bundle.chars - over * 5 // 4), keep_users=True)
        files = rcp.render(bundle, composed, layers_dir)
    if len(rcp.install_block(files)) > budget:
        _log(f"note: installed block is {len(rcp.install_block(files)):,} chars, over the {budget:,} budget")
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (out_dir / name).write_text(content)
    # --out outside the project means "do not touch the project": no latest link, no state.json.
    in_project = out_dir.resolve().is_relative_to(Path(cwd).resolve())
    latest = Path(cwd) / ".handoff" / "latest"
    if in_project:
        try:
            latest.parent.mkdir(parents=True, exist_ok=True)
            if latest.is_symlink() or latest.exists():
                latest.unlink()
            target = out_dir.name if out_dir.parent.resolve() == latest.parent.resolve() else out_dir.resolve()
            latest.symlink_to(target)
        except OSError:
            pass
    _log(f"wrote {out_dir}/HANDOFF.md ({len(files['HANDOFF.md']):,} chars)")

    if a.install:
        p = rcp.install(cwd, files)
        _log(f"installed into {p}")
    if a.stdout:
        print(files["HANDOFF.md"])

    if not in_project:
        _log("output is outside the project; .handoff/state.json not updated (next delta unchanged)")
        return 0
    newest = bundle.sessions[0] if bundle.sessions else None
    state.record(
        cwd, rcp.name,
        sha=gitsrc.head_sha(cwd), source=bundle.source,
        session_id=newest.id if newest else "",
        last_message_uuid=newest.messages[-1].uuid if newest and newest.messages else "",
        output_dir=str(out_dir),
    )
    return 0


def cmd_sources(a: argparse.Namespace) -> int:
    cwd = str(Path(a.path).resolve())
    for name, cls in SOURCES.items():
        s = cls()
        paths = s.session_paths(cwd)
        docs = s.project_docs(cwd)
        ident = s.identity_docs()
        flag = "*" if (paths or docs) else " "
        print(f"{flag} {name:12s} sessions={len(paths):<3d} project_docs={len(docs):<2d} identity_docs={len(ident)}")
        if a.verbose:
            if paths:
                print(f"      sessions in {home_to_tilde(str(paths[0].parent))}, newest first:")
            for p in paths[:5]:
                try:
                    sess = s.read_session(p)
                except (OSError, ValueError):
                    continue
                first = next((m.text for m in sess.messages if m.role == "user"), "")
                first = " ".join(redact(first)[0].split())
                first = first if len(first) <= 70 else first[:69] + "…"
                print(f"      {_when(sess)}  {p.stem[:8]}  {len(sess.messages):>4d} msgs  {first}")
            for d in docs + ident:
                print(f"      {d.path} ({d.chars} chars)")
    return 0


def cmd_profiles(a: argparse.Namespace) -> int:
    for name in RECIPIENTS:
        p = get_recipient(name).profile
        if a.json:
            print(json.dumps(p.__dict__, indent=2))
            continue
        print(f"{name:12s} → {p.instruction_file:28s} budget={p.context_budget:,}")
        if a.verbose:
            print(f"    style: {p.style}")
            print(f"    notes: {p.notes}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="handoff",
                                 description="Hand off coding-agent context to another model or app.")
    ap.add_argument("--version", action="version", version=f"handoff {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="build a handoff for the current project")
    c.add_argument("--to", required=True, help=f"recipient: {', '.join(RECIPIENTS)}")
    c.add_argument("--from", dest="source", help=f"source (auto-detected): {', '.join(SOURCES)}")
    c.add_argument("--path", default=os.getcwd(), help="project directory (default: cwd)")
    c.add_argument("--sessions", type=int, default=1, help="how many recent sessions to include")
    c.add_argument("--session", help="specific session id (prefix ok)")
    c.add_argument("--budget", type=int, help="character budget override")
    c.add_argument("--no-llm", action="store_true", help="deterministic output, no model call")
    c.add_argument("--backend", choices=["openai", "ollama", "lmstudio", "openrouter"],
                   help="backend preset (or set HANDOFF_BASE_URL)")
    c.add_argument("--model", help="model name (or HANDOFF_MODEL)")
    c.add_argument("--base-url", help="OpenAI-compatible base URL")
    c.add_argument("--install", action="store_true",
                   help="write into the recipient's instruction file (marker block)")
    c.add_argument("--out", help="output directory (default .handoff/<ts>-<recipient>)")
    c.add_argument("--brief", action="store_true",
                   help="install only next.md and delta.md; the transcript stays in the output directory")
    c.add_argument("--stdout", action="store_true", help="also print HANDOFF.md")
    c.add_argument("--keep-pii", action="store_true",
                   help="keep emails, phone numbers and the home path (secrets are always redacted)")
    c.add_argument("--dry-run", action="store_true",
                   help="print exactly what would be sent to the backend, write nothing")
    c.set_defaults(fn=cmd_create)

    s = sub.add_parser("sources", help="show what source state exists for this project")
    s.add_argument("--path", default=os.getcwd())
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_sources)

    p = sub.add_parser("profiles", help="list recipient profiles")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_profiles)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

import time

from handoff.redact import redact


def test_patterns():
    txt = ("key sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcd and ghp_" + "a" * 36
           + "\nexport OPENAI_API_KEY=abcdefghijklmnop\nAWS_ACCESS_KEY_ID=AKIAABCDEFGHIJKLMNOP\nPORT=8080\n")
    out, n = redact(txt)
    assert "sk-ant" not in out and "ghp_a" not in out and "abcdefghijklmnop" not in out
    assert "AKIA" not in out
    assert "PORT=8080" in out
    assert n >= 4


def test_leaves_normal_text():
    s = "Run `uv run pytest` then commit. The token budget is 24000."
    assert redact(s) == (s, 0)


def test_no_false_positives_seen_in_real_sessions():
    """Each line was wrongly redacted in a real Codex session."""
    for s in [
        "| failed jobs: **11** - Task-specific-retry-backoff-settings |",
        "token=next((p.read_text().strip() for p in candidates if p.exists()), None)",
        "TOKEN_FILE_PRESENT=/Users/me/.cache/tool/token",
        "    sensitive_tokens: set[str],",
        # unclosed calls (`private_key=str(project_root / ...`) are redacted: the value stops at the
        # space, and a call is only skipped when it is visibly closed
        "        private_key=str(project_root)",
        'API_KEY="$OPENAI_API_KEY"',
        'api_key_file="/Users/me/.service_api_key"',
        "rg -n 'Authorization: Bearer [A-Za-z0-9]|SERVICE_API_KEY=[A-Za-z0-9._-]{24,})'",
        "awk '/^(export|OPENAI_API_KEY=|secret|key|token)/'",
        '{"output":"OPENAI_API_KEY=UNSET\\nSERVICE_API_KEY=UNSET\\n"}',
        '{"yield_time_ms":10000,"max_output_tokens":20000000}',
        '"cached_tokens": 123456789, "original_token_count": 987654321',
        '"input_tokens": 4769438, "output_tokens": 101524,…',
    ]:
        assert redact(s) == (s, 0), s


def test_tokens_missed_in_real_sessions():
    hf, rp = "hf_" + "AbCdEfGhIjKlMnOpQrStUvWxYz01234567", "rpa_" + "A1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q7R8"
    out, n = redact(f"cd x && HF_TOKEN={hf} python train.py; RUNPOD_API_KEY={rp} runpodctl get pod")
    assert hf not in out and rp not in out and n == 2
    out, _ = redact("here : sk-proj-" + "Ab1_" * 10)
    assert "[REDACTED:openai]" in out


def test_recall_kept_while_fixing_false_positives():
    """A missed key leaks to a third-party model; a false positive only loses context."""
    ant, proj = "sk-ant-api03-" + "Ab1_" * 8, "sk-proj-" + "Cd2-" * 8
    for leak in [
        '{"output":"done\\n' + ant + '\\n"}',  # JSON-escaped tool output
        "https://x.test/cb?key%3D" + proj,
        'password = "p@ss!w0rd#2026"',
        'db_password: "correct-horse-battery"',
        '"api_key": "abcd1234efgh5678"',
        "cd x && OPENAI_API_KEY=Zx9_Yw8-Vu7_Ts6 python run.py",
    ]:
        out, n = redact(leak)
        assert n >= 1, leak
        for secret in (ant, proj, "p@ss!w0rd#2026", "correct-horse-battery", "abcd1234efgh5678",
                       "Zx9_Yw8-Vu7_Ts6"):
            assert secret not in out, leak


def test_recall_for_unusual_keys_and_values():
    or_key = "sk-or-v1-" + "0123456789abcdef" * 4
    for text, secret in [
        ("OPENROUTER=" + or_key, or_key),
        ("key: sk-abc_def-GHI123jkl-mno456", "sk-abc_def-GHI123jkl-mno456"),
        ("GITHUB_TOKENS=tok_9f8e7d6c5b4a", "tok_9f8e7d6c5b4a"),
        ('PASSWORD="$ecretPass99"', "$ecretPass99"),
        ("SECRET=.hidden-Value-77", ".hidden-Value-77"),
        ("password=(Paren)Pass42", "(Paren)Pass42"),
        ("API_KEY={curly}Key8812", "{curly}Key8812"),
        (r"DB_PASSWORD=back\slash\Pass9", r"back\slash\Pass9"),
        ('{\\"api_key\\": \\"abcd1234efgh5678\\"}', "abcd1234efgh5678"),
    ]:
        out, n = redact(text)
        assert secret not in out and n >= 1, text


def test_long_values_redacted_quickly():
    t0 = time.perf_counter()
    out, n = redact("API_KEY=" + "a(" * 50_000)
    assert n == 1 and time.perf_counter() - t0 < 1


def test_skips_are_narrow():
    for text, secret in [
        ("PASSWORD=pa|ss|word123", "pa|ss|word123"),
        ("SECRET=abc]*xyz99876", "abc]*xyz99876"),
        ("API_TOKEN=12345678901", "12345678901"),
        ('"auth_token": 987654321012', "987654321012"),
        ("SECRET=[REDACTED:env]leftover-Secret9", "leftover-Secret9"),
        ("password=Pass(word)123", "Pass(word)123"),
    ]:
        out, n = redact(text)
        assert secret not in out and n >= 1, text


def test_more_credential_formats():
    secrets = {
        "postgres://app:Hunter2pass@db.internal:5432/x": "Hunter2pass",
        "redis://:s3cretRedis99@cache:6379": "s3cretRedis99",
        "stripe sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc": "4eC39HqLyjWDarjtT1zdp7dc",
        "whsec_" + "MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw": "MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw",
        "client_secret GOCSPX-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3": "a1B2c3D4e5F6g7H8i9J0k1L2m3",
        "ya29." + "a0AfH6SMBx1y2z3A4b5C6d7E8f9G0h": "a0AfH6SMBx1y2z3A4b5C6d7E8f9G0h",
        "glpat-" + "xYz12AbC34dEf56GhI78jK": "xYz12AbC34dEf56GhI78jK",
        "npm_" + "a" * 18 + "B" * 18: "a" * 18 + "B" * 18,
        "SG." + "A" * 22 + "." + "b" * 43: "b" * 43,
        "https://hooks.slack.com/services/T000/B000/XXXXsecretXXXX": "XXXXsecretXXXX",
        "https://discord.com/api/webhooks/123456/abcDEF_secret-token": "abcDEF_secret-token",
        "gsk_" + "Q" * 44: "Q" * 44,
        "xai-" + "R" * 44: "R" * 44,
        "r8_" + "S" * 37: "S" * 37,
        "curl -H 'Authorization: Basic dXNlcjpwYXNzd29yZA=='": "dXNlcjpwYXNzd29yZA==",
        "curl -H 'Cookie: session=abc123def456; csrftoken=zzz999'": "abc123def456",
    }
    for text, secret in secrets.items():
        out, n = redact(text)
        assert secret not in out and n >= 1, text
    assert redact("postgres://app:Hunter2pass@db/x")[0] == "postgres://app:[REDACTED:url_password]@db/x"


def test_personal_data_and_home_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "alice"))
    text = (f"cd {tmp_path}/alice/Documents/proj && mail jane.doe+vc@fund.example.com "
            "or call (609) 555-0123 / +1 609-555-0199")
    out, n = redact(text)
    assert "jane.doe" not in out and "555-0123" not in out and "555-0199" not in out
    assert "cd ~/Documents/proj" in out and str(tmp_path) not in out
    assert n == 3  # the home path is normalized, not counted
    kept, _ = redact(text, pii=False)
    assert "jane.doe+vc@fund.example.com" in kept and "(609) 555-0123" in kept
    for s in ["git clone git@github.com:o/r.git", "Co-Authored-By: X <noreply@anthropic.com>",
              "2026-10-30T19:14:16Z somepkg-6.0.1.0-cp312", "port 8000, pid 12345, 3.14159",
              "defaults:\n  - env/maze_small@env.train\n  - model/small@actor.model"]:
        assert redact(s) == (s, 0), s


def test_home_dir_glued_to_other_text_is_still_rewritten(monkeypatch, tmp_path):
    home = f"{tmp_path}/home/alice"  # does not exist, so it resolves to itself
    monkeypatch.setenv("HOME", home)
    out, _ = redact(f"gcc -I{home}/inc; traceback .../{home}/x.py")
    assert "alice" not in out


def test_symlinked_home_dir_is_rewritten_in_both_forms(monkeypatch, tmp_path):
    # /home/me -> /storage/home/me on many clusters; Claude Code records the resolved cwd
    real = tmp_path / "storage" / "home" / "alice"
    real.mkdir(parents=True)
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "alice").symlink_to(real)
    monkeypatch.setenv("HOME", str(tmp_path / "home" / "alice"))
    out, _ = redact(f"cd {real}/proj && cat {tmp_path}/home/alice/notes")
    assert out == "cd ~/proj && cat ~/notes"


def test_bundle_paths_notes_and_delta_are_scrubbed(monkeypatch, tmp_path):
    from handoff.model import Bundle, Delta, Doc
    from handoff.redact import redact_bundle

    home = tmp_path / "alice"
    monkeypatch.setenv("HOME", str(home))
    b = Bundle(source="t", recipient="codex", cwd=f"{home}/proj",
               project=[Doc(f"{home}/proj/AGENTS.md", "ping ops@corp.example.com", "t")],
               notes=[f"read {home}/proj/x"],
               delta=Delta(commits=["abc fix: rotate OPENAI_API_KEY=sk-proj-" + "Zz9_" * 8],
                           status=f" M {home}/proj/a.py"))
    redact_bundle(b)
    assert b.cwd == "~/proj" and b.project[0].path == "~/proj/AGENTS.md"
    assert "ops@corp" not in b.project[0].content and b.notes == ["read ~/proj/x"]
    assert "sk-proj" not in b.delta.commits[0] and str(home) not in b.delta.status


def test_url_password_with_reserved_characters():
    for url, pw in [("postgres://app:p@ss/w:rd9@db.example.com:5432/x", "p@ss/w:rd9"),
                    ("mongodb://u:a@b@c@cluster0.example.net/?retryWrites=true", "a@b@c"),
                    ("redis://:plain99@cache:6379", "plain99"),
                    ("https://u:" + "L0ng" * 100 + "@host/x", "L0ng" * 100)]:
        out, n = redact(url)
        assert n == 1 and pw not in out and "[REDACTED:url_password]@" in out, url


def test_url_password_scan_is_bounded():
    t0 = time.perf_counter()
    redact("://u:" * 50_000 + "@host")
    assert time.perf_counter() - t0 < 1

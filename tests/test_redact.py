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

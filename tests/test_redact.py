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

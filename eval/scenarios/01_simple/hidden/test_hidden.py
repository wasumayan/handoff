import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent


def run(*args):
    return subprocess.run([sys.executable, "app.py", *args], cwd=HERE, capture_output=True, text=True)


def test_verbose_emits_debug():
    r = run("bob", "--verbose")
    assert r.returncode == 0
    assert "DEBUG verbose mode on" in r.stderr
    assert "INFO hello, bob" in r.stderr


def test_quiet_without_flag():
    r = run("bob")
    assert r.returncode == 0
    assert "DEBUG" not in r.stderr
    assert "hello, bob" in r.stderr


def test_no_print():
    assert "print(" not in (HERE / "app.py").read_text()

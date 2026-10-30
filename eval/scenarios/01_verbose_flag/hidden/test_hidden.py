import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent


def run(cwd, *args):
    return subprocess.run([sys.executable, str(REPO / "app.py"), *args], cwd=cwd,
                          capture_output=True, text=True)


def test_verbose_logs_debug_to_stderr(tmp_path):
    r = run(tmp_path, "bob", "--verbose")
    assert r.returncode == 0
    assert "DEBUG verbose mode on" in r.stderr
    assert "INFO hello, bob" in r.stderr


def test_verbose_writes_debug_log_file(tmp_path):
    run(tmp_path, "bob", "--verbose")
    log = tmp_path / "debug.log"
    assert log.is_file() and "verbose mode on" in log.read_text()


def test_quiet_without_flag(tmp_path):
    r = run(tmp_path, "bob")
    assert r.returncode == 0 and "DEBUG" not in r.stderr and "hello, bob" in r.stderr
    assert not (tmp_path / "debug.log").exists()


def test_logsetup_untouched():
    assert hashlib.sha256((REPO / "logsetup.py").read_bytes()).hexdigest() == "e870e74366c8cc29d527105d6aa4760887351c748667b4d176c29b5deef00489"

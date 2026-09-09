"""A disconnected deployment releases its lock and leaves no running child work."""

import fcntl
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("disconnect", [False, True])
def test_remote_cancellation_reaps_work_and_releases_lock(tmp_path, disconnect):
    lock = tmp_path / "lock"
    child_id = tmp_path / "child"
    supervisor_id = tmp_path / "supervisor"
    child = (
        "import os,time; from pathlib import Path; "
        f"Path({str(child_id)!r}).write_text(str(os.getpid())); time.sleep(60)"
    )
    supervisor = (
        "import runpy,fcntl; "
        f"m=runpy.run_path({str(ROOT / 'scripts/operations/remote.py')!r}); "
        "m['supervise_connection'](); "
        f"lock=open({str(lock)!r},'a'); fcntl.flock(lock,fcntl.LOCK_EX); "
        f"m['run']({sys.executable!r},'-c',{child!r})"
    )
    launcher = (
        "import subprocess; from pathlib import Path; "
        f"p=subprocess.Popen([{sys.executable!r},'-c',{supervisor!r}]); "
        f"Path({str(supervisor_id)!r}).write_text(str(p.pid)); p.wait()"
    )
    parent = subprocess.Popen([sys.executable, "-c", launcher])
    try:
        deadline = time.monotonic() + 10
        while not child_id.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert child_id.exists(), "child work did not start"
        if disconnect:
            parent.kill()
            parent.wait(timeout=3)
        else:
            os.kill(int(supervisor_id.read_text()), signal.SIGTERM)
        with lock.open("a") as stream:
            deadline = time.monotonic() + 10
            while True:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    assert time.monotonic() < deadline, "deployment lock remained held"
                    time.sleep(0.05)
        with pytest.raises(ProcessLookupError):
            os.kill(int(child_id.read_text()), 0)
    finally:
        for path in (supervisor_id, child_id):
            if path.exists():
                try:
                    os.kill(int(path.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=3)


def test_storage_check_runs_without_site_packages():
    result = subprocess.run(
        [sys.executable, "-S", str(ROOT / "scripts/operations/check_storage.py"), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

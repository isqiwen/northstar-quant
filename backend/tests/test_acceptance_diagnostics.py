"""Failed installed children retain actionable diagnostics without exposing passwords."""

import runpy
from pathlib import Path

import pytest


def test_failed_child_reports_exit_and_redacted_log(tmp_path, monkeypatch):
    support = Path(__file__).resolve().parents[2] / "scripts/acceptance"
    monkeypatch.syspath_prepend(str(support))
    application = runpy.run_path(str(support / "support/processes.py"))["InstalledApplication"]
    executable = tmp_path / "northstar"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import os,sys\n"
        "print('startup failed: '+os.environ['NORTHSTAR_DATABASE_URL'],flush=True)\n"
        "sys.exit(7)\n"
    )
    executable.chmod(0o700)
    import os

    env = {
        **os.environ,
        "NORTHSTAR_DATABASE_URL": "postgresql+psycopg://test:secret-diagnostic-password@host/db",
    }
    app = application(str(executable), tmp_path, env)
    with pytest.raises(RuntimeError) as failure:
        with app.api("data-api"):
            pytest.fail("failed process must not become ready")
    message = str(failure.value)
    assert "exit=7" in message and "startup failed" in message
    assert "secret-diagnostic-password" not in message
    assert "secret-diagnostic-password" not in app.logs()

"""Dependency bootstrap failure and repeat deployment must not alter running services."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def bootstrap(tmp_path, monkeypatch):
    source = tmp_path / "dependencies.py"
    source.write_text(
        (ROOT / "scripts/operations/dependencies.py")
        .read_text()
        .replace("/opt/northstar", str(tmp_path))
    )
    spec = importlib.util.spec_from_file_location("dependencies", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    private = tmp_path / "private.env"
    private.write_text("secret")
    private.chmod(0o600)
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)
    return module, {
        "app": "data-hub",
        "env_file": str(private),
        "directory": str(tmp_path),
        "directory_program": "# directory preparation",
    }


def test_prepared_host_does_not_install_or_restart_services(bootstrap, monkeypatch):
    module, request = bootstrap
    monkeypatch.setattr(module, "available", lambda *args: True)
    monkeypatch.setattr(module.shutil, "which", lambda name: name)
    calls = []
    monkeypatch.setattr(module, "admin", lambda *args: calls.append(args))
    monkeypatch.setattr(module, "run", lambda *args: calls.append(args))
    module.prepare(request)
    assert len(calls) == 1 and calls[0][1:3] == ("-c", request["directory_program"])


@pytest.mark.parametrize("app", ["data-hub", "live", "database"])
def test_fresh_host_installs_tools_and_verifies_them(bootstrap, monkeypatch, app):
    module, request = bootstrap
    request["app"] = app
    original = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path, *a, **kw: (
            "ID=debian\nVERSION_CODENAME=bookworm\n"
            if str(path) == "/etc/os-release"
            else original(path, *a, **kw)
        ),
    )
    monkeypatch.setattr(
        Path,
        "exists",
        lambda path: True if str(path) == "/etc/os-release" else path.is_file() or path.is_dir(),
    )
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    counts = {}

    def available(*args):
        counts[args] = counts.get(args, 0) + 1
        return args in (("docker", "info"), ("git", "--version")) or counts[args] > 1

    monkeypatch.setattr(module, "available", available)
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout=""))
    monkeypatch.setattr(module.subprocess, "check_output", lambda *a, **kw: "amd64\n")
    calls = []
    monkeypatch.setattr(module, "admin", lambda *args: calls.append(args))
    monkeypatch.setattr(module, "run", lambda *args: calls.append(args))
    module.prepare(request)
    assert any("docker-ce" in call for call in calls)
    assert any("docker-compose-plugin" in call for call in calls)
    assert any("uv==0.11.6" in call for call in calls)
    assert not any("remove" in call or "restart" in call for call in calls)


def test_existing_docker_access_failure_does_not_restart_daemon(bootstrap, monkeypatch):
    module, request = bootstrap
    monkeypatch.setattr(module, "available", lambda *args: args != ("docker", "info"))
    monkeypatch.setattr(module.shutil, "which", lambda name: name)
    calls = []
    monkeypatch.setattr(module, "admin", lambda *args: calls.append(args))
    with pytest.raises(ValueError, match="Docker 不可访问"):
        module.prepare(request)
    assert len(calls) == 1 and calls[0][1:3] == ("-c", request["directory_program"])

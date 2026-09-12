"""Dependency bootstrap failure and repeat deployment must not alter running services."""

from __future__ import annotations

import importlib.util
from pathlib import Path

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
        "docker_program": "# Docker configuration",
    }


def test_prepared_host_does_not_install_or_restart_services(bootstrap, monkeypatch):
    module, request = bootstrap
    monkeypatch.setattr(module, "available", lambda *args: True)
    calls = []
    monkeypatch.setattr(module, "admin", lambda *args: calls.append(args))
    monkeypatch.setattr(module, "run", lambda *args: calls.append(args))
    module.prepare(request)
    assert len(calls) == 1
    assert calls[0][1:3] == ("-c", request["directory_program"])


@pytest.mark.parametrize(
    "missing",
    [
        ("git", "--version"),
        ("uv", "--version"),
        ("docker", "compose", "version"),
        ("docker", "buildx", "version"),
    ],
)
def test_missing_dependency_fails_without_host_changes(bootstrap, monkeypatch, missing):
    module, request = bootstrap
    monkeypatch.setattr(module, "available", lambda *args: args != missing)
    monkeypatch.setattr(module, "admin", lambda *args: pytest.fail("unexpected host mutation"))
    with pytest.raises(ValueError, match="预先安装"):
        module.prepare(request)


def test_existing_docker_access_failure_does_not_restart_daemon(bootstrap, monkeypatch):
    module, request = bootstrap
    monkeypatch.setattr(module, "available", lambda *args: args != ("docker", "info"))
    calls = []
    monkeypatch.setattr(module, "admin", lambda *args: calls.append(args))
    with pytest.raises(ValueError, match="Docker 不可访问"):
        module.prepare(request)
    assert calls == []

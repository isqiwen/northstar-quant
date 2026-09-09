"""Daemon settings survive deployment and reload failures remain retryable."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def docker_config(monkeypatch):
    source = Path(__file__).resolve().parents[2] / "scripts/operations/docker_configuration.py"
    spec = importlib.util.spec_from_file_location("docker_configuration", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    return module


def test_merge_validate_and_reload_without_restart(docker_config, tmp_path, monkeypatch):
    module = docker_config
    path = tmp_path / "daemon.json"
    path.write_text(json.dumps({"log-driver": "local", "registry-mirrors": ["https://old.test"]}))
    path.chmod(0o600)
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda args, **kw: calls.append(args))
    monkeypatch.setattr(
        module.subprocess,
        "check_output",
        lambda *a, **kw: json.dumps(module.MIRRORS if len(calls) > 1 else []),
    )
    module.configure(path)
    assert json.loads(path.read_text()) == {
        "log-driver": "local",
        "registry-mirrors": module.MIRRORS,
    }
    assert path.stat().st_mode & 0o777 == 0o600
    assert calls[0][:2] == ["dockerd", "--validate"]
    assert calls[1:] == [["systemctl", "reload", "docker"]]
    module.configure(path)
    assert len(calls) == 2


def test_invalid_configuration_preserves_file(docker_config, tmp_path, monkeypatch):
    path = tmp_path / "daemon.json"
    path.write_text('{"unknown-option":true}')

    def reject(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(docker_config.subprocess, "run", reject)
    with pytest.raises(subprocess.CalledProcessError):
        docker_config.configure(path)
    assert path.read_text() == '{"unknown-option":true}'
    assert list(tmp_path.iterdir()) == [path]


def test_failed_reload_is_retried(docker_config, tmp_path, monkeypatch):
    module = docker_config
    path = tmp_path / "daemon.json"
    path.write_text(json.dumps({"registry-mirrors": module.MIRRORS}))
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda args, **kw: calls.append(args))
    monkeypatch.setattr(module.subprocess, "check_output", lambda *a, **kw: "[]")
    with pytest.raises(ValueError, match="热加载未生效"):
        module.configure(path)
    monkeypatch.setattr(
        module.subprocess,
        "check_output",
        lambda *a, **kw: json.dumps(module.MIRRORS if len(calls) == 2 else []),
    )
    module.configure(path)
    assert calls == [["systemctl", "reload", "docker"]] * 2

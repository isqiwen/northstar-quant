"""Provisioning must use default SSH identity and verify the fixed account before deploy."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def ctl(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts/northstarctl.py"
    spec = importlib.util.spec_from_file_location("northstarctl", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: False)
    return module


def test_existing_ready_account_needs_no_bootstrap_key_or_admin_login(ctl, monkeypatch):
    calls = []
    monkeypatch.setattr(
        ctl.subprocess,
        "run",
        lambda args, **kw: calls.append(args) or SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(ctl, "deployment_key", lambda c: pytest.fail("should reuse ready account"))
    ctl.prepare_account({"host": "hub.local"})
    assert len(calls) == 1
    assert calls[0][calls[0].index("-l") + 1] == "northstar"
    assert "-p" not in calls[0]


@pytest.mark.parametrize("interactive", [False, True])
def test_initialization_uses_default_user_and_port_then_verifies_northstar(
    ctl, monkeypatch, interactive
):
    calls = []
    monkeypatch.setattr(ctl.sys.stdin, "isatty", lambda: interactive)
    monkeypatch.setattr(ctl, "deployment_key", lambda c: "ssh-ed25519 public-key")

    def run(args, **kw):
        calls.append((args, kw))
        return SimpleNamespace(returncode=1 if len(calls) == 1 else 0)

    monkeypatch.setattr(ctl.subprocess, "run", run)
    ctl.prepare_account({"host": "hub.local"})
    probe, setup, verified = [x[0] for x in calls]
    assert probe == verified
    assert "-l" not in setup and "-p" not in setup
    assert ("-tt" in setup) == interactive
    assert setup[-2] == "hub.local"
    assert ("BatchMode=no" in setup) == interactive
    assert calls[1][1]["check"] is True and calls[2][1]["check"] is True


def test_effective_ssh_identity_selects_its_public_key(ctl, monkeypatch, tmp_path):
    key = tmp_path / "host-specific.pub"
    key.write_text("ssh-ed25519 selected-public-key\n")
    calls = []
    monkeypatch.setattr(
        ctl.subprocess,
        "check_output",
        lambda args, **kw: calls.append(args) or f"identityfile {str(key)[:-4]}\n",
    )
    assert ctl.deployment_key({"host": "hub.local"}) == "ssh-ed25519 selected-public-key"
    assert calls == [["ssh", "-G", "-l", "northstar", "hub.local"]]


def test_failed_bootstrap_aborts_before_ready_probe(ctl, monkeypatch):
    import subprocess

    calls = []
    monkeypatch.setattr(ctl, "deployment_key", lambda c: "ssh-ed25519 public-key")

    def run(args, **kw):
        calls.append(args)
        if kw.get("check"):
            raise subprocess.CalledProcessError(1, args)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(ctl.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        ctl.prepare_account({"host": "hub.local"})
    assert len(calls) == 2

"""Host account setup retains authorized keys and rejects privileged account collisions."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def account(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[3] / "scripts/operations/host_account.py"
    spec = importlib.util.spec_from_file_location("host_account", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    home = tmp_path / "home/northstar"
    home.mkdir(parents=True)
    monkeypatch.setattr(module, "DEPLOY_HOME", home)
    monkeypatch.setattr(module, "SUDOERS", tmp_path / "sudoers.d/northstar-deploy")
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.os, "chown", lambda *a: None)
    monkeypatch.setattr(module.os, "fchown", lambda *a: None)
    user = SimpleNamespace(pw_uid=1000, pw_gid=1000, pw_dir=str(home), pw_shell="/bin/bash")
    monkeypatch.setattr(module.pwd, "getpwnam", lambda _: user)
    monkeypatch.setattr(module.shutil, "which", lambda name: name)
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda args, **kw: calls.append(args))
    return module, home, user, calls


def test_repeated_initialization_preserves_keys_and_installs_private_access(account):
    module, home, _, calls = account
    ssh = home / ".ssh"
    ssh.mkdir()
    keys = ssh / "authorized_keys"
    keys.write_text('from="192.0.2.1" ssh-ed25519 existing-key retained\n')
    module.initialize("ssh-ed25519 new-key deployment")
    module.initialize("ssh-ed25519 new-key changed-comment")
    assert keys.read_text() == (
        'from="192.0.2.1" ssh-ed25519 existing-key retained\nssh-ed25519 new-key deployment\n'
    )
    assert keys.stat().st_mode & 0o777 == 0o600
    assert ssh.stat().st_mode & 0o777 == 0o700
    assert module.SUDOERS.stat().st_mode & 0o777 == 0o440
    assert module.SUDOERS.read_bytes() == module.RULE
    assert not any(call[0] == "useradd" for call in calls)
    assert ["visudo", "-c"] in calls


def test_privileged_existing_account_is_not_adopted(account):
    module, home, user, _ = account
    user.pw_uid = 0
    with pytest.raises(ValueError, match="拒绝接管"):
        module.initialize("ssh-ed25519 key deployment")
    assert not (home / ".ssh").exists()
    assert not module.SUDOERS.exists()


def test_existing_ssh_symlink_is_not_followed(account):
    module, home, _, _ = account
    target = home.parent / "another-user"
    target.mkdir()
    (home / ".ssh").symlink_to(target)
    with pytest.raises(ValueError, match="符号链接"):
        module.initialize("ssh-ed25519 key deployment")
    assert list(target.iterdir()) == []


def test_first_initialization_creates_account_and_preserves_docker_access(account, monkeypatch):
    module, _, user, calls = account
    created = False

    def lookup(name):
        if not created:
            raise KeyError(name)
        return user

    def run(args, **kwargs):
        nonlocal created
        calls.append(args)
        if args[0] == "useradd":
            created = True

    monkeypatch.setattr(module.pwd, "getpwnam", lookup)
    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module.grp, "getgrnam", lambda name: object())
    module.initialize("ssh-ed25519 key deployment")
    assert created
    assert ["usermod", "--append", "--groups", "docker", "northstar"] in calls
    module.initialize("ssh-ed25519 key deployment")
    assert sum(call[0] == "useradd" for call in calls) == 1

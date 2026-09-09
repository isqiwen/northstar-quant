"""Prevent a local directory or a wrong/read-write share from passing deployment admission."""

import pytest

from northstar_quant.data_management.storage_identity import verify_mount


def test_nfs_guard_rejects_wrong_mounts_and_research_write_access():
    expected = {
        "mount": "/mnt/northstar",
        "server": "192.0.2.8",
        "export": "/verified-export",
        "version": "4",
        "mode": "ro",
    }
    actual = {
        "target": "/mnt/northstar",
        "source": "192.0.2.8:/verified-export",
        "fstype": "nfs4",
        "options": "ro,hard,vers=4.1,proto=tcp",
    }
    verify_mount(expected, actual)
    for changed in (
        {},
        {**actual, "fstype": "ext4"},
        {**actual, "target": "/mnt"},
        {**actual, "source": "192.0.2.8:/other"},
        {**actual, "options": "rw,hard,vers=4.1"},
        {**actual, "options": "ro,soft,vers=4.1"},
        {**actual, "options": "ro,hard,vers=3"},
    ):
        with pytest.raises(ValueError):
            verify_mount(expected, changed)


@pytest.mark.parametrize("fstype", ["nfs", "nfs4", "cifs", "tmpfs", "overlay", ""])
def test_local_storage_rejects_network_and_temporary_filesystems(tmp_path, fstype):
    from northstar_quant.data_management.storage_identity import verify_local_directory

    with pytest.raises(ValueError, match="Local storage requires"):
        verify_local_directory(
            tmp_path.resolve(), {"fstype": fstype, "options": "rw"}, writable=True
        )


def test_local_storage_requires_existing_real_directory_and_writable_filesystem(tmp_path):
    from northstar_quant.data_management.storage_identity import verify_local_directory

    root = tmp_path.resolve() / "market"
    observed = {"fstype": "ext4", "options": "rw,relatime"}
    with pytest.raises(ValueError, match="existing"):
        verify_local_directory(root, observed, writable=True)
    assert not root.exists()
    root.mkdir()
    verify_local_directory(root, observed, writable=True)
    alias = root.parent / "alias"
    alias.symlink_to(root)
    with pytest.raises(ValueError, match="symlinks"):
        verify_local_directory(alias, observed, writable=True)
    with pytest.raises(ValueError, match="read-only"):
        verify_local_directory(root, {**observed, "options": "ro"}, writable=True)
    verify_local_directory(root, {**observed, "options": "ro"}, writable=False)


def test_local_preflight_does_not_resolve_nas_or_accept_nfs_fallback(tmp_path, monkeypatch):
    import importlib.util
    import json
    import socket
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    from uuid import uuid4

    spec = importlib.util.spec_from_file_location(
        "check_storage", Path(__file__).resolve().parents[3] / "scripts/operations/check_storage.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path.resolve()
    environment = {"NORTHSTAR_STORAGE_MODE": "local", "NORTHSTAR_NAS_ADDRESS": "absent.invalid"}
    volumes = []
    for share in ("source", "market", "research"):
        (root / share).mkdir()
        environment[f"NORTHSTAR_{share.upper()}_STORAGE_ID"] = str(uuid4())
        environment[f"NORTHSTAR_{share.upper()}_NFS_EXPORT"] = ""
        volumes.append(
            {
                "type": "bind",
                "source": str(root / share),
                "target": f"/var/lib/northstar/{share}",
                "read_only": share == "research",
            }
        )
    config = {"services": {"storage-check": {"environment": environment, "volumes": volumes}}}
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        payload = (
            config
            if command[0] == "docker"
            else {"filesystems": [{"fstype": "ext4", "options": "rw"}]}
        )
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

    def dns(*args):
        raise AssertionError("local mode must not contact NAS")

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(socket, "gethostbyname", dns)
    monkeypatch.setattr(sys, "argv", ["check_storage.py", "--app", "data_hub"])
    module.main()
    assert len([command for command in commands if command[0] == "findmnt"]) == 3
    environment["NORTHSTAR_STORAGE_MODE"] = "wrong"
    with pytest.raises(SystemExit):
        module.main()
    environment["NORTHSTAR_STORAGE_MODE"] = "local"
    volumes[1]["source"] = volumes[0]["source"]
    with pytest.raises(SystemExit):
        module.main()

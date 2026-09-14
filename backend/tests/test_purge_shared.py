"""Full uninstall deletes project backups without entering unrelated NAS paths."""

import json
from pathlib import Path
from uuid import uuid4

import pytest


def share(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "purge_shared", Path(__file__).parents[2] / "scripts/operations/purge_shared.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "MARKET", tmp_path / "quant")
    monkeypatch.setattr(module.socket, "getaddrinfo", lambda *a: [])
    module.MARKET.mkdir()
    identity = str(uuid4())
    (module.MARKET / module.MARKER).write_text(identity)
    host = {
        "preflight": lambda: set(),
        "run": lambda *args: json.dumps(
            {
                "filesystems": [
                    {
                        "target": str(module.MARKET),
                        "source": "nas.local:/quant",
                        "fstype": "nfs4",
                    }
                ]
            }
        ),
    }
    return module, host, identity


def test_full_cleanup_removes_backup_batches_but_preserves_nas_snapshot(tmp_path, monkeypatch):
    module, host, identity = share(tmp_path, monkeypatch)
    for app in ("data-hub", "research"):
        batch = module.MARKET / "backups" / app / "batch"
        batch.mkdir(parents=True)
        (batch / "database.sqlite3").write_bytes(b"private retained facts")
    outside = tmp_path / "unrelated"
    outside.mkdir()
    (outside / "precious").write_bytes(b"other project")
    (module.MARKET / "backups/research/external").symlink_to(outside)
    snapshot = module.MARKET / "@Recently-Snapshot"
    snapshot.mkdir()
    (snapshot / "system-snapshot").symlink_to(outside)
    module.clear_share(host, "nas.local", identity)
    assert not (module.MARKET / "backups").exists()
    assert not (module.MARKET / module.MARKER).exists()
    assert (outside / "precious").read_bytes() == b"other project"
    assert (snapshot / "system-snapshot").is_symlink()


def test_foreign_backup_prevents_any_shared_deletion(tmp_path, monkeypatch):
    module, host, identity = share(tmp_path, monkeypatch)
    (module.MARKET / "backups/other-project").mkdir(parents=True)
    with pytest.raises(ValueError, match="非项目"):
        module.clear_share(host, "nas.local", identity)
    assert (module.MARKET / module.MARKER).exists()

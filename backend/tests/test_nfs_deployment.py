"""Storage routing and failed preparation must never conceal retained market facts."""

import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def nfs(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "northstar_nfs", ROOT / "scripts/operations/nfs.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "MARKET", tmp_path / "market")
    monkeypatch.setattr(module, "STATE", tmp_path / "state")
    monkeypatch.setattr(module, "UNITS", tmp_path / "units")
    monkeypatch.setattr(module, "EXPORTS", tmp_path / "exports")
    return module


@pytest.mark.parametrize("host", ["core.local", "research.local", "storage.local"])
def test_selected_server_preserves_data_writer_and_reader(nfs, host):
    settings = {
        name: {"host": value}
        for name, value in [
            ("database", "core.local"),
            ("data_hub", "core.local"),
            ("research", "research.local"),
        ]
    }
    settings["nfs"] = {"host": host, "user": "qiwen", "port": 22}
    plan = nfs.topology(settings)
    assert plan["server"] == host
    assert plan["writer"] == "core.local" and plan["reader"] == "research.local"
    assert nfs.topology({}) is None


@pytest.mark.parametrize("item", [{"server": "research"}, {"enabled": False}, {}, {"host": ""}])
def test_invalid_nfs_configuration_is_rejected(nfs, item):
    with pytest.raises(ValueError):
        nfs.topology({"nfs": item})


def test_nonempty_directory_rejected_before_install_or_mount(nfs, monkeypatch):
    nfs.MARKET.mkdir()
    evidence = nfs.MARKET / "fixed.parquet"
    evidence.write_bytes(b"fixed evidence")
    monkeypatch.setattr(nfs, "mounted", lambda: None)
    monkeypatch.setattr(
        nfs, "install", lambda *a: pytest.fail("dependency mutation before preflight")
    )
    with pytest.raises(ValueError, match="非空"):
        nfs.prepare_client(
            {"host": "core.local", "server": "research.local", "writer": "core.local"}
        )
    assert evidence.read_bytes() == b"fixed evidence"
    assert not nfs.UNITS.exists()


@pytest.mark.parametrize(
    "change",
    [
        {"source": "other:/market"},
        {"options": "rw,soft,vers=4"},
        {"options": "ro,hard,vers=4"},
        {"fstype": "ext4"},
    ],
)
def test_wrong_existing_mount_is_never_replaced(nfs, change):
    current = {
        "source": "research.local:/market",
        "options": "rw,hard,vers=4",
        "fstype": "nfs4",
    } | change
    with pytest.raises(ValueError):
        nfs.check_client(current, "research.local:/market", "rw")


@pytest.mark.parametrize("host,mode", [("core.local", "rw"), ("research.local", "ro")])
def test_client_boot_order_identity_and_idempotent_setup(nfs, monkeypatch, host, mode):
    nfs.MARKET.mkdir()
    nfs.STATE.mkdir()
    identity = str(uuid4())
    (nfs.MARKET / ".northstar-storage-id").write_text(identity + "\n")
    source = f"storage.local:{nfs.MARKET}"
    monkeypatch.setattr(
        nfs,
        "mounted",
        lambda: {"source": source, "fstype": "nfs4", "options": f"{mode},hard,vers=4"},
    )
    calls = []
    monkeypatch.setattr(nfs, "install", lambda p: calls.append(("install", p)))
    monkeypatch.setattr(nfs, "run", lambda *a: calls.append(a))
    request = {"host": host, "server": "storage.local", "writer": "core.local"}
    nfs.prepare_client(request)
    first = (nfs.UNITS / nfs.UNIT).read_bytes()
    nfs.prepare_client(request)
    nfs.verify_client()
    assert (nfs.UNITS / nfs.UNIT).read_bytes() == first
    assert json.loads((nfs.STATE / "client.json").read_text())["identity"] == identity
    assert list(nfs.MARKET.iterdir()) == [nfs.MARKET / ".northstar-storage-id"]
    assert not any("restart" in call for call in calls)
    assert (
        f"Requires={nfs.UNIT}" in (nfs.UNITS / "docker.service.d/northstar-market.conf").read_text()
    )
    monkeypatch.setattr(nfs, "mounted", lambda: None)
    with pytest.raises(ValueError, match="未就绪"):
        nfs.verify_client()


def test_lost_or_foreign_identity_refuses_start(nfs, monkeypatch):
    nfs.MARKET.mkdir()
    nfs.STATE.mkdir()
    source = f"core.local:{nfs.MARKET}"
    (nfs.STATE / "client.json").write_text(
        json.dumps({"source": source, "mode": "ro", "identity": str(uuid4())})
    )
    (nfs.MARKET / ".northstar-storage-id").write_text(str(uuid4()) + "\n")
    monkeypatch.setattr(
        nfs, "mounted", lambda: {"source": source, "fstype": "nfs4", "options": "ro,hard,vers=4"}
    )
    with pytest.raises(ValueError, match="UUID"):
        nfs.verify_client()


def test_published_objects_readable_but_raw_sources_remain_private(tmp_path):
    import stat

    from northstar_quant.data_management.files import SourceFiles
    from northstar_quant.data_management.publications import _write_publication

    for name, shared in [("source", False), ("market", True)]:
        root = tmp_path / name
        files = SourceFiles(root, shared_read=shared, min_free_bytes=0)
        saved = files.store(b"fixed publication")
        path = root / "objects" / saved.content_hash[:2] / saved.content_hash
        assert files.read(saved.content_hash, saved.byte_count) == b"fixed publication"
        assert bool(stat.S_IMODE(path.stat().st_mode) & 0o004) == shared
        assert bool(stat.S_IMODE(path.parent.stat().st_mode) & 0o005) == shared
        assert not stat.S_IMODE((root / "staging").stat().st_mode) & 0o077
    publication = tmp_path / "market" / "data.parquet"
    _write_publication(publication, b"fixed parquet")
    assert stat.S_IMODE(publication.stat().st_mode) == 0o644


def test_server_exports_only_selected_clients_and_retains_existing_identity(nfs, monkeypatch):
    import os
    from types import SimpleNamespace

    nfs.MARKET.mkdir()
    nfs.STATE.mkdir()
    retained_id = str(uuid4())
    (nfs.MARKET / ".northstar-storage-id").write_text(retained_id + "\n")
    evidence = nfs.MARKET / "fixed.parquet"
    evidence.write_bytes(b"retained evidence")
    evidence.chmod(0o600)
    monkeypatch.setattr(nfs, "mounted", lambda: None)
    monkeypatch.setattr(nfs, "install", lambda p: None)
    monkeypatch.setattr(
        nfs.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
    )
    monkeypatch.setattr(
        nfs.socket,
        "gethostbyname",
        lambda host: {"core.local": "192.0.2.1", "research.local": "192.0.2.2"}[host],
    )
    monkeypatch.setattr(nfs.shutil, "which", lambda p: None)
    calls = []
    monkeypatch.setattr(nfs, "run", lambda *a: calls.append(a))
    request = {"server": "storage.local", "writer": "core.local", "reader": "research.local"}
    nfs.prepare_server(request)
    first = nfs.EXPORTS.read_text()
    assert "192.0.2.1(rw," in first and "192.0.2.2(ro," in first
    assert "all_squash" in first and "no_root_squash" not in first
    assert evidence.read_bytes() == b"retained evidence"
    assert nfs.identity() == retained_id
    assert evidence.stat().st_mode & 0o004
    nfs.prepare_server(request)
    assert nfs.EXPORTS.read_text() == first
    assert not any("restart" in call for call in calls)

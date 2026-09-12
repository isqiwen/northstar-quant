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
    settings["nfs"] = {"host": host}
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
        nfs, "require_client", lambda *a: pytest.fail("dependency mutation before preflight")
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
    source = "storage.local:/quant"
    monkeypatch.setattr(
        nfs,
        "mounted",
        lambda: {"source": source, "fstype": "nfs4", "options": f"{mode},hard,vers=4"},
    )
    calls = []
    monkeypatch.setattr(nfs, "require_client", lambda: calls.append(("require_client",)))
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


@pytest.mark.parametrize("host", ["storage.local", "core.local"])
def test_writer_initializes_empty_external_share_once(nfs, monkeypatch, host):
    nfs.MARKET.mkdir()
    nfs.STATE.mkdir()
    monkeypatch.setattr(
        nfs,
        "mounted",
        lambda: {"source": "storage.local:/quant", "fstype": "nfs4", "options": "rw,hard,vers=4"},
    )
    packages = []
    monkeypatch.setattr(nfs, "require_client", lambda: packages.append("checked"))
    monkeypatch.setattr(nfs, "run", lambda *a: None)
    monkeypatch.setattr(nfs.os, "geteuid", lambda: 0)
    request = {"host": host, "server": "storage.local", "writer": host}
    nfs.prepare(request)
    first = nfs.identity()
    nfs.prepare(request)
    assert nfs.identity() == first
    assert packages == ["checked", "checked"]


def test_reader_cannot_initialize_missing_share_identity(nfs, monkeypatch):
    nfs.MARKET.mkdir()
    nfs.STATE.mkdir()
    monkeypatch.setattr(
        nfs,
        "mounted",
        lambda: {"source": "storage.local:/quant", "fstype": "nfs4", "options": "ro,hard,vers=4"},
    )
    monkeypatch.setattr(nfs, "require_client", lambda: None)
    monkeypatch.setattr(nfs, "run", lambda *a: None)
    with pytest.raises(ValueError, match="首次请先部署"):
        nfs.prepare_client(
            {"host": "research.local", "server": "storage.local", "writer": "core.local"}
        )
    assert not list(nfs.MARKET.iterdir())


def test_missing_client_dependency_does_not_create_mount_configuration(nfs, monkeypatch):
    monkeypatch.setattr(nfs, "mounted", lambda: None)
    monkeypatch.setattr(nfs.shutil, "which", lambda name: None)
    monkeypatch.setattr(nfs, "run", lambda *args: pytest.fail("unexpected systemd change"))
    with pytest.raises(ValueError, match="预先安装 NFS 客户端"):
        nfs.prepare_client({"host": "core.local", "server": "nas.local", "writer": "core.local"})
    assert not nfs.UNITS.exists()
    assert not nfs.MARKET.exists()

"""A missing or wrong NAS mount must never turn into an empty local data store."""

from uuid import uuid4

import pytest

from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.storage_identity import initialize, probe, require_identity


def test_missing_or_wrong_mount_refuses_startup_without_creating_source_directories(
    tmp_path, monkeypatch
):
    identity = str(uuid4())
    root = tmp_path / "unmounted"
    root.mkdir()
    monkeypatch.setenv("NORTHSTAR_DATA_DIR", str(root))
    monkeypatch.setenv("NORTHSTAR_STORAGE_ID", identity)
    with pytest.raises(ValueError, match="mount"):
        SourceFiles.from_environment()
    assert list(root.iterdir()) == []
    initialize(root, str(uuid4()))
    with pytest.raises(ValueError, match="identity"):
        SourceFiles.from_environment()
    with pytest.raises(ValueError, match="identity"):
        initialize(root, identity)


def test_mount_identity_and_probe_preserve_existing_content(tmp_path, monkeypatch):
    identity = str(uuid4())
    initialize(tmp_path, identity)
    monkeypatch.setenv("NORTHSTAR_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NORTHSTAR_STORAGE_ID", identity)
    files = SourceFiles.from_environment()
    saved = files.store(b"retained source evidence")
    initialize(tmp_path, identity)
    probe(tmp_path, identity)
    assert files.read(saved.content_hash, saved.byte_count) == b"retained source evidence"
    assert not list(tmp_path.glob(".storage-probe-*"))
    marker = tmp_path / ".northstar-storage-id"
    marker.unlink()
    marker.symlink_to(tmp_path / "objects")
    with pytest.raises(ValueError):
        require_identity(tmp_path, identity)


def test_initialization_does_not_adopt_existing_unidentified_data(tmp_path):
    path = tmp_path / "valuable"
    path.write_bytes(b"existing data")
    with pytest.raises(ValueError, match="migration"):
        initialize(tmp_path, str(uuid4()))
    assert path.read_bytes() == b"existing data"
    assert not (tmp_path / ".northstar-storage-id").exists()

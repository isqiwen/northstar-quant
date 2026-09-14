"""Protect retained source bytes and resource limits on a real filesystem."""

from hashlib import sha256
from pathlib import Path

import pytest

from northstar_quant.data_management.files import SourceFiles


def test_retained_bytes_survive_original_loss_and_corruption_never_gets_replaced(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original.csv"
    payload = b"\xef\xbb\xbfnot yet parsed\r\n\xff"
    original.write_bytes(payload)
    files = SourceFiles(tmp_path / "archive")
    first = files.store(original.read_bytes())
    original.unlink()
    assert SourceFiles(files.root).read(first.content_hash, first.byte_count) == payload
    assert files.store(payload) == first
    assert len(files.inventory()) == 1
    path = files.root / "objects" / first.content_hash[:2] / first.content_hash
    path.write_bytes(b"x" * first.byte_count)
    assert files.inspect(first.content_hash, first.byte_count) == "CORRUPT"
    with pytest.raises(ValueError, match="digest"):
        files.store(payload)
    with pytest.raises(ValueError, match="identity"):
        files.read("../../original.csv", first.byte_count)
    path.unlink()
    path.symlink_to(tmp_path / "outside")
    assert files.inspect(first.content_hash, first.byte_count) == "CORRUPT"
    with pytest.raises(ValueError, match="unreadable"):
        files.store(payload)


def test_capacity_rejects_without_half_publishing_and_recovers_complete_orphan(
    tmp_path: Path,
) -> None:
    files = SourceFiles(
        tmp_path / "archive", max_file_bytes=10, max_total_bytes=10, min_free_bytes=0
    )
    complete = files.store(b"123456")
    with pytest.raises(ValueError, match="capacity"):
        files.store(b"67890")
    assert files.inventory() == [complete]
    assert files.health()["incomplete_file_count"] == 0
    # Files can precede database registration. Re-reception reuses complete bytes.
    assert complete.content_hash == sha256(b"123456").hexdigest()
    assert files.store(b"123456") == complete
    full = SourceFiles(tmp_path / "no-space", min_free_bytes=2**63)
    with pytest.raises(ValueError, match="free disk"):
        full.store(b"valid")
    assert full.inventory() == []


def test_unlimited_archive_avoids_rescanning_retained_history(tmp_path, monkeypatch):
    files = SourceFiles(tmp_path / "source")
    monkeypatch.setattr(files, "health", lambda: pytest.fail("write scanned archive"))
    first, second = files.store_many([b"first response", b"second response"])
    assert files.store(b"first response") == first
    assert files.read(second.content_hash, second.byte_count) == b"second response"


@pytest.mark.parametrize("free_gib,status", [(20, "OK"), (8, "WARNING"), (4, "LOW")])
def test_disk_reserve_warns_before_blocking_and_preserves_existing_bytes(
    tmp_path, monkeypatch, free_gib, status
):
    from types import SimpleNamespace

    files = SourceFiles(tmp_path / "source")
    first = files.store(b"retained fact")
    monkeypatch.setattr(
        "os.fstatvfs",
        lambda fd: SimpleNamespace(
            f_bavail=free_gib * 1024**3,
            f_frsize=1,
            f_blocks=100 * 1024**3,
            f_files=1000,
            f_favail=100,
        ),
    )
    assert files.capacity()["status"] == status
    if status == "LOW":
        with pytest.raises(ValueError, match="free disk"):
            files.store(b"new fact")
    else:
        files.store(b"new fact")
    assert files.store(b"retained fact") == first
    assert files.read(first.content_hash, first.byte_count) == b"retained fact"
    assert not list((files.root / "staging").iterdir())


def test_exhausted_inodes_reject_before_accepting_bytes(tmp_path, monkeypatch):
    from types import SimpleNamespace

    files = SourceFiles(tmp_path / "source")
    monkeypatch.setattr(
        "os.fstatvfs",
        lambda fd: SimpleNamespace(
            f_bavail=50 * 1024**3,
            f_frsize=1,
            f_blocks=100 * 1024**3,
            f_files=1000,
            f_favail=0,
        ),
    )
    with pytest.raises(ValueError, match="free disk"):
        files.store(b"new fact")
    assert files.inventory() == []

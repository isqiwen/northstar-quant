"""Protect retained source bytes and resource limits on a real filesystem."""

from hashlib import sha256
from pathlib import Path

import pytest

from northstar_quant.data.files import SourceFiles


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

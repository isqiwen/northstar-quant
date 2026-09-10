"""Backup streams keep immutable bytes and quota checks across duplicates and failures."""

import pytest

from northstar_quant.data_management.files import SourceFiles


def test_streamed_copy_deduplicates_without_spending_capacity_twice(tmp_path):
    files = SourceFiles(tmp_path / "archive", max_file_bytes=4, max_total_bytes=8, min_free_bytes=0)
    records = files.store_many(iter([b"abcd", b"abcd", b"efgh"]))
    assert records[0] == records[1]
    assert files.health()["used_bytes"] == 8
    assert [files.read(r.content_hash, r.byte_count) for r in records] == [
        b"abcd",
        b"abcd",
        b"efgh",
    ]
    assert files.store_many([b"abcd"]) == (records[0],)
    with pytest.raises(ValueError, match="capacity"):
        files.store(b"ijkl")


def test_batch_failure_retains_only_complete_verified_objects_and_releases_lock(tmp_path):
    files = SourceFiles(tmp_path / "archive", max_file_bytes=4, max_total_bytes=8, min_free_bytes=0)
    with pytest.raises(ValueError, match="capacity"):
        files.store_many([b"abcd", b"efgh", b"ijkl"])
    records = files.inventory()
    assert len(records) == 2
    assert {files.read(r.content_hash, r.byte_count) for r in records} == {b"abcd", b"efgh"}
    assert list((files.root / "staging").iterdir()) == []
    assert files.store(b"abcd") in records


def test_batch_rejects_corrupted_existing_object(tmp_path):
    files = SourceFiles(tmp_path / "archive", min_free_bytes=0)
    saved = files.store(b"fixed")
    path = files.root / "objects" / saved.content_hash[:2] / saved.content_hash
    path.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="digest"):
        files.store_many([b"fixed"])
    assert path.read_bytes() == b"wrong"

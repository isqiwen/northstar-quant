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


@pytest.mark.parametrize("same_content", [False, True])
def test_parallel_immutable_writes_overlap_and_deduplicate_without_replacement(
    tmp_path, monkeypatch, same_content
):
    import os
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    files = SourceFiles(tmp_path / "archive")
    rendezvous = Barrier(2, timeout=5)
    link = os.link

    def concurrent_link(*args, **kwargs):
        rendezvous.wait()  # Both files are fully flushed before either publishes.
        return link(*args, **kwargs)

    monkeypatch.setattr(os, "link", concurrent_link)
    contents = [
        b"first immutable response",
        b"first immutable response" if same_content else b"second response",
    ]
    with ThreadPoolExecutor(2) as pool:
        saved = list(pool.map(files.store, contents))
    assert len(files.inventory()) == (1 if same_content else 2)
    for item, content in zip(saved, contents):
        assert files.read(item.content_hash, item.byte_count) == content
    assert files.health()["incomplete_file_count"] == 0


def test_parallel_writers_cannot_overrun_explicit_archive_quota(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    files = SourceFiles(tmp_path / "archive", max_file_bytes=10, max_total_bytes=10)
    start = Barrier(2, timeout=5)

    def store(content):
        start.wait()
        try:
            return files.store(content)
        except ValueError as error:
            assert "capacity" in str(error)
            return None

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(store, [b"abcdef", b"123456"]))
    assert sum(item is not None for item in results) == 1
    assert sum(item.byte_count for item in files.inventory()) == 6


def test_orphan_removal_waits_until_parallel_writer_finishes(tmp_path, monkeypatch):
    import os
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    files = SourceFiles(tmp_path / "archive")
    old = files.store(b"orphan")
    entered, release, removing = Event(), Event(), Event()
    link = os.link

    def paused_link(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return link(*args, **kwargs)

    monkeypatch.setattr(os, "link", paused_link)

    def remove():
        removing.set()
        files.remove_verified(old.content_hash, old.byte_count)

    with ThreadPoolExecutor(2) as pool:
        writer = pool.submit(files.store, b"new immutable object")
        assert entered.wait(5)
        removal = pool.submit(remove)
        try:
            assert removing.wait(5)
            assert not removal.done()
            assert files.read(old.content_hash, old.byte_count) == b"orphan"
        finally:
            release.set()
        new = writer.result()
        removal.result()
    assert files.inventory() == [new]


def test_failed_directory_sync_is_not_acknowledged_and_retry_verifies_orphan(tmp_path, monkeypatch):
    files = SourceFiles(tmp_path / "archive")
    sync = files._sync

    def failure(path):
        if path.parent == files.root / "objects":
            raise OSError("injected directory sync failure")
        sync(path)

    monkeypatch.setattr(files, "_sync", failure)
    with pytest.raises(OSError, match="sync failure"):
        files.store(b"complete but unacknowledged")
    monkeypatch.setattr(files, "_sync", sync)
    saved = files.store(b"complete but unacknowledged")
    assert files.inventory() == [saved]
    assert files.read(saved.content_hash, saved.byte_count) == b"complete but unacknowledged"

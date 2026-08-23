from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import stat
import tarfile

import pytest

from scripts.deploy import venv_archive


def _build_archive(*members: tuple[str, str, bytes | str]) -> BytesIO:
    stream = BytesIO()
    with tarfile.open(fileobj=stream, mode="w:") as archive:
        for name, kind, contents in members:
            info = tarfile.TarInfo(name)
            if kind == "directory":
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                archive.addfile(info)
                continue
            if kind == "file":
                payload = contents if isinstance(contents, bytes) else contents.encode("utf-8")
                info.size = len(payload)
                info.mode = 0o755 if name.endswith("northstar") else 0o644
                archive.addfile(info, BytesIO(payload))
                continue
            if kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = str(contents)
                archive.addfile(info)
                continue
            if kind == "hardlink":
                info.type = tarfile.LNKTYPE
                info.linkname = str(contents)
                archive.addfile(info)
                continue
            if kind == "device":
                info.type = tarfile.CHRTYPE
                info.devmajor = 1
                info.devminor = 3
                archive.addfile(info)
                continue
            raise AssertionError(f"unsupported test member kind: {kind}")
    stream.seek(0)
    return stream


@pytest.fixture
def allow_test_directories(monkeypatch: pytest.MonkeyPatch) -> None:
    """The production receiver requires root-owned parents; test temp dirs are not."""

    monkeypatch.setattr(
        venv_archive,
        "_require_root_controlled_directory",
        lambda _path, _label: None,
    )


def test_receive_venv_archive_materializes_a_root_side_copy(
    tmp_path: Path,
    allow_test_directories: None,
) -> None:
    target = tmp_path / "release" / ".venv"
    target.parent.mkdir()
    archive = _build_archive(
        (".", "directory", b""),
        ("bin", "directory", b""),
        ("bin/northstar", "file", b"#!/bin/sh\nexit 0\n"),
        ("pyvenv.cfg", "file", "home = /trusted/python\n"),
    )

    venv_archive.receive_venv_archive(
        archive,
        target_dir=target,
        temporary_dir=tmp_path,
    )

    assert (target / "bin" / "northstar").read_bytes() == b"#!/bin/sh\nexit 0\n"
    assert (target / "pyvenv.cfg").read_text(encoding="utf-8") == "home = /trusted/python\n"
    if os.name != "nt":
        assert (target / "bin" / "northstar").stat().st_mode & 0o111
        assert stat.S_IMODE(target.stat().st_mode) == 0o750
        assert stat.S_IMODE((target / "bin").stat().st_mode) == 0o750
        assert stat.S_IMODE((target / "pyvenv.cfg").stat().st_mode) == 0o640
    assert not list(tmp_path.glob(".northstar-venv.*"))


@pytest.mark.parametrize(
    ("members", "expected_error"),
    (
        (
            (
                (".", "directory", b""),
                ("bin", "directory", b""),
                ("bin/python", "symlink", "/etc/shadow"),
            ),
            "regular file or directory",
        ),
        (
            (
                (".", "directory", b""),
                ("bin", "directory", b""),
                ("bin/python", "hardlink", "bin/northstar"),
            ),
            "regular file or directory",
        ),
        (
            (
                (".", "directory", b""),
                ("../outside", "file", b"unsafe"),
            ),
            "escapes virtual environment",
        ),
        (
            (
                (".", "directory", b""),
                ("etc", "directory", b""),
                ("etc/shadow", "file", b"unsafe"),
            ),
            "unsupported virtual-environment root",
        ),
        (
            (
                (".", "directory", b""),
                ("bin", "directory", b""),
                ("bin/python", "device", b""),
            ),
            "regular file or directory",
        ),
        (
            (
                (".", "directory", b""),
                ("bin", "directory", b""),
                ("bin/python", "file", b"one"),
                ("bin/python", "file", b"two"),
            ),
            "duplicate member",
        ),
    ),
)
def test_receive_venv_archive_rejects_unsafe_members(
    tmp_path: Path,
    allow_test_directories: None,
    members: tuple[tuple[str, str, bytes | str], ...],
    expected_error: str,
) -> None:
    target = tmp_path / ".venv"

    with pytest.raises(venv_archive.VenvArchiveError, match=expected_error):
        venv_archive.receive_venv_archive(
            _build_archive(*members),
            target_dir=target,
            temporary_dir=tmp_path,
        )

    assert not target.exists()
    assert not target.is_symlink()


def test_receive_venv_archive_rejects_archives_over_the_extraction_cap(
    tmp_path: Path,
    allow_test_directories: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(venv_archive, "_MAX_EXTRACTED_BYTES", 3)
    archive = _build_archive(
        (".", "directory", b""),
        ("bin", "directory", b""),
        ("bin/northstar", "file", b"four"),
    )

    with pytest.raises(venv_archive.VenvArchiveError, match="exceeds 3 extracted bytes"):
        venv_archive.receive_venv_archive(
            archive,
            target_dir=tmp_path / ".venv",
            temporary_dir=tmp_path,
        )


def test_receive_venv_archive_bounds_member_iteration_before_full_indexing(
    tmp_path: Path,
    allow_test_directories: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(venv_archive, "_MAX_MEMBER_COUNT", 2)
    original_next = tarfile.TarFile.next
    next_call_count = 0

    def counted_next(archive: tarfile.TarFile) -> tarfile.TarInfo | None:
        nonlocal next_call_count
        next_call_count += 1
        return original_next(archive)

    def fail_getmembers(_archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
        raise AssertionError("validation must not materialize the complete member list")

    monkeypatch.setattr(tarfile.TarFile, "next", counted_next)
    monkeypatch.setattr(tarfile.TarFile, "getmembers", fail_getmembers)
    archive = _build_archive(
        (".", "directory", b""),
        ("bin", "directory", b""),
        *[(f"bin/file-{index}", "file", b"x") for index in range(32)],
    )

    with pytest.raises(venv_archive.VenvArchiveError, match="exceeds 2 members"):
        venv_archive.receive_venv_archive(
            archive,
            target_dir=tmp_path / ".venv",
            temporary_dir=tmp_path,
        )

    # TarFile may inspect the first header while opening, then validation only
    # needs the first member past the cap. It must not walk all 34 members.
    assert next_call_count <= 4

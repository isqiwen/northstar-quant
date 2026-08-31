#!/usr/bin/env python3
"""Receive a service-built virtual environment into a root-owned release.

The hermetic PEP 517 bootstrap must run as the unprivileged service identity:
dependency build hooks are executable code and must never gain root privileges.
The resulting virtual environment is therefore treated as hostile input. This module reads an
unprivileged tar stream into a root-private temporary file, validates every
member, then materializes a fresh immutable destination. It never traverses or
recursively changes a service-writable tree as root.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tarfile
import tempfile
from typing import BinaryIO, Final, Iterator

try:  # Allow direct-script execution as well as package imports.
    from .platform_support import PlatformSupportError, require_linux_x86_64
except ImportError:  # pragma: no cover - direct-script invocation path.
    _DEPLOY_DIRECTORY = Path(__file__).resolve().parent
    if str(_DEPLOY_DIRECTORY) not in sys.path:
        sys.path.insert(0, str(_DEPLOY_DIRECTORY))
    from platform_support import PlatformSupportError, require_linux_x86_64


class VenvArchiveError(ValueError):
    """The service-built virtual-environment archive is unsafe."""


_COPY_BLOCK_SIZE: Final = 1024 * 1024
_MAX_ARCHIVE_BYTES: Final = 8 * 1024 * 1024 * 1024
_MAX_MEMBER_COUNT: Final = 250_000
_MAX_EXTRACTED_BYTES: Final = 8 * 1024 * 1024 * 1024
_UNSAFE_MODE_BITS: Final = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX
_ALLOWED_TOP_LEVEL_NAMES: Final = frozenset(
    {
        ".gitignore",
        ".lock",
        "CACHEDIR.TAG",
        "bin",
        "include",
        "lib",
        "lib64",
        "pyvenv.cfg",
        "share",
    }
)


def _require_linux_x86_64_host() -> None:
    """Reject unsupported hosts before receiving a service-built venv."""

    try:
        require_linux_x86_64()
    except PlatformSupportError as exc:
        raise VenvArchiveError(str(exc)) from exc


def receive_venv_archive(
    source: BinaryIO,
    *,
    target_dir: Path,
    temporary_dir: Path,
    service_group_id: int | None = None,
) -> None:
    """Safely receive one uncompressed, dereferenced virtual-environment tar.

    The source is intentionally a stream opened by the unprivileged producer.
    Root receives no path into the producer's writable directory. The target
    parent and temporary directory must already be root-controlled.
    """

    _require_linux_x86_64_host()
    # Do not resolve these paths: resolving an existing leaf symlink before
    # the lexists check would turn a rejected target into a different path.
    target_dir = target_dir.absolute()
    temporary_dir = temporary_dir.absolute()
    _require_root_controlled_directory(temporary_dir, "temporary directory")
    _require_root_controlled_directory(target_dir.parent, "target parent directory")
    if os.path.lexists(target_dir):
        raise VenvArchiveError(f"refusing to overwrite virtual environment: {target_dir}")

    archive_path: Path | None = None
    target_created = False
    try:
        archive_path = _capture_stream(source, temporary_dir)
        members = _validate_archive(archive_path)
        target_dir.mkdir(mode=0o750)
        if service_group_id is not None:
            os.chown(target_dir, 0, service_group_id)
        os.chmod(target_dir, 0o750)
        target_created = True
        _extract_members(archive_path, members, target_dir, service_group_id)
        _fsync_directory(target_dir)
        _fsync_directory(target_dir.parent)
    except Exception:
        if target_created:
            shutil.rmtree(target_dir, ignore_errors=True)
        raise
    finally:
        if archive_path is not None:
            archive_path.unlink(missing_ok=True)


def _require_root_controlled_directory(path: Path, label: str) -> None:
    if not path.is_dir() or path.is_symlink():
        raise VenvArchiveError(f"{label} must be a non-symlink directory: {path}")
    metadata = path.stat()
    if metadata.st_uid != 0:
        raise VenvArchiveError(f"{label} must be root-owned: {path}")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise VenvArchiveError(f"{label} must not be group/world writable: {path}")


def _capture_stream(source: BinaryIO, temporary_dir: Path) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix=".northstar-venv.", dir=temporary_dir)
    archive_path = Path(raw_path)
    total = 0
    try:
        with os.fdopen(descriptor, "wb") as destination:
            while block := source.read(_COPY_BLOCK_SIZE):
                total += len(block)
                if total > _MAX_ARCHIVE_BYTES:
                    raise VenvArchiveError(
                        f"virtual-environment archive exceeds {_MAX_ARCHIVE_BYTES} bytes"
                    )
                destination.write(block)
            destination.flush()
            os.fsync(destination.fileno())
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    return archive_path


def _validate_archive(archive_path: Path) -> tuple[tuple[tarfile.TarInfo, PurePosixPath], ...]:
    validated: list[tuple[tarfile.TarInfo, PurePosixPath]] = []
    names: set[PurePosixPath] = set()
    directory_names: set[PurePosixPath] = {PurePosixPath(".")}
    file_names: set[PurePosixPath] = set()
    total_size = 0
    try:
        # ``TarFile.getmembers()`` first parses and retains every archive
        # member. An attacker can therefore force unbounded memory consumption
        # before the member-count limit is checked. Stream the metadata instead
        # and stop reading immediately after the first member beyond the cap.
        with tarfile.open(archive_path, mode="r|*") as archive:
            for member in _iter_bounded_members(archive):
                relative_path = _normalize_member_name(member.name)
                if relative_path == PurePosixPath("."):
                    if not member.isdir():
                        raise VenvArchiveError("archive root must be a directory")
                    continue
                if relative_path in names:
                    raise VenvArchiveError(f"archive contains duplicate member: {relative_path}")
                if relative_path.parts[0] not in _ALLOWED_TOP_LEVEL_NAMES:
                    raise VenvArchiveError(
                        "archive member has an unsupported virtual-environment root: "
                        f"{relative_path}"
                    )
                names.add(relative_path)
                if member.mode & _UNSAFE_MODE_BITS:
                    raise VenvArchiveError(f"archive member has unsafe mode bits: {relative_path}")
                if getattr(member, "sparse", None):
                    raise VenvArchiveError(f"archive member must not be sparse: {relative_path}")

                parent = relative_path.parent
                if parent not in directory_names:
                    raise VenvArchiveError(
                        f"archive member parent must be an earlier directory: {relative_path}"
                    )
                if member.isdir():
                    if relative_path in file_names:
                        raise VenvArchiveError(f"archive changes file into directory: {relative_path}")
                    directory_names.add(relative_path)
                elif member.isreg():
                    if relative_path in directory_names or relative_path in file_names:
                        raise VenvArchiveError(f"archive reuses member path: {relative_path}")
                    total_size += member.size
                    if total_size > _MAX_EXTRACTED_BYTES:
                        raise VenvArchiveError(
                            "virtual-environment archive exceeds "
                            f"{_MAX_EXTRACTED_BYTES} extracted bytes"
                        )
                    file_names.add(relative_path)
                else:
                    raise VenvArchiveError(
                        "archive member must be a regular file or directory: "
                        f"{relative_path}"
                    )
                validated.append((member, relative_path))
    except (OSError, tarfile.TarError) as exc:
        raise VenvArchiveError(f"unable to read virtual-environment archive: {exc}") from exc

    if not validated:
        raise VenvArchiveError("virtual-environment archive is empty")
    return tuple(validated)


def _iter_bounded_members(archive: tarfile.TarFile) -> Iterator[tarfile.TarInfo]:
    """Yield archive members without allowing TarFile to build a full index."""

    member_count = 0
    while True:
        member = archive.next()
        if member is None:
            return
        member_count += 1
        if member_count > _MAX_MEMBER_COUNT:
            raise VenvArchiveError(
                f"virtual-environment archive exceeds {_MAX_MEMBER_COUNT} members"
            )
        yield member


def _normalize_member_name(name: str) -> PurePosixPath:
    if not name or "\x00" in name or "\\" in name:
        raise VenvArchiveError(f"archive member has unsafe name: {name!r}")
    normalized = name.removeprefix("./")
    if normalized in {"", "."}:
        return PurePosixPath(".")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise VenvArchiveError(f"archive member escapes virtual environment: {name!r}")
    return path


def _extract_members(
    archive_path: Path,
    members: tuple[tuple[tarfile.TarInfo, PurePosixPath], ...],
    target_dir: Path,
    service_group_id: int | None,
) -> None:
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            for member, relative_path in members:
                destination = target_dir.joinpath(*relative_path.parts)
                if member.isdir():
                    destination.mkdir(mode=0o750)
                    if service_group_id is not None:
                        os.chown(destination, 0, service_group_id)
                    os.chmod(destination, 0o750)
                    continue
                source = archive.extractfile(member)
                if source is None:
                    raise VenvArchiveError(f"unable to read archive member: {relative_path}")
                mode = 0o750 if member.mode & 0o111 else 0o640
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                flags |= getattr(os, "O_NOFOLLOW", 0)
                descriptor = -1
                try:
                    descriptor = os.open(destination, flags, mode)
                    with os.fdopen(descriptor, "wb") as destination_file:
                        with source:
                            shutil.copyfileobj(source, destination_file, _COPY_BLOCK_SIZE)
                        destination_file.flush()
                        os.fsync(destination_file.fileno())
                    if service_group_id is not None:
                        os.chown(destination, 0, service_group_id)
                except Exception:
                    source.close()
                    if descriptor >= 0:
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass
                    raise
    except (OSError, tarfile.TarError) as exc:
        raise VenvArchiveError(f"unable to extract virtual-environment archive: {exc}") from exc


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Receive a dereferenced service-built venv tar into a root-owned directory."
    )
    parser.add_argument("--target-dir", required=True, type=Path)
    parser.add_argument("--temporary-dir", required=True, type=Path)
    parser.add_argument("--service-group", required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        _require_linux_x86_64_host()
    except VenvArchiveError as exc:
        print(f"unsupported host: {exc}", file=sys.stderr)
        return 1
    if os.geteuid() != 0:
        print("venv archive receiver must run as root", file=sys.stderr)
        return 1
    try:
        import grp

        service_group_id = grp.getgrnam(args.service_group).gr_gid
    except KeyError:
        print(f"service group does not exist: {args.service_group}", file=sys.stderr)
        return 1
    try:
        receive_venv_archive(
            sys.stdin.buffer,
            target_dir=args.target_dir,
            temporary_dir=args.temporary_dir,
            service_group_id=service_group_id,
        )
    except VenvArchiveError as exc:
        print(f"unsafe virtual-environment archive: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Receive a deployment-user stream into a root-managed secret candidate.

The Linux deployment user owns the short-lived upload directory. A privileged
process must therefore never open a path in that directory: doing so after a
separate ``test``/``stat`` check permits a pathname race. This helper accepts
only standard input and publishes the bytes to one of two fixed FHS locations
after fully syncing the file and its metadata.

It intentionally has no source-path argument. ``provision.sh`` opens the
upload as the unprivileged SSH identity and pipes that already-open descriptor
to this program.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NoReturn


class HandoffError(RuntimeError):
    """The root-side candidate handoff cannot safely continue."""


_RELEASE_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9._-]+$")
_MAX_SECRET_BYTES: Final = 1_048_576


@dataclass(frozen=True)
class _HandoffSpec:
    parent: Path
    parent_group: str
    parent_mode: int
    target_group: str
    target_mode: int
    filename_prefix: str


_HANDOFF_SPECS: Final = {
    "environment": _HandoffSpec(
        parent=Path("/etc/northstar"),
        parent_group="northstar",
        parent_mode=0o750,
        target_group="northstar",
        target_mode=0o640,
        filename_prefix=".northstar-quant.",
    ),
    "ntfy-bootstrap": _HandoffSpec(
        parent=Path("/var/lib/northstar/deploy-state"),
        parent_group="root",
        parent_mode=0o700,
        target_group="root",
        target_mode=0o600,
        filename_prefix=".ntfy-bootstrap.",
    ),
}


def _fail(message: str) -> NoReturn:
    raise HandoffError(message)


def _candidate_name(*, kind: str, release_id: str, spec: _HandoffSpec) -> str:
    if not _RELEASE_ID_PATTERN.fullmatch(release_id):
        _fail("release identifier is invalid")
    return f"{spec.filename_prefix}{release_id}.candidate.env"


def _group_id(group_name: str) -> int:
    try:
        import grp
    except ModuleNotFoundError as exc:
        _fail("secure handoff requires the Linux group database")
        raise AssertionError("unreachable") from exc
    try:
        return grp.getgrnam(group_name).gr_gid
    except KeyError as exc:
        _fail(f"required group is unavailable: {group_name}")
        raise AssertionError("unreachable") from exc


def _open_managed_parent(spec: _HandoffSpec) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        parent_fd = os.open(spec.parent, flags)
    except OSError as exc:
        _fail("managed handoff parent is unavailable or a symbolic link")
        raise AssertionError("unreachable") from exc

    parent_stat = os.fstat(parent_fd)
    expected_group_id = _group_id(spec.parent_group)
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != 0
        or parent_stat.st_gid != expected_group_id
        or stat.S_IMODE(parent_stat.st_mode) != spec.parent_mode
    ):
        os.close(parent_fd)
        _fail("managed handoff parent ownership or mode is invalid")
    return parent_fd


def _open_temporary_file(*, parent_fd: int, kind: str, release_id: str) -> tuple[str, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    for _ in range(32):
        name = f".handoff-{kind}-{release_id}-{secrets.token_hex(16)}.tmp"
        try:
            return name, os.open(name, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError:
            continue
        except OSError as exc:
            _fail("cannot create the root-managed temporary handoff file")
            raise AssertionError("unreachable") from exc
    _fail("cannot allocate a unique root-managed temporary handoff file")


def _write_all(file_descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(file_descriptor, data[offset:])
        if written <= 0:
            _fail("cannot write the root-managed temporary handoff file")
        offset += written


def _copy_standard_input(*, target_fd: int) -> int:
    total_bytes = 0
    while True:
        try:
            chunk = os.read(0, 65_536)
        except OSError as exc:
            _fail("cannot read the unprivileged handoff stream")
            raise AssertionError("unreachable") from exc
        if not chunk:
            return total_bytes
        total_bytes += len(chunk)
        if total_bytes > _MAX_SECRET_BYTES:
            _fail("handoff stream exceeds the fixed secret-file size limit")
        _write_all(target_fd, chunk)


def _candidate_exists(*, parent_fd: int, candidate_name: str) -> bool:
    try:
        os.stat(candidate_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        _fail("cannot inspect the root-managed candidate path")
        raise AssertionError("unreachable") from exc
    return True


def _verify_candidate(
    *,
    parent_fd: int,
    candidate_name: str,
    expected_group_id: int,
    expected_mode: int,
) -> None:
    try:
        candidate_stat = os.stat(
            candidate_name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        _fail("cannot verify the root-managed candidate")
        raise AssertionError("unreachable") from exc
    if (
        not stat.S_ISREG(candidate_stat.st_mode)
        or candidate_stat.st_uid != 0
        or candidate_stat.st_gid != expected_group_id
        or stat.S_IMODE(candidate_stat.st_mode) != expected_mode
        or candidate_stat.st_nlink != 1
    ):
        _fail("root-managed candidate ownership, type, link count, or mode is invalid")


def receive_from_standard_input(*, kind: str, release_id: str) -> None:
    """Atomically publish one verified root-managed candidate from stdin."""

    geteuid = getattr(os, "geteuid", None)
    if geteuid is None or geteuid() != 0:
        _fail("secure handoff requires Linux root")
    try:
        spec = _HANDOFF_SPECS[kind]
    except KeyError as exc:
        _fail("handoff kind is unsupported")
        raise AssertionError("unreachable") from exc

    candidate_name = _candidate_name(kind=kind, release_id=release_id, spec=spec)
    parent_fd = _open_managed_parent(spec)
    temporary_name = ""
    temporary_fd = -1
    published = False
    completed = False
    expected_group_id = _group_id(spec.target_group)
    try:
        if _candidate_exists(parent_fd=parent_fd, candidate_name=candidate_name):
            _fail("root-managed candidate already exists")

        temporary_name, temporary_fd = _open_temporary_file(
            parent_fd=parent_fd,
            kind=kind,
            release_id=release_id,
        )
        _copy_standard_input(target_fd=temporary_fd)
        os.fchown(temporary_fd, 0, expected_group_id)
        os.fchmod(temporary_fd, spec.target_mode)
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = -1

        # link(2) is an atomic no-overwrite publication. Unlike rename/cp/mv
        # across filesystems, it cannot silently fall back to copying a source
        # pathname owned by the SSH deployment user.
        os.link(
            temporary_name,
            candidate_name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
        published = True
        os.fsync(parent_fd)

        os.unlink(temporary_name, dir_fd=parent_fd)
        temporary_name = ""
        os.fsync(parent_fd)
        _verify_candidate(
            parent_fd=parent_fd,
            candidate_name=candidate_name,
            expected_group_id=expected_group_id,
            expected_mode=spec.target_mode,
        )
        completed = True
    except OSError as exc:
        _fail("root-managed candidate filesystem operation failed")
        raise AssertionError("unreachable") from exc
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except OSError:
                pass
        if published and not completed:
            # A normal exception after publication must not leave a candidate
            # that blocks a retry. SIGKILL/power loss deliberately bypasses
            # this cleanup and remains a later durable-transaction concern.
            try:
                os.unlink(candidate_name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except OSError:
                pass
        os.close(parent_fd)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Receive an unprivileged deployment stream into a root-managed candidate."
    )
    parser.add_argument("kind", choices=tuple(_HANDOFF_SPECS))
    parser.add_argument("release_id")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        receive_from_standard_input(kind=args.kind, release_id=args.release_id)
    except HandoffError as exc:
        print(f"secure handoff failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

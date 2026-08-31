#!/usr/bin/env python3
"""Receive a deployment artifact stream into a root-managed candidate.

The SSH deployment identity owns the short-lived upload directory, so a root
installer must never reopen the uploaded artifact by pathname.  The
unprivileged ``provision.sh`` process opens that pathname once and sends the
already-open descriptor through standard input.  This receiver copies the
stream into the fixed root-owned deployment state directory while calculating
the expected SHA-256, then atomically publishes the verified candidate.

This protects the artifact bytes themselves.  The caller still runs this
script from the transient control archive; making that control runner
immutable is deliberately a later release-pipeline concern.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import platform
import re
import secrets
import stat
import sys
from pathlib import Path
from typing import Final, NoReturn


class ArtifactHandoffError(RuntimeError):
    """A deployment artifact cannot cross into the root-owned boundary."""


_DEPLOY_STATE_DIR: Final = Path("/var/lib/northstar/deploy-state")
_RELEASE_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_MAX_ARTIFACT_BYTES: Final = 4 * 1024 * 1024 * 1024
_COPY_BLOCK_SIZE: Final = 1_048_576


def _fail(message: str) -> NoReturn:
    raise ArtifactHandoffError(message)


def _candidate_name(release_id: str) -> str:
    if not _RELEASE_ID_PATTERN.fullmatch(release_id):
        _fail("release identifier is invalid")
    return f".artifact.{release_id}.candidate.tar.gz"


def _validate_expected_sha256(expected_sha256: str) -> str:
    if not _SHA256_PATTERN.fullmatch(expected_sha256):
        _fail("expected artifact SHA-256 is invalid")
    return expected_sha256


def _open_managed_parent() -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        parent_fd = os.open(_DEPLOY_STATE_DIR, flags)
    except OSError as exc:
        _fail("artifact handoff parent is unavailable or a symbolic link")
        raise AssertionError("unreachable") from exc

    parent_stat = os.fstat(parent_fd)
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != 0
        or parent_stat.st_gid != 0
        or stat.S_IMODE(parent_stat.st_mode) != 0o700
    ):
        os.close(parent_fd)
        _fail("artifact handoff parent ownership or mode is invalid")
    return parent_fd


def _candidate_exists(*, parent_fd: int, candidate_name: str) -> bool:
    try:
        os.stat(candidate_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    except OSError as exc:
        _fail("cannot inspect the root-managed artifact candidate path")
        raise AssertionError("unreachable") from exc
    return True


def _open_temporary_file(*, parent_fd: int, release_id: str) -> tuple[str, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    for _ in range(32):
        temporary_name = f".artifact-handoff-{release_id}-{secrets.token_hex(16)}.tmp"
        try:
            return temporary_name, os.open(temporary_name, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError:
            continue
        except OSError as exc:
            _fail("cannot create the root-managed temporary artifact file")
            raise AssertionError("unreachable") from exc
    _fail("cannot allocate a unique root-managed temporary artifact file")


def _write_all(file_descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        try:
            written = os.write(file_descriptor, data[offset:])
        except OSError as exc:
            _fail("cannot write the root-managed temporary artifact file")
            raise AssertionError("unreachable") from exc
        if written <= 0:
            _fail("cannot write the root-managed temporary artifact file")
        offset += written


def _copy_stream(*, source_fd: int, target_fd: int) -> tuple[int, str]:
    """Copy an already-open input descriptor and calculate its exact digest."""

    digest = hashlib.sha256()
    total_bytes = 0
    while True:
        try:
            chunk = os.read(source_fd, _COPY_BLOCK_SIZE)
        except OSError as exc:
            _fail("cannot read the unprivileged artifact handoff stream")
            raise AssertionError("unreachable") from exc
        if not chunk:
            return total_bytes, digest.hexdigest()
        total_bytes += len(chunk)
        if total_bytes > _MAX_ARTIFACT_BYTES:
            _fail("artifact handoff stream exceeds the fixed size limit")
        digest.update(chunk)
        _write_all(target_fd, chunk)


def _verify_candidate(*, parent_fd: int, candidate_name: str) -> None:
    try:
        candidate_stat = os.stat(
            candidate_name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
    except OSError as exc:
        _fail("cannot verify the root-managed artifact candidate")
        raise AssertionError("unreachable") from exc
    if (
        not stat.S_ISREG(candidate_stat.st_mode)
        or candidate_stat.st_uid != 0
        or candidate_stat.st_gid != 0
        or stat.S_IMODE(candidate_stat.st_mode) != 0o600
        or candidate_stat.st_nlink != 1
    ):
        _fail("root-managed artifact candidate ownership, type, link count, or mode is invalid")


def receive_from_standard_input(*, release_id: str, expected_sha256: str) -> None:
    """Atomically publish one verified root-managed artifact candidate from stdin."""

    geteuid = getattr(os, "geteuid", None)
    machine = platform.machine().strip().lower().replace("-", "_")
    if (
        platform.system() != "Linux"
        or machine not in {"x86_64", "amd64"}
        or geteuid is None
        or geteuid() != 0
    ):
        _fail("artifact handoff requires Linux x86_64 root")

    candidate_name = _candidate_name(release_id)
    expected_sha256 = _validate_expected_sha256(expected_sha256)
    parent_fd = _open_managed_parent()
    temporary_name = ""
    temporary_fd = -1
    published = False
    completed = False
    try:
        if _candidate_exists(parent_fd=parent_fd, candidate_name=candidate_name):
            _fail("root-managed artifact candidate already exists")

        temporary_name, temporary_fd = _open_temporary_file(
            parent_fd=parent_fd,
            release_id=release_id,
        )
        _, actual_sha256 = _copy_stream(source_fd=0, target_fd=temporary_fd)
        if not hmac.compare_digest(actual_sha256, expected_sha256):
            _fail("artifact handoff SHA-256 verification failed")
        os.fchown(temporary_fd, 0, 0)
        os.fchmod(temporary_fd, 0o600)
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = -1

        # link(2) is an atomic no-overwrite publication.  It never resolves a
        # deployment-user-owned source pathname after the stream was opened.
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
        _verify_candidate(parent_fd=parent_fd, candidate_name=candidate_name)
        completed = True
    except OSError as exc:
        _fail("root-managed artifact candidate filesystem operation failed")
        raise AssertionError("unreachable") from exc
    finally:
        if temporary_fd >= 0:
            try:
                os.close(temporary_fd)
            except OSError:
                pass
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except OSError:
                pass
        if published and not completed:
            # Ordinary failures must not block a retry.  SIGKILL/power loss
            # deliberately bypasses this cleanup and remains a P6-WP09
            # durable-transaction/recovery concern.
            try:
                os.unlink(candidate_name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except OSError:
                pass
        os.close(parent_fd)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Receive an unprivileged deployment artifact stream into a root-managed candidate."
    )
    parser.add_argument("release_id")
    parser.add_argument("expected_sha256")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        receive_from_standard_input(
            release_id=args.release_id,
            expected_sha256=args.expected_sha256,
        )
    except ArtifactHandoffError as exc:
        print(f"artifact handoff failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Install the one-time, root-owned Northstar release-gate trust anchor.

This program is deliberately outside the normal deployment control plane.  It
is for a root operator who has independently reviewed the Python gate source,
calculated its digest, and reviewed the public OpenSSH release-authority file.
Neither ``deploy.py`` nor ``provision.sh`` may invoke it.  In particular, it
must never accept source code from the deployment user's SSH staging area.

The installed gate is intentionally small and fixed:

* ``/usr/local/libexec/northstar-quant/release-gate`` is the only sudo entry
  point and uses Python isolated mode;
* ``root_release_runner.py`` is copied by descriptor after its exact digest is
  verified; and
* ``/etc/northstar/release-allowed-signers`` is a root-owned public authority
  file used to verify detached release manifests.

All destination paths are constants.  Publication is no-overwrite and a
partial bootstrap is retained for manual investigation instead of being
silently repaired or removed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import re
import secrets
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NoReturn


class ReleaseGateBootstrapError(RuntimeError):
    """A one-time release-gate bootstrap request is unsafe or incomplete."""


ROOT_GATE_DIRECTORY: Final = Path("/usr/local/libexec/northstar-quant")
ROOT_RUNNER_PATH: Final = ROOT_GATE_DIRECTORY / "root_release_runner.py"
ROOT_GATE_WRAPPER_PATH: Final = ROOT_GATE_DIRECTORY / "release-gate"
ROOT_CONFIG_DIRECTORY: Final = Path("/etc/northstar")
ALLOWED_SIGNERS_PATH: Final = ROOT_CONFIG_DIRECTORY / "release-allowed-signers"

ROOT_CONFIRMATION: Final = "INSTALL_ROOT_RELEASE_GATE"
SIGNER_PRINCIPAL: Final = "northstar-release"
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_MAX_GATE_SOURCE_BYTES: Final = 1_048_576
_MAX_ALLOWED_SIGNERS_BYTES: Final = 65_536
_READ_BLOCK_SIZE: Final = 65_536
_UNTRUSTED_STAGING_ROOTS: Final = (
    Path("/tmp"),
    Path("/var/tmp"),
    Path("/run/user"),
    Path("/dev/shm"),
)
_PUBLIC_KEY_PREFIXES: Final = ("ssh-", "ecdsa-", "sk-")
_PRIVATE_KEY_MARKERS: Final = (
    b"-----BEGIN ",
    b"PRIVATE KEY",
    b"OPENSSH PRIVATE KEY",
)
_ROOT_GATE_WRAPPER: Final = (
    b"#!/bin/sh\n"
    b"# Installed only by release_gate_bootstrap.py; do not edit in place.\n"
    b"exec /usr/bin/env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin "
    b"/usr/bin/python3 -I "
    b"/usr/local/libexec/northstar-quant/root_release_runner.py \"$@\"\n"
)


@dataclass(frozen=True)
class ReleaseGateBootstrapPlan:
    """The exact, operator-reviewed bytes accepted by a bootstrap run."""

    gate_source: Path
    gate_sha256: str
    allowed_signers_source: Path
    allowed_signers_sha256: str


def _fail(message: str) -> NoReturn:
    raise ReleaseGateBootstrapError(message)


def _validate_sha256(value: str, *, label: str) -> str:
    if not _SHA256_PATTERN.fullmatch(value):
        _fail(f"{label} must be an exact lowercase SHA-256 digest")
    return value


def _require_explicit_root_confirmation(*, apply: bool, confirmation: str) -> None:
    if not apply:
        _fail("root release-gate bootstrap requires --apply")
    if confirmation != ROOT_CONFIRMATION:
        _fail(
            "root release-gate bootstrap requires "
            f"--confirm-root-gate-bootstrap {ROOT_CONFIRMATION}"
        )


def _require_linux_root() -> None:
    geteuid = getattr(os, "geteuid", None)
    if sys.platform != "linux" or geteuid is None or geteuid() != 0:
        _fail("root release-gate bootstrap requires Linux root")


def _is_untrusted_staging_path(path: Path) -> bool:
    """Return whether *path* belongs to a known deployment-staging root."""

    return any(path == root or root in path.parents for root in _UNTRUSTED_STAGING_ROOTS)


def _resolve_explicit_source(path: Path, *, label: str) -> Path:
    if not path.is_absolute():
        _fail(f"{label} must be an explicit absolute path")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        _fail(f"{label} is unavailable")
        raise AssertionError("unreachable") from exc
    if _is_untrusted_staging_path(resolved):
        _fail(f"{label} must not be read from SSH staging or a temporary directory")
    return resolved


def _assert_root_controlled_directory_chain(path: Path) -> None:
    """Require an existing source directory chain to be root-controlled."""

    current = path
    while True:
        try:
            metadata = os.lstat(current)
        except OSError as exc:
            _fail("reviewed gate source directory chain is unavailable")
            raise AssertionError("unreachable") from exc
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            _fail("reviewed gate source directory chain is not root-controlled")
        if current == current.parent:
            return
        current = current.parent


def _read_regular_file(
    path: Path,
    *,
    label: str,
    maximum_bytes: int,
    require_root_controlled_source: bool,
) -> bytes:
    """Read a regular non-link file through one stable descriptor."""

    resolved = _resolve_explicit_source(path, label=label)
    if require_root_controlled_source:
        _assert_root_controlled_directory_chain(resolved.parent)

    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(resolved, flags)
    except OSError as exc:
        _fail(f"{label} cannot be opened as a regular non-link file")
        raise AssertionError("unreachable") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            _fail(f"{label} must be a single-link regular file")
        if stat.S_IMODE(before.st_mode) & 0o022:
            _fail(f"{label} must not be group- or world-writable")
        if require_root_controlled_source and (before.st_uid != 0 or before.st_gid != 0):
            _fail("reviewed gate source must be owned by root:root")
        if before.st_size > maximum_bytes:
            _fail(f"{label} exceeds its fixed size limit")

        chunks: list[bytes] = []
        total_bytes = 0
        while True:
            try:
                block = os.read(descriptor, _READ_BLOCK_SIZE)
            except OSError as exc:
                _fail(f"{label} cannot be read")
                raise AssertionError("unreachable") from exc
            if not block:
                break
            total_bytes += len(block)
            if total_bytes > maximum_bytes:
                _fail(f"{label} exceeds its fixed size limit")
            chunks.append(block)

        after = os.fstat(descriptor)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
        ):
            _fail(f"{label} changed while it was being reviewed")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _verify_expected_digest(*, payload: bytes, expected_sha256: str, label: str) -> str:
    expected_sha256 = _validate_sha256(expected_sha256, label=label)
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if not hmac.compare_digest(actual_sha256, expected_sha256):
        _fail(f"{label} does not match the explicitly reviewed SHA-256 digest")
    return actual_sha256


def _validate_allowed_signers(payload: bytes) -> None:
    """Accept only public allowed-signers records for the release principal."""

    if not payload or len(payload) > _MAX_ALLOWED_SIGNERS_BYTES:
        _fail("allowed-signers public file has an invalid size")
    if b"\x00" in payload or any(marker in payload for marker in _PRIVATE_KEY_MARKERS):
        _fail("allowed-signers source must contain public keys only, never private material")
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as exc:
        _fail("allowed-signers source must be ASCII public-key text")
        raise AssertionError("unreachable") from exc

    record_count = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 3 or fields[0] != SIGNER_PRINCIPAL:
            _fail("allowed-signers records must name only the northstar-release principal")
        if not fields[1].startswith(_PUBLIC_KEY_PREFIXES):
            _fail("allowed-signers record has an unsupported OpenSSH public-key type")
        try:
            base64.b64decode(fields[2], validate=True)
        except ValueError as exc:
            _fail("allowed-signers record has invalid OpenSSH public-key bytes")
            raise AssertionError("unreachable") from exc
        record_count += 1
    if record_count == 0:
        _fail("allowed-signers source must contain at least one public release signer")


def _open_existing_root_directory(parent_fd: int, name: str) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        _fail("fixed root-gate destination directory is unavailable")
        raise AssertionError("unreachable") from exc
    _verify_root_directory_descriptor(descriptor)
    return descriptor


def _verify_root_directory_descriptor(descriptor: int) -> None:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        os.close(descriptor)
        _fail("fixed root-gate destination directory is not root-controlled")


def _open_or_create_root_directory(parent_fd: int, name: str, *, mode: int) -> int:
    try:
        return _open_existing_root_directory(parent_fd, name)
    except ReleaseGateBootstrapError:
        try:
            os.mkdir(name, mode=mode, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except FileExistsError:
            return _open_existing_root_directory(parent_fd, name)
        except OSError as exc:
            _fail("cannot create a fixed root-gate destination directory")
            raise AssertionError("unreachable") from exc
        descriptor = _open_existing_root_directory(parent_fd, name)
        try:
            os.fchown(descriptor, 0, 0)
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
            os.fsync(parent_fd)
        except OSError as exc:
            os.close(descriptor)
            _fail("cannot secure a fixed root-gate destination directory")
            raise AssertionError("unreachable") from exc
        return descriptor


def _open_gate_directory() -> int:
    root_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        usr_fd = _open_existing_root_directory(root_fd, "usr")
        try:
            local_fd = _open_existing_root_directory(usr_fd, "local")
            try:
                libexec_fd = _open_or_create_root_directory(local_fd, "libexec", mode=0o755)
                try:
                    return _open_or_create_root_directory(
                        libexec_fd,
                        "northstar-quant",
                        mode=0o750,
                    )
                finally:
                    os.close(libexec_fd)
            finally:
                os.close(local_fd)
        finally:
            os.close(usr_fd)
    finally:
        os.close(root_fd)


def _open_config_directory() -> int:
    root_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        etc_fd = _open_existing_root_directory(root_fd, "etc")
        try:
            return _open_or_create_root_directory(etc_fd, "northstar", mode=0o750)
        finally:
            os.close(etc_fd)
    finally:
        os.close(root_fd)


def _assert_destination_absent(*, parent_fd: int, name: str) -> None:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        _fail("cannot inspect a fixed root-gate destination")
        raise AssertionError("unreachable") from exc
    _fail("refusing to overwrite an existing root-gate trust-anchor file")


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        try:
            written = os.write(descriptor, payload[offset:])
        except OSError as exc:
            _fail("cannot write a root-gate trust-anchor file")
            raise AssertionError("unreachable") from exc
        if written <= 0:
            _fail("cannot write a root-gate trust-anchor file")
        offset += written


def _verify_published_file(*, parent_fd: int, name: str, mode: int, expected_sha256: str) -> None:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        _fail("published root-gate trust-anchor file is unavailable")
        raise AssertionError("unreachable") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != mode
            or metadata.st_nlink != 1
        ):
            _fail("published root-gate trust-anchor file metadata is invalid")
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, _READ_BLOCK_SIZE)
            if not block:
                break
            digest.update(block)
        if not hmac.compare_digest(digest.hexdigest(), expected_sha256):
            _fail("published root-gate trust-anchor file digest is invalid")
    finally:
        os.close(descriptor)


def _publish_new_file(*, parent_fd: int, name: str, payload: bytes, mode: int) -> None:
    """Publish one root-owned file without replacing any existing evidence."""

    _assert_destination_absent(parent_fd=parent_fd, name=name)
    temporary_name = f".release-gate-bootstrap-{secrets.token_hex(16)}.tmp"
    descriptor = -1
    published = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
        descriptor = os.open(temporary_name, flags, mode, dir_fd=parent_fd)
        _write_all(descriptor, payload)
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1

        os.link(
            temporary_name,
            name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
        published = True
        os.fsync(parent_fd)
        os.unlink(temporary_name, dir_fd=parent_fd)
        os.fsync(parent_fd)
        _verify_published_file(
            parent_fd=parent_fd,
            name=name,
            mode=mode,
            expected_sha256=hashlib.sha256(payload).hexdigest(),
        )
    except OSError as exc:
        _fail("root-gate trust-anchor publication failed")
        raise AssertionError("unreachable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if not published:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def bootstrap_release_gate(
    *,
    gate_source: Path,
    expected_gate_sha256: str,
    allowed_signers_source: Path,
    expected_allowed_signers_sha256: str,
    apply: bool,
    confirmation: str,
) -> ReleaseGateBootstrapPlan:
    """Install the immutable root gate after explicit, manual confirmation."""

    _require_linux_root()
    _require_explicit_root_confirmation(apply=apply, confirmation=confirmation)

    gate_payload = _read_regular_file(
        gate_source,
        label="reviewed gate source",
        maximum_bytes=_MAX_GATE_SOURCE_BYTES,
        require_root_controlled_source=True,
    )
    gate_sha256 = _verify_expected_digest(
        payload=gate_payload,
        expected_sha256=expected_gate_sha256,
        label="reviewed gate source",
    )
    allowed_signers_payload = _read_regular_file(
        allowed_signers_source,
        label="allowed-signers source",
        maximum_bytes=_MAX_ALLOWED_SIGNERS_BYTES,
        require_root_controlled_source=False,
    )
    _validate_allowed_signers(allowed_signers_payload)
    allowed_signers_sha256 = _verify_expected_digest(
        payload=allowed_signers_payload,
        expected_sha256=expected_allowed_signers_sha256,
        label="allowed-signers source",
    )

    gate_directory_fd = _open_gate_directory()
    config_directory_fd = _open_config_directory()
    try:
        # Check every fixed final name before publishing any bytes.  If an
        # interruption occurs after this point, preserve the partial root-owned
        # evidence and require a human review rather than silently overwriting
        # a trust-anchor file on retry.
        _assert_destination_absent(parent_fd=gate_directory_fd, name=ROOT_RUNNER_PATH.name)
        _assert_destination_absent(parent_fd=gate_directory_fd, name=ROOT_GATE_WRAPPER_PATH.name)
        _assert_destination_absent(parent_fd=config_directory_fd, name=ALLOWED_SIGNERS_PATH.name)

        _publish_new_file(
            parent_fd=config_directory_fd,
            name=ALLOWED_SIGNERS_PATH.name,
            payload=allowed_signers_payload,
            mode=0o644,
        )
        _publish_new_file(
            parent_fd=gate_directory_fd,
            name=ROOT_RUNNER_PATH.name,
            payload=gate_payload,
            mode=0o640,
        )
        _publish_new_file(
            parent_fd=gate_directory_fd,
            name=ROOT_GATE_WRAPPER_PATH.name,
            payload=_ROOT_GATE_WRAPPER,
            mode=0o750,
        )
    finally:
        os.close(config_directory_fd)
        os.close(gate_directory_fd)

    return ReleaseGateBootstrapPlan(
        gate_source=_resolve_explicit_source(gate_source, label="reviewed gate source"),
        gate_sha256=gate_sha256,
        allowed_signers_source=_resolve_explicit_source(
            allowed_signers_source,
            label="allowed-signers source",
        ),
        allowed_signers_sha256=allowed_signers_sha256,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "One-time, root-operated bootstrap of the immutable Northstar release gate. "
            "This is never a normal SSH deployment command."
        )
    )
    parser.add_argument(
        "--gate-source",
        type=Path,
        required=True,
        help="Absolute root-owned path to the manually reviewed root_release_runner.py source.",
    )
    parser.add_argument(
        "--expected-gate-sha256",
        required=True,
        help="Exact SHA-256 of the manually reviewed gate source.",
    )
    parser.add_argument(
        "--allowed-signers-source",
        type=Path,
        required=True,
        help="Absolute path to a manually reviewed nonsecret OpenSSH allowed-signers file.",
    )
    parser.add_argument(
        "--expected-allowed-signers-sha256",
        required=True,
        help="Exact SHA-256 of the manually reviewed public allowed-signers file.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Acknowledge that this writes the fixed root-owned trust-anchor paths.",
    )
    parser.add_argument(
        "--confirm-root-gate-bootstrap",
        default="",
        help=f"Must be exactly {ROOT_CONFIRMATION}.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        plan = bootstrap_release_gate(
            gate_source=args.gate_source,
            expected_gate_sha256=args.expected_gate_sha256,
            allowed_signers_source=args.allowed_signers_source,
            expected_allowed_signers_sha256=args.expected_allowed_signers_sha256,
            apply=args.apply,
            confirmation=args.confirm_root_gate_bootstrap,
        )
    except ReleaseGateBootstrapError as exc:
        print(f"release-gate bootstrap failed: {exc}", file=sys.stderr)
        return 1
    print(
        "release_gate_bootstrap="
        f'{{"gate_sha256":"{plan.gate_sha256}",'
        f'"allowed_signers_sha256":"{plan.allowed_signers_sha256}",'
        f'"runner":"{ROOT_RUNNER_PATH}",'
        f'"wrapper":"{ROOT_GATE_WRAPPER_PATH}",'
        f'"allowed_signers":"{ALLOWED_SIGNERS_PATH}"}}'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

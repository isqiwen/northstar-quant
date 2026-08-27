#!/usr/bin/env python3
"""Fixed, root-owned release gate for signed Linux deployments.

Only this small program is reachable through the deployment account's sudo
rule.  It accepts a bounded binary stream, verifies the controller-independent
OpenSSH release signature, writes all bytes below root-owned state, verifies
the signed archive indexes, and then executes a fixed entrypoint only after
the control archive has become root-owned.  It never opens a path below SSH
staging and never accepts an executable path or shell snippet from stdin.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import posixpath
import re
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO, Final, Iterator, Mapping, NoReturn, Sequence

try:  # Importing this root-only module must remain possible on Windows.
    import fcntl
except ImportError:  # pragma: no cover - exercised only outside Linux.
    fcntl = None  # type: ignore[assignment]


class RootReleaseRunnerError(RuntimeError):
    """The fixed root gate cannot safely accept or execute a release."""


ROOT_RUNNER_PATH: Final = "/usr/local/libexec/northstar-quant/release-gate"
ROOT_RUNNER_MODULE_PATH: Final = Path("/usr/local/libexec/northstar-quant/root_release_runner.py")
ALLOWED_SIGNERS_PATH: Final = Path("/etc/northstar/release-allowed-signers")
SSH_KEYGEN_PATH: Final = Path("/usr/bin/ssh-keygen")
DEPLOY_STATE_DIR: Final = Path("/var/lib/northstar/deploy-state")
TRANSACTION_ROOT: Final = DEPLOY_STATE_DIR / "transactions"
DEPLOY_LOCK_PATH: Final = DEPLOY_STATE_DIR / "release-gate.lock"
GATE_PROTOCOL: Final = "northstar.release-gate.v1"
MANIFEST_FORMAT: Final = "northstar.release-manifest.v1"
SIGNATURE_NAMESPACE: Final = "northstar-quant-release-v1"
ENVIRONMENT_SIGNATURE_NAMESPACE: Final = "northstar-quant-release-environment-v1"
SIGNER_PRINCIPAL: Final = "northstar-release"
CONTROL_ENTRYPOINT: Final = "scripts/deploy/gate_release.sh"
_MAGIC: Final = b"NSRGATE1\x00"
_MAX_MANIFEST_BYTES: Final = 8 * 1024 * 1024
_MAX_SIGNATURE_BYTES: Final = 64 * 1024
_MAX_RUNTIME_BYTES: Final = 4 * 1024 * 1024 * 1024
_MAX_CONTROL_BYTES: Final = 64 * 1024 * 1024
_MAX_ENVIRONMENT_BYTES: Final = 1024 * 1024
_MAX_ARCHIVE_ENTRIES: Final = 20_000
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REVISION_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
_UV_VERSION_PATTERN: Final = re.compile(r"^[0-9][0-9A-Za-z.+-]{0,63}$")
_MEMBER_COMPONENT_PATTERN: Final = re.compile(r"^[A-Za-z0-9._-]+$")
_NOFOLLOW: Final = getattr(os, "O_NOFOLLOW", 0)
_SAFE_PATH: Final = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_RUNTIME_PROFILE_FIELDS: Final = (
    "runtime_storage_dir",
    "runtime_downloads_dir",
    "runtime_reports_dir",
    "runtime_log_dir",
    "runtime_cache_dir",
    "runtime_matplotlib_dir",
)
_RUNTIME_PARENT_DIRECTORIES: Final = frozenset(
    {
        "/var/lib/northstar",
        "/var/cache/northstar",
        "/var/log/northstar",
        "/mnt/northstar-quant",
        "/data/northstar-quant",
    }
)
_RUNTIME_RESERVED_LEAVES: Final = frozenset(
    {
        "/var/cache/northstar/dashboard",
        "/var/cache/northstar/venv-build",
        "/var/cache/northstar/uv-cache",
    }
)
_PROFILE_FIELDS: Final = frozenset(
    {
        "app_name",
        "confirm_live_deploy",
        "dashboard_deploy_enabled",
        "keep_releases",
        "ntfy_deploy_enabled",
        "python_version",
        "runtime_cache_dir",
        "runtime_downloads_dir",
        "runtime_log_dir",
        "runtime_matplotlib_dir",
        "runtime_reports_dir",
        "runtime_storage_dir",
        "service_mode",
        "service_user",
        "setup_server",
        "systemd_service_name",
        "uv_version",
    }
)
_MANIFEST_FIELDS: Final = frozenset(
    {
        "control",
        "created_at",
        "entrypoint",
        "environment_upload",
        "format",
        "gate_identity",
        "gate_protocol",
        "profile",
        "release_id",
        "revision",
        "runtime",
    }
)
_BUNDLE_FIELDS: Final = frozenset({"entries", "sha256", "size_bytes"})
_ENTRY_FIELDS: Final = frozenset({"kind", "mode", "path", "sha256", "size_bytes"})


@dataclass(frozen=True, slots=True)
class _Entry:
    path: str
    kind: str
    mode: int
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class _Bundle:
    sha256: str
    size_bytes: int
    entries: tuple[_Entry, ...]


@dataclass(frozen=True, slots=True)
class _Manifest:
    release_id: str
    revision: str
    gate_identity: str
    profile: Mapping[str, str]
    environment_upload: bool
    runtime: _Bundle
    control: _Bundle


@dataclass(frozen=True, slots=True)
class Submission:
    """A framed public request and opaque release bytes for stdin transport."""

    manifest: bytes
    signature: bytes
    runtime_path: Path
    control_path: Path
    environment_path: Path | None
    environment_signature: bytes | None = None


def _fail(message: str) -> NoReturn:
    raise RootReleaseRunnerError(message)


def _canonical_json(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("release manifest contains duplicate JSON keys")
        result[key] = value
    return result


def _require_digest(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        _fail(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_nonnegative_integer(value: object, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _fail(f"{label} must be a non-negative integer")
    return value


def _require_member_path(value: object) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        _fail("release manifest bundle path is unsafe")
    parts = value.split("/")
    if any(part in {"", ".", ".."} or not _MEMBER_COMPONENT_PATTERN.fullmatch(part) for part in parts):
        _fail("release manifest bundle path is unsafe")
    return value


def _parse_entry(payload: object) -> _Entry:
    if not isinstance(payload, dict) or set(payload) != _ENTRY_FIELDS:
        _fail("release manifest bundle entry fields are invalid")
    kind = payload["kind"]
    if kind not in {"file", "directory"}:
        _fail("release manifest bundle entry kind is invalid")
    mode = _require_nonnegative_integer(payload["mode"], label="bundle entry mode")
    if mode > 0o777 or mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
        _fail("release manifest bundle entry has a privileged mode")
    size_bytes = _require_nonnegative_integer(payload["size_bytes"], label="bundle entry size")
    if kind == "directory" and size_bytes != 0:
        _fail("release manifest directory entry has non-zero size")
    return _Entry(
        path=_require_member_path(payload["path"]),
        kind=kind,
        mode=mode,
        size_bytes=size_bytes,
        sha256=_require_digest(payload["sha256"], label="bundle entry sha256"),
    )


def _parse_bundle(payload: object) -> _Bundle:
    if not isinstance(payload, dict) or set(payload) != _BUNDLE_FIELDS:
        _fail("release manifest bundle fields are invalid")
    entries_payload = payload["entries"]
    if not isinstance(entries_payload, list) or not entries_payload or len(entries_payload) > _MAX_ARCHIVE_ENTRIES:
        _fail("release manifest bundle entries are invalid")
    entries = tuple(_parse_entry(item) for item in entries_payload)
    paths = tuple(entry.path for entry in entries)
    if paths != tuple(sorted(paths)) or len(set(paths)) != len(paths):
        _fail("release manifest bundle entries must be sorted and unique")
    regular_paths = {entry.path for entry in entries if entry.kind == "file"}
    for entry in entries:
        components = entry.path.split("/")
        if any("/".join(components[:index]) in regular_paths for index in range(1, len(components))):
            _fail("release manifest bundle member appears below a file")
    size_bytes = _require_nonnegative_integer(payload["size_bytes"], label="bundle size")
    if size_bytes == 0:
        _fail("release manifest bundle size must be positive")
    return _Bundle(
        sha256=_require_digest(payload["sha256"], label="bundle sha256"),
        size_bytes=size_bytes,
        entries=entries,
    )


def _require_canonical_timestamp(value: object) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail("release manifest timestamp is not canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise RootReleaseRunnerError("release manifest timestamp is invalid") from exc
    canonical = parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    if canonical != value:
        _fail("release manifest timestamp is not canonical UTC")


def _require_runtime_path(value: str, *, field: str) -> str:
    if (
        not value.startswith("/")
        or value.startswith("//")
        or "//" in value
        or "/../" in f"/{value}/"
        or "/./" in f"/{value}/"
        or not re.fullmatch(r"[A-Za-z0-9/._-]+", value)
        or posixpath.normpath(value) != value
    ):
        _fail(f"release manifest {field} path is invalid")
    if posixpath.dirname(value) not in _RUNTIME_PARENT_DIRECTORIES:
        _fail(f"release manifest {field} must be a direct managed runtime leaf")
    if value in _RUNTIME_RESERVED_LEAVES:
        _fail(f"release manifest {field} uses a reserved runtime leaf")
    return value


def _validate_runtime_profile_paths(profile: Mapping[str, str]) -> None:
    paths = tuple(_require_runtime_path(profile[field], field=field) for field in _RUNTIME_PROFILE_FIELDS)
    for index, first_path in enumerate(paths):
        for second_path in paths[index + 1 :]:
            if first_path == second_path or first_path.startswith(f"{second_path}/") or second_path.startswith(
                f"{first_path}/"
            ):
                _fail("release manifest runtime paths overlap")


def _parse_profile(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict) or set(payload) != _PROFILE_FIELDS:
        _fail("release manifest profile fields are invalid")
    profile: dict[str, str] = {}
    for key, value in payload.items():
        if not isinstance(value, str) or not value or "\x00" in value or len(value.encode("utf-8")) > 1024:
            _fail("release manifest profile values are invalid")
        profile[key] = value
    if profile["app_name"] != "northstar-quant" or profile["service_user"] != "northstar":
        _fail("release manifest profile identity is invalid")
    if profile["systemd_service_name"] != "northstar-quant":
        _fail("release manifest systemd service is invalid")
    if profile["service_mode"] not in {"health", "scheduler"}:
        _fail("release manifest service mode is invalid")
    if profile["confirm_live_deploy"] not in {"NO", "YES"}:
        _fail("release manifest live confirmation is invalid")
    if any(profile[name] not in {"0", "1"} for name in ("setup_server", "dashboard_deploy_enabled", "ntfy_deploy_enabled")):
        _fail("release manifest boolean profile field is invalid")
    if profile["ntfy_deploy_enabled"] != "0":
        _fail("root release gate does not accept an unsigned ntfy bootstrap path")
    if not _UV_VERSION_PATTERN.fullmatch(profile["uv_version"]):
        _fail("release manifest uv version is invalid")
    try:
        if int(profile["keep_releases"]) < 2:
            _fail("release manifest keep_releases is unsafe")
    except ValueError as exc:
        raise RootReleaseRunnerError("release manifest keep_releases is invalid") from exc
    _validate_runtime_profile_paths(profile)
    return profile


def parse_manifest(raw: bytes, *, expected_gate_identity: str) -> _Manifest:
    """Parse exact canonical manifest bytes without importing control code."""

    if not raw or len(raw) > _MAX_MANIFEST_BYTES:
        _fail("release manifest size is invalid")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RootReleaseRunnerError("release manifest is not valid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != _MANIFEST_FIELDS:
        _fail("release manifest fields are invalid")
    if payload["format"] != MANIFEST_FORMAT or payload["gate_protocol"] != GATE_PROTOCOL:
        _fail("release manifest format is unsupported")
    if payload["entrypoint"] != CONTROL_ENTRYPOINT:
        _fail("release manifest control entrypoint is not fixed")
    _require_canonical_timestamp(payload["created_at"])
    release_id = payload["release_id"]
    revision = payload["revision"]
    if not isinstance(release_id, str) or not _RELEASE_ID_PATTERN.fullmatch(release_id):
        _fail("release manifest release identifier is invalid")
    if not isinstance(revision, str) or not _REVISION_PATTERN.fullmatch(revision):
        _fail("release manifest revision is not a full committed SHA")
    if not isinstance(payload["environment_upload"], bool):
        _fail("release manifest environment_upload is invalid")
    gate_identity = _require_digest(payload["gate_identity"], label="gate identity")
    if not hmac.compare_digest(gate_identity, expected_gate_identity):
        _fail("release manifest was made for a different root gate identity")
    manifest = _Manifest(
        release_id=release_id,
        revision=revision,
        gate_identity=gate_identity,
        profile=_parse_profile(payload["profile"]),
        environment_upload=payload["environment_upload"],
        runtime=_parse_bundle(payload["runtime"]),
        control=_parse_bundle(payload["control"]),
    )
    if raw != _canonical_json(payload):
        _fail("release manifest is not canonical JSON")
    return manifest


def _read_exact(stream: BinaryIO, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        block = stream.read(remaining)
        if not block:
            _fail("release submission ended before a declared frame completed")
        chunks.append(block)
        remaining -= len(block)
    return b"".join(chunks)


def _read_length(stream: BinaryIO, *, width: int, maximum: int, label: str) -> int:
    raw = _read_exact(stream, width)
    value = struct.unpack("!I" if width == 4 else "!Q", raw)[0]
    if value > maximum:
        _fail(f"release submission {label} exceeds its fixed size limit")
    return value


def _write_length(destination: BinaryIO, value: int, *, width: int) -> None:
    destination.write(struct.pack("!I" if width == 4 else "!Q", value))


def environment_signature_payload(*, release_id: str, environment: bytes) -> bytes:
    """Return the exact private payload authorized for one release environment."""

    if not _RELEASE_ID_PATTERN.fullmatch(release_id):
        _fail("release environment signature has an invalid release identifier")
    if not environment or len(environment) > _MAX_ENVIRONMENT_BYTES:
        _fail("release environment signature payload size is invalid")
    return b"northstar-release-environment-v1\x00" + release_id.encode("ascii") + b"\x00" + environment


def _validate_transport_signature(signature: bytes, *, label: str) -> None:
    if not signature or len(signature) > _MAX_SIGNATURE_BYTES:
        _fail(f"release submission {label} size is invalid")


def write_submission(destination: BinaryIO, submission: Submission) -> None:
    """Serialize a bounded submission without loading runtime archives into RAM."""

    if not submission.manifest or len(submission.manifest) > _MAX_MANIFEST_BYTES:
        raise RootReleaseRunnerError("release submission manifest size is invalid")
    _validate_transport_signature(submission.signature, label="signature")
    paths = ((submission.runtime_path, _MAX_RUNTIME_BYTES), (submission.control_path, _MAX_CONTROL_BYTES))
    for path, maximum in paths:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > maximum:
            raise RootReleaseRunnerError("release submission archive path is unsafe")
    environment_size = 0
    if submission.environment_path is not None:
        if not submission.environment_path.is_file() or submission.environment_path.is_symlink():
            raise RootReleaseRunnerError("release submission environment path is unsafe")
        environment_size = submission.environment_path.stat().st_size
        if environment_size == 0 or environment_size > _MAX_ENVIRONMENT_BYTES:
            raise RootReleaseRunnerError("release submission environment size is invalid")
        if submission.environment_signature is None:
            raise RootReleaseRunnerError("release submission environment signature is required")
        _validate_transport_signature(submission.environment_signature, label="environment signature")
    elif submission.environment_signature is not None:
        raise RootReleaseRunnerError("release submission has an environment signature without an environment")

    destination.write(_MAGIC)
    _write_length(destination, len(submission.manifest), width=4)
    destination.write(submission.manifest)
    _write_length(destination, len(submission.signature), width=4)
    destination.write(submission.signature)
    for path in (submission.runtime_path, submission.control_path):
        _write_length(destination, path.stat().st_size, width=8)
        _copy_file_to_stream(path, destination)
    _write_length(destination, environment_size, width=8)
    if submission.environment_path is not None:
        assert submission.environment_signature is not None
        _write_length(destination, len(submission.environment_signature), width=4)
        destination.write(submission.environment_signature)
        _copy_file_to_stream(submission.environment_path, destination)
    else:
        _write_length(destination, 0, width=4)
    flush = getattr(destination, "flush", None)
    if flush is not None:
        flush()


def _copy_file_to_stream(path: Path, destination: BinaryIO) -> None:
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            destination.write(block)


def _assert_linux_root() -> None:
    if sys.platform != "linux" or getattr(os, "geteuid", lambda: -1)() != 0:
        _fail("root release gate requires Linux root")


def _assert_root_controlled_path(path: Path, *, file_mode: int | None = None) -> None:
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode):
        _fail("root release gate path must not be a symbolic link")
    if file_mode is None:
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022:
            _fail("root release gate directory is not root-controlled")
        return
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != file_mode
        or metadata.st_nlink != 1
    ):
        _fail("root release gate file is not root-controlled")


def _gate_identity() -> str:
    _assert_root_controlled_path(ROOT_RUNNER_MODULE_PATH, file_mode=0o640)
    return _file_sha256(ROOT_RUNNER_MODULE_PATH)


def _assert_gate_layout() -> None:
    _assert_linux_root()
    _assert_root_controlled_path(ROOT_RUNNER_MODULE_PATH.parent)
    _ensure_root_owned_directory(Path("/var/lib/northstar"), mode=0o755)
    _ensure_root_owned_directory(DEPLOY_STATE_DIR, mode=0o700)
    _assert_root_controlled_path(ALLOWED_SIGNERS_PATH.parent)
    _assert_root_controlled_path(ALLOWED_SIGNERS_PATH, file_mode=0o644)
    _ensure_root_owned_directory(TRANSACTION_ROOT, mode=0o700)


def _verify_signature(payload: bytes, signature: bytes, *, namespace: str) -> None:
    if not signature.startswith(b"-----BEGIN SSH SIGNATURE-----") or len(signature) > _MAX_SIGNATURE_BYTES:
        _fail("release manifest detached signature is invalid")
    try:
        metadata = os.lstat(SSH_KEYGEN_PATH)
    except OSError as exc:
        raise RootReleaseRunnerError("root release gate requires /usr/bin/ssh-keygen") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not stat.S_IMODE(metadata.st_mode) & 0o111
    ):
        _fail("root release gate requires ssh-keygen for signature verification")
    descriptor, temporary = tempfile.mkstemp(prefix=".release-signature-", dir=DEPLOY_STATE_DIR)
    try:
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, signature)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        result = subprocess.run(
            [
                str(SSH_KEYGEN_PATH),
                "-Y",
                "verify",
                "-f",
                str(ALLOWED_SIGNERS_PATH),
                "-I",
                SIGNER_PRINCIPAL,
                "-n",
                namespace,
                "-s",
                temporary,
            ],
            input=payload,
            check=False,
            capture_output=True,
            env={"PATH": _SAFE_PATH},
        )
        if result.returncode != 0:
            _fail("release signature is not trusted")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
            _fsync_directory(DEPLOY_STATE_DIR)
        except FileNotFoundError:
            pass
        except OSError:
            _fail("release signature evidence cannot be safely removed")


def _verify_environment_signature(
    *, environment_path: Path, signature: bytes, release_id: str
) -> None:
    try:
        environment = environment_path.read_bytes()
    except OSError as exc:
        raise RootReleaseRunnerError("release environment cannot be read for signature verification") from exc
    _verify_signature(
        environment_signature_payload(release_id=release_id, environment=environment),
        signature,
        namespace=ENVIRONMENT_SIGNATURE_NAMESPACE,
    )


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short release-gate write")
        view = view[written:]


def _receive_stream_to_file(stream: BinaryIO, *, length: int, destination: Path, maximum: int) -> str:
    if length <= 0 or length > maximum:
        _fail("release submission archive length is invalid")
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | _NOFOLLOW, 0o600)
    digest = hashlib.sha256()
    remaining = length
    try:
        while remaining:
            block = stream.read(min(1024 * 1024, remaining))
            if not block:
                _fail("release submission archive ended early")
            remaining -= len(block)
            digest.update(block)
            _write_all(descriptor, block)
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return digest.hexdigest()


def _new_incoming_directory() -> Path:
    for _ in range(32):
        candidate = DEPLOY_STATE_DIR / f".release-gate-incoming-{uuid.uuid4().hex}"
        try:
            os.mkdir(candidate, 0o700)
            os.chown(candidate, 0, 0)
            os.chmod(candidate, 0o700)
            _fsync_directory(DEPLOY_STATE_DIR)
            return candidate
        except FileExistsError:
            continue
    _fail("root release gate cannot allocate a private incoming directory")


def _assert_bundle_matches(path: Path, expected: _Bundle, *, control: bool) -> None:
    if path.stat().st_size != expected.size_bytes or not hmac.compare_digest(_file_sha256(path), expected.sha256):
        _fail("release bundle digest or size differs from the signed manifest")
    actual: list[_Entry] = []
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            for member in archive:
                if len(actual) >= _MAX_ARCHIVE_ENTRIES:
                    _fail("release bundle exceeds the member limit")
                member_path = _require_member_path(member.name.removesuffix("/"))
                if control and member_path != "DEPLOY_CONTROL_META.json" and not member_path.startswith("scripts/deploy/"):
                    _fail("signed control bundle has a member outside scripts/deploy")
                if member.issym() or member.islnk() or member.issparse() or not (member.isfile() or member.isdir()):
                    _fail("release bundle contains a link or special member")
                if member.mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                    _fail("release bundle contains a privileged member mode")
                if member.isdir():
                    actual.append(
                        _Entry(member_path, "directory", stat.S_IMODE(member.mode), 0, hashlib.sha256(b"").hexdigest())
                    )
                    continue
                member_file = archive.extractfile(member)
                if member_file is None:
                    _fail("release bundle member is unreadable")
                digest = hashlib.sha256()
                size_bytes = 0
                with member_file:
                    while block := member_file.read(1024 * 1024):
                        size_bytes += len(block)
                        digest.update(block)
                if size_bytes != member.size:
                    _fail("release bundle member size is inconsistent")
                actual.append(_Entry(member_path, "file", stat.S_IMODE(member.mode), size_bytes, digest.hexdigest()))
    except RootReleaseRunnerError:
        raise
    except (EOFError, OSError, tarfile.TarError, UnicodeError) as exc:
        raise RootReleaseRunnerError("release bundle cannot be read") from exc
    actual_tuple = tuple(sorted(actual, key=lambda entry: entry.path))
    if actual_tuple != expected.entries:
        _fail("release bundle member index differs from the signed manifest")
    if control and CONTROL_ENTRYPOINT not in {entry.path for entry in actual_tuple if entry.kind == "file"}:
        _fail("signed control bundle lacks the fixed release entrypoint")


def _temporary_destination(destination: Path) -> tuple[int, Path]:
    """Create an unlinked-to-final root-private file next to *destination*."""

    if os.path.lexists(destination):
        _fail("root release gate refuses to overwrite a managed candidate")
    for _ in range(32):
        temporary = destination.parent / f".{destination.name}.partial-{uuid.uuid4().hex}"
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | _NOFOLLOW,
                0o600,
            )
            return descriptor, temporary
        except FileExistsError:
            continue
    _fail("root release gate cannot allocate a private publication file")


def _publish_complete_temporary(*, descriptor: int, temporary: Path, destination: Path, mode: int) -> None:
    """Publish one fully synced file with no overwrite and a final link count of one."""

    try:
        os.fchown(descriptor, 0, 0)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.link(temporary, destination, follow_symlinks=False)
        _fsync_directory(destination.parent)
        os.unlink(temporary)
        _fsync_directory(destination.parent)
    except FileExistsError as exc:
        raise RootReleaseRunnerError("root release gate refuses to overwrite a managed candidate") from exc
    except OSError as exc:
        raise RootReleaseRunnerError("root release gate cannot publish a managed candidate") from exc


def _publish_bytes(destination: Path, payload: bytes, *, mode: int = 0o600) -> None:
    descriptor, temporary = _temporary_destination(destination)
    try:
        _write_all(descriptor, payload)
        # The completion helper always closes its descriptor, including when it
        # raises. Clear local ownership first so an error cannot close a reused
        # descriptor while preserving the incomplete root-owned evidence.
        published_descriptor = descriptor
        descriptor = -1
        _publish_complete_temporary(
            descriptor=published_descriptor,
            temporary=temporary,
            destination=destination,
            mode=mode,
        )
    except BaseException:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise


def _publish_copy(source: Path, destination: Path, *, mode: int = 0o600) -> None:
    """Copy from root-owned evidence without creating persistent hard links."""

    source_descriptor = -1
    destination_descriptor = -1
    try:
        source_descriptor = os.open(source, os.O_RDONLY | os.O_CLOEXEC | _NOFOLLOW)
        before = os.fstat(source_descriptor)
        if not stat.S_ISREG(before.st_mode):
            _fail("root release gate copy source must be a regular file")
        destination_descriptor, temporary = _temporary_destination(destination)
        while True:
            block = os.read(source_descriptor, 1024 * 1024)
            if not block:
                break
            _write_all(destination_descriptor, block)
        after = os.fstat(source_descriptor)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
        ):
            _fail("root release gate copy source changed during publication")
        published_descriptor = destination_descriptor
        destination_descriptor = -1
        _publish_complete_temporary(
            descriptor=published_descriptor,
            temporary=temporary,
            destination=destination,
            mode=mode,
        )
    except BaseException:
        if destination_descriptor >= 0:
            try:
                os.close(destination_descriptor)
            except OSError:
                pass
        raise
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)


def _ensure_root_owned_directory(path: Path, *, mode: int) -> None:
    """Create exactly one fixed root-owned directory without repairing it."""

    if os.path.lexists(path):
        _assert_root_controlled_path(path)
        if stat.S_IMODE(os.lstat(path).st_mode) != mode:
            _fail("root release gate directory has an unexpected mode")
        return
    _assert_root_controlled_path(path.parent)
    try:
        os.mkdir(path, mode)
        os.chown(path, 0, 0)
        os.chmod(path, mode)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise RootReleaseRunnerError("root release gate cannot create a fixed directory") from exc
    _assert_root_controlled_path(path)


@contextmanager
def _exclusive_deploy_lock() -> Iterator[None]:
    """Hold the fixed root-only deployment lock until a request is terminal."""

    if fcntl is None:
        _fail("root release gate requires Linux advisory locking")
    if not os.path.lexists(DEPLOY_LOCK_PATH):
        _publish_bytes(DEPLOY_LOCK_PATH, b"", mode=0o600)
    _assert_root_controlled_path(DEPLOY_LOCK_PATH, file_mode=0o600)
    descriptor = -1
    try:
        descriptor = os.open(DEPLOY_LOCK_PATH, os.O_RDWR | os.O_CLOEXEC | _NOFOLLOW)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RootReleaseRunnerError("another root release transaction is already active") from exc
        yield
    finally:
        if descriptor >= 0:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _ensure_control_directory(destination: Path, relative_parts: Sequence[str]) -> Path:
    current = destination
    for part in relative_parts:
        current = current / part
        _ensure_root_owned_directory(current, mode=0o755)
    return current


def _extract_control(control_archive: Path, destination: Path) -> None:
    _ensure_root_owned_directory(destination, mode=0o700)
    with tarfile.open(control_archive, mode="r:gz") as archive:
        for member in archive:
            member_path = _require_member_path(member.name.removesuffix("/"))
            parts = member_path.split("/")
            target = destination.joinpath(*parts)
            if destination not in target.parents and target != destination:
                _fail("control extraction path escaped its root")
            if member.isdir():
                _ensure_control_directory(destination, parts)
                continue
            _ensure_control_directory(destination, parts[:-1])
            source = archive.extractfile(member)
            if source is None:
                _fail("control bundle file cannot be read")
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | _NOFOLLOW,
                0o640,
            )
            try:
                with source:
                    while block := source.read(1024 * 1024):
                        _write_all(descriptor, block)
                os.fchown(descriptor, 0, 0)
                os.fchmod(descriptor, 0o640)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    _fsync_directory(destination)


def _assert_root_owned_control_tree(control_dir: Path) -> None:
    """Require every imported control dependency to have static root metadata."""

    _assert_root_controlled_path(control_dir)
    if stat.S_IMODE(os.lstat(control_dir).st_mode) != 0o700:
        _fail("root release gate control root has an unexpected mode")
    for directory, directories, files in os.walk(control_dir, followlinks=False):
        current = Path(directory)
        for name in directories:
            candidate = current / name
            _assert_root_controlled_path(candidate)
            if stat.S_IMODE(os.lstat(candidate).st_mode) != 0o755:
                _fail("root release gate control directory has an unexpected mode")
        for name in files:
            _assert_root_controlled_path(current / name, file_mode=0o640)


def _populate_transaction_from_incoming(*, incoming: Path, release_id: str) -> Path:
    transaction_dir = TRANSACTION_ROOT / release_id
    _assert_root_controlled_path(transaction_dir)
    if stat.S_IMODE(os.lstat(transaction_dir).st_mode) != 0o700:
        _fail("release transaction directory has an unexpected mode")
    for name in ("release-manifest.json", "release-manifest.sig", "runtime.tar.gz", "control.tar.gz"):
        _publish_copy(incoming / name, transaction_dir / name)
    environment = incoming / "environment.env"
    if environment.exists():
        _publish_copy(environment, transaction_dir / "environment.env")
    _extract_control(transaction_dir / "control.tar.gz", transaction_dir / "control")
    _assert_root_owned_control_tree(transaction_dir / "control")
    return transaction_dir


def _prepare_runtime_candidates(*, transaction_dir: Path, manifest: _Manifest) -> tuple[Path, Path | None]:
    artifact_candidate = DEPLOY_STATE_DIR / f".artifact.{manifest.release_id}.candidate.tar.gz"
    _publish_copy(transaction_dir / "runtime.tar.gz", artifact_candidate)
    environment_candidate: Path | None = None
    if manifest.environment_upload:
        environment_candidate = Path("/etc/northstar") / f".northstar-quant.{manifest.release_id}.candidate.env"
        _publish_copy(transaction_dir / "environment.env", environment_candidate)
    return artifact_candidate, environment_candidate


def _invoke_control_entrypoint(
    *,
    transaction_dir: Path,
    manifest: _Manifest,
    artifact_candidate: Path,
    environment_candidate: Path | None,
) -> None:
    control_dir = transaction_dir / "control"
    entrypoint = control_dir / CONTROL_ENTRYPOINT
    _assert_root_controlled_path(control_dir)
    _assert_root_controlled_path(entrypoint, file_mode=0o640)
    profile = manifest.profile
    environment = {
        "PATH": _SAFE_PATH,
        "APP_NAME": profile["app_name"],
        "SERVICE_USER": profile["service_user"],
        "SYSTEMD_SERVICE_NAME": profile["systemd_service_name"],
        "SERVICE_HOME": "/var/lib/northstar",
        "APP_ROOT": "/opt/northstar",
        "CONFIG_DIR": "/etc/northstar",
        "STATE_DIR": "/var/lib/northstar",
        "CACHE_DIR": "/var/cache/northstar",
        "LOG_DIR": "/var/log/northstar",
        "SERVICE_MODE": profile["service_mode"],
        "PYTHON_VERSION": profile["python_version"],
        "UV_VERSION": profile["uv_version"],
        "KEEP_RELEASES": profile["keep_releases"],
        "CONFIRM_LIVE_DEPLOY": profile["confirm_live_deploy"],
        "RUNTIME_STORAGE_DIR": profile["runtime_storage_dir"],
        "RUNTIME_DOWNLOADS_DIR": profile["runtime_downloads_dir"],
        "RUNTIME_REPORTS_DIR": profile["runtime_reports_dir"],
        "RUNTIME_LOG_DIR": profile["runtime_log_dir"],
        "RUNTIME_CACHE_DIR": profile["runtime_cache_dir"],
        "RUNTIME_MATPLOTLIB_DIR": profile["runtime_matplotlib_dir"],
        "DASHBOARD_DEPLOY_ENABLED": profile["dashboard_deploy_enabled"],
        "SETUP_SERVER": profile["setup_server"],
        "RELEASE_ID": manifest.release_id,
        "ARTIFACT_TARBALL": str(artifact_candidate),
        "ARTIFACT_SHA256": manifest.runtime.sha256,
        "CANDIDATE_ENV_FILE": "" if environment_candidate is None else str(environment_candidate),
        "RELEASE_TRANSACTION_ROOT": str(TRANSACTION_ROOT),
        "RELEASE_MANIFEST_FILE": str(transaction_dir / "release-manifest.json"),
        "RELEASE_MANIFEST_SIGNATURE_FILE": str(transaction_dir / "release-manifest.sig"),
    }
    result = subprocess.run(
        ["/bin/bash", "-p", str(entrypoint)],
        check=False,
        env=environment,
    )
    if result.returncode != 0:
        _fail("root-owned release control failed; durable transaction requires recovery review")


def _transaction_hook(control_dir: Path) -> Path:
    hook = control_dir / "scripts/deploy/release_transaction_hook.py"
    _assert_root_controlled_path(hook, file_mode=0o640)
    return hook


def _run_transaction_hook(*, control_dir: Path, arguments: Sequence[str]) -> None:
    hook = _transaction_hook(control_dir)
    result = subprocess.run(
        ["/usr/bin/python3", "-I", str(hook), "--root", str(TRANSACTION_ROOT), *arguments],
        check=False,
        env={"PATH": _SAFE_PATH},
        capture_output=True,
    )
    if result.returncode != 0:
        _fail("root release gate cannot persist the durable transaction journal")


def _begin_transaction(*, control_dir: Path, manifest: _Manifest, request_sha256: str) -> None:
    """Start the control bundle's audited journal before candidate mutation."""

    _run_transaction_hook(
        control_dir=control_dir,
        arguments=("begin", manifest.release_id, request_sha256, manifest.runtime.sha256),
    )


def _transition_transaction(*, control_dir: Path, release_id: str, state: str) -> None:
    _run_transaction_hook(
        control_dir=control_dir,
        arguments=("transition", release_id, state),
    )


def _record_precontrol_failure(*, control_dir: Path, release_id: str) -> None:
    """Persist a terminal pre-migration failure without guessing recovery."""

    try:
        _transition_transaction(control_dir=control_dir, release_id=release_id, state="failed")
    except RootReleaseRunnerError:
        # A damaged or unexpectedly advanced journal remains tangible evidence
        # for a human operator; this gate never repairs it automatically.
        return


def submit_from_stream(stream: BinaryIO) -> dict[str, str]:
    """Receive a signed release submission and invoke only root-owned control."""

    _assert_gate_layout()
    if _read_exact(stream, len(_MAGIC)) != _MAGIC:
        _fail("release submission has an unknown protocol header")
    manifest_bytes = _read_exact(
        stream,
        _read_length(stream, width=4, maximum=_MAX_MANIFEST_BYTES, label="manifest"),
    )
    signature = _read_exact(
        stream,
        _read_length(stream, width=4, maximum=_MAX_SIGNATURE_BYTES, label="signature"),
    )
    gate_identity = _gate_identity()
    manifest = parse_manifest(manifest_bytes, expected_gate_identity=gate_identity)
    _verify_signature(manifest_bytes, signature, namespace=SIGNATURE_NAMESPACE)
    incoming = _new_incoming_directory()
    _publish_bytes(incoming / "release-manifest.json", manifest_bytes)
    _publish_bytes(incoming / "release-manifest.sig", signature)
    runtime_length = _read_length(stream, width=8, maximum=_MAX_RUNTIME_BYTES, label="runtime")
    runtime_digest = _receive_stream_to_file(
        stream,
        length=runtime_length,
        destination=incoming / "runtime.tar.gz",
        maximum=_MAX_RUNTIME_BYTES,
    )
    control_length = _read_length(stream, width=8, maximum=_MAX_CONTROL_BYTES, label="control")
    control_digest = _receive_stream_to_file(
        stream,
        length=control_length,
        destination=incoming / "control.tar.gz",
        maximum=_MAX_CONTROL_BYTES,
    )
    environment_length = _read_length(stream, width=8, maximum=_MAX_ENVIRONMENT_BYTES, label="environment")
    environment_signature_length = _read_length(
        stream,
        width=4,
        maximum=_MAX_SIGNATURE_BYTES,
        label="environment signature",
    )
    if manifest.environment_upload != (environment_length > 0):
        _fail("release submission environment presence differs from signed manifest")
    if environment_length:
        environment_signature = _read_exact(stream, environment_signature_length)
        _validate_transport_signature(environment_signature, label="environment signature")
        _receive_stream_to_file(
            stream,
            length=environment_length,
            destination=incoming / "environment.env",
            maximum=_MAX_ENVIRONMENT_BYTES,
        )
        _verify_environment_signature(
            environment_path=incoming / "environment.env",
            signature=environment_signature,
            release_id=manifest.release_id,
        )
    elif environment_signature_length:
        _fail("release submission has an environment signature without an environment")
    if stream.read(1):
        _fail("release submission has trailing bytes")
    if not hmac.compare_digest(runtime_digest, manifest.runtime.sha256) or not hmac.compare_digest(control_digest, manifest.control.sha256):
        _fail("release submission archive digest differs from signed manifest")
    _assert_bundle_matches(incoming / "runtime.tar.gz", manifest.runtime, control=False)
    _assert_bundle_matches(incoming / "control.tar.gz", manifest.control, control=True)

    incoming_control = incoming / "control"
    _extract_control(incoming / "control.tar.gz", incoming_control)
    _assert_root_owned_control_tree(incoming_control)
    transaction_started = False
    control_invoked = False
    with _exclusive_deploy_lock():
        try:
            _begin_transaction(
                control_dir=incoming_control,
                manifest=manifest,
                request_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            )
            transaction_started = True
            _transition_transaction(
                control_dir=incoming_control,
                release_id=manifest.release_id,
                state="verified",
            )
            transaction_dir = _populate_transaction_from_incoming(
                incoming=incoming,
                release_id=manifest.release_id,
            )
            artifact_candidate, environment_candidate = _prepare_runtime_candidates(
                transaction_dir=transaction_dir,
                manifest=manifest,
            )
            control_invoked = True
            _invoke_control_entrypoint(
                transaction_dir=transaction_dir,
                manifest=manifest,
                artifact_candidate=artifact_candidate,
                environment_candidate=environment_candidate,
            )
        except (OSError, RootReleaseRunnerError, subprocess.SubprocessError):
            if transaction_started and not control_invoked:
                _record_precontrol_failure(
                    control_dir=incoming_control,
                    release_id=manifest.release_id,
                )
            raise
    return {
        "gate_identity": gate_identity,
        "release_id": manifest.release_id,
        "state": "promoted",
        "transaction_dir": str(transaction_dir),
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fixed Northstar root release gate.")
    subcommands = parser.add_subparsers(dest="operation", required=True)
    subcommands.add_parser("identity")
    subcommands.add_parser("submit")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        if args.operation == "identity":
            _assert_gate_layout()
            print(json.dumps({"gate_identity": _gate_identity(), "gate_protocol": GATE_PROTOCOL}, separators=(",", ":"), sort_keys=True))
            return 0
        result = submit_from_stream(sys.stdin.buffer)
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    except (OSError, RootReleaseRunnerError, subprocess.SubprocessError) as exc:
        print(f"root release gate denied request: {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

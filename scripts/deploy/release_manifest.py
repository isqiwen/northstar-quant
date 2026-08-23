"""Canonical, signed input manifest for the root release gate.

The manifest deliberately describes only public release facts: immutable
runtime/control bundle hashes and indexes, a committed revision, a fixed gate
identity, and an allowlisted deployment profile.  It never contains an
environment file, a secret digest, a controller filesystem path, or an
executable command supplied by the SSH deployment account.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import stat
import tarfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Mapping, Sequence


class ReleaseManifestError(ValueError):
    """A release manifest or one of its immutable bundle inputs is unsafe."""


FORMAT: Final = "northstar.release-manifest.v1"
GATE_PROTOCOL: Final = "northstar.release-gate.v1"
CONTROL_ENTRYPOINT: Final = "scripts/deploy/gate_release.sh"
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REVISION_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
_UV_VERSION_PATTERN: Final = re.compile(r"^[0-9][0-9A-Za-z.+-]{0,63}$")
_SAFE_MEMBER_COMPONENT: Final = re.compile(r"^[A-Za-z0-9._-]+$")
_MAX_MANIFEST_BYTES: Final = 8 * 1024 * 1024
_MAX_BUNDLE_ENTRIES: Final = 20_000
_MAX_PROFILE_VALUE_BYTES: Final = 1024
_SECRET_FIELD_MARKERS: Final = (
    "secret",
    "token",
    "password",
    "credential",
    "dsn",
    "private_key",
    "environment_hash",
)
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
        "service_mode",
        "service_user",
        "setup_server",
        "systemd_service_name",
        "uv_version",
        "runtime_cache_dir",
        "runtime_downloads_dir",
        "runtime_log_dir",
        "runtime_matplotlib_dir",
        "runtime_reports_dir",
        "runtime_storage_dir",
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
class BundleEntry:
    """One stable, non-link archive member recorded in a release manifest."""

    path: str
    kind: str
    mode: int
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class BundleDescriptor:
    """Digest and complete member index for one immutable tar.gz bundle."""

    sha256: str
    size_bytes: int
    entries: tuple[BundleEntry, ...]


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    """Exact public facts that a signer authorizes for a release request."""

    release_id: str
    revision: str
    created_at: datetime
    gate_identity: str
    profile: Mapping[str, str]
    environment_upload: bool
    runtime: BundleDescriptor
    control: BundleDescriptor

    def canonical_bytes(self) -> bytes:
        return canonical_manifest_bytes(self)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def _canonical_json(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReleaseManifestError("manifest timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ReleaseManifestError("manifest timestamp must be canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ReleaseManifestError("manifest timestamp is invalid") from exc
    if _timestamp(parsed) != value:
        raise ReleaseManifestError("manifest timestamp is not canonical")
    return parsed


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise ReleaseManifestError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _require_release_id(value: object) -> str:
    if not isinstance(value, str) or not _RELEASE_ID_PATTERN.fullmatch(value):
        raise ReleaseManifestError("release_id is invalid")
    return value


def _require_revision(value: object) -> str:
    if not isinstance(value, str) or not _REVISION_PATTERN.fullmatch(value):
        raise ReleaseManifestError("revision must be a full committed Git SHA-1")
    return value


def _require_member_path(value: object) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise ReleaseManifestError("bundle entry path is unsafe")
    parts = value.split("/")
    if any(part in {"", ".", ".."} or not _SAFE_MEMBER_COMPONENT.fullmatch(part) for part in parts):
        raise ReleaseManifestError("bundle entry path is unsafe")
    return value


def _require_nonnegative_int(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ReleaseManifestError(f"{field} must be a non-negative integer")
    return value


def _validate_profile(profile: Mapping[str, str]) -> dict[str, str]:
    if set(profile) != _PROFILE_FIELDS:
        raise ReleaseManifestError("release profile fields are not exactly allowlisted")
    normalized: dict[str, str] = {}
    for key, value in profile.items():
        lower_key = key.casefold()
        if any(marker in lower_key for marker in _SECRET_FIELD_MARKERS):
            raise ReleaseManifestError("release profile must not contain secret fields")
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ReleaseManifestError("release profile values must be non-empty strings")
        if len(value.encode("utf-8")) > _MAX_PROFILE_VALUE_BYTES:
            raise ReleaseManifestError("release profile value exceeds the fixed size limit")
        normalized[key] = value
    if normalized["app_name"] != "northstar-quant" or normalized["service_user"] != "northstar":
        raise ReleaseManifestError("release profile has an unexpected production identity")
    if normalized["systemd_service_name"] != "northstar-quant":
        raise ReleaseManifestError("release profile has an unexpected systemd service")
    if normalized["service_mode"] not in {"health", "scheduler"}:
        raise ReleaseManifestError("release profile service mode is unsupported")
    if normalized["confirm_live_deploy"] not in {"NO", "YES"}:
        raise ReleaseManifestError("release profile live confirmation is invalid")
    for key in ("setup_server", "dashboard_deploy_enabled", "ntfy_deploy_enabled"):
        if normalized[key] not in {"0", "1"}:
            raise ReleaseManifestError(f"release profile {key} must be 0 or 1")
    if normalized["ntfy_deploy_enabled"] != "0":
        raise ReleaseManifestError("release gate does not accept an unsigned ntfy bootstrap path")
    if not _UV_VERSION_PATTERN.fullmatch(normalized["uv_version"]):
        raise ReleaseManifestError("release profile uv_version is invalid")
    try:
        keep_releases = int(normalized["keep_releases"])
    except ValueError as exc:
        raise ReleaseManifestError("release profile keep_releases is invalid") from exc
    if keep_releases < 2:
        raise ReleaseManifestError("release profile keep_releases must retain one rollback candidate")
    _validate_runtime_profile_paths(normalized)
    return normalized


def _require_runtime_profile_path(value: str, *, field: str) -> str:
    if (
        not value.startswith("/")
        or value.startswith("//")
        or "//" in value
        or "/../" in f"/{value}/"
        or "/./" in f"/{value}/"
        or not re.fullmatch(r"[A-Za-z0-9/._-]+", value)
        or posixpath.normpath(value) != value
    ):
        raise ReleaseManifestError(f"release profile {field} path is invalid")
    if posixpath.dirname(value) not in _RUNTIME_PARENT_DIRECTORIES:
        raise ReleaseManifestError(f"release profile {field} must be a direct managed runtime leaf")
    if value in _RUNTIME_RESERVED_LEAVES:
        raise ReleaseManifestError(f"release profile {field} uses a reserved runtime leaf")
    return value


def _validate_runtime_profile_paths(profile: Mapping[str, str]) -> None:
    paths = tuple(
        _require_runtime_profile_path(profile[field], field=field) for field in _RUNTIME_PROFILE_FIELDS
    )
    for index, first_path in enumerate(paths):
        for second_path in paths[index + 1 :]:
            if first_path == second_path or first_path.startswith(f"{second_path}/") or second_path.startswith(
                f"{first_path}/"
            ):
                raise ReleaseManifestError("release profile runtime paths overlap")


def _validate_entry(entry: BundleEntry) -> BundleEntry:
    path = _require_member_path(entry.path)
    if entry.kind not in {"file", "directory"}:
        raise ReleaseManifestError("bundle entry kind is unsupported")
    mode = _require_nonnegative_int(entry.mode, field="bundle entry mode")
    if mode > 0o777 or mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
        raise ReleaseManifestError("bundle entry has a privileged mode")
    size_bytes = _require_nonnegative_int(entry.size_bytes, field="bundle entry size")
    sha256 = _require_sha256(entry.sha256, field="bundle entry sha256")
    if entry.kind == "directory" and size_bytes != 0:
        raise ReleaseManifestError("directory bundle entry must have zero size")
    return BundleEntry(path=path, kind=entry.kind, mode=mode, size_bytes=size_bytes, sha256=sha256)


def _validate_descriptor(descriptor: BundleDescriptor) -> BundleDescriptor:
    sha256 = _require_sha256(descriptor.sha256, field="bundle sha256")
    size_bytes = _require_nonnegative_int(descriptor.size_bytes, field="bundle size")
    if size_bytes == 0:
        raise ReleaseManifestError("bundle size must be positive")
    if not descriptor.entries or len(descriptor.entries) > _MAX_BUNDLE_ENTRIES:
        raise ReleaseManifestError("bundle entry count is invalid")
    entries = tuple(_validate_entry(entry) for entry in descriptor.entries)
    if tuple(sorted(entry.path for entry in entries)) != tuple(entry.path for entry in entries):
        raise ReleaseManifestError("bundle entries must be sorted by path")
    if len({entry.path for entry in entries}) != len(entries):
        raise ReleaseManifestError("bundle contains duplicate member paths")
    file_paths = {entry.path for entry in entries if entry.kind == "file"}
    for entry in entries:
        if any("/".join(entry.path.split("/")[:index]) in file_paths for index in range(1, len(entry.path.split("/")))):
            raise ReleaseManifestError("bundle entry is below a regular file")
    return BundleDescriptor(sha256=sha256, size_bytes=size_bytes, entries=entries)


def canonical_manifest_bytes(manifest: ReleaseManifest) -> bytes:
    """Validate and serialize a release manifest into one exact byte stream."""

    profile = _validate_profile(manifest.profile)
    runtime = _validate_descriptor(manifest.runtime)
    control = _validate_descriptor(manifest.control)
    payload = {
        "control": _descriptor_payload(control),
        "created_at": _timestamp(manifest.created_at),
        "entrypoint": CONTROL_ENTRYPOINT,
        "environment_upload": manifest.environment_upload,
        "format": FORMAT,
        "gate_identity": _require_sha256(manifest.gate_identity, field="gate_identity"),
        "gate_protocol": GATE_PROTOCOL,
        "profile": profile,
        "release_id": _require_release_id(manifest.release_id),
        "revision": _require_revision(manifest.revision),
        "runtime": _descriptor_payload(runtime),
    }
    if not isinstance(manifest.environment_upload, bool):
        raise ReleaseManifestError("environment_upload must be boolean")
    return _canonical_json(payload)


def _descriptor_payload(descriptor: BundleDescriptor) -> dict[str, object]:
    return {
        "entries": [asdict(entry) for entry in descriptor.entries],
        "sha256": descriptor.sha256,
        "size_bytes": descriptor.size_bytes,
    }


def parse_manifest(raw: bytes) -> ReleaseManifest:
    """Parse only an exact canonical manifest; reject duplicate JSON keys."""

    if not raw or len(raw) > _MAX_MANIFEST_BYTES:
        raise ReleaseManifestError("manifest size is invalid")
    try:
        decoded = raw.decode("utf-8")
        payload = json.loads(decoded, object_pairs_hook=_reject_duplicate_object_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseManifestError("manifest is not valid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != _MANIFEST_FIELDS:
        raise ReleaseManifestError("manifest fields are invalid")
    if payload["format"] != FORMAT or payload["gate_protocol"] != GATE_PROTOCOL:
        raise ReleaseManifestError("manifest format or gate protocol is unsupported")
    if payload["entrypoint"] != CONTROL_ENTRYPOINT:
        raise ReleaseManifestError("manifest control entrypoint is not fixed")
    if not isinstance(payload["environment_upload"], bool):
        raise ReleaseManifestError("manifest environment_upload is invalid")
    if not isinstance(payload["profile"], dict):
        raise ReleaseManifestError("manifest profile is invalid")
    profile = _validate_profile(payload["profile"])
    manifest = ReleaseManifest(
        release_id=_require_release_id(payload["release_id"]),
        revision=_require_revision(payload["revision"]),
        created_at=_parse_timestamp(payload["created_at"]),
        gate_identity=_require_sha256(payload["gate_identity"], field="gate_identity"),
        profile=profile,
        environment_upload=payload["environment_upload"],
        runtime=_descriptor_from_payload(payload["runtime"]),
        control=_descriptor_from_payload(payload["control"]),
    )
    if raw != canonical_manifest_bytes(manifest):
        raise ReleaseManifestError("manifest is not canonical JSON")
    return manifest


def _reject_duplicate_object_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseManifestError("manifest contains duplicate JSON keys")
        result[key] = value
    return result


def _descriptor_from_payload(payload: object) -> BundleDescriptor:
    if not isinstance(payload, dict) or set(payload) != _BUNDLE_FIELDS:
        raise ReleaseManifestError("bundle descriptor fields are invalid")
    entries_payload = payload["entries"]
    if not isinstance(entries_payload, list):
        raise ReleaseManifestError("bundle descriptor entries are invalid")
    entries: list[BundleEntry] = []
    for item in entries_payload:
        if not isinstance(item, dict) or set(item) != _ENTRY_FIELDS:
            raise ReleaseManifestError("bundle entry fields are invalid")
        entries.append(
            BundleEntry(
                path=_require_member_path(item["path"]),
                kind=item["kind"] if isinstance(item["kind"], str) else "",
                mode=_require_nonnegative_int(item["mode"], field="bundle entry mode"),
                size_bytes=_require_nonnegative_int(item["size_bytes"], field="bundle entry size"),
                sha256=_require_sha256(item["sha256"], field="bundle entry sha256"),
            )
        )
    return _validate_descriptor(
        BundleDescriptor(
            sha256=_require_sha256(payload["sha256"], field="bundle sha256"),
            size_bytes=_require_nonnegative_int(payload["size_bytes"], field="bundle size"),
            entries=tuple(entries),
        )
    )


def bundle_descriptor(path: Path, *, expected_root: str | None = None) -> BundleDescriptor:
    """Index a gzip tar bundle without extracting a member to the filesystem."""

    if not path.is_file() or path.is_symlink():
        raise ReleaseManifestError("bundle must be a regular non-symlink file")
    entries: list[BundleEntry] = []
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            for member in archive:
                if len(entries) >= _MAX_BUNDLE_ENTRIES:
                    raise ReleaseManifestError("bundle exceeds the member count limit")
                member_path = _require_member_path(member.name.removesuffix("/"))
                if (
                    expected_root is not None
                    and member_path != "DEPLOY_CONTROL_META.json"
                    and member_path != expected_root
                    and not member_path.startswith(f"{expected_root}/")
                ):
                    raise ReleaseManifestError("control bundle member is outside the allowed root")
                if member.issym() or member.islnk() or member.issparse() or not (member.isfile() or member.isdir()):
                    raise ReleaseManifestError("bundle contains a non-regular or link member")
                if member.mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
                    raise ReleaseManifestError("bundle contains a privileged mode")
                if member.isdir():
                    digest = hashlib.sha256(b"").hexdigest()
                    size_bytes = 0
                    kind = "directory"
                else:
                    source = archive.extractfile(member)
                    if source is None:
                        raise ReleaseManifestError("bundle member cannot be read")
                    digest_builder = hashlib.sha256()
                    size_bytes = 0
                    with source:
                        for block in iter(lambda: source.read(1024 * 1024), b""):
                            size_bytes += len(block)
                            digest_builder.update(block)
                    if size_bytes != member.size:
                        raise ReleaseManifestError("bundle member size changed while indexing")
                    digest = digest_builder.hexdigest()
                    kind = "file"
                entries.append(
                    BundleEntry(
                        path=member_path,
                        kind=kind,
                        mode=stat.S_IMODE(member.mode),
                        size_bytes=size_bytes,
                        sha256=digest,
                    )
                )
    except ReleaseManifestError:
        raise
    except (EOFError, OSError, tarfile.TarError, UnicodeError) as exc:
        raise ReleaseManifestError("bundle cannot be read as a valid gzip tar archive") from exc
    descriptor = BundleDescriptor(
        sha256=_file_sha256(path),
        size_bytes=path.stat().st_size,
        entries=tuple(sorted(entries, key=lambda entry: entry.path)),
    )
    return _validate_descriptor(descriptor)


def verify_bundle(path: Path, descriptor: BundleDescriptor, *, expected_root: str | None = None) -> None:
    """Fail closed unless a file exactly matches the signed descriptor."""

    actual = bundle_descriptor(path, expected_root=expected_root)
    expected = _validate_descriptor(descriptor)
    if actual != expected:
        raise ReleaseManifestError("bundle does not match the immutable manifest index")


def build_manifest(
    *,
    release_id: str,
    revision: str,
    gate_identity: str,
    profile: Mapping[str, str],
    environment_upload: bool,
    runtime_bundle: Path,
    control_bundle: Path,
    created_at: datetime | None = None,
) -> ReleaseManifest:
    """Build one canonical public manifest from exact local bundle bytes."""

    return ReleaseManifest(
        release_id=_require_release_id(release_id),
        revision=_require_revision(revision),
        created_at=created_at or datetime.now(UTC),
        gate_identity=_require_sha256(gate_identity, field="gate_identity"),
        profile=_validate_profile(profile),
        environment_upload=environment_upload,
        runtime=bundle_descriptor(runtime_bundle),
        control=bundle_descriptor(control_bundle, expected_root="scripts/deploy"),
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

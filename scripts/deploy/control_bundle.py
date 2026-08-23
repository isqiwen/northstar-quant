"""Build the signed control bundle consumed by the Linux release gate.

The runtime artifact and the deployment control code deliberately travel as
different opaque inputs.  A normal SSH deployment user may transport this
bundle, but may never execute it with privilege: the fixed root release gate
verifies the signed release manifest, extracts this archive below a root-only
transaction directory, and invokes only :data:`CONTROL_ENTRYPOINT` there.

This module has no target-side side effects.  It is safe to use on Windows and
Linux workstations while preparing a release.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from stat import S_IMODE
from typing import Final, Iterable

try:  # Allow direct-script execution as well as package imports.
    from .archive_policy import archive_path_is_excluded
except ImportError:  # pragma: no cover - direct-script invocation path.
    from archive_policy import archive_path_is_excluded


class ControlBundleError(ValueError):
    """The release control bundle cannot be built safely."""


CONTROL_BUNDLE_FORMAT: Final = "northstar-release-control-v1"
CONTROL_ENTRYPOINT: Final = "scripts/deploy/gate_release.sh"
_CONTROL_ROOT: Final = Path("scripts/deploy")
_CONTROL_METADATA_PATH: Final = Path("DEPLOY_CONTROL_META.json")


@dataclass(frozen=True)
class ControlArtifact:
    """A portable, immutable input for the root release gate."""

    path: Path
    sha256: str
    size_bytes: int


def file_sha256(path: Path) -> str:
    """Return the SHA-256 for one regular local file."""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _iter_control_paths(project_root: Path) -> Iterable[Path]:
    root = project_root / _CONTROL_ROOT
    if not root.is_dir() or root.is_symlink():
        raise ControlBundleError(f"release control source is unavailable: {_CONTROL_ROOT}")
    if not (project_root / CONTROL_ENTRYPOINT).is_file():
        raise ControlBundleError(f"release control entrypoint is unavailable: {CONTROL_ENTRYPOINT}")

    yield root
    for candidate in sorted(root.rglob("*")):
        relative = candidate.relative_to(project_root)
        if candidate.is_symlink():
            raise ControlBundleError(f"release control cannot contain a symbolic link: {relative}")
        is_directory = candidate.is_dir()
        if archive_path_is_excluded(relative, is_directory=is_directory):
            continue
        if candidate.is_file() or is_directory:
            yield candidate
            continue
        raise ControlBundleError(f"release control contains an unsupported file type: {relative}")


def _add_path(archive: tarfile.TarFile, *, project_root: Path, path: Path) -> None:
    relative = path.relative_to(project_root)
    metadata = path.stat()
    member = tarfile.TarInfo(relative.as_posix())
    member.uid = 0
    member.gid = 0
    member.uname = ""
    member.gname = ""
    member.mode = S_IMODE(metadata.st_mode)
    member.mtime = int(metadata.st_mtime)
    if path.is_dir():
        member.type = tarfile.DIRTYPE
        member.size = 0
        archive.addfile(member)
        return
    member.size = metadata.st_size
    with path.open("rb") as source:
        archive.addfile(member, source)


def _metadata_bytes(*, built_at: datetime) -> bytes:
    payload = {
        "entrypoint": CONTROL_ENTRYPOINT,
        "format": CONTROL_BUNDLE_FORMAT,
        "generated_at": built_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def build_control_artifact(
    *,
    project_root: Path,
    output_dir: Path,
    release_id: str,
    built_at: datetime | None = None,
) -> ControlArtifact:
    """Create a no-overwrite control bundle for a signed release request."""

    if not release_id or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for character in release_id):
        raise ControlBundleError("release identifier is invalid for a control bundle")
    project_root = project_root.resolve()
    output_dir = output_dir.resolve()
    source_root = (project_root / _CONTROL_ROOT).resolve()
    if output_dir == source_root or output_dir in source_root.parents:
        raise ControlBundleError("control bundle output cannot be inside its source tree")
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"northstar-release-control-{release_id}.tar.gz"
    if destination.exists():
        raise ControlBundleError(f"refusing to overwrite an existing control bundle: {destination}")
    built_at = built_at or datetime.now(UTC)
    metadata = _metadata_bytes(built_at=built_at)

    try:
        with tarfile.open(destination, mode="x:gz", format=tarfile.PAX_FORMAT) as archive:
            for path in _iter_control_paths(project_root):
                _add_path(archive, project_root=project_root, path=path)
            member = tarfile.TarInfo(_CONTROL_METADATA_PATH.as_posix())
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            member.mode = 0o644
            member.mtime = int(built_at.timestamp())
            member.size = len(metadata)
            archive.addfile(member, BytesIO(metadata))
    except ControlBundleError:
        destination.unlink(missing_ok=True)
        raise
    except (OSError, tarfile.TarError) as exc:
        destination.unlink(missing_ok=True)
        raise ControlBundleError(f"unable to write release control bundle: {exc}") from exc
    destination.chmod(0o600)
    return ControlArtifact(
        path=destination,
        sha256=file_sha256(destination),
        size_bytes=destination.stat().st_size,
    )

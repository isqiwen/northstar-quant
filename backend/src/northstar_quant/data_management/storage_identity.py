"""Bind retained files to an explicit storage identity, on local disk or NFS."""

from __future__ import annotations

import ipaddress
import os
import stat
import tempfile
from pathlib import Path
from uuid import UUID

_MARKER = ".northstar-storage-id"


def require_identity(root: Path, identity: str) -> None:
    if str(UUID(identity)) != identity:
        raise ValueError("NORTHSTAR_STORAGE_ID must be a canonical UUID")
    try:
        descriptor = os.open(root / _MARKER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("Storage identity is not a regular file")
            content = stream.read(38)
    except OSError as error:
        raise ValueError(
            "Source mount is missing or inaccessible; storage not initialized"
        ) from error
    if content != (identity + "\n").encode("ascii"):
        raise ValueError("Source mount identity does not match NORTHSTAR_STORAGE_ID")


def initialize(root: Path, identity: str) -> None:
    """Initialize prepared storage; never adopt unidentified existing data."""
    from .files import SourceFiles

    if str(UUID(identity)) != identity:
        raise ValueError("NORTHSTAR_STORAGE_ID must be a canonical UUID")
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise ValueError("Source directory must already exist as a dedicated absolute directory")
    if (root / _MARKER).exists():
        require_identity(root, identity)
    else:
        if any(root.iterdir()):
            raise ValueError("Source directory is not empty; explicit data migration is required")
        descriptor, temporary = tempfile.mkstemp(prefix=".identity-", dir=root)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write((identity + "\n").encode("ascii"))
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, root / _MARKER, follow_symlinks=False)
            SourceFiles._sync(root)
        finally:
            Path(temporary).unlink(missing_ok=True)
    SourceFiles(root)


def probe(root: Path, identity: str) -> None:
    """Check the actual writable client mount without changing retained source objects."""
    from .files import SourceFiles

    require_identity(root, identity)
    with tempfile.TemporaryDirectory(prefix=".storage-probe-", dir=root) as directory:
        files = SourceFiles(Path(directory), min_free_bytes=0)
        content = b"Northstar source mount write/link/fsync/read probe\n"
        stored = files.store(content)
        if files.read(stored.content_hash, stored.byte_count) != content:
            raise ValueError("Source mount failed source-file round trip")


def verify_mount(expected: dict[str, str], observed: dict[str, str]) -> None:
    mount = expected["mount"]
    version = expected["version"]
    if version not in {"3", "4", "4.1", "4.2"}:
        raise ValueError("NFS version must be 3, 4, 4.1 or 4.2")
    ipaddress.ip_address(expected["server"])
    if not expected["export"].startswith("/") or any(c in expected["export"] for c in "\n\r,:"):
        raise ValueError("Set the verified absolute QNAP NFS export path")
    if observed.get("target") != mount or observed.get("fstype") not in {"nfs", "nfs4"}:
        raise ValueError(
            "NAS mount is not ready: expected an NFS mount at the exact configured path"
        )
    source = observed.get("source", "")
    if source not in {
        f"{expected['server']}:{expected['export']}",
        f"{expected.get('hostname', 'nas.local')}:{expected['export']}",
    }:
        raise ValueError("Mounted NFS server/export does not match the deployment configuration")
    options = observed.get("options", "").split(",")
    actual = next((option[5:] for option in options if option.startswith("vers=")), "")
    if actual != version and not (version == "4" and actual.startswith("4.")):
        raise ValueError("Mounted NFS version does not match the deployment configuration")
    if (
        expected["mode"] not in options
        or "hard" not in options
        or "soft" in options
        or "softerr" in options
    ):
        raise ValueError("NFS requires hard mounts and the configured read/write access mode")


def verify_local_directory(root: Path, observed: dict[str, str], *, writable: bool) -> None:
    """Local mode admits dedicated persistent directories, never a failed network mount."""
    if not root.is_absolute() or not root.is_dir() or root.resolve() != root:
        raise ValueError("Local storage must be an existing absolute directory without symlinks")
    if root == Path("/"):
        raise ValueError("Local storage requires a dedicated directory")
    if observed.get("fstype") not in {"ext4", "xfs", "btrfs", "zfs"}:
        raise ValueError("Local storage requires ext4/xfs/btrfs/zfs, not NFS/SMB/tmpfs/overlay")
    options = observed.get("options", "").split(",")
    if writable and "rw" not in options:
        raise ValueError("Local storage filesystem is read-only")

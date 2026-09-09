"""Bind retained files to an explicit directory identity, independent of storage provider."""

from __future__ import annotations

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

"""Bounded regular-file hashing and durable completion records for owned backups."""

import hashlib
import os
import stat
from pathlib import Path


def file_hash(path: Path) -> str:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("backup content must be a regular file")
        digest = hashlib.sha256()
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_record(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_record(path: Path, *, max_bytes: int = 20 * 1024 * 1024) -> bytes:
    """Read a bounded completion record without following links or waiting on a FIFO."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        details = os.fstat(stream.fileno())
        if not stat.S_ISREG(details.st_mode) or details.st_size > max_bytes:
            raise ValueError("backup manifest is not a bounded regular file")
        content = stream.read(max_bytes + 1)
    if len(content) != details.st_size or len(content) > max_bytes:
        raise ValueError("backup manifest changed or exceeded its size limit")
    return content

"""Bounded, immutable local source bytes; names supplied by users never become paths.

The current deployment owns one private POSIX directory. A file is visible under
its digest only after its complete bytes have been flushed, verified and linked
without replacement. A database failure may leave an unreferenced complete file;
inventory exposes it, but this Module deliberately provides no deletion operation.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast


@dataclass(frozen=True, slots=True)
class FileObject:
    content_hash: str
    byte_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SourceFiles:
    """Retain and verify one digest-named copy, with limits shared by CLI and Web."""

    def __init__(
        self,
        root: Path,
        *,
        max_file_bytes: int = 5 * 1024 * 1024,
        max_total_bytes: int = 10 * 1024**3,
        min_free_bytes: int = 256 * 1024**2,
    ) -> None:
        if not root.is_absolute():
            raise ValueError("source storage requires an absolute private directory")
        if (
            type(max_file_bytes) is not int
            or not 1 <= max_file_bytes <= 5 * 1024 * 1024
            or type(max_total_bytes) is not int
            or max_total_bytes < max_file_bytes
            or type(min_free_bytes) is not int
            or min_free_bytes < 0
        ):
            raise ValueError("invalid source file, archive or free-space limit")
        self.root = root.resolve()
        if self.root == Path(self.root.anchor) or self.root == Path.home():
            raise ValueError("source storage must be a dedicated private directory")
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        self.min_free_bytes = min_free_bytes
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ("objects", "staging"):
            self._directory(self.root / name)

    @classmethod
    def from_environment(cls) -> SourceFiles:
        value = os.environ.get("NORTHSTAR_DATA_DIR")
        if not value:
            raise ValueError("NORTHSTAR_DATA_DIR must name the managed private source directory")
        if (Path(value) / ".restore-incomplete").exists():
            raise ValueError("source restore is incomplete; do not start the application")
        return cls(
            Path(value),
            max_total_bytes=int(os.environ.get("NORTHSTAR_ARCHIVE_MAX_BYTES", str(10 * 1024**3))),
            min_free_bytes=int(
                os.environ.get("NORTHSTAR_ARCHIVE_MIN_FREE_BYTES", str(256 * 1024**2))
            ),
        )

    @staticmethod
    def _sync(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _directory(self, path: Path) -> None:
        if path.is_symlink():
            raise ValueError("source archive directories must not be symbolic links")
        if not path.exists():
            path.mkdir(mode=0o700, exist_ok=True)
            self._sync(path.parent)
        if not path.is_dir():
            raise ValueError("source archive directory is not available")

    def _path(self, content_hash: str) -> Path:
        if not isinstance(content_hash, str) or re.fullmatch(r"[0-9a-f]{64}", content_hash) is None:
            raise ValueError("invalid source content identity")
        parent = self.root / "objects" / content_hash[:2]
        if (self.root / "objects").is_symlink() or parent.is_symlink():
            raise ValueError("source archive directories must not be symbolic links")
        return parent / content_hash

    @contextmanager
    def _writer(self) -> Iterator[None]:
        descriptor = os.open(
            self.root / ".write.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def store(self, content: bytes) -> FileObject:
        if not isinstance(content, bytes) or not 1 <= len(content) <= self.max_file_bytes:
            raise ValueError("source must be nonempty bytes within the 5 MiB upload limit")
        identity = hashlib.sha256(content).hexdigest()
        result = FileObject(identity, len(content))
        destination = self._path(identity)
        with self._writer():
            if destination.exists() or destination.is_symlink():
                self.read(identity, len(content))
                return result
            usage = self.health()
            if cast(int, usage["used_bytes"]) + len(content) > self.max_total_bytes:
                raise ValueError("managed source archive capacity exceeded; nothing accepted")
            if shutil.disk_usage(self.root).free < self.min_free_bytes + len(content):
                raise ValueError("insufficient free disk space for durable source reception")
            self._directory(destination.parent)
            staging = self.root / "staging"
            self._directory(staging)
            descriptor, temporary = tempfile.mkstemp(prefix="receive-", dir=staging)
            path = Path(temporary)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                if hashlib.sha256(path.read_bytes()).hexdigest() != identity:
                    raise ValueError("source bytes failed verification before publication")
                os.link(path, destination, follow_symlinks=False)
                self._sync(destination.parent)
            finally:
                path.unlink(missing_ok=True)
                self._sync(staging)
        return result

    def read(self, content_hash: str, byte_count: int) -> bytes:
        if type(byte_count) is not int or not 1 <= byte_count <= self.max_file_bytes:
            raise ValueError("source byte count exceeds the current file limit")
        path = self._path(content_hash)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                details = os.fstat(stream.fileno())
                if not stat.S_ISREG(details.st_mode) or details.st_size != byte_count:
                    raise ValueError("managed source file size or type is corrupt")
                content = stream.read(self.max_file_bytes + 1)
        except OSError as error:
            raise ValueError("managed source file is missing or unreadable") from error
        if len(content) != byte_count or hashlib.sha256(content).hexdigest() != content_hash:
            raise ValueError("managed source file content does not match its saved digest")
        return content

    def inspect(self, content_hash: str, byte_count: int) -> str:
        path = self._path(content_hash)
        if not path.exists() and not path.is_symlink():
            return "MISSING"
        try:
            self.read(content_hash, byte_count)
        except ValueError:
            return "CORRUPT"
        return "AVAILABLE"

    def inventory(self) -> list[FileObject]:
        """Enumerate actual objects, without trusting their names as integrity evidence."""

        result = []
        objects = self.root / "objects"
        if objects.is_symlink():
            raise ValueError("source archive directory must not be a symbolic link")
        for prefix in objects.iterdir():
            if (
                prefix.is_symlink()
                or not prefix.is_dir()
                or re.fullmatch(r"[0-9a-f]{2}", prefix.name) is None
            ):
                raise ValueError("unexpected object directory in managed source archive")
            for path in prefix.iterdir():
                if (
                    path.is_symlink()
                    or not path.is_file()
                    or re.fullmatch(r"[0-9a-f]{64}", path.name) is None
                    or not path.name.startswith(prefix.name)
                ):
                    raise ValueError("unexpected object in managed source archive")
                result.append(FileObject(path.name, path.stat().st_size))
        return sorted(result, key=lambda item: item.content_hash)

    def health(self) -> dict[str, object]:
        objects = self.inventory()
        staging = self.root / "staging"
        if staging.is_symlink():
            raise ValueError("source staging directory must not be a symbolic link")
        incomplete = list(staging.iterdir())
        if any(item.is_symlink() or not item.is_file() for item in incomplete):
            raise ValueError("unexpected object in source staging directory")
        used = sum(item.byte_count for item in objects) + sum(
            item.stat().st_size for item in incomplete
        )
        return {
            "used_bytes": used,
            "object_count": len(objects),
            "incomplete_file_count": len(incomplete),
            "max_file_bytes": self.max_file_bytes,
            "max_total_bytes": self.max_total_bytes,
            "min_free_bytes": self.min_free_bytes,
            "free_bytes": shutil.disk_usage(self.root).free,
            "deletion_enabled": False,
        }

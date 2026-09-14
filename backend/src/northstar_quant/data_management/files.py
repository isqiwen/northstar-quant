"""Bounded, immutable local source bytes; names supplied by users never become paths.

The current deployment owns one private POSIX directory. A file is visible under
its digest only after its complete bytes have been flushed, verified and linked
without replacement. A database failure may leave an unreferenced complete file;
inventory exposes it, but this Module deliberately provides no deletion operation.
"""

from __future__ import annotations

import fcntl
import hashlib
import logging
import os
import re
import stat
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
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
        max_total_bytes: int | None = None,
        min_free_bytes: int | None = None,
        shared_read: bool = False,
    ) -> None:
        if not root.is_absolute():
            raise ValueError("source storage requires an absolute private directory")
        if (
            type(max_file_bytes) is not int
            or not 1 <= max_file_bytes <= 5 * 1024 * 1024
            or (
                max_total_bytes is not None
                and (type(max_total_bytes) is not int or max_total_bytes < max_file_bytes)
            )
            or (
                min_free_bytes is not None
                and (type(min_free_bytes) is not int or min_free_bytes < 0)
            )
        ):
            raise ValueError("invalid source file, archive or free-space limit")
        self.shared_read = shared_read
        self.root = root.resolve()
        if self.root == Path(self.root.anchor) or self.root == Path.home():
            raise ValueError("source storage must be a dedicated private directory")
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        self.min_free_bytes = min_free_bytes
        self.root.mkdir(parents=True, exist_ok=True, mode=0o755 if shared_read else 0o700)
        for name in ("objects", "staging"):
            self._directory(self.root / name)

    @classmethod
    def from_environment(cls) -> SourceFiles:
        value = os.environ.get("NORTHSTAR_DATA_DIR")
        if not value:
            raise ValueError("NORTHSTAR_DATA_DIR must name the managed private source directory")
        identity = os.environ.get("NORTHSTAR_STORAGE_ID")
        if identity:
            from .storage_identity import require_identity

            require_identity(Path(value), identity)
        if (Path(value) / ".restore-incomplete").exists():
            raise ValueError("source restore is incomplete; do not start the application")
        return cls(
            Path(value),
            max_total_bytes=(
                int(os.environ["NORTHSTAR_ARCHIVE_MAX_BYTES"])
                if os.environ.get("NORTHSTAR_ARCHIVE_MAX_BYTES")
                else None
            ),
            min_free_bytes=(
                int(os.environ["NORTHSTAR_ARCHIVE_MIN_FREE_BYTES"])
                if os.environ.get("NORTHSTAR_ARCHIVE_MIN_FREE_BYTES")
                else None
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
            path.mkdir(
                mode=0o755 if self.shared_read and path.name != "staging" else 0o700, exist_ok=True
            )
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
    def _writer(self, *, exclusive: bool = True) -> Iterator[None]:
        descriptor = os.open(
            self.root / ".write.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        started = perf_counter()
        acquired = started
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            acquired = perf_counter()
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            logging.getLogger(__name__).info(
                "Source write shared_read=%s exclusive=%s wait=%.4f hold=%.4f",
                self.shared_read,
                exclusive,
                acquired - started,
                perf_counter() - acquired,
            )

    def store(self, content: bytes) -> FileObject:
        with self._writer(exclusive=self.max_total_bytes is not None):
            return self._store_locked(content, None)[0]

    def store_many(self, contents: Iterable[bytes]) -> tuple[FileObject, ...]:
        """Share admission for immutable writes; serialize an explicitly bounded archive."""
        with self._writer(exclusive=self.max_total_bytes is not None):
            used_bytes = (
                cast(int, self.health()["used_bytes"]) if self.max_total_bytes is not None else 0
            )
            result = []
            for content in contents:
                saved, added = self._store_locked(content, used_bytes)
                used_bytes += added
                result.append(saved)
            return tuple(result)

    def _store_locked(self, content: bytes, used_bytes: int | None) -> tuple[FileObject, int]:
        if not isinstance(content, bytes) or not 1 <= len(content) <= self.max_file_bytes:
            raise ValueError("source must be nonempty bytes within the 5 MiB upload limit")
        identity = hashlib.sha256(content).hexdigest()
        result = FileObject(identity, len(content))
        destination = self._path(identity)
        if destination.exists() or destination.is_symlink():
            self.read(identity, len(content))
            # A competing writer may have linked but not yet synced its directory.
            self._sync(destination.parent.parent)
            self._sync(destination.parent)
            return result, 0
        if self.max_total_bytes is not None:
            if used_bytes is None:
                used_bytes = cast(int, self.health()["used_bytes"])
            if used_bytes + len(content) > self.max_total_bytes:
                raise ValueError("managed source archive capacity exceeded; nothing accepted")
        capacity = self.capacity()
        if (
            cast(int, capacity["free_bytes"]) < cast(int, capacity["min_free_bytes"]) + len(content)
            or capacity["free_inodes"] == 0
        ):
            raise ValueError("insufficient free disk space for durable source reception")
        self._directory(destination.parent)
        staging = self.root / "staging"
        self._directory(staging)
        descriptor, temporary = tempfile.mkstemp(prefix="receive-", dir=staging)
        started = perf_counter()
        written = verified = linked = started
        added = len(content)
        path = Path(temporary)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                if self.shared_read:
                    os.fchmod(stream.fileno(), 0o644)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            written = perf_counter()
            if hashlib.sha256(path.read_bytes()).hexdigest() != identity:
                raise ValueError("source bytes failed verification before publication")
            verified = perf_counter()
            try:
                os.link(path, destination, follow_symlinks=False)
            except FileExistsError:
                # The winner must contain these exact immutable bytes, not merely
                # have the expected name. Corruption/symlinks are never replaced.
                self.read(identity, len(content))
                added = 0
            # Sync the shard's entry as well: another concurrent writer may have
            # created the directory and died before syncing its parent.
            self._sync(destination.parent.parent)
            self._sync(destination.parent)
            linked = perf_counter()
        finally:
            path.unlink(missing_ok=True)
            self._sync(staging)
        logging.getLogger(__name__).info(
            "Source durable shared_read=%s bytes=%s write=%.4f verify=%.4f link=%.4f cleanup=%.4f",
            self.shared_read,
            len(content),
            written - started,
            verified - written,
            linked - verified,
            perf_counter() - linked,
        )
        return result, added

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

    def remove_verified(self, content_hash: str, byte_count: int) -> None:
        """Unlink one verified orphan; caller must hold the Data reference freeze."""
        with self._writer():
            self.read(content_hash, byte_count)
            path = self._path(content_hash)
            path.unlink()
            self._sync(path.parent)

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

    def capacity(self) -> dict[str, object]:
        """Observe this filesystem without scanning objects, writing or repairing it."""
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            usage = os.fstatvfs(descriptor)
        finally:
            os.close(descriptor)
        free = usage.f_bavail * usage.f_frsize
        inodes = usage.f_favail if usage.f_files else None
        total = usage.f_blocks * usage.f_frsize
        reserve = (
            self.min_free_bytes if self.min_free_bytes is not None else max(1024**3, total // 20)
        )
        warning = max(reserve * 2, total // 10)
        return {
            "status": "LOW"
            if free <= reserve or inodes == 0
            else ("WARNING" if free <= warning else "OK"),
            "warning_free_bytes": warning,
            "free_bytes": free,
            "total_bytes": total,
            "min_free_bytes": reserve,
            "free_inodes": inodes,
        }

    def health(self) -> dict[str, object]:
        objects = self.inventory()
        staging = self.root / "staging"
        if staging.is_symlink():
            raise ValueError("source staging directory must not be a symbolic link")
        incomplete = list(staging.iterdir())
        sizes = []
        for item in incomplete:
            try:
                details = item.lstat()
            except FileNotFoundError:
                continue  # A parallel writer completed its disposable staging file.
            if not stat.S_ISREG(details.st_mode):
                raise ValueError("unexpected object in source staging directory")
            sizes.append(details.st_size)
        used = sum(item.byte_count for item in objects) + sum(sizes)
        return {
            "used_bytes": used,
            "object_count": len(objects),
            "incomplete_file_count": len(sizes),
            "max_file_bytes": self.max_file_bytes,
            "max_total_bytes": self.max_total_bytes,
            **self.capacity(),
            "deletion_enabled": False,
        }

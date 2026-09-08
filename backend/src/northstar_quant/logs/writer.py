"""One disk writer per process, isolated from application logging calls."""

from __future__ import annotations

import errno
import fcntl
import json
import os
import re
from pathlib import Path
from queue import Empty, Queue
from threading import Event, Lock, Thread
from time import monotonic, time
from typing import Any

_SECRET = re.compile(r"(?i)(password|passwd|token|secret|authorization)([\s\"'=:\\]+)([^\s,;\"}]+)")
_USERINFO = re.compile(r"(://)[^\s/@]+:[^\s/@]+@")


class FileSink:
    """Startup validates ownership. Only the writer thread rotates or writes afterward."""

    def __init__(self, path: Path, max_bytes: int, backups: int) -> None:
        self.path, self.max_bytes, self.backups = path, max_bytes, backups
        self.fd: int | None = None
        self.size = 0
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.lock_fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._open()
        except BaseException:
            os.close(self.lock_fd)
            raise

    def _open(self) -> None:
        descriptor = os.open(
            self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            size = os.fstat(descriptor).st_size
        except BaseException:
            os.close(descriptor)
            raise
        self.fd, self.size = descriptor, size

    def write(self, content: bytes) -> None:
        if self.fd is None:
            self._open()
        if self.size and self.size + len(content) > self.max_bytes:
            assert self.fd is not None
            os.close(self.fd)
            self.fd = None
            for index in range(self.backups, 0, -1):
                old = Path(str(self.path) + (f".{index - 1}" if index > 1 else ""))
                if old.exists():
                    os.replace(old, Path(str(self.path) + f".{index}"))
            self._open()
        assert self.fd is not None
        remaining = memoryview(content)
        while remaining:
            written = os.write(self.fd, remaining)
            if not written:
                raise OSError(errno.EIO, "log write made no progress")
            self.size += written
            remaining = remaining[written:]

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        os.close(self.lock_fd)


def encode(event: dict[str, Any]) -> bytes:
    event = dict(event)
    message = event.pop("template")
    arguments = event.pop("arguments")
    try:
        # Bounded arguments alone don't bound printf field widths/precision.
        for match in re.finditer(r"%(?:\([^)]+\))?[-+# 0]*(\d+|\*)?(?:\.(\d+|\*))?", message):
            if any(value and (value == "*" or int(value) > 512) for value in match.groups()):
                raise ValueError("unbounded log format")
        if arguments:
            message = message % arguments
    except (ValueError, TypeError, OverflowError):
        message += " [invalid log format]"
    message = _USERINFO.sub(r"\1[redacted]@", message[:4096])
    event["message"] = _SECRET.sub(r"\1\2[redacted]", message)
    return (json.dumps(event, ensure_ascii=True, allow_nan=False) + "\n").encode()


class Writer:
    def __init__(self, sink: FileSink, capacity: int, batch_size: int = 64) -> None:
        self.sink = sink
        self.batch_size = batch_size
        self.queue: Queue[dict[str, Any]] = Queue(maxsize=capacity)
        self.lock = Lock()
        self.dropped = self.written = self.errors = 0
        self.last_error: str | None = None
        self.progress = monotonic()
        self.stop = Event()
        self.thread = Thread(target=self._run, name="northstar-log-writer", daemon=True)
        try:
            self.thread.start()
        except BaseException:
            sink.close()
            raise

    def status(self) -> dict[str, object]:
        with self.lock:
            return {
                "status": "DEGRADED"
                if (
                    self.dropped
                    or self.last_error
                    or not self.thread.is_alive()
                    or monotonic() - self.progress > 5
                )
                else "OK",
                "queue_depth": self.queue.qsize(),
                "queue_capacity": self.queue.maxsize,
                "dropped_records": self.dropped,
                "written_records": self.written,
                "write_errors": self.errors,
                "last_error": self.last_error,
                "writer_alive": self.thread.is_alive(),
                "writer_stalled": monotonic() - self.progress > 5,
            }

    def drop(self, count: int = 1) -> None:
        with self.lock:
            self.dropped += count

    def _run(self) -> None:
        retry_at = 0.0
        reported = (-1, -1)
        report_at = 0.0
        try:
            while not self.stop.is_set() or not self.queue.empty():
                batch: list[dict[str, Any]] = []
                for _ in range(self.batch_size):
                    try:
                        batch.append(self.queue.get_nowait())
                    except Empty:
                        break
                now = monotonic()
                with self.lock:
                    self.progress = now
                    counts = (self.dropped, self.errors)
                health = counts != reported and now >= report_at
                if now < retry_at:
                    self.drop(len(batch))
                elif batch or health:
                    delivered = 0
                    try:
                        for event in batch:
                            self.sink.write(encode(event))
                            delivered += 1
                            with self.lock:
                                self.written += 1
                        if health:
                            self.sink.write(
                                encode(
                                    {
                                        "timestamp": time(),
                                        "application": self.sink.path.parent.name,
                                        "component": self.sink.path.stem,
                                        "pid": os.getpid(),
                                        "level": "WARNING" if any(counts) else "INFO",
                                        "logger": "northstar_quant.logs",
                                        "template": "logging_health",
                                        "arguments": (),
                                        "dropped_records": counts[0],
                                        "write_errors": counts[1],
                                    }
                                )
                            )
                            reported = counts
                            report_at = now + 1
                        with self.lock:
                            self.last_error = None
                    except (OSError, ValueError, TypeError) as error:
                        with self.lock:
                            self.errors += 1
                            self.last_error = (
                                errno.errorcode.get(error.errno or errno.EIO, "IO_ERROR")
                                if isinstance(error, OSError)
                                else "ENCODING_ERROR"
                            )
                        self.drop(len(batch) - delivered)
                        retry_at = now + 1
                # No producer notification/formatting/disk lock is needed. Idle and
                # failed disks have bounded retry frequency instead of busy loops.
                self.stop.wait(0.05)
        finally:
            self.sink.close()

    def close(self, timeout: float = 1.0) -> bool:
        self.stop.set()
        self.thread.join(timeout=timeout)
        return not self.thread.is_alive()

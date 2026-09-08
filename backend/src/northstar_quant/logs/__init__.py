"""Bounded asynchronous operational logs. Durable trading facts are not log records."""

from __future__ import annotations

import atexit
import logging
import math
import os
from itertools import islice
from pathlib import Path
from queue import Full
from typing import Any
from uuid import uuid4

from .writer import FileSink, Writer


def _value(value: object) -> object:
    # Never run caller-provided repr/str, serialize payloads, or keep mutable
    # objects/traceback frames alive in the queue.
    if type(value) is str:
        return value[:256]
    if value is None or type(value) is bool:
        return value
    if type(value) is int and value.bit_length() <= 128:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    return "<omitted>"


class AsyncHandler(logging.Handler):
    def __init__(self, writer: Writer, application: str, component: str) -> None:
        super().__init__()
        self.writer = writer
        self.setLevel(logging.INFO)
        self.application, self.component = application, component
        self.session = uuid4().hex
        self.accepting = True

    def emit(self, record: logging.LogRecord) -> None:
        if not self.accepting:
            return
        arguments: object = ()
        if type(record.args) is tuple:
            arguments = tuple(_value(arg) for arg in record.args[:8])
        elif type(record.args) is dict:
            arguments = {
                key[:64]: _value(value)
                for key, value in islice(record.args.items(), 8)
                if type(key) is str
            }
        event: dict[str, Any] = {
            "timestamp": record.created,
            "application": self.application,
            "component": self.component,
            "session": self.session,
            "pid": record.process,
            "level": record.levelname,
            "logger": record.name[:128],
            "template": record.msg[:2048] if type(record.msg) is str else "<omitted>",
            "arguments": arguments,
        }
        if record.exc_info:
            exception_type, _, trace = record.exc_info
            frames = []
            for _ in range(8):
                if trace is None:
                    break
                code = trace.tb_frame.f_code
                frames.append((code.co_filename[-128:], code.co_name[:64], trace.tb_lineno))
                trace = trace.tb_next
            event["exception"] = {
                "type": exception_type.__name__ if exception_type else "Unknown",
                "frames": frames,
            }
        try:
            self.writer.queue.put_nowait(event)
        except Full:
            self.writer.drop()

    def close(self) -> None:
        self.accepting = False
        super().close()


class LogRuntime:
    def __init__(
        self,
        application: str,
        component: str,
        directory: Path,
        capacity: int,
        max_bytes: int,
        backups: int,
    ) -> None:
        self.application, self.component = application, component
        self.path = directory / application / f"{component}.log"
        # Operational logs are deliberately low throughput in the kernel: at most
        # eight records per 50 ms batch while running, reducing GIL/disk bursts.
        self.writer = Writer(
            FileSink(self.path, max_bytes, backups),
            capacity,
            batch_size=8 if (application, component) == ("live", "kernel") else 64,
        )
        self.handler = AsyncHandler(self.writer, application, component)

    def close(self) -> None:
        self.handler.close()
        self.writer.close()

    def status(self) -> dict[str, object]:
        return {
            "application": self.application,
            "component": self.component,
            **self.writer.status(),
        }


_current: LogRuntime | None = None


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def configure(application: str, component: str) -> LogRuntime:
    """Configure once in each installed application's process, before business startup."""
    global _current
    if (application, component) not in {
        ("data_hub", "api"),
        ("data_hub", "worker"),
        ("research", "api"),
        ("live", "api"),
        ("live", "kernel"),
    }:
        raise ValueError("unknown application log owner")
    if _current is not None:
        if (_current.application, _current.component) != (application, component):
            raise RuntimeError("independent applications require independent logging processes")
        return _current
    directory = Path(os.environ.get("NORTHSTAR_LOG_DIR", ".northstar/logs")).expanduser().resolve()
    _current = LogRuntime(
        application,
        component,
        directory,
        _integer("NORTHSTAR_LOG_QUEUE", 1024, 128, 8192),
        _integer("NORTHSTAR_LOG_MAX_BYTES", 10 * 1024 * 1024, 1024 * 1024, 100 * 1024 * 1024),
        _integer("NORTHSTAR_LOG_BACKUPS", 5, 1, 20),
    )
    # Replace server console/access handlers: no synchronous fallback or per-request
    # access stream in Live. Third-party diagnostics default to WARNING.
    root = logging.getLogger()
    root.handlers = [_current.handler]
    root.setLevel(logging.WARNING)
    logging.getLogger("northstar_quant").setLevel(logging.INFO)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
        logger.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").disabled = True
    atexit.register(_current.close)
    logging.getLogger(__name__).info("application logging started")
    return _current


def status() -> dict[str, object]:
    return {"status": "NOT_CONFIGURED"} if _current is None else _current.status()

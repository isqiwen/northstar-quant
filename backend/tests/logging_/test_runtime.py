"""Operational logs never synchronously format objects or wait for disk recovery."""

import errno
import json
import logging
import sys
from datetime import date
from pathlib import Path
from threading import Event, Thread
from time import monotonic, perf_counter_ns, sleep

import pytest

from northstar_quant.logging_ import LogRuntime, writer
from northstar_quant.logging_.writer import FileSink


def record(message="event %s", arguments=("bounded",), exc_info=None):
    return logging.LogRecord(
        "northstar_quant.test", logging.INFO, __file__, 1, message, arguments, exc_info
    )


def wait_for(predicate):
    deadline = monotonic() + 3
    while not predicate():
        assert monotonic() < deadline
        sleep(0.01)


def test_midnight_rotation_restart_and_retention_keep_owners_isolated(tmp_path, monkeypatch):
    class Clock(date):
        current = date(2026, 9, 9)

        @classmethod
        def today(cls):
            return cls.current

    monkeypatch.setattr(writer, "date", Clock)
    other = FileSink(tmp_path, "live", "api", 32, 2)
    other.write(b"API evidence\n")
    other.close()
    sink = FileSink(tmp_path, "live", "kernel", 32, 2)
    try:
        sink.write(b"before midnight\n")
        first = sink.path
        assert first.name == "northstar-live-kernel-2026-09-09.log"
        Clock.current = date(2026, 9, 10)
        sink.write(b"after midnight\n")
        assert first.read_bytes() == b"before midnight\n"
        assert sink.path.name == "northstar-live-kernel-2026-09-10.log"
        # A new date must not permit a second process to own this program's logs.
        with pytest.raises(BlockingIOError):
            FileSink(tmp_path, "live", "kernel", 32, 2)
        # Clock correction appends to that date rather than truncating its history.
        Clock.current = date(2026, 9, 9)
        sink.write(b"clock corrected\n")
        assert first.read_bytes() == b"before midnight\nclock corrected\n"
        for day in range(11, 16):
            Clock.current = date(2026, 9, day)
            for _ in range(5):
                sink.write(b"bounded archive\n")
            assert len(list(sink.directory.glob("northstar-live-kernel-*.log*"))) <= 3
    finally:
        sink.close()
    current = sink.path.read_bytes()
    restarted = FileSink(tmp_path, "live", "kernel", 32, 2)
    try:
        assert restarted.path.read_bytes() == current
        assert other.path.read_bytes() == b"API evidence\n"
    finally:
        restarted.close()


def test_separate_owners_rotate_and_keep_safe_bounded_records(tmp_path: Path) -> None:
    class Payload:
        def __str__(self):
            raise AssertionError("logging must not format caller-owned objects")

        __repr__ = __str__

    owners = [
        LogRuntime(app, role, tmp_path, 128, 1200, 2)
        for app, role in (
            ("data_hub", "api"),
            ("data_hub", "worker"),
            ("research", "api"),
            ("live", "api"),
            ("live", "kernel"),
        )
    ]
    try:
        for runtime in owners:
            for _ in range(20):
                runtime.handler.handle(record("password=%s payload=%s", ("private", Payload())))
            runtime.handler.handle(record("%999999999s", ("small",)))
            try:
                raise ValueError("exception secret must not be retained")
            except ValueError:
                runtime.handler.handle(record("operation failed", (), sys.exc_info()))
    finally:
        for runtime in owners:
            runtime.close()
    for runtime in owners:
        files = list(runtime.path.parent.glob(runtime.path.name + "*"))
        data = [f for f in files if not f.name.endswith(".lock")]
        assert len(data) <= 3
        text = "".join(f.read_text() for f in data)
        assert "private" not in text and "exception secret" not in text
        events = [json.loads(line) for line in text.splitlines()]
        assert all(e["application"] == runtime.application for e in events)
        assert all(e["component"] == runtime.component for e in events)
        assert any(e.get("exception", {}).get("type") == "ValueError" for e in events)
        assert all(len(line) < 1200 for line in text.splitlines())
        assert not runtime.writer.thread.is_alive()
        # A new incarnation appends/rotates the same bounded role files.
        restarted = LogRuntime(runtime.application, runtime.component, tmp_path, 128, 1200, 2)
        restarted.close()


def test_stalled_sink_and_full_queue_do_not_wait_on_producer_or_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release, completed = Event(), Event(), Event()
    original = FileSink.write

    def stall(self, content):
        entered.set()
        assert release.wait(10)
        original(self, content)

    monkeypatch.setattr(FileSink, "write", stall)
    runtime = LogRuntime("live", "kernel", tmp_path, 128, 4096, 2)
    timings = []
    logger = logging.Logger("northstar_quant.latency", logging.INFO)
    logger.addHandler(runtime.handler)
    try:
        assert entered.wait(2)

        def produce():
            for _ in range(5000):
                start = perf_counter_ns()
                logger.info("event %s", "bounded")
                timings.append(perf_counter_ns() - start)
            completed.set()

        producer = Thread(target=produce)
        producer.start()
        # Writer remains stalled for the whole producer run, so completion cannot
        # be accidentally explained by a fast disk or a concurrently released gate.
        assert completed.wait(2)
        producer.join()
        assert not release.is_set()
        health = runtime.status()
        assert health["queue_depth"] == 128
        assert health["dropped_records"] == 5000 - 128
        assert health["status"] == "DEGRADED"
        assert runtime.writer.close(timeout=0.01) is False
        timings.sort()
        print({"stalled_sink_log_call_p99_us": timings[int(len(timings) * 0.99)] / 1000})
    finally:
        release.set()
        runtime.close()


def test_disk_failure_counts_loss_and_recovers_without_producer_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed = Event()
    original = FileSink.write

    def disk_full(self, content):
        if not failed.is_set():
            raise OSError(errno.ENOSPC, "private path")
        original(self, content)

    monkeypatch.setattr(FileSink, "write", disk_full)
    runtime = LogRuntime("live", "kernel", tmp_path, 128, 4096, 2)
    try:
        runtime.handler.handle(record())
        wait_for(lambda: runtime.status()["write_errors"] > 0)
        assert runtime.status()["last_error"] == "ENOSPC"
        wait_for(lambda: runtime.status()["dropped_records"] >= 1)
        failed.set()
        wait_for(lambda: runtime.status()["last_error"] is None)
        runtime.handler.handle(record("recovered", ()))
        wait_for(lambda: runtime.status()["written_records"] >= 1)
    finally:
        runtime.close()
    assert "recovered" in runtime.path.read_text()
    assert "private path" not in runtime.path.read_text()


def test_same_role_rejects_second_writer_instead_of_corrupting_rotation(tmp_path: Path) -> None:
    runtime = LogRuntime("live", "kernel", tmp_path, 128, 4096, 2)
    try:
        with pytest.raises(BlockingIOError):
            LogRuntime("live", "kernel", tmp_path, 128, 4096, 2)
        runtime.handler.handle(record())
    finally:
        runtime.close()
    assert '"message": "event bounded"' in runtime.path.read_text()


def test_normal_standard_logging_call_cost_and_confirmed_file_output(tmp_path: Path) -> None:
    runtime = LogRuntime("live", "kernel", tmp_path, 2048, 1024 * 1024, 2)
    logger = logging.Logger("northstar_quant.latency", logging.INFO)
    logger.addHandler(runtime.handler)
    elapsed = []
    try:
        for index in range(1000):
            start = perf_counter_ns()
            logger.info("command accepted id=%d", index)
            elapsed.append(perf_counter_ns() - start)
    finally:
        runtime.close()
    assert runtime.status()["dropped_records"] == 0
    assert runtime.status()["written_records"] == 1000
    records = [json.loads(line) for line in runtime.path.read_text().splitlines()]
    assert sum(r.get("message", "").startswith("command accepted") for r in records) == 1000
    elapsed.sort()
    print({"normal_log_call_p99_us": elapsed[990] / 1000})


def test_factory_failure_is_logged_without_exception_parameters(tmp_path: Path) -> None:
    import os
    import subprocess

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "\n".join(
                [
                    "from northstar_quant.apps.logging import logged_application",
                    "@logged_application('live', 'kernel')",
                    "def application():",
                    "    raise ValueError('private configuration value')",
                    "try:",
                    "    application()",
                    "except ValueError:",
                    "    pass",
                ]
            ),
        ],
        env=dict(os.environ, NORTHSTAR_LOG_DIR=str(tmp_path)),
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert not completed.stderr
    content = "".join(
        p.read_text() for p in (tmp_path / "live").glob("northstar-live-kernel-*.log")
    )
    assert "application initialization failed" in content
    assert '"type": "ValueError"' in content
    assert "private configuration value" not in content

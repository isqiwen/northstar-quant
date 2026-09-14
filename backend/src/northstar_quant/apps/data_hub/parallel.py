"""Spawned sync pipelines with measured host admission and bounded shutdown."""

import logging
import multiprocessing as mp
import os
import signal
from collections.abc import Callable
from multiprocessing.process import BaseProcess
from multiprocessing.synchronize import Event
from pathlib import Path
from queue import Empty, Full
from threading import Event as ThreadEvent
from threading import Thread
from time import monotonic
from typing import Any

import psutil  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]
from sqlalchemy import Engine, text

from northstar_quant.apps.storage import open_database, require_current_database
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.data_management.tushare import acquisition, process_next
from northstar_quant.logging_ import AsyncHandler, LogRuntime


class _LogForwarder:
    def __init__(self, queue: Any) -> None:
        self.queue = queue

    def drop(self) -> None:
        pass  # Bounded operational logs must never block durable data processing.


def _pipeline(stop: Event, initializer: Callable[[], None] | None, log_queue: Any) -> None:
    # Sanitize before crossing the process boundary; only the parent writes/rotates.
    log_queue.cancel_join_thread()
    handler = AsyncHandler(_LogForwarder(log_queue), "data_hub", "worker")
    logging.getLogger().handlers = [handler]
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger("northstar_quant").setLevel(logging.INFO)
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    # CPU parallelism is across independent processes, without nested Arrow pools.
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    if initializer is not None:
        initializer()
    engine = open_database()
    client = acquisition.open_client()
    try:
        require_current_database(engine)
        library = DataLibrary(engine, SourceFiles.from_environment())
        idle = 0.1
        while not stop.is_set():
            parent = mp.parent_process()
            if parent is not None and not parent.is_alive():
                break
            result = process_next(library, client=client, plan=False)
            if result is None:
                stop.wait(idle)
                idle = min(1.0, idle * 2)
            else:
                idle = 0.1
                logging.getLogger(__name__).info(
                    "Data sync %s: %s", result["request_id"], result["status"]
                )
    except Exception:
        logging.getLogger(__name__).exception("Sync pipeline failed; persisted job will recover")
        raise
    finally:
        client.close()
        engine.dispose()
        handler.close()


class Pipelines:
    """Parent owns process lifecycle only; PostgreSQL owns jobs and rate admission."""

    def __init__(self, logs: LogRuntime, initializer: Callable[[], None] | None = None) -> None:
        self.context = mp.get_context("spawn")
        self.log_queue = self.context.Queue(maxsize=1024)
        self.log_stop = ThreadEvent()

        def relay() -> None:
            while not self.log_stop.is_set():
                try:
                    event = self.log_queue.get(timeout=0.1)
                except Empty:
                    continue
                try:
                    logs.writer.queue.put_nowait(event)
                except Full:
                    logs.writer.drop()

        self.log_thread = Thread(target=relay, name="sync-log-relay", daemon=True)
        self.log_thread.start()
        self.children: list[tuple[BaseProcess, Event]] = []
        self.initializer = initializer
        self.peak = psutil.Process().memory_info().rss + 128 * 1024**2
        self.next_sample = 0.0

    def maintain(self, engine: Engine) -> None:
        if monotonic() < self.next_sample:
            return
        self.next_sample = monotonic() + 2
        remaining = []
        resident = 0
        for process, stop in self.children:
            if not process.is_alive():
                process.join()
                logging.getLogger(__name__).info(
                    "Sync process %s exited: %s", process.pid, process.exitcode
                )
                process.close()
                continue
            try:
                rss = psutil.Process(process.pid).memory_info().rss
                resident += rss
                self.peak = max(self.peak, rss)
            except psutil.NoSuchProcess:
                pass
            remaining.append((process, stop))
        self.children = remaining
        cpus = (
            len(os.sched_getaffinity(0))
            if hasattr(os, "sched_getaffinity")
            else os.cpu_count() or 1
        )
        available = int(psutil.virtual_memory().available)
        try:
            maximum = Path("/sys/fs/cgroup/memory.max").read_text().strip()
            if maximum != "max":
                available = min(
                    available,
                    max(0, int(maximum) - int(Path("/sys/fs/cgroup/memory.current").read_text())),
                )
        except (OSError, ValueError):
            pass
        try:
            quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()
            if quota != "max":
                cpus = min(cpus, max(1, int(quota) // int(period)))
        except (OSError, ValueError):
            pass
        with engine.connect() as connection:
            free = int(
                connection.scalar(
                    text("""SELECT current_setting('max_connections')::int
                - current_setting('superuser_reserved_connections')::int
                - (SELECT count(*) FROM pg_stat_activity)""")
                )
            )
        # A pipeline holds a library gate, an ownership session and at most two
        # short DB operations. Leave DB slots and one measured process footprint
        # available for management; do not assign an arbitrary CPU percentage.
        slots = max(0, (free - 8 + len(remaining) * 4) // 4)
        memory = max(0, (available + resident - self.peak) // self.peak)
        target = min(cpus, slots, memory)
        active = [(p, s) for p, s in remaining if not s.is_set()]
        for _, stop in active[target:]:
            stop.set()
        # Ramp up one process per measurement; observe actual RSS before adding more.
        if len(remaining) < target:
            stop = self.context.Event()
            process = self.context.Process(
                target=_pipeline, args=(stop, self.initializer, self.log_queue), name="tushare-sync"
            )
            process.start()
            self.children.append((process, stop))
            logging.getLogger(__name__).info(
                "Sync processes=%s target=%s cpus=%s", len(self.children), target, cpus
            )

    def close(self) -> None:
        for _, stop in self.children:
            stop.set()
        deadline = monotonic() + 8
        for process, _ in self.children:
            process.join(max(0, deadline - monotonic()))
        # Forced exit leaves RUNNING/raw evidence for connection-lock recovery.
        for process, _ in self.children:
            if process.is_alive():
                process.kill()
        for process, _ in self.children:
            process.join()
            process.close()
        self.children.clear()
        self.log_stop.set()
        self.log_thread.join(timeout=1)
        self.log_queue.close()

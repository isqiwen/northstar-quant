"""Low-frequency retention child; slow cleanup never owns collector supervision."""

import logging
import multiprocessing as mp
import signal
from multiprocessing.process import BaseProcess
from multiprocessing.synchronize import Event
from time import monotonic

from northstar_quant.apps.storage import open_database, require_current_database
from northstar_quant.data_management.contract_data.retention import release_rejected
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.logging_ import configure


def _run(stop: Event) -> None:
    logs = configure("data_hub", "retention")
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    engine = open_database()
    try:
        require_current_database(engine)
        files = SourceFiles.from_environment()
        while not stop.is_set():
            parent = mp.parent_process()
            if parent is not None and not parent.is_alive():
                break
            started = monotonic()
            try:
                removed = release_rejected(engine, files)
                logging.getLogger(__name__).info(
                    "Retention removed=%s elapsed=%.3f", removed, monotonic() - started
                )
            except Exception:
                # Rollback releases the source gate; retry only this maintenance task.
                logging.getLogger(__name__).exception("Retention deferred; collectors continue")
            stop.wait(30)
    finally:
        engine.dispose()
        logs.close()


class RetentionWorker:
    def __init__(self) -> None:
        self.context = mp.get_context("spawn")
        self.stop = self.context.Event()
        self.process: BaseProcess | None = None
        self.next_start = 0.0

    def maintain(self) -> None:
        if self.process is not None:
            if self.process.is_alive():
                return
            self.process.join()
            self.process.close()
            self.process = None
        if monotonic() < self.next_start:
            return
        self.next_start = monotonic() + 30
        self.process = self.context.Process(target=_run, args=(self.stop,), name="data-retention")
        self.process.start()

    def close(self) -> None:
        self.stop.set()
        if self.process is not None:
            self.process.join(timeout=5)
            if self.process.is_alive():
                self.process.kill()
                self.process.join()
            self.process.close()
            self.process = None

"""Independently supervised bounded Data processor; no HTTP or browser lifecycle."""

import logging
import signal
from threading import Event

from northstar_quant.apps.storage import open_database, require_current_database
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.data_management.processing import process_attempt
from northstar_quant.logging_ import configure


def run() -> None:
    runtime_logs = configure("data_hub", "worker")
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    engine = None
    try:
        engine = open_database()
        require_current_database(engine)
        library = DataLibrary(engine, SourceFiles.from_environment())
        # One bounded operation holds the existing publication lock. Pending
        # receipts remain independent; no in-memory queue or expiry-based takeover.
        while not stop.is_set():
            result = process_attempt(library)
            if result is None:
                stop.wait(1)
            else:
                logging.getLogger(__name__).info(
                    "Data attempt %s: %s", result["attempt_id"], result["status"]
                )
    except Exception:
        logging.getLogger(__name__).exception("Data worker failed")
        raise
    finally:
        if engine is not None:
            engine.dispose()
        logging.getLogger(__name__).info("Data worker stopped")
        runtime_logs.close()

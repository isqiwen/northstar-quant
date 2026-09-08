"""Independently supervised bounded Data processor; no HTTP or browser lifecycle."""

import logging
import signal
from threading import Event

from northstar_quant.apps.storage import open_database, require_current_database
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.data_management.processing import process_attempt


def run() -> None:
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    engine = open_database()
    try:
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
    finally:
        engine.dispose()

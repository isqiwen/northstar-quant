"""Independently supervised bounded Data processor; no HTTP or browser lifecycle."""

import logging
import signal
from collections.abc import Callable
from threading import Event

from northstar_quant.apps.storage import open_database, require_current_database
from northstar_quant.data_management.compaction import process_next as compact_next
from northstar_quant.data_management.contract_data.processing import process_next as contract_next
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.data_management.processing import process_attempt
from northstar_quant.data_management.tushare.claiming import prepare
from northstar_quant.logging_ import configure

from .parallel import Pipelines
from .retention_worker import RetentionWorker


def run(*, initializer: Callable[[], None] | None = None) -> None:
    runtime_logs = configure("data_hub", "worker")
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    engine = None
    pipelines = Pipelines(runtime_logs, initializer)
    retention = RetentionWorker()
    try:
        engine = open_database()
        require_current_database(engine)
        library = DataLibrary(engine, SourceFiles.from_environment())
        while not stop.is_set():
            pipelines.maintain(engine)
            retention.maintain()
            prepare(engine)
            contract_next(engine)
            result = process_attempt(library)
            compacted = compact_next(engine, library._files)
            if compacted is not None:
                logging.getLogger(__name__).info(
                    "Data compaction %s: %s", compacted["compaction_id"], compacted["status"]
                )
            if result is None and compacted is None:
                stop.wait(1)
            elif result is not None:
                logging.getLogger(__name__).info(
                    "Data attempt %s: %s", result["attempt_id"], result["status"]
                )
    except Exception:
        logging.getLogger(__name__).exception("Data worker failed")
        raise
    finally:
        retention.close()
        pipelines.close()
        if engine is not None:
            engine.dispose()
        logging.getLogger(__name__).info("Data worker stopped")
        runtime_logs.close()

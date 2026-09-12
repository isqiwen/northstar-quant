"""One claimed factor job in a supervised process, independent of the Web API."""

from __future__ import annotations

import signal
from threading import Event
from uuid import UUID

from northstar_quant.data_management.publications import PublishedDatasets

from .factor_catalog import FactorCatalog
from .storage import open_store, require_current


def child(identity: UUID) -> None:
    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    engine = open_store()
    try:
        require_current(engine)
        FactorCatalog(engine, PublishedDatasets.from_environment()).execute(identity, stop=stop)
    finally:
        engine.dispose()

"""Fixed factor jobs survive Web lifetimes; cancellation never publishes a result."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from northstar_quant.research.factor_catalog import FactorCatalog
from northstar_quant.research.storage import initialize, open_store
from tests.test_paper import _study


@pytest.mark.parametrize("local", [False, True])
def test_factor_queue_identity_cancel_and_recovery(
    postgres_engine, clean_database, tmp_path, monkeypatch, local
):
    library, dataset, config = _study(postgres_engine, tmp_path)
    library = library.publications
    engine = open_store(tmp_path / "factor.sqlite3") if local else postgres_engine
    if local:
        initialize(engine)
    catalog = FactorCatalog(engine, library)
    revision = catalog.register(dict(config.strategy.factors)["momentum"])
    identity = uuid4()
    load = library.load_dataset

    def unavailable(*_):
        raise AssertionError("canceled work must not load market files")

    queued = catalog.submit(revision, dataset.snapshot_id, identity)
    assert queued["status"] == "QUEUED" and queued["done"] == 0
    assert catalog.submit(revision, dataset.snapshot_id, identity) == queued
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(lambda _: catalog.claim(identity), range(2)))
    assert sorted(claimed) == [False, True]
    assert catalog.cancel(identity)["status"] == "CANCEL_REQUESTED"
    monkeypatch.setattr(library, "load_dataset", unavailable)
    canceled = catalog.execute(identity)
    assert canceled["status"] == "CANCELED" and canceled["result"] is None
    monkeypatch.setattr(library, "load_dataset", load)
    assert catalog.submit(revision, dataset.snapshot_id, identity) == canceled
    with pytest.raises(DBAPIError, match="immutable"):
        with engine.begin() as connection:
            connection.execute(text("UPDATE factor_runs SET status='QUEUED'"))
    monkeypatch.setattr(library, "load_dataset", load)
    next_id = uuid4()
    catalog.submit(revision, dataset.snapshot_id, next_id)
    assert catalog.claim(next_id)
    stop = Event()
    stop.set()
    assert catalog.execute(next_id, stop=stop)["status"] == "INTERRUPTED"
    assert catalog.queued() is None
    dead = uuid4()
    catalog.submit(revision, dataset.snapshot_id, dead)
    assert catalog.claim(dead)
    reopened = FactorCatalog(engine, library)
    reopened.interrupt()  # supervisor holds exclusive process-lifetime ownership
    assert reopened.get(dead)["status"] == "INTERRUPTED"
    good = uuid4()
    reopened.submit(revision, dataset.snapshot_id, good)
    assert reopened.claim(good)
    result = reopened.execute(good)
    assert result["status"] == "SUCCEEDED"
    assert result["done"] == result["total"] == len(dataset.bars)
    assert FactorCatalog(engine, library).get(good) == result
    if local:
        engine.dispose()

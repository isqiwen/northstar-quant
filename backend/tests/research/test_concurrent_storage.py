"""Readers must not block cancellation; competing workers must not double-claim."""

import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

from sqlalchemy import text

from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.research.storage import initialize, open_store
from northstar_quant.research.tasks.store import TaskStore


def queued(engine):
    study = tomllib.loads((Path(__file__).parents[1] / "data/intraday.toml").read_text())
    identity = uuid4()
    TaskStore(engine).submit(
        identity, uuid4(), "a" * 64, ResearchConfig.from_mapping(study["research"]), 12, {}
    )
    return str(identity)


def test_read_snapshot_does_not_block_cancellation(tmp_path, monkeypatch):
    monkeypatch.delenv("NORTHSTAR_RESEARCH_DIR", raising=False)
    engine = open_store(tmp_path / "research.sqlite3")
    initialize(engine)
    identity = queued(engine)
    other = open_store(tmp_path / "research.sqlite3")
    try:
        with engine.connect() as reader, ThreadPoolExecutor(max_workers=1) as pool:
            query = text("SELECT status FROM research_jobs WHERE task_id=:id")
            assert reader.execute(query, {"id": identity}).scalar_one() == "QUEUED"
            result = pool.submit(TaskStore(other).control, identity, "cancel").result(timeout=2)
            assert result["status"] == "CANCELLED"
            # The reader sees its original snapshot, while a new reader sees the commit.
            assert reader.execute(query, {"id": identity}).scalar_one() == "QUEUED"
        assert TaskStore(engine).get(identity)["status"] == "CANCELLED"
    finally:
        engine.dispose()
        other.dispose()


def test_competing_workers_claim_one_attempt(tmp_path, monkeypatch):
    monkeypatch.delenv("NORTHSTAR_RESEARCH_DIR", raising=False)
    engine = open_store(tmp_path / "research.sqlite3")
    initialize(engine)
    identity = queued(engine)
    engines = [open_store(tmp_path / "research.sqlite3") for _ in range(2)]
    barrier = Barrier(2)

    def claim(store):
        barrier.wait(timeout=3)
        return store.claim()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, [TaskStore(e) for e in engines]))
        assert sum(result is not None for result in results) == 1
        saved = TaskStore(engine).get(identity)
        assert saved["status"] == "RUNNING"
        assert len(saved["attempts"]) == 1
    finally:
        for e in [engine, *engines]:
            e.dispose()

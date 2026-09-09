"""Execute one claimed job using the existing calculation and immutable result store."""

from __future__ import annotations

import signal
from threading import Event
from time import monotonic
from uuid import UUID

from northstar_quant import code_revision
from northstar_quant.data_management.publications import DatasetReader
from northstar_quant.research.backtesting import run_research
from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.research.runs import RunStore

from .resources import sample
from .store import TaskStore


def execute(
    store: TaskStore, library: DatasetReader, identity: str, *, stop: Event | None = None
) -> None:
    task = store.get(identity)
    attempt = task["attempt_id"]
    last = 0.0

    def progress(done: int, total: int) -> None:
        nonlocal last
        if stop is not None and stop.is_set():
            raise InterruptedError("研究进程停止")
        now = monotonic()
        if done == total or now - last >= 0.2:
            store.progress(identity, attempt, done)
            store.measure(attempt, sample())
            last = now

    try:
        if task["code_revision"] != code_revision():
            raise ValueError("实现版本不匹配，请创建新任务")
        dataset = library.load_dataset(UUID(task["snapshot_id"]))
        if (
            dataset.content_hash != task["snapshot_hash"]
            or len(dataset.bars) != task["total"]
            or dataset.details is None
            or dataset.details.to_dict() != task["snapshot_evidence"]
        ):
            raise ValueError("固定快照身份或记录数量不匹配")
        config = ResearchConfig.from_mapping(task["config"])
        progress(0, len(dataset.bars))
        result = run_research(dataset, config, progress=progress)
        store.finalizing(identity, attempt)
        run_id = RunStore(store.engine).save(dataset, config, result)
        store.measure(attempt, sample())
        store.finish(identity, attempt, "SUCCEEDED", "结果已完整保存", run_id)
    except InterruptedError as error:
        store.finish(identity, attempt, "INTERRUPTED", str(error))
    except Exception as error:
        store.finish(identity, attempt, "FAILED", str(error))


def child(identity: str) -> None:
    from northstar_quant.data_management.publications import PublishedDatasets
    from northstar_quant.research.storage import open_store, require_current

    stop = Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    engine = open_store()
    try:
        require_current(engine)
        execute(TaskStore(engine), PublishedDatasets.from_environment(), identity, stop=stop)
    finally:
        engine.dispose()

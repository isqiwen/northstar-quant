"""Fixed inputs, cancellation and stale completion under durable execution."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from northstar_quant.research.runs import RunStore
from northstar_quant.research.storage import initialize, open_store
from northstar_quant.research.tasks.execution import execute
from northstar_quant.research.tasks.store import TaskStore
from tests.test_paper import _study


def test_fixed_task_retry_cancel_and_publication(postgres_engine, clean_database, tmp_path):
    library, dataset, config = _study(postgres_engine, tmp_path)
    engine = open_store(tmp_path / "research.sqlite3")
    initialize(engine)
    store = TaskStore(engine)
    identity = uuid4()
    task = store.submit(
        identity,
        dataset.snapshot_id,
        dataset.content_hash,
        config,
        len(dataset.bars),
        dataset.details.to_dict(),
    )
    assert task["status"] == "QUEUED"
    assert (
        store.submit(
            identity,
            dataset.snapshot_id,
            dataset.content_hash,
            config,
            len(dataset.bars),
            dataset.details.to_dict(),
        )
        == task
    )
    with pytest.raises(ValueError, match="different"):
        store.submit(
            identity,
            dataset.snapshot_id,
            "a" * 64,
            config,
            len(dataset.bars),
            dataset.details.to_dict(),
        )
    with engine.begin() as c, pytest.raises(DBAPIError, match="immutable"):
        c.execute(text("UPDATE research_jobs SET snapshot_hash='changed'"))
    first = store.claim()
    assert first["task_id"] == str(identity)
    store.progress(str(identity), first["attempt_id"], 1)
    store.recover()
    assert store.get(str(identity))["status"] == "INTERRUPTED"
    store.control(str(identity), "retry")
    second = store.claim()
    assert second["attempt_id"] != first["attempt_id"]
    with pytest.raises(InterruptedError):
        store.progress(str(identity), first["attempt_id"], 2)
    store.finish(str(identity), first["attempt_id"], "SUCCEEDED", run_id="f" * 64)
    assert store.get(str(identity))["status"] == "RUNNING"
    execute(store, library, str(identity))
    done = store.get(str(identity))
    assert done["status"] == "SUCCEEDED", done
    assert done["completed"] == len(dataset.bars), dataset.details.to_dict()
    assert len(done["attempts"]) == 2
    assert RunStore(engine).get(done["run_id"])["snapshot"]["content_hash"] == dataset.content_hash
    other = uuid4()
    store.submit(
        other,
        dataset.snapshot_id,
        dataset.content_hash,
        config,
        len(dataset.bars),
        dataset.details.to_dict(),
    )
    assert store.control(str(other), "cancel")["status"] == "CANCELLED"
    assert store.claim() is None
    running = uuid4()
    store.submit(
        running,
        dataset.snapshot_id,
        dataset.content_hash,
        config,
        len(dataset.bars),
        dataset.details.to_dict(),
    )
    store.claim()
    assert store.control(str(running), "cancel")["status"] == "CANCEL_REQUESTED"
    execute(store, library, str(running))
    assert store.get(str(running))["status"] == "CANCELLED"
    assert store.get(str(running))["run_id"] is None
    engine.dispose()


def test_corrupt_fixed_input_cannot_publish(postgres_engine, clean_database, tmp_path):
    library, dataset, config = _study(postgres_engine, tmp_path)
    engine = open_store(tmp_path / "research.sqlite3")
    initialize(engine)
    store = TaskStore(engine)
    identity = uuid4()
    store.submit(
        identity,
        dataset.snapshot_id,
        "a" * 64,
        config,
        len(dataset.bars),
        dataset.details.to_dict(),
    )
    store.claim()
    execute(store, library, str(identity))
    failed = store.get(str(identity))
    assert failed["status"] == "FAILED"
    assert "不匹配" in failed["reason"]
    assert RunStore(engine).list() == []
    engine.dispose()


def test_worker_death_keeps_child_fence_then_requires_explicit_retry(
    postgres_engine, clean_database, tmp_path, monkeypatch
):
    import os
    import subprocess
    import sys
    import time

    from northstar_quant.data_management.storage_identity import initialize as identify

    market = tmp_path / "market"
    market.mkdir()
    identity = str(uuid4())
    identify(market, identity)
    monkeypatch.setenv("NORTHSTAR_MARKET_DIR", str(market))
    monkeypatch.setenv("NORTHSTAR_MARKET_STORAGE_ID", identity)
    library, dataset, config = _study(postgres_engine, tmp_path)
    database = tmp_path / "worker.sqlite3"
    engine = open_store(database)
    initialize(engine)
    store = TaskStore(engine)
    task = store.submit(
        uuid4(),
        dataset.snapshot_id,
        dataset.content_hash,
        config,
        len(dataset.bars),
        dataset.details.to_dict(),
    )
    environment = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("NORTHSTAR_LIVE", "NORTHSTAR_SIMNOW"))
        and k != "NORTHSTAR_DATABASE_URL"
    }
    environment.update(
        NORTHSTAR_RESEARCH_DATABASE=str(database), NORTHSTAR_LOG_DIR=str(tmp_path / "logs")
    )
    child_file = tmp_path / "child.pid"
    # Fault injection only: freeze the real child before it executes, preserving inherited FDs.
    script = """
import subprocess,sys,os
from pathlib import Path
from northstar_quant.apps.research.worker import run
original=subprocess.Popen
def gated(args,**kwargs):
    child_code="import os,signal; os.kill(os.getpid(), signal.SIGSTOP)"
    child=original([sys.executable,'-c',child_code],**kwargs)
    Path(sys.argv[1]).write_text(str(child.pid))
    return child
subprocess.Popen=gated
run()
"""
    owner = subprocess.Popen(
        [sys.executable, "-c", script, str(child_file)],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    child_pid = None
    replacement = None
    try:
        deadline = time.monotonic() + 15
        while not child_file.exists() and time.monotonic() < deadline:
            assert owner.poll() is None
            time.sleep(0.05)
        assert child_file.exists()
        child_pid = int(child_file.read_text())
        owner.kill()
        owner.wait(timeout=5)
        rejected = subprocess.run(
            [sys.executable, "-c", "from northstar_quant.apps.research.worker import run; run()"],
            env=environment,
            capture_output=True,
            timeout=10,
        )
        assert rejected.returncode != 0
        assert "持有执行权" in rejected.stderr.decode()
        assert store.get(task["task_id"])["status"] == "RUNNING"
        os.kill(child_pid, 9)
        child_pid = None
        replacement = subprocess.Popen(
            [sys.executable, "-c", "from northstar_quant.apps.research.worker import run; run()"],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + 15
        while store.get(task["task_id"])["status"] == "RUNNING" and time.monotonic() < deadline:
            time.sleep(0.05)
        assert store.get(task["task_id"])["status"] == "INTERRUPTED"
        store.control(task["task_id"], "retry")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            completed = store.get(task["task_id"])
            if completed["status"] == "SUCCEEDED":
                break
            assert completed["status"] != "FAILED", completed
            time.sleep(0.05)
        assert completed["status"] == "SUCCEEDED", completed
        assert len(completed["attempts"]) == 2
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=5)
        if child_pid is not None:
            try:
                os.kill(child_pid, 9)
            except ProcessLookupError:
                pass
        if replacement is not None:
            replacement.terminate()
            replacement.wait(timeout=15)
        engine.dispose()

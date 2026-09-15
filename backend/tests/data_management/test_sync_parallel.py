"""Real spawned pipelines: overlapping work, fenced death recovery and global quotas."""

import json

# ruff: noqa: F811
import multiprocessing as mp
import os
import time
from pathlib import Path

from sqlalchemy import text

from northstar_quant.apps.data_hub.parallel import _pipeline
from northstar_quant.data_management.tushare import acquisition, jobs, planning
from northstar_quant.data_management.tushare.quality import normalize as normalize_response
from tests.data_management.test_tushare import automatic, pending, response  # noqa: F401, F811


def _download(api, parameters, token, **kwargs):
    root = Path(os.environ["PARALLEL_TEST_SIGNALS"])
    (root / str(os.getpid())).write_text(parameters["ts_code"])
    deadline = time.monotonic() + 20
    while not (root / "release").exists():
        if time.monotonic() >= deadline:
            raise AssertionError("parallel download did not release")
        time.sleep(0.02)
    content = json.loads(response())
    content["data"]["items"][0][0] = parameters["ts_code"]
    return json.dumps(content).encode()


def _normalize(content, selected):
    root = Path(os.environ["PARALLEL_TEST_SIGNALS"])
    (root / f"quality-{os.getpid()}").touch()
    deadline = time.monotonic() + 20
    while not (root / "quality-release").exists():
        assert time.monotonic() < deadline
        time.sleep(0.02)
    return normalize_response(content, selected)


def _initialize():
    acquisition.fetch = _download
    jobs.normalize = _normalize


def _second(library):
    with library._engine.begin() as connection:
        connection.execute(
            text("""INSERT INTO data_sync_contracts
            (ts_code,exchange,product,kind,details,planned_revision)
            VALUES('RB2611.SHF','SHFE','RB','1','{"list_date":"20250101","delist_date":"20260902","last_ddate":"20260903"}',1)""")
        )
        connection.execute(
            text("""INSERT INTO data_contract_collections(scope,start_date,end_date)
            VALUES('RB2611.SHF','2025-01-01','2026-09-02')""")
        )
        planning.enqueue(
            connection,
            "daily",
            "RB2611.SHF",
            {"ts_code": "RB2611.SHF", "start_date": "20260901", "end_date": "20260901"},
            "2026-09-01",
            "2026-09-01",
        )


def _await(predicate):
    deadline = time.monotonic() + 20
    while not predicate():
        assert time.monotonic() < deadline, "parallel pipeline timed out"
        time.sleep(0.03)


def test_spawned_downloads_overlap_and_dead_owner_recovers_without_stealing_live_job(
    automatic, monkeypatch, tmp_path
):
    pending(automatic)
    _second(automatic)
    signals = tmp_path / "signals"
    signals.mkdir()
    monkeypatch.setenv("PARALLEL_TEST_SIGNALS", str(signals))
    monkeypatch.setenv("NORTHSTAR_DATABASE_URL", os.environ["NORTHSTAR_TEST_DATABASE_URL"])
    monkeypatch.setenv("NORTHSTAR_DATABASE_OWNER", "data_hub")
    monkeypatch.setenv("NORTHSTAR_DATA_DIR", str(automatic._files.root))
    context = mp.get_context("spawn")
    queue = context.Queue(1024)
    children = []

    def start():
        stop = context.Event()
        process = context.Process(target=_pipeline, args=(stop, _initialize, queue))
        process.start()
        children.append((process, stop))
        return process

    def validated():
        with automatic._engine.connect() as connection:
            return (
                connection.scalar(
                    text("SELECT count(*) FROM data_sync_jobs WHERE status='VALIDATED'")
                )
                == 2
            )

    try:
        first = start()
        second = start()
        _await(lambda: (signals / str(first.pid)).exists() and (signals / str(second.pid)).exists())
        # Both processes are inside actual acquisition at once; no GIL/thread pool shortcut.
        assert (signals / str(first.pid)).read_text() != (signals / str(second.pid)).read_text()
        first.kill()
        first.join(5)
        third = start()
        _await(lambda: (signals / str(third.pid)).exists())
        assert (signals / str(third.pid)).read_text() == (signals / str(first.pid)).read_text()
        with automatic._engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM data_sync_jobs WHERE status='RUNNING'")
                )
                == 2
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM data_sync_attempts WHERE outcome='INTERRUPTED'")
                )
                == 1
            )
        (signals / "release").touch()
        _await(
            lambda: (
                (signals / f"quality-{second.pid}").exists()
                and (signals / f"quality-{third.pid}").exists()
            )
        )
        (signals / "quality-release").touch()
        try:
            _await(validated)
        except AssertionError:
            with automatic._engine.connect() as connection:
                raise AssertionError(
                    connection.execute(text("SELECT scope,status,error FROM data_sync_jobs")).all()
                )
        with automatic._engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM data_sync_receipts")) == 2
            assert connection.scalar(text("SELECT count(*) FROM data_sync_coverage")) == 2
    finally:
        (signals / "release").touch()
        (signals / "quality-release").touch()
        for process, stop in children:
            stop.set()
        for process, _ in children:
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
            process.close()
        queue.close()


def test_concurrent_admission_shares_one_persisted_rate_budget(automatic, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    pending(automatic)
    _second(automatic)
    with automatic._engine.begin() as connection:
        connection.execute(text("UPDATE data_sync_settings SET requests_per_minute=1"))
    entered, release = Event(), Event()

    def download(*args):
        entered.set()
        assert release.wait(10)
        return response()

    monkeypatch.setattr(acquisition, "fetch", download)
    with ThreadPoolExecutor(4) as pool:
        first = pool.submit(jobs.process_next, automatic)
        assert entered.wait(10)
        try:
            others = [pool.submit(jobs.process_next, automatic) for _ in range(3)]
            assert [future.result() for future in others] == [None, None, None]
            with automatic._engine.connect() as connection:
                assert connection.scalar(text("SELECT count(*) FROM data_sync_attempts")) == 1
        finally:
            release.set()
        assert first.result()["status"] == "VALIDATED"

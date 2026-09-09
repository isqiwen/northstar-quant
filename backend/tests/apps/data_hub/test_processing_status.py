"""Backlog remains observable beyond the recent list and never recovers work on read."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import UUID

from sqlalchemy import Engine

import northstar_quant.data_management.processing as processing
from northstar_quant.apps.data_hub import create_app
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from tests.apps.browser import ProtocolClient, _browser_session
from tests.data_management.test_processing import submit


def test_complete_queue_and_readonly_running_status(
    postgres_engine: Engine, clean_database: None, tmp_path: Path, monkeypatch
):
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "sources"))
    with ProtocolClient(
        create_app(postgres_engine, library), base_url="http://127.0.0.1"
    ) as client:
        _browser_session(client)
        empty = client.get("/api/processing/status").json()
        assert empty["total"] == 0
        assert empty["oldest_pending_id"] is None
        assert empty["oldest_pending_seconds"] is None
        attempts = [submit(library) for _ in range(51)]
        status = client.get("/api/processing/status").json()
        assert status["total"] == status["pending"] == 51
        assert len(client.get("/api/attempts").json()) == 50
        assert status["oldest_pending_id"] == attempts[0]["attempt_id"]
        assert status["oldest_pending_at"] == attempts[0]["created_at"]
        assert status["oldest_pending_seconds"] >= 0
        started, release = Event(), Event()
        original = processing._import_csv

        def hold(*args, **kwargs):
            started.set()
            assert release.wait(10)
            return original(*args, **kwargs)

        monkeypatch.setattr(processing, "_import_csv", hold)
        with ThreadPoolExecutor(max_workers=1) as executor:
            worker = executor.submit(processing.process_attempt, library)
            try:
                assert started.wait(5)
                active = client.get("/api/processing/status").json()
                assert active["pending"] == 50
                assert active["running"] == 1
                assert active["oldest_pending_id"] == attempts[1]["attempt_id"]
                assert library.attempt(UUID(str(attempts[0]["attempt_id"])))["status"] == "RUNNING"
            finally:
                release.set()
            assert worker.result()["status"] == "PUBLISHED"
        monkeypatch.setattr(processing, "code_revision", lambda: "b" * 40)
        assert processing.process_attempt(library)["status"] == "FAILED"
        # A newly composed API observes persisted counts, independent of prior client state.
    with ProtocolClient(
        create_app(postgres_engine, library), base_url="http://127.0.0.1"
    ) as client:
        _browser_session(client)
        status = client.get("/api/processing/status").json()
        assert (
            status["total"],
            status["pending"],
            status["running"],
            status["published"],
            status["failed"],
        ) == (51, 49, 0, 1, 1)

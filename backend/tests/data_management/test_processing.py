"""Persisted admissions survive API loss and cannot be stolen from active processing."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine

import northstar_quant.data_management.processing as processing
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from tests.data_management.test_library import _study


def submit(library: DataLibrary, request_id: str | None = None) -> dict[str, object]:
    content, spec, _ = _study()
    return library.submit(
        content,
        filename="synthetic.csv",
        source_name=str(spec["source_name"]),
        use_basis="Synthetic integration input; private retention permitted.",
        allow_retention=True,
        allow_download=True,
        spec=spec,
        request_id=request_id or str(uuid4()),
    )


def test_pending_survives_admission_and_audit_and_processes_once(
    postgres_engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "sources"))
    request = str(uuid4())
    first = submit(library, request)
    second = submit(library)
    assert first["status"] == second["status"] == "PENDING"
    assert submit(library, request) == first
    library.reconcile()
    assert library.list_datasets() == ()
    # A separately constructed owner reads durable work; admission objects aren't needed.
    worker = DataLibrary(postgres_engine, SourceFiles(tmp_path / "sources"))
    done = processing.process_attempt(worker)
    assert done is not None and done["attempt_id"] == first["attempt_id"]
    assert done["status"] == "PUBLISHED"
    assert processing.process_attempt(worker, UUID(str(first["attempt_id"]))) == done
    assert worker.attempt(UUID(str(second["attempt_id"])))["status"] == "PENDING"
    assert processing.process_attempt(worker)["status"] == "PUBLISHED"
    assert processing.process_attempt(worker) is None


def test_active_processing_does_not_block_or_interrupt_new_admission(
    postgres_engine: Engine, clean_database: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "sources"))
    first = submit(library)
    started, release = Event(), Event()
    original = processing._import_csv

    def hold(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(processing, "_import_csv", hold)
    with ThreadPoolExecutor(max_workers=2) as executor:
        running = executor.submit(processing.process_attempt, library)
        try:
            assert started.wait(5)
            second = executor.submit(submit, library).result(timeout=3)
            assert second["status"] == "PENDING"
            assert library.attempt(UUID(str(first["attempt_id"])))["status"] == "RUNNING"
        finally:
            release.set()
        assert running.result(timeout=10)["status"] == "PUBLISHED"
    assert library.attempt(UUID(str(second["attempt_id"])))["status"] == "PENDING"


def test_worker_refuses_changed_implementation_without_publishing(
    postgres_engine: Engine, clean_database: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "sources"))
    first = submit(library)
    monkeypatch.setattr(processing, "code_revision", lambda: "b" * 40)
    failed = processing.process_attempt(library)
    assert failed is not None and failed["status"] == "FAILED"
    assert "implementation differs" in str(failed["error"])
    assert library.list_datasets() == ()
    assert library.download(UUID(str(first["source_id"])))[1] == _study()[0]


def test_killed_worker_leaves_input_and_requires_explicit_retry(
    postgres_engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    import os
    import subprocess
    import sys
    import time

    from sqlalchemy import text

    files = SourceFiles(tmp_path / "sources")
    library = DataLibrary(postgres_engine, files)
    first = submit(library)
    second = submit(library)
    environment = dict(
        os.environ,
        NORTHSTAR_DATABASE_URL=postgres_engine.url.render_as_string(hide_password=False),
        NORTHSTAR_DATA_DIR=str(tmp_path / "sources"),
        NORTHSTAR_LOG_DIR=str(tmp_path / "logs"),
    )
    # Hold a real storage dependency, so the worker is killed during processing,
    # not after an already completed tiny sample. No sleep-based race injection.
    with postgres_engine.begin() as barrier:
        barrier.execute(text("LOCK TABLE canonical_bar IN ACCESS EXCLUSIVE MODE"))
        child = subprocess.Popen(
            [sys.executable, "-c", "from northstar_quant.apps.data_hub.worker import run; run()"],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 10
            while library.attempt(UUID(str(first["attempt_id"])))["status"] == "PENDING":
                assert child.poll() is None
                assert time.monotonic() < deadline
                time.sleep(0.05)
            assert library.attempt(UUID(str(first["attempt_id"])))["status"] == "RUNNING"
            child.kill()
            child.wait(timeout=5)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            child.communicate(timeout=5)
    assert library.list_datasets() == ()
    assert library.download(UUID(str(first["source_id"])))[1] == _study()[0]
    resumed = processing.process_attempt(library)
    assert resumed is not None and resumed["attempt_id"] == second["attempt_id"]
    assert resumed["status"] == "PUBLISHED"
    failed = library.attempt(UUID(str(first["attempt_id"])))
    assert failed["status"] == "FAILED" and "interrupted" in str(failed["error"])
    assert processing.process_attempt(library, UUID(str(first["attempt_id"]))) == failed
    retry = library.reprocess(
        UUID(str(first["source_id"])), spec=_study()[1], request_id=str(uuid4())
    )
    assert retry["status"] == "PUBLISHED" and retry["attempt_id"] != first["attempt_id"]

"""Storage failures remain visible without touching data or contacting a broker."""

from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine

from northstar_quant.apps.live import create_app as live_web_app
from northstar_quant.apps.live.instances import Instances
from northstar_quant.apps.live.kernel import create_app
from northstar_quant.cli import main
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.live import LiveAuth, LiveClient
from northstar_quant.live.instances import Instance
from tests.apps.browser import ProtocolClient as TestClient
from tests.apps.browser import login_response


def test_readonly_diagnostics_report_capacity_and_missing_storage(
    live_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "sources"
    library = DataLibrary(live_engine, SourceFiles(root, min_free_bytes=2**63))
    auth = LiveAuth("r" * 48, "c" * 48)
    with TestClient(create_app(live_engine, library, auth)) as http:
        client = LiveClient("http://localhost", LiveAuth(auth.read_token), client=http)
        assert http.get("/diagnostics").status_code == 403
        result = client.diagnostics()
        assert result["status"] == "DEGRADED"
        assert result["database"]["status"] == "REACHABLE"
        assert result["database"]["disk_capacity"] == "OBSERVED"
        assert result["source_filesystem"]["status"] == "LOW"
        assert result["source_filesystem"]["free_bytes"] > 0
        borrowed = client.for_runtime(UUID(result["live_runtime"]["runtime_id"]))
        monkeypatch.setattr(LiveClient, "from_environment", lambda: borrowed)
        assert main(["check"]) == 2
        root.rename(tmp_path / "retained")
        assert client.diagnostics()["source_filesystem"]["status"] == "UNAVAILABLE"
        assert not root.exists()  # Observation must not repair or recreate missing storage.
        with TestClient(
            live_web_app(instances=Instances({"sim": borrowed}, [Instance("sim", "simnow_dev")])),
            base_url="http://localhost",
        ) as console:
            page = login_response(console)
            assert page.status_code == 200
            observation = console.get("/api/live/diagnostics").json()
            assert observation["source_filesystem"]["status"] == "UNAVAILABLE"
            assert observation["status"] == "DEGRADED"
        assert client.streams.list() == []


def test_database_failure_keeps_diagnostics_readable_without_leaking_credentials(
    tmp_path: Path,
) -> None:
    from northstar_quant.live.storage import initialize, open_store

    private_path = tmp_path / "private-user-never-expose.sqlite"
    engine = open_store(private_path)
    initialize(engine)
    auth = LiveAuth("r" * 48, "c" * 48)
    try:
        library = DataLibrary(engine, SourceFiles(tmp_path / "files", min_free_bytes=0))
        application = create_app(engine, library, auth)
        engine.dispose()
        private_path.rename(tmp_path / "retained.sqlite")
        with TestClient(application) as http:
            client = LiveClient("http://localhost", auth, client=http)
            result = client.diagnostics()
            assert result["status"] == "DEGRADED"
            assert result["database"]["status"] == "UNAVAILABLE"
            assert result["source_filesystem"]["status"] == "OK"
            assert "never-expose" not in str(result) and "private-user" not in str(result)
            assert not private_path.exists()
    finally:
        engine.dispose()


def test_sqlite_diagnostics_measure_its_volume_and_preserve_missing_file(tmp_path):
    from northstar_quant.live.diagnostics import observe
    from northstar_quant.live.storage import open_store
    from northstar_quant.persistence.sql import write_transaction

    path = tmp_path / "live.sqlite"
    engine = open_store(path)
    library = DataLibrary(engine, SourceFiles(tmp_path / "archive", min_free_bytes=0))
    try:
        with write_transaction(engine) as connection:
            connection.exec_driver_sql("CREATE TABLE evidence (value TEXT)")
            connection.exec_driver_sql("INSERT INTO evidence VALUES ('confirmed')")
        result = observe(engine, library)
        assert result["status"] == "OK"
        database = result["database"]
        assert database["disk_capacity"] == "OBSERVED"
        assert database["database_bytes"] == path.stat().st_size
        assert database["wal_bytes"] == Path(str(path) + "-wal").stat().st_size
        assert database["free_bytes"] > 0
        assert str(tmp_path) not in str(result)
        engine.dispose()  # Force the next observation to open a new pool connection.
        path.rename(tmp_path / "retained.sqlite")
        unavailable = observe(engine, library)
        assert unavailable["status"] == "DEGRADED"
        assert unavailable["database"]["status"] == "UNAVAILABLE"
        assert unavailable["database"]["disk_capacity"] == "UNAVAILABLE"
        assert not path.exists()
    finally:
        engine.dispose()

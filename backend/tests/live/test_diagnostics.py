"""Storage failures remain visible without touching data or contacting a broker."""

from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine, create_engine

from northstar_quant.apps.live import create_app as live_web_app
from northstar_quant.apps.live.kernel import create_app
from northstar_quant.cli import main
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.live import LiveAuth, LiveClient
from tests.apps.browser import ProtocolClient as TestClient


def test_readonly_diagnostics_report_capacity_and_missing_storage(
    postgres_engine: Engine, clean_database: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del clean_database
    root = tmp_path / "sources"
    library = DataLibrary(postgres_engine, SourceFiles(root, min_free_bytes=2**63))
    auth = LiveAuth("r" * 48, "c" * 48)
    with TestClient(create_app(postgres_engine, library, auth)) as http:
        client = LiveClient("http://localhost", LiveAuth(auth.read_token), client=http)
        assert http.get("/diagnostics").status_code == 403
        result = client.diagnostics()
        assert result["status"] == "DEGRADED"
        assert result["database"] == {"status": "REACHABLE", "disk_capacity": "UNKNOWN"}
        assert result["source_filesystem"]["status"] == "LOW"
        assert result["source_filesystem"]["free_bytes"] > 0
        borrowed = client.for_runtime(UUID(result["live_runtime"]["runtime_id"]))
        monkeypatch.setattr(LiveClient, "from_environment", lambda: borrowed)
        assert main(["check"]) == 2
        root.rename(tmp_path / "retained")
        assert client.diagnostics()["source_filesystem"]["status"] == "UNAVAILABLE"
        assert not root.exists()  # Observation must not repair or recreate missing storage.
        with TestClient(live_web_app(live=borrowed), base_url="http://localhost") as console:
            page = console.get("/api/browser-session")
            assert page.status_code == 200
            observation = console.get("/api/live/diagnostics").json()
            assert observation["source_filesystem"]["status"] == "UNAVAILABLE"
            assert observation["status"] == "DEGRADED"
        assert client.streams.list() == []


def test_database_failure_keeps_diagnostics_readable_without_leaking_credentials(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        "postgresql+psycopg://private-user:never-expose@127.0.0.1:1/missing",
        connect_args={"connect_timeout": 1},
    )
    auth = LiveAuth("r" * 48, "c" * 48)
    try:
        library = DataLibrary(engine, SourceFiles(tmp_path, min_free_bytes=0))
        with TestClient(create_app(engine, library, auth)) as http:
            client = LiveClient("http://localhost", auth, client=http)
            result = client.diagnostics()
            assert result["status"] == "DEGRADED"
            assert result["database"]["status"] == "UNAVAILABLE"
            assert result["source_filesystem"]["status"] == "OK"
            assert "never-expose" not in str(result) and "private-user" not in str(result)
    finally:
        engine.dispose()

"""Synthetic callbacks exercise durable reception; these are not SimNow evidence."""

from __future__ import annotations

import re
import time
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from test_broker_ledger import ledger_query, position_baseline
from test_live import OPEN, tick

from northstar_quant.broker import streams as module
from northstar_quant.broker.records import BrokerEvent
from northstar_quant.broker.settings import Credentials
from northstar_quant.broker.streams import BrokerStreams
from northstar_quant.data.files import SourceFiles
from northstar_quant.data.library import DataLibrary
from northstar_quant.research import ResearchConfig
from northstar_quant.sessions import SessionStore
from northstar_quant.web import create_app


class Clock(datetime):
    at = OPEN

    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        return cls.at if tz is not None else cls.at.replace(tzinfo=None)


def prepare(
    engine: Engine, root: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[DataLibrary, UUID, str, dict[str, Any]]:
    library = DataLibrary(engine, SourceFiles(root / "archive"))
    position_baseline(engine)
    source = ledger_query(engine)
    configuration = SessionStore(engine, library).save_configuration(
        "shadow", ResearchConfig(threshold=Decimal("0.001"))
    )
    calls: dict[str, Any] = {"count": 0, "ready": Event()}

    def receive(*args: Any, **kwargs: Any) -> None:
        calls["count"] += 1
        calls["accept"] = kwargs["on_event"]
        calls["ready"].set()
        while not kwargs["should_stop"]():
            time.sleep(0.01)
        if "tail" in calls:
            kwargs["on_event"](calls["tail"])

    monkeypatch.setattr(module.ctp, "stream_account", receive)
    monkeypatch.setattr(module.ctp, "sdk_status", lambda: {"available": True})
    monkeypatch.setattr(
        module, "load_credentials", lambda: Credentials("123456", "secret", "test", "code")
    )
    Clock.at = OPEN
    monkeypatch.setattr(module, "datetime", Clock)
    return library, source, str(configuration["configuration_id"]), calls


def start(
    streams: BrokerStreams, source: UUID, configuration: str, identifier: UUID
) -> dict[str, object]:
    return streams.start(
        source,
        configuration,
        request_id=identifier,
        duration_seconds=300,
        allow_retention=True,
        use_basis="Synthetic engineering acceptance",
    )


def logins(accept: Any) -> None:
    for index, channel in enumerate(("TD", "MD"), 1):
        accept(
            BrokerEvent(
                index,
                channel,
                "OnRspUserLogin",
                index,
                True,
                OPEN.isoformat().replace("+00:00", "Z"),
                0,
                {"UserID": "123456", "BrokerID": "9999", "TradingDay": "20260907"},
            )
        )


def test_stream_commits_inputs_before_shadow_decisions_and_preserves_pause_retry_restart(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    library, source, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    streams, identifier = BrokerStreams(postgres_engine, library), uuid4()
    try:
        start(streams, source, configuration, identifier)
        assert calls["ready"].wait(3)
        logins(calls["accept"])
        for index, seconds in enumerate(range(0, 181, 4), 3):
            event = tick(
                index,
                OPEN + timedelta(seconds=seconds),
                price="3100" if seconds < 120 else "3110",
                volume=100 + index,
            )
            Clock.at = datetime.fromisoformat(event.received_at)
            calls["accept"](event)
        report = streams.get(identifier)
        assert report["received"] == report["cursor"] == event.sequence
        assert report["state"]["market"]["status"] == "READY"
        assert report["steps"][0]["result"]["intent"]["target_fraction"] == "0.5"
        assert report["order_sending"] is False and report["cancel_sending"] is False
        assert report["steps"][0]["result"]["bar"]["confirmed_by_sequence"] == event.sequence
        # Same original input returns the committed effect, without a new bar/signal.
        calls["accept"](event)
        assert streams.get(identifier)["steps"] == report["steps"]
        with pytest.raises(ValueError, match="conflicts"):
            calls["accept"](replace(event, data={**event.data, "LastPrice": "3200"}))
        pause = uuid4()
        paused = streams.control(identifier, "PAUSE", request_id=pause)
        next_event = tick(event.sequence + 1, OPEN + timedelta(seconds=184), volume=1000)
        Clock.at = datetime.fromisoformat(next_event.received_at)
        calls["accept"](next_event)
        assert streams.get(identifier)["received"] == next_event.sequence
        assert streams.get(identifier)["steps"] == report["steps"]
        assert streams.control(identifier, "PAUSE", request_id=pause) == paused
        streams.control(identifier, "RESUME", request_id=uuid4())
        assert "market" not in streams.get(identifier)["state"]
        assert streams.control(identifier, "PAUSE", request_id=pause) == paused
        assert "market" not in streams.get(identifier)["state"]
        # The database-level source is immutable; the callback can be traced separately.
        with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM broker_stream_events WHERE stream_id=:id"), {"id": identifier}
            )
        assert streams.events(identifier)[-1]["event"] == next_event.to_dict()
        assert streams.verify_all() == 1
    finally:
        streams.close()
    assert streams.get(identifier)["status"] == "STOPPED"
    monkeypatch.setattr(module, "load_credentials", lambda: pytest.fail("retry loaded credentials"))
    restored = BrokerStreams(postgres_engine, library)
    assert restored.get(identifier)["paused"]
    assert start(restored, source, configuration, identifier)["status"] == "STOPPED"
    assert calls["count"] == 1
    with pytest.raises(ValueError, match="resume needs"):
        restored.control(identifier, "RESUME", request_id=uuid4())
    Clock.at = OPEN
    assert (
        restored.get(identifier)["market_age_seconds"] < 0
    )  # Never label future receipt as fresh.


def test_stream_retains_unprocessed_source_and_retries_only_the_missing_projection(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    library, source, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    streams, identifier = BrokerStreams(postgres_engine, library), uuid4()
    original = module.advance_market
    try:
        start(streams, source, configuration, identifier)
        assert calls["ready"].wait(3)
        logins(calls["accept"])
        event = tick(3, OPEN)
        Clock.at = datetime.fromisoformat(event.received_at)
        monkeypatch.setattr(
            module,
            "advance_market",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("stop before projection")),
        )
        with pytest.raises(RuntimeError, match="stop before"):
            calls["accept"](event)
        report = streams.get(identifier)
        assert report["received"] == 3 and report["cursor"] == 2
        assert streams.events(identifier)[-1]["event"] == event.to_dict()
        assert (
            streams.verify_all() == 1
        )  # An unprocessed durable tail is valid interrupted evidence.
        monkeypatch.setattr(module, "advance_market", original)
        calls["accept"](event)
        assert streams.get(identifier)["cursor"] == 3
        Clock.at += timedelta(seconds=6)
        for _ in range(100):
            if streams.get(identifier)["reason"] == "QUOTE_STALE":
                break
            time.sleep(0.01)
        assert streams.get(identifier)["paused"]
        assert streams.get(identifier)["reason"] == "QUOTE_STALE"
        assert streams.get(identifier)["state"]["last_pause_reason"] == "QUOTE_STALE"
        with pytest.raises(ValueError, match="already running"):
            start(BrokerStreams(postgres_engine, library), source, configuration, uuid4())
    finally:
        streams.close()

    assert streams.get(identifier)["state"]["last_pause_reason"] == "QUOTE_STALE"


def test_browser_stream_start_stop_requires_csrf_and_never_reconnects_on_reads(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    library, source, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    identifier = uuid4()
    payload = {
        "query_batch_id": str(source),
        "configuration_id": configuration,
        "request_id": str(identifier),
        "duration_seconds": 300,
        "allow_retention": True,
        "use_basis": "Synthetic engineering acceptance",
    }
    with TestClient(create_app(postgres_engine, library), base_url="http://127.0.0.1") as client:
        assert client.post("/api/streams", json=payload).status_code == 403
        page = client.get("/streams")
        assert page.status_code == 200 and calls["count"] == 0
        token = re.search(r'<meta name="northstar-csrf" content="([^"]+)">', page.text)
        assert token
        client.headers["X-Northstar-CSRF"] = token.group(1)
        assert (
            client.post("/api/streams", json={**payload, "allow_retention": False}).status_code
            == 422
        )
        assert (
            client.post("/api/streams", json={**payload, "td_front": "tcp://elsewhere"}).status_code
            == 422
        )
        assert calls["count"] == 0
        assert client.post("/api/streams", json=payload).status_code == 201
        assert calls["ready"].wait(3)
        logins(calls["accept"])
        assert client.get(f"/streams/{identifier}").status_code == 200
        assert len(client.get(f"/api/streams/{identifier}/events").json()) == 2
        stopped = client.post(
            f"/api/streams/{identifier}/control",
            json={"action": "STOP", "request_id": str(uuid4())},
        )
        assert stopped.status_code == 200
        assert client.post("/api/streams", json=payload).status_code == 201
        assert calls["count"] == 1


def test_identity_error_cannot_resume_and_stop_keeps_tail_callbacks(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    library, source, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    streams, identifier = BrokerStreams(postgres_engine, library), uuid4()
    try:
        start(streams, source, configuration, identifier)
        assert calls["ready"].wait(3)
        logins(calls["accept"])
        bad = BrokerEvent(
            3,
            "TD",
            "OnRtnTrade",
            None,
            None,
            OPEN.isoformat().replace("+00:00", "Z"),
            0,
            {"InvestorID": "654321", "BrokerID": "9999"},
        )
        calls["accept"](bad)
        assert streams.get(identifier)["reason"] == "ACCOUNT_CALLBACK_IDENTITY_MISMATCH"
        with pytest.raises(ValueError, match="identity error"):
            streams.control(identifier, "RESUME", request_id=uuid4())
        calls["tail"] = replace(bad, sequence=4, data={"InvestorID": "123456", "BrokerID": "9999"})
        streams.control(identifier, "STOP", request_id=uuid4())
    finally:
        streams.close()
    report = streams.get(identifier)
    assert report["status"] == "STOPPED" and report["received"] == report["cursor"] == 4
    assert streams.events(identifier)[-1]["event"] == calls["tail"].to_dict()
    with TestClient(create_app(postgres_engine, library), base_url="http://127.0.0.1") as client:
        assert client.get(f"/api/streams/{identifier}").status_code == 403
        assert client.get(f"/streams/{identifier}").status_code == 200
        result = client.get(f"/api/streams/{identifier}").json()
        assert result["paused"] and result["connection"] == "NOT_ATTACHED"
        assert calls["count"] == 1


def test_lost_owner_database_connection_stops_reception_without_reacquiring_lock(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    library, source, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    streams, identifier = BrokerStreams(postgres_engine, library), uuid4()
    try:
        start(streams, source, configuration, identifier)
        assert calls["ready"].wait(3)
        logins(calls["accept"])
        with postgres_engine.begin() as connection:
            owner_pid = connection.execute(
                text("""
                SELECT pid FROM pg_locks
                WHERE locktype='advisory' AND granted AND classid=0
                    AND objid=728401929 AND objsubid=1
                    AND database=(SELECT oid FROM pg_database WHERE datname=current_database())
            """)
            ).scalar_one()
            assert owner_pid != connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
            assert connection.execute(
                text("SELECT pg_terminate_backend(:pid)"), {"pid": owner_pid}
            ).scalar_one()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            report = streams.get(identifier)
            if report["status"] == "FAILED" and report["connection"] == "NOT_ATTACHED":
                break
            time.sleep(0.01)
        assert report["status"] == "FAILED"
        assert report["reason"] == "RECEPTION_OR_PERSISTENCE_FAILED"
        assert report["paused"] and report["connection"] == "NOT_ATTACHED"
        assert report["received"] == report["cursor"] == 2
        monkeypatch.setattr(
            module,
            "load_credentials",
            lambda: pytest.fail("failed stream retry loaded credentials"),
        )
        assert start(streams, source, configuration, identifier)["status"] == "FAILED"
        assert calls["count"] == 1
        with postgres_engine.begin() as connection:
            assert connection.execute(
                text("SELECT pg_try_advisory_xact_lock(728401929)")
            ).scalar_one()
    finally:
        streams.close()

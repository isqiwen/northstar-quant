"""Synthetic callbacks exercise durable reception; these are not SimNow evidence."""

from __future__ import annotations

import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from queue import Empty, Queue
from threading import Event
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from northstar_quant.accounting.ledger import BrokerLedger
from northstar_quant.broker.events import BrokerEvent
from northstar_quant.broker.records import BrokerRecords
from northstar_quant.broker.settings import Credentials
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.live import streams as module
from northstar_quant.live.streams import LiveStreams
from northstar_quant.research.artifacts import ResearchUsages
from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.research.configurations import ConfigurationStore
from northstar_quant.strategies.configuration import StrategyConfig
from tests.accounting.test_ledger import ledger_query, position_baseline, trade
from tests.apps.browser import ProtocolClient as TestClient
from tests.apps.browser import login_response
from tests.live.test_market import OPEN, tick


class Clock(datetime):
    at = OPEN

    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        return cls.at if tz is not None else cls.at.replace(tzinfo=None)


def prepare(
    engine: Engine,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    trading_day: str = "20260907",
) -> tuple[DataLibrary, UUID, str, dict[str, Any]]:
    monkeypatch.setenv("NORTHSTAR_BROKER_PROFILE", "simnow_dev")
    monkeypatch.setenv("NORTHSTAR_SIMNOW_USER_ID", "123456")
    monkeypatch.setenv("NORTHSTAR_SIMNOW_PASSWORD", "secret")
    monkeypatch.setenv("NORTHSTAR_SIMNOW_APP_ID", "test")
    monkeypatch.setenv("NORTHSTAR_SIMNOW_AUTH_CODE", "code")
    library = DataLibrary(engine, SourceFiles(root / "archive"), usages=ResearchUsages(engine).list)
    position_baseline(engine, day=trading_day)
    source = ledger_query(engine, day=trading_day)
    configuration = ConfigurationStore(engine).save_configuration(
        "shadow",
        ResearchConfig(
            strategy=StrategyConfig.create(supplied={"threshold": str(Decimal("0.001"))})
        ),
    )
    calls: dict[str, Any] = {"count": 0, "ready": Event()}

    def receive(*args: Any, **kwargs: Any) -> None:
        calls["count"] += 1
        incoming: Queue[Any] = Queue(maxsize=1)

        def accept(event: BrokerEvent) -> None:
            completed = Event()
            errors: list[BaseException] = []
            incoming.put((event, completed, errors), timeout=5)
            assert completed.wait(5), "synthetic core did not consume callback"
            if errors:
                raise errors[0]

        calls["accept"] = accept
        calls["ready"].set()
        while not kwargs["should_stop"]():
            try:
                event, completed, errors = incoming.get(timeout=0.01)
            except Empty:
                continue
            try:
                kwargs["on_event"](event)
            except BaseException as error:
                errors.append(error)
            finally:
                completed.set()
        if "tail" in calls:
            kwargs["on_event"](calls["tail"])

    monkeypatch.setattr(module.ctp, "stream_account", receive)
    monkeypatch.setattr(module.ctp, "sdk_status", lambda: {"available": True})
    monkeypatch.setattr(
        module, "load_credentials", lambda: Credentials("123456", "secret", "test", "code")
    )
    Clock.at = datetime.combine(datetime.fromisoformat(trading_day).date(), OPEN.timetz())
    monkeypatch.setattr(module, "datetime", Clock)
    return library, source, str(configuration["configuration_id"]), calls


def start(
    streams: LiveStreams, source: UUID, configuration: str, identifier: UUID
) -> dict[str, object]:
    return streams.start(
        source,
        configuration,
        request_id=identifier,
        duration_seconds=300,
        allow_retention=True,
        use_basis="Synthetic engineering acceptance",
    )


def logins(accept: Any, *, at: datetime = OPEN, trading_day: str | None = None) -> None:
    trading_day = at.strftime("%Y%m%d") if trading_day is None else trading_day
    for index, channel in enumerate(("TD", "MD"), 1):
        accept(
            BrokerEvent(
                index,
                channel,
                "OnRspUserLogin",
                index,
                True,
                at.isoformat().replace("+00:00", "Z"),
                0,
                {"UserID": "123456", "BrokerID": "9999", "TradingDay": trading_day},
            )
        )


def test_stream_durable_inputs_pause_retry_restart(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    library, source, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    streams, identifier = LiveStreams(postgres_engine, library), uuid4()
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
        # A healthy core still runs the independent freshness/ownership monitor.
        Clock.at += timedelta(seconds=6)
        for _ in range(100):
            if streams.get(identifier)["reason"] == "QUOTE_STALE":
                break
            time.sleep(0.01)
        assert streams.get(identifier)["paused"]
        assert streams.get(identifier)["reason"] == "QUOTE_STALE"
        with pytest.raises(ValueError, match="already running"):
            start(LiveStreams(postgres_engine, library), source, configuration, uuid4())
        pause = uuid4()
        paused = streams.control(identifier, "PAUSE", request_id=pause)
        next_event = tick(event.sequence + 1, OPEN + timedelta(seconds=188), volume=1000)
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
        with pytest.raises(ValueError, match="conflicts"):
            calls["accept"](replace(event, data={**event.data, "LastPrice": "3200"}))
    finally:
        streams.close()
    assert streams.get(identifier)["status"] == "FAILED"
    monkeypatch.setattr(module, "load_credentials", lambda: pytest.fail("retry loaded credentials"))
    restored = LiveStreams(postgres_engine, library)
    assert restored.get(identifier)["paused"]
    assert start(restored, source, configuration, identifier)["status"] == "FAILED"
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
    streams, identifier = LiveStreams(postgres_engine, library), uuid4()
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
        streams.close()
        assert streams.get(identifier)["status"] == "FAILED"
        # Explicit local replay of the retained receipt on a disposed receiver.
        # Pausing is durable, so catch-up cannot revive shadow or reconnect.
        restored = LiveStreams(postgres_engine, library)
        restored.accept(identifier, event)
        assert restored.get(identifier)["cursor"] == 3
        assert restored.get(identifier)["paused"]
        assert restored.get(identifier)["steps"] == []
        before = restored.get(identifier)["steps"]
        restored.accept(identifier, event)
        assert restored.get(identifier)["steps"] == before
        assert calls["count"] == 1
        with pytest.raises(ValueError, match="resume needs"):
            restored.control(identifier, "RESUME", request_id=uuid4())
    finally:
        streams.close()


def test_account_failure_keeps_source_and_local_catchup_never_replays_shadow(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    library, source, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    ledger = BrokerLedger(postgres_engine)
    baseline = UUID(ledger.context(source)["baseline_id"])
    streams, identifier = LiveStreams(postgres_engine, library), uuid4()
    Clock.at = datetime.now(UTC)
    try:
        start(streams, source, configuration, identifier)
        assert calls["ready"].wait(3)
        calls["accept"](
            BrokerEvent(
                1,
                "TD",
                "OnRspUserLogin",
                1,
                True,
                Clock.at.isoformat().replace("+00:00", "Z"),
                0,
                {"UserID": "123456", "BrokerID": "9999", "TradingDay": "20260907"},
            )
        )
        event = BrokerEvent(
            2,
            "TD",
            "OnRtnTrade",
            None,
            None,
            datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            0,
            trade(),
        )
        with monkeypatch.context() as interrupted:

            def fail_application(*args: Any, **kwargs: Any) -> None:
                raise RuntimeError("interrupted after durable receipt")

            interrupted.setattr(BrokerLedger, "advance_stream", fail_application)
            with pytest.raises(RuntimeError, match="after durable"):
                calls["accept"](event)
        saved = streams.get(identifier)
        assert saved["received"] == 2 and saved["cursor"] == 1
        assert saved["account_progress"]["through_sequence"] == 1
        assert saved["account_progress"]["pending"] == 1
        assert saved["paused"]
        assert streams.events(identifier)[-1]["event"] == event.to_dict()
    finally:
        streams.close()

    reopened = LiveStreams(postgres_engine, library)
    assert reopened.get(identifier)["account_progress"]["through_sequence"] == 1
    result = reopened.catchup_account(identifier, baseline, 2)
    assert result["status"] == "READY" and result["pending"] == 0
    entry = ledger.get(UUID(result["entry_id"]))
    assert entry["new_fill_count"] == 1
    assert reopened.catchup_account(identifier, baseline, 2) == result
    assert reopened.get(identifier)["cursor"] == 1  # No historical shadow replay.
    assert reopened.get(identifier)["connection"] == "NOT_ATTACHED"
    assert reopened.get(identifier)["order_sending"] is False
    assert calls["count"] == 1


def test_query_cannot_overtake_pending_market_receipt_clock_regression(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    library, source, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    ledger = BrokerLedger(postgres_engine)
    baseline = UUID(ledger.context(source)["baseline_id"])
    streams, identifier = LiveStreams(postgres_engine, library), uuid4()
    try:
        start(streams, source, configuration, identifier)
        assert calls["ready"].wait(3)
        # Independent application and database clocks are retained separately;
        # regression within the application receipt sequence remains unsafe.
        received = datetime.now(UTC) + timedelta(seconds=2)
        logins(calls["accept"], at=received, trading_day="20260907")
        event = BrokerEvent(
            3,
            "MD",
            "OnRtnDepthMarketData",
            None,
            None,
            (received - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            0,
            {"InstrumentID": "rb2610", "TradingDay": "20260907"},
        )
        with monkeypatch.context() as interrupted:

            def unavailable(*args: Any, **kwargs: Any) -> None:
                raise RuntimeError("synthetic account processing unavailable")

            interrupted.setattr(BrokerLedger, "advance_stream", unavailable)
            with pytest.raises(RuntimeError, match="processing unavailable"):
                calls["accept"](event)
        later = ledger_query(postgres_engine)
        with pytest.raises(ValueError, match="local catchup"):
            ledger.ingest(baseline, later, request_id=uuid4())
        result = streams.catchup_account(identifier, baseline, 3)
        assert result["pending"] == 0 and result["status"] == "UNKNOWN"
        assert result["reason"] == "STREAM_ACCOUNT_RECEIPT_TIME_REGRESSED"
        assert ledger.verify_all()["position_entries_count"] == 1
    finally:
        streams.close()


def test_browser_stream_start_stop_requires_csrf_and_never_reconnects_on_reads(
    live_web_app,
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
    with TestClient(live_web_app(postgres_engine, library), base_url="http://127.0.0.1") as client:
        assert client.post("/api/streams", json=payload).status_code == 401
        page = login_response(client)
        assert page.status_code == 200 and calls["count"] == 0
        token = page.json()["csrf"]
        client.headers["X-Northstar-CSRF"] = token
        client.headers["X-Live-Runtime-ID"] = client.get("/api/live/status").json()["runtime_id"]
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
        assert client.get(f"/api/streams/{identifier}").status_code == 200
        assert len(client.get(f"/api/streams/{identifier}/events").json()) == 2
        stopped = client.post(
            f"/api/streams/{identifier}/control",
            json={"action": "STOP", "request_id": str(uuid4())},
        )
        assert stopped.status_code == 200
        assert client.post("/api/streams", json=payload).status_code == 201
        assert calls["count"] == 1


def test_identity_error_cannot_resume_and_stop_keeps_tail_callbacks(
    live_web_app,
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    library, source, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    streams, identifier = LiveStreams(postgres_engine, library), uuid4()
    # Account callbacks follow the fixed baseline and source query, independently
    # of the calendar date on which this synthetic trading day is replayed.
    finished_at = BrokerRecords(postgres_engine).get(source)["capture"]["finished_at"]
    Clock.at = datetime.fromisoformat(finished_at) + timedelta(microseconds=1)
    try:
        start(streams, source, configuration, identifier)
        assert calls["ready"].wait(3)
        logins(calls["accept"], at=Clock.at, trading_day="20260907")
        bad = BrokerEvent(
            3,
            "TD",
            "OnRtnTrade",
            None,
            None,
            Clock.at.isoformat().replace("+00:00", "Z"),
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
    with TestClient(live_web_app(postgres_engine, library), base_url="http://127.0.0.1") as client:
        assert client.get(f"/api/streams/{identifier}").status_code == 401
        assert client.get("/streams").status_code == 404
        assert login_response(client).status_code == 200
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
    streams, identifier = LiveStreams(postgres_engine, library), uuid4()
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


def test_environment_switch_rejects_old_query_before_credentials_or_sdk(
    postgres_engine, clean_database, tmp_path, monkeypatch
):
    library, source, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    monkeypatch.setenv("NORTHSTAR_BROKER_PROFILE", "simnow_trading")

    def forbidden():
        pytest.fail("wrong-environment evidence must be rejected before credentials are read")

    monkeypatch.setattr(module, "load_credentials", forbidden)
    streams = LiveStreams(postgres_engine, library)
    try:
        with pytest.raises(ValueError, match="environment differs"):
            start(streams, source, configuration, uuid4())
        assert calls["count"] == 0
    finally:
        streams.close()

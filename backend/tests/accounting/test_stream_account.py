"""Saved callback progress applies real-shaped synthetic fills, never an external order."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text

from northstar_quant.accounting.baselines import BrokerBaselines
from northstar_quant.accounting.ledger import BrokerLedger
from northstar_quant.broker.events import BrokerEvent
from northstar_quant.live.streams import LiveStreams
from tests.accounting.test_baselines import saved_query
from tests.accounting.test_ledger import ledger_query, trade
from tests.accounting.test_stream_ledger import accept, login
from tests.live.test_streams import Clock, prepare, start


def test_market_callbacks_do_not_create_entries_or_scan_history_and_paused_trades_still_apply(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    library, source, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    ledger, streams, stream_id = (
        BrokerLedger(postgres_engine),
        LiveStreams(postgres_engine, library),
        uuid4(),
    )
    baseline_id = UUID(ledger.context(source)["baseline_id"])
    try:
        Clock.at = datetime.now(UTC)
        start(streams, source, configuration, stream_id)
        assert calls["ready"].wait(3)
        waiting = ledger.stream_progress(stream_id)
        assert waiting["status"] == "WAITING_FOR_TD_LOGIN" and waiting["entry_id"] is None
        assert ledger.bind_stream(baseline_id, stream_id) == waiting
        with pytest.raises(ValueError, match="another baseline"):
            ledger.bind_stream(uuid4(), stream_id)
        login(calls)
        streams.control(stream_id, "PAUSE", request_id=uuid4())
        with monkeypatch.context() as guard:
            guard.setattr(
                BrokerLedger,
                "_history",
                lambda *args, **kwargs: pytest.fail(
                    "ordinary market processing must not read the position history"
                ),
            )
            for sequence in range(2, 32):
                now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                calls["accept"](
                    BrokerEvent(
                        sequence,
                        "MD",
                        "OnRtnDepthMarketData",
                        None,
                        None,
                        now,
                        0,
                        {"InstrumentID": "rb2610", "TradingDay": "20260907"},
                    )
                )
            progress = ledger.stream_progress(stream_id)
        assert progress["status"] == "READY" and progress["through_sequence"] == 31
        assert progress["entry_id"] is None and progress["pending"] == 0
        accept(calls, 32, "OnRtnTrade", trade())
        first = ledger.get(UUID(ledger.stream_progress(stream_id)["entry_id"]))
        assert first["fill_count"] == 1 and first["source_stream"]["after_sequence"] == 31
        accept(calls, 33, "OnRtnTrade", trade(Price="3200"))
        assert ledger.stream_progress(stream_id)["status"] == "UNKNOWN"
        with pytest.raises(ValueError, match="caught-up, known"):
            streams.control(stream_id, "RESUME", request_id=uuid4())
        accept(calls, 34, "OnRtnTrade", trade("T2"))
        unknown = ledger.get(UUID(ledger.stream_progress(stream_id)["entry_id"]))
        assert unknown["fill_count"] == 2 and unknown["status"] == "UNKNOWN"
        assert streams.get(stream_id)["received"] == 34
        assert ledger.advance_stream(stream_id, 32) == ledger.stream_progress(stream_id)
        assert ledger.verify_all()["position_entries_count"] == 3
    finally:
        streams.close()


def test_entry_commit_before_progress_failure_retries_without_duplicate_fills(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    library, source, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    ledger, streams, stream_id = (
        BrokerLedger(postgres_engine),
        LiveStreams(postgres_engine, library),
        uuid4(),
    )
    baseline_id = UUID(ledger.context(source)["baseline_id"])
    original = BrokerLedger._ingest

    def interrupted(self: BrokerLedger, *args: Any, **kwargs: Any) -> dict[str, Any]:
        original(self, *args, **kwargs)
        raise RuntimeError("synthetic crash after entry commit, before progress commit")

    try:
        Clock.at = datetime.now(UTC)
        start(streams, source, configuration, stream_id)
        assert calls["ready"].wait(3)
        login(calls)
        with monkeypatch.context() as crash:
            crash.setattr(BrokerLedger, "_ingest", interrupted)
            with pytest.raises(RuntimeError, match="after entry commit"):
                accept(calls, 2, "OnRtnTrade", trade())
        pending = ledger.stream_progress(stream_id)
        assert pending["through_sequence"] == 1 and pending["pending"] == 1
        assert pending["entry_id"] is None
        with postgres_engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM broker_position_entries")
                ).scalar_one()
                == 0
            )
        later = ledger_query(postgres_engine, trades=(trade(),))
        with pytest.raises(ValueError, match="local catchup"):
            ledger.ingest(baseline_id, later, request_id=uuid4())
        with ThreadPoolExecutor(max_workers=2) as pool:
            retries = list(pool.map(lambda _: ledger.advance_stream(stream_id, 2), range(2)))
        assert retries[0] == retries[1]
        assert retries[0]["entry_id"] is not None and retries[0]["pending"] == 0
        queried = ledger.ingest(baseline_id, later, request_id=uuid4())
        assert queried["new_fill_count"] == 0 and queried["duplicate_count"] == 1
        assert ledger.verify_all()["position_entries_count"] == 2
    finally:
        streams.close()


def test_bounded_catchup_batches_only_requested_saved_material_and_preserves_later_pending(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    library, source, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    ledger, streams, stream_id = (
        BrokerLedger(postgres_engine),
        LiveStreams(postgres_engine, library),
        uuid4(),
    )
    try:
        Clock.at = datetime.now(UTC)
        start(streams, source, configuration, stream_id)
        assert calls["ready"].wait(3)
        login(calls)
        with monkeypatch.context() as fail:

            def unavailable(*args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("synthetic account writer unavailable")

            fail.setattr(BrokerLedger, "_ingest", unavailable)
            for sequence in (2, 3, 4):
                with pytest.raises(RuntimeError, match="account writer unavailable"):
                    accept(calls, sequence, "OnRtnTrade", trade(f"T{sequence}"))
        first = ledger.advance_stream(stream_id, 3)
        assert first["through_sequence"] == 3 and first["pending"] == 1
        entry = ledger.get(UUID(first["entry_id"]))
        assert entry["fill_count"] == entry["new_fill_count"] == 2
        assert entry["source_stream"]["through_sequence"] == 3
        with pytest.raises(ValueError, match="unreceived"):
            ledger.advance_stream(stream_id, 5)
        second = ledger.advance_stream(stream_id, 4)
        assert second["pending"] == 0
        assert ledger.get(UUID(second["entry_id"]))["new_fill_count"] == 1
        assert ledger.verify_all()["position_entries_count"] == 2
        # Restore verification re-reads the original source, not merely cursor/hash columns.
        with postgres_engine.begin() as connection:
            connection.exec_driver_sql("SET LOCAL session_replication_role = replica")
            connection.execute(
                text("""
                UPDATE broker_stream_events SET event_hash=repeat('0',64)
                WHERE stream_id=:id AND sequence=1
            """),
                {"id": stream_id},
            )
        with pytest.raises(ValueError):
            ledger.verify_all()
    finally:
        streams.close()


def test_manual_entry_with_market_tail_is_reused_by_fixed_account_catchup(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    library, _, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    monkeypatch.setenv("NORTHSTAR_LIVE_ENVIRONMENT", "simnow_trading")
    # This second approved synthetic environment has no baseline at stream start.
    # Establishing one locally does not silently bind the already-running stream.
    source = ledger_query(postgres_engine, profile="simnow_trading")
    baseline_source = saved_query(postgres_engine, profile="simnow_trading")
    ledger, streams = BrokerLedger(postgres_engine), LiveStreams(postgres_engine, library)
    stream_id, baseline_id = uuid4(), uuid4()
    try:
        Clock.at = datetime.now(UTC)
        start(streams, source, configuration, stream_id)
        assert calls["ready"].wait(3)
        assert ledger.stream_progress(stream_id)["status"] == "UNBOUND"
        BrokerBaselines(postgres_engine).establish(baseline_source, request_id=baseline_id)
        login(calls)
        streams.control(stream_id, "PAUSE", request_id=uuid4())
        accept(calls, 2, "OnRtnTrade", trade())
        received = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        calls["accept"](
            BrokerEvent(
                3,
                "MD",
                "OnRtnDepthMarketData",
                None,
                None,
                received,
                0,
                {"InstrumentID": "rb2610", "TradingDay": "20260907"},
            )
        )
        manual = ledger.ingest_stream(baseline_id, stream_id, 3, request_id=uuid4())
        assert manual["new_fill_count"] == manual["fill_count"] == 1
        assert manual["source_stream"]["through_sequence"] == 3
        assert ledger.stream_progress(stream_id)["status"] == "UNBOUND"

        progress = streams.catchup_account(stream_id, baseline_id, 3)
        assert progress["status"] == "READY"
        assert progress["through_sequence"] == 3 and progress["last_material_sequence"] == 2
        assert progress["pending"] == 0 and progress["entry_id"] == manual["entry_id"]
        assert streams.catchup_account(stream_id, baseline_id, 3) == progress
        assert ledger.get(UUID(manual["entry_id"])) == manual
        assert ledger.verify_all()["position_entries_count"] == 1
        assert streams.get(stream_id)["order_sending"] is False
    finally:
        streams.close()


def test_account_unknown_blocks_existing_and_new_streams_despite_successful_login(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    library, source, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    ledger, streams = BrokerLedger(postgres_engine), LiveStreams(postgres_engine, library)
    baseline_id = UUID(ledger.context(source)["baseline_id"])
    first_stream, next_stream = uuid4(), uuid4()
    try:
        Clock.at = datetime.now(UTC)
        start(streams, source, configuration, first_stream)
        assert calls["ready"].wait(3)
        login(calls)
        assert ledger.stream_progress(first_stream)["status"] == "READY"

        # A later, independently saved incomplete query changes the account book,
        # without receiving another callback in the already-bound stream.
        uncertain = ledger.ingest(
            baseline_id,
            ledger_query(postgres_engine, failure="QUERY_TIMEOUT"),
            request_id=uuid4(),
        )
        assert uncertain["status"] == "UNKNOWN"
        progress = ledger.stream_progress(first_stream)
        assert progress["status"] == "UNKNOWN" and progress["reason"] == "ACCOUNT_LEDGER_UNKNOWN"
        assert progress["entry_id"] is None
        assert progress["account_entry_id"] == uncertain["entry_id"]
        assert progress["through_sequence"] == 1 and progress["pending"] == 0
        assert streams.get(first_stream)["account_progress"] == progress
        assert streams.get(first_stream)["paused"] is True
        with pytest.raises(ValueError, match="caught-up, known"):
            streams.control(first_stream, "RESUME", request_id=uuid4())
        streams.close()

        calls["ready"].clear()
        Clock.at = datetime.now(UTC)
        start(streams, source, configuration, next_stream)
        assert calls["ready"].wait(3)
        assert ledger.stream_progress(next_stream)["status"] == "UNKNOWN"
        login(calls)
        after_login = ledger.stream_progress(next_stream)
        assert after_login["status"] == "UNKNOWN"
        assert after_login["entry_id"] is None
        assert after_login["account_entry_id"] == uncertain["entry_id"]
        assert streams.get(next_stream)["account_progress"] == after_login
        assert streams.get(next_stream)["paused"] is True
        with pytest.raises(ValueError, match="caught-up, known"):
            streams.control(next_stream, "RESUME", request_id=uuid4())
        assert ledger.get(UUID(uncertain["entry_id"])) == uncertain
        assert ledger.verify_all()["position_entries_count"] == 1
    finally:
        streams.close()


def test_progress_read_during_new_receipt_and_booking_never_reports_false_damage(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    from northstar_quant.accounting import stream_progress

    library, source, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    ledger = BrokerLedger(postgres_engine)
    streams, stream_id = LiveStreams(postgres_engine, library), uuid4()
    try:
        Clock.at = datetime.now(UTC)
        start(streams, source, configuration, stream_id)
        assert calls["ready"].wait(3)
        login(calls)
        original = stream_progress.read_stream_source
        interleave = True

        def read_while_booking(connection, identifier):
            nonlocal interleave
            received = original(connection, identifier)
            if interleave:
                interleave = False
                # A real receipt and account transaction commits between the
                # observer's two owner reads; no external broker is contacted.
                accept(calls, 2, "OnRtnTrade", trade())
            return received

        with monkeypatch.context() as race:
            race.setattr(stream_progress, "read_stream_source", read_while_booking)
            observed = ledger.stream_progress(stream_id)
        assert observed["status"] == "READY"
        assert observed["through_sequence"] == 1 and observed["pending"] == 0
        current = ledger.stream_progress(stream_id)
        assert current["through_sequence"] == 2 and current["pending"] == 0
        assert ledger.get(UUID(current["entry_id"]))["fill_count"] == 1
    finally:
        streams.close()

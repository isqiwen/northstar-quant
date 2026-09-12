"""Copied synthetic stream callbacks join the existing ledger, without a broker connection."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine

from northstar_quant.accounting.ledger import BrokerLedger
from northstar_quant.broker.events import BrokerEvent
from northstar_quant.execution.reviews import OrderReviews
from northstar_quant.live.streams import LiveStreams
from tests.accounting.test_ledger import ledger_query, position, trade
from tests.execution.test_orders import order
from tests.live.test_streams import Clock, prepare, start


def accept(
    calls: dict[str, Any],
    sequence: int,
    callback: str,
    data: dict[str, Any],
    *,
    is_last: bool | None = None,
) -> None:
    now = datetime.now(UTC)
    Clock.at = now
    calls["accept"](
        BrokerEvent(
            sequence,
            "TD",
            callback,
            None,
            is_last,
            now.isoformat().replace("+00:00", "Z"),
            0,
            data,
        )
    )


def login(calls: dict[str, Any]) -> None:
    accept(
        calls,
        1,
        "OnRspUserLogin",
        {
            "BrokerID": "9999",
            "UserID": "123456",
            "TradingDay": "20260907",
        },
        is_last=True,
    )


def test_stream_prefixes_share_query_dedup_and_fixed_order_history_while_paused(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    library, source, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    ledger = BrokerLedger(postgres_engine)
    baseline = UUID(ledger.context(source)["baseline_id"])
    query_entry = ledger.ingest(baseline, source, request_id=uuid4())
    streams, identifier = LiveStreams(postgres_engine, library), uuid4()
    first_fill, second_fill = trade(Volume=1), trade("T2", Volume=1)
    try:
        Clock.at = datetime.now(UTC)
        start(streams, source, configuration, identifier)
        assert calls["ready"].wait(3)
        login(calls)
        streams.control(identifier, "PAUSE", request_id=uuid4())
        accept(calls, 2, "OnRtnOrder", order(VolumeTraded=1, VolumeTotal=1, OrderStatus="1"))
        accept(calls, 3, "OnRtnTrade", first_fill)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda _: ledger.advance_stream(identifier, 3),
                    range(2),
                )
            )
        assert results[0] == results[1] and results[0]["status"] == "READY"
        command = UUID(results[0]["entry_id"])
        first = ledger.get(command)
        assert first["fill_count"] == first["new_fill_count"] == 1
        assert first["source_batch_id"] == str(source)
        assert first["added_fills"][0]["source_stream_id"] == str(identifier)
        assert first["added_fills"][0]["fee"] is None and first["cash_projection"] is None
        assert ledger.context(source)["source_entry"] == query_entry
        assert streams.get(identifier)["paused"]
        accept(calls, 4, "OnRtnOrder", order())
        accept(calls, 5, "OnRtnTrade", second_fill)
        assert ledger.get(command) == first  # Reading the fixed prefix cannot import its new tail.
        second = ledger.get(UUID(ledger.stream_progress(identifier)["entry_id"]))
        assert second["status"] == "READY" and second["new_fill_count"] == 1
        assert second["source_stream"]["after_sequence"] == 4
        assert second["fill_count"] == 2
        assert ledger.ingest_stream(baseline, identifier, 3, request_id=command) == first
        with pytest.raises(ValueError, match="bound"):
            ledger.ingest_stream(baseline, identifier, 5, request_id=command)
        with pytest.raises(ValueError, match="fixed entry"):
            ledger.ingest_stream(baseline, identifier, 3, request_id=uuid4())
    finally:
        streams.close()
    later = ledger_query(
        postgres_engine,
        trades=(first_fill, second_fill),
        positions=(position(),),
        orders=(order(),),
    )
    checked = ledger.compare(UUID(second["entry_id"]), later, request_id=uuid4())
    assert checked["status"] == "MATCHED" and checked["unrecorded_fills"] == []
    orders = OrderReviews(postgres_engine).check(UUID(checked["check_id"]), request_id=uuid4())
    assert orders["status"] == "MATCHED"
    assert orders["orders"][0]["ledger_filled_lots"] == 2
    observed = orders["orders"][0]["observations"]
    assert [item["reported_traded_lots"] for item in observed] == [1, 2, 2]
    assert observed[0]["source_stream_id"] == observed[1]["source_stream_id"] == str(identifier)
    queried = ledger.ingest(baseline, later, request_id=uuid4())
    assert queried["status"] == "READY" and queried["new_fill_count"] == 0
    assert queried["duplicate_count"] == 2 and queried["fill_count"] == 2
    assert (
        ledger.get(command) == first
        and OrderReviews(postgres_engine).get(UUID(orders["check_id"])) == orders
    )
    assert ledger.verify_all() == {
        "position_entries_count": 6,
        "position_checks_count": 1,
    }
    assert calls["count"] == 1


def test_stream_conflicts_unknown_identity_and_source_chronology_do_not_rewrite_fills(
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
        Clock.at = datetime.now(UTC)
        start(streams, source, configuration, identifier)
        assert calls["ready"].wait(3)
        login(calls)
        accept(calls, 2, "OnRtnTrade", trade())
        first = ledger.get(UUID(ledger.stream_progress(identifier)["entry_id"]))
        assert first["status"] == "READY"
        # Same identity with different economics cannot replace the original observation.
        accept(calls, 3, "OnRtnTrade", trade(Price="3200"))
        conflicted = ledger.get(UUID(ledger.stream_progress(identifier)["entry_id"]))
        assert conflicted["fill_count"] == 1 and conflicted["added_fills"] == []
        assert conflicted["status"] == "UNKNOWN"
        assert "TRADE_IDENTITY_CONFLICT" in {item["code"] for item in conflicted["problems"]}
        accept(calls, 4, "OnRtnTrade", trade("FOREIGN", InvestorID="654321"))
        unknown = ledger.get(UUID(ledger.stream_progress(identifier)["entry_id"]))
        assert unknown["position_projection"] == {"status": "UNKNOWN", "positions": []}
        assert unknown["fill_count"] == 1
        assert "STREAM_ACCOUNT_CALLBACK_IDENTITY_NOT_CONFIRMED" in {
            item["code"] for item in unknown["problems"]
        }
        assert streams.events(identifier)[-1]["event"]["data"]["InvestorID"] == "654321"
        assert ledger.get(UUID(first["entry_id"])) == first
    finally:
        streams.close()
    # A later processed prefix cannot be replaced by an older source window.
    with pytest.raises(ValueError, match="ordered non-overlapping"):
        ledger.ingest(baseline, source, request_id=uuid4())
    assert first["execution"] == {"order_sending": False, "cancel_sending": False}

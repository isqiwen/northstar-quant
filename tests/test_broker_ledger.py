"""Confirmed synthetic callbacks exercise accounting, not external trade acceptance."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_broker_baselines import saved_query
from test_broker_records import _capture

from northstar_quant.broker.baselines import BrokerBaselines
from northstar_quant.broker.ledger import BrokerLedger
from northstar_quant.broker.records import BrokerRecords
from northstar_quant.broker.settings import get_profile
from northstar_quant.data.catalog.services import CatalogCommands


def position_baseline(engine: Engine) -> UUID:
    """Register explicitly synthetic product metadata, then fix a saved flat query."""
    with Session(engine) as session, session.begin():
        exchange = CatalogCommands.register_exchange(
            session, code="SHFE", name="Synthetic exchange", timezone_name="Asia/Shanghai"
        )
        CatalogCommands.register_product(
            session,
            exchange_id=exchange.id,
            code="RB",
            name="Synthetic product",
            price_tick=Decimal("1"),
            contract_multiplier=Decimal("10"),
            quantity_unit="TON",
        )
    identifier = uuid4()
    BrokerBaselines(engine).establish(saved_query(engine), request_id=identifier)
    return identifier


def trade(trade_id: str = "T1", **changes: Any) -> dict[str, Any]:
    return {
        "BrokerID": "9999",
        "InvestorID": "123456",
        "InstrumentID": "rb2610",
        "ExchangeID": "SHFE",
        "TradeID": trade_id,
        "OrderSysID": "O1",
        "Direction": "0",
        "OffsetFlag": "0",
        "HedgeFlag": "1",
        "Price": "3100.1",
        "Volume": 2,
        "TradeDate": "20260907",
        "TradeTime": "09:30:00",
        "TradingDay": "20260907",
        **changes,
    }


def position(direction: str = "2", quantity: int = 2, **changes: Any) -> dict[str, Any]:
    return {
        "BrokerID": "9999",
        "InvestorID": "123456",
        "InstrumentID": "rb2610",
        "ExchangeID": "SHFE",
        "TradingDay": "20260907",
        "PosiDirection": direction,
        "HedgeFlag": "1",
        "PositionDate": "1",
        "Position": quantity,
        "TodayPosition": quantity,
        "YdPosition": 0,
        **changes,
    }


def ledger_query(
    engine: Engine,
    *,
    trades: tuple[dict[str, Any], ...] = (),
    positions: tuple[dict[str, Any], ...] = (),
    async_trades: tuple[dict[str, Any], ...] = (),
    day: str = "20260907",
    failure: str | None = None,
    profile: str = "simnow_dev",
    account: str = "123456",
) -> UUID:
    """Public saved-query writer with copied synthetic CTP rows, no broker/credentials."""
    records, identifier = BrokerRecords(engine), uuid4()
    records.begin(get_profile(profile).identity(), account, "rb2610", request_id=identifier)
    capture = _capture(profile=profile, account=account)
    events = []
    for event in capture.events:
        data = None if event.data is None else dict(event.data)
        if data is not None and "TradingDay" in data:
            data["TradingDay"] = day
        if event.callback == "OnRspQryInstrument" and data is not None:
            data.update(
                ProductClass="1",
                ProductID="rb",
                DeliveryYear=2026,
                DeliveryMonth=10,
                OpenDate="20251016",
                ExpireDate="20261015",
            )
        rows = (
            trades
            if event.callback == "OnRspQryTrade"
            else positions
            if event.callback == "OnRspQryInvestorPosition"
            else None
        )
        if rows:
            for index, row in enumerate(rows):
                events.append(
                    replace(
                        event,
                        sequence=len(events) + 1,
                        data=dict(row),
                        is_last=index == len(rows) - 1,
                        received_at=capture.started_at,
                    )
                )
        else:
            events.append(
                replace(event, sequence=len(events) + 1, data=data, received_at=capture.started_at)
            )
    for row in async_trades:
        events.append(
            replace(
                events[-1],
                sequence=len(events) + 1,
                channel="TD",
                callback="OnRtnTrade",
                request_id=None,
                is_last=None,
                error_id=0,
                data=dict(row),
            )
        )
    records.finish(
        identifier,
        replace(
            capture,
            events=tuple(events),
            failure_code=failure,
            finished_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        ),
    )
    return identifier


def test_confirmed_fills_keep_gross_sides_partial_fills_and_deduplicate_across_queries(
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    del clean_database
    baseline = position_baseline(postgres_engine)
    ledger = BrokerLedger(postgres_engine)
    first_fill = trade()
    first = ledger_query(postgres_engine, trades=(first_fill,), async_trades=(first_fill,))
    original = BrokerRecords(postgres_engine).get(first)
    command = uuid4()
    entry = ledger.ingest(baseline, first, request_id=command)
    assert entry["status"] == "READY"
    assert entry["fill_count"] == 1 and entry["duplicate_count"] == 1
    assert entry["added_fills"][0]["price"] == "3100.1"
    assert entry["added_fills"][0]["fee"] is None and entry["cash_projection"] is None
    assert ledger.ingest(baseline, first, request_id=command) == entry
    # One order has several fills; opposite OPEN must retain both gross sides.
    fills = (
        first_fill,
        trade("T2", Volume=1),
        trade("T3", Direction="1", Volume=1),
        trade("T4", Direction="1", OffsetFlag="3", Volume=1),
    )
    second = ledger_query(postgres_engine, trades=fills)
    next_entry = ledger.ingest(baseline, second, request_id=uuid4())
    assert next_entry["fill_count"] == 4 and next_entry["new_fill_count"] == 3
    assert next_entry["duplicate_count"] == 1
    amounts = {item["direction"]: item for item in next_entry["position_projection"]["positions"]}
    assert amounts["LONG"]["today_lots"] == 2
    assert amounts["SHORT"]["today_lots"] == 1
    assert all(item["yesterday_lots"] == 0 for item in amounts.values())
    later = ledger_query(postgres_engine, trades=fills, positions=(position(), position("3", 1)))
    check = ledger.compare(UUID(next_entry["entry_id"]), later, request_id=uuid4())
    assert check["status"] == "MATCHED" and check["unrecorded_fills"] == []
    assert check["reconciliation"] == "UNRECONCILED"
    assert check["execution"] == {"order_sending": False, "cancel_sending": False}
    assert BrokerRecords(postgres_engine).get(first) == original
    assert BrokerLedger(postgres_engine).get(command) == entry
    assert ledger.context(first)["source_entry"] == entry


def test_same_trade_id_opposite_sides_are_not_silently_deduplicated(
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    del clean_database
    baseline = position_baseline(postgres_engine)
    source = ledger_query(postgres_engine, trades=(trade(), trade(Direction="1", OrderSysID="O2")))
    entry = BrokerLedger(postgres_engine).ingest(baseline, source, request_id=uuid4())
    assert entry["fill_count"] == 2
    assert [item["today_lots"] for item in entry["position_projection"]["positions"]] == [2, 2]


@pytest.mark.parametrize(
    "change,code",
    [
        ({"Volume": 0}, "TRADE_FIELDS_NOT_CONFIRMED"),
        ({"Price": "NaN"}, "TRADE_FIELDS_NOT_CONFIRMED"),
        ({"OffsetFlag": "2"}, "UNSUPPORTED_POSITION_EFFECT"),
        ({"OffsetFlag": "3"}, "POSITION_EFFECTS_CANNOT_BE_RESOLVED"),
        ({"OffsetFlag": "4"}, "POSITION_EFFECTS_CANNOT_BE_RESOLVED"),
        ({"HedgeFlag": "3"}, "UNSUPPORTED_POSITION_EFFECT"),
        ({"InstrumentID": "cu2610"}, "CANONICAL_CONTRACT_NOT_CONFIRMED"),
    ],
)
def test_unsupported_or_incomplete_facts_never_create_zero_fees_or_reverse_positions(
    postgres_engine: Engine,
    clean_database: None,
    change: dict[str, Any],
    code: str,
) -> None:
    del clean_database
    baseline = position_baseline(postgres_engine)
    source = ledger_query(postgres_engine, trades=(trade(**change),))
    entry = BrokerLedger(postgres_engine).ingest(baseline, source, request_id=uuid4())
    assert entry["status"] == "UNKNOWN"
    assert code in {item["code"] for item in entry["problems"]}
    assert entry["position_projection"] == {"status": "UNKNOWN", "positions": []}
    assert entry["cash_projection"] is None
    assert (
        len(
            BrokerRecords(postgres_engine).get(source)["completeness"]["sections"]["trades"]["rows"]
        )
        == 1
    )


def test_identity_conflict_is_retained_without_replacing_the_accepted_fill(
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    del clean_database
    baseline, ledger = position_baseline(postgres_engine), BrokerLedger(postgres_engine)
    first = ledger.ingest(
        baseline, ledger_query(postgres_engine, trades=(trade(),)), request_id=uuid4()
    )
    conflicting = ledger_query(postgres_engine, trades=(trade(Price="3101"),))
    result = ledger.ingest(baseline, conflicting, request_id=uuid4())
    assert result["fill_count"] == 1 and result["added_fills"] == []
    assert result["status"] == "UNKNOWN"
    assert "TRADE_IDENTITY_CONFLICT" in {item["code"] for item in result["problems"]}
    assert ledger.get(UUID(first["entry_id"])) == first


def test_later_new_trades_and_other_contract_positions_show_differences_not_auto_ingestion(
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    del clean_database
    baseline, ledger = position_baseline(postgres_engine), BrokerLedger(postgres_engine)
    source = ledger_query(postgres_engine)
    entry_id = uuid4()
    entry = ledger.ingest(baseline, source, request_id=entry_id)
    later = ledger_query(
        postgres_engine, trades=(trade(),), positions=(position(), position(InstrumentID="cu2610"))
    )
    result = ledger.compare(entry_id, later, request_id=uuid4())
    assert result["status"] == "DIFFERENCES"
    assert len(result["unrecorded_fills"]) == 1
    assert {item["symbol"] for item in result["positions"]} == {"RB2610", "CU2610"}
    assert all(item["delta_today"] == 2 for item in result["positions"])
    assert ledger.get(entry_id) == entry and entry["fill_count"] == 0
    assert ledger.verify_all() == {"position_entries_count": 1, "position_checks_count": 1}


def test_independence_scope_idempotency_and_same_query_concurrency(
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    del clean_database
    baseline, ledger = position_baseline(postgres_engine), BrokerLedger(postgres_engine)
    source = ledger_query(postgres_engine)
    premature = ledger_query(postgres_engine)
    entry_id = uuid4()
    with ThreadPoolExecutor(max_workers=2) as executor:
        entries = list(
            executor.map(lambda _: ledger.ingest(baseline, source, request_id=entry_id), range(2))
        )
    assert entries[0] == entries[1]
    for query in (source, premature):
        with pytest.raises(ValueError, match="independent"):
            ledger.compare(entry_id, query, request_id=uuid4())
    with pytest.raises(ValueError, match="already has"):
        ledger.ingest(baseline, source, request_id=uuid4())
    other = ledger_query(postgres_engine, account="234567")
    with pytest.raises(ValueError, match="environment and account"):
        ledger.ingest(baseline, other, request_id=uuid4())
    with pytest.raises(ValueError, match="environment and account"):
        ledger.compare(entry_id, other, request_id=uuid4())
    later = ledger_query(postgres_engine)
    check_id = uuid4()
    result = ledger.compare(entry_id, later, request_id=check_id)
    assert ledger.compare(entry_id, later, request_id=check_id) == result
    assert BrokerLedger(postgres_engine).get_check(check_id) == result
    assert ledger.context(later)["current_check"] == result
    with pytest.raises(ValueError, match="bound"):
        ledger.compare(entry_id, source, request_id=check_id)


@pytest.mark.parametrize(
    "kind", ["incomplete", "different_day", "missing_position", "missing_trade", "async"]
)
def test_unknown_observations_never_report_quantity_match(
    postgres_engine: Engine,
    clean_database: None,
    kind: str,
) -> None:
    del clean_database
    baseline, ledger = position_baseline(postgres_engine), BrokerLedger(postgres_engine)
    entry_id = uuid4()
    ledger.ingest(baseline, ledger_query(postgres_engine, trades=(trade(),)), request_id=entry_id)
    later = ledger_query(
        postgres_engine,
        trades=() if kind == "missing_trade" else (trade(),),
        positions=(position(TodayPosition=None),) if kind == "missing_position" else (position(),),
        failure="SDK_UNAVAILABLE" if kind == "incomplete" else None,
        day="20260908" if kind == "different_day" else "20260907",
        async_trades=(trade(),) if kind == "async" else (),
    )
    result = ledger.compare(entry_id, later, request_id=uuid4())
    assert result["status"] == "UNKNOWN" and result["problems"]
    assert result["reconciliation"] == "UNRECONCILED"


def test_immutable_chain_and_index_integrity_survive_restart_and_detect_corruption(
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    del clean_database
    baseline, ledger = position_baseline(postgres_engine), BrokerLedger(postgres_engine)
    identifier = uuid4()
    ledger.ingest(baseline, ledger_query(postgres_engine, trades=(trade(),)), request_id=identifier)
    with pytest.raises(DBAPIError, match="immutable"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM broker_position_entries WHERE entry_id=:id"), {"id": identifier}
            )
    with postgres_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(
            text("UPDATE broker_position_entries SET source_batch_id=:source WHERE entry_id=:id"),
            {"id": identifier, "source": uuid4()},
        )
    with pytest.raises(ValueError, match="index"):
        BrokerLedger(postgres_engine).get(identifier)


def test_later_open_cannot_hide_an_earlier_close_without_established_inventory(
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    del clean_database
    baseline, ledger = position_baseline(postgres_engine), BrokerLedger(postgres_engine)
    source = ledger_query(
        postgres_engine,
        trades=(
            trade("close", Direction="1", OffsetFlag="3", TradeTime="10:00:00"),
            trade("open", TradeTime="10:05:00"),
        ),
    )
    entry = ledger.ingest(baseline, source, request_id=uuid4())
    assert entry["fill_count"] == 2
    assert entry["position_projection"]["status"] == "UNKNOWN"
    assert "POSITION_EFFECTS_CANNOT_BE_RESOLVED" in {item["code"] for item in entry["problems"]}


def test_conflicting_new_trade_observations_remain_unknown_without_changing_ledger(
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    del clean_database
    baseline, ledger = position_baseline(postgres_engine), BrokerLedger(postgres_engine)
    entry = ledger.ingest(baseline, ledger_query(postgres_engine), request_id=uuid4())
    later = ledger_query(postgres_engine, trades=(trade(), trade(Price="3200")))
    comparison = ledger.compare(UUID(entry["entry_id"]), later, request_id=uuid4())
    assert comparison["status"] == "UNKNOWN"
    assert "TRADE_IDENTITY_CONFLICT" in {item["code"] for item in comparison["problems"]}
    assert ledger.get(UUID(entry["entry_id"])) == entry


def test_complete_query_omitting_previous_trade_cannot_silently_advance_a_ready_ledger(
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    del clean_database
    baseline, ledger = position_baseline(postgres_engine), BrokerLedger(postgres_engine)
    first = ledger.ingest(
        baseline, ledger_query(postgres_engine, trades=(trade(),)), request_id=uuid4()
    )
    later = ledger.ingest(baseline, ledger_query(postgres_engine), request_id=uuid4())
    assert later["fill_count"] == 1 and later["added_fills"] == []
    assert later["status"] == "UNKNOWN"
    assert "RECORDED_TRADES_MISSING_FROM_LATER_QUERY" in {
        item["code"] for item in later["problems"]
    }
    assert ledger.get(UUID(first["entry_id"])) == first


def test_missing_canonical_contract_is_detected_without_repairing_catalog(
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    del clean_database
    baseline, ledger = position_baseline(postgres_engine), BrokerLedger(postgres_engine)
    entry = ledger.ingest(
        baseline, ledger_query(postgres_engine, trades=(trade(),)), request_id=uuid4()
    )
    contract_id = UUID(entry["added_fills"][0]["contract_id"])
    from northstar_quant.data.catalog.models import FuturesContract

    with Session(postgres_engine) as session, session.begin():
        session.execute(text("SET LOCAL session_replication_role = replica"))
        contract = session.get(FuturesContract, contract_id)
        assert contract is not None
        session.delete(contract)
    with pytest.raises(ValueError):
        BrokerLedger(postgres_engine).verify_all()
    with Session(postgres_engine) as session:
        assert session.get(FuturesContract, contract_id) is None

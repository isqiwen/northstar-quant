"""Saved synthetic order observations exercise matching, never external execution."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from test_broker_ledger import ledger_query, position, position_baseline, trade

from northstar_quant.broker.ledger import BrokerLedger


def order(**changes: Any) -> dict[str, Any]:
    """One explicit synthetic SHFE order; no actual account or broker connection."""
    return {
        "BrokerID": "9999",
        "InvestorID": "123456",
        "TradingDay": "20260907",
        "ExchangeID": "SHFE",
        "InstrumentID": "rb2610",
        "OrderSysID": "O1",
        "FrontID": 1,
        "SessionID": 2,
        "OrderRef": "0001",
        "Direction": "0",
        "CombOffsetFlag": "0",
        "CombHedgeFlag": "1",
        "LimitPrice": "3101",
        "OrderPriceType": "2",
        "TimeCondition": "3",
        "VolumeCondition": "1",
        "MinVolume": 1,
        "VolumeTotalOriginal": 2,
        "VolumeTraded": 2,
        "VolumeTotal": 0,
        "OrderStatus": "0",
        "OrderSubmitStatus": "3",
        **changes,
    }


def _parent(
    engine: Engine,
    *,
    fills: tuple[dict[str, Any], ...],
    latest_orders: tuple[dict[str, Any], ...],
    source_orders: tuple[dict[str, Any], ...] = (),
    new_fills: tuple[dict[str, Any], ...] = (),
    duplicate_source_fills: tuple[dict[str, Any], ...] = (),
    async_orders: tuple[dict[str, Any], ...] = (),
    latest_failure: str | None = None,
) -> tuple[BrokerLedger, UUID, dict[str, Any]]:
    """Fix long-opening fills, then an independent public quantity comparison."""
    baseline, ledger = position_baseline(engine), BrokerLedger(engine)
    source = ledger_query(
        engine, trades=fills, orders=source_orders, async_trades=duplicate_source_fills
    )
    entry = ledger.ingest(baseline, source, request_id=uuid4())
    observed = fills + new_fills
    later = ledger_query(
        engine,
        trades=observed,
        positions=(position(quantity=sum(fill["Volume"] for fill in observed)),)
        if observed
        else (),
        orders=latest_orders,
        async_orders=async_orders,
        failure=latest_failure,
    )
    parent_id = uuid4()
    ledger.compare(UUID(entry["entry_id"]), later, request_id=parent_id)
    return ledger, parent_id, entry


@pytest.mark.parametrize(
    "submit,description", [("1", "CANCEL_SUBMITTED"), ("5", "CANCEL_REJECTED")]
)
def test_partial_order_remains_active_when_cancel_is_submitted_or_rejected(
    postgres_engine: Engine, clean_database: None, submit: str, description: str
) -> None:
    del clean_database
    ledger, parent, _ = _parent(
        postgres_engine,
        fills=(trade(Volume=1),),
        latest_orders=(
            order(VolumeTraded=1, VolumeTotal=1, OrderStatus="1", OrderSubmitStatus=submit),
        ),
    )
    result = ledger.check_orders(parent, request_id=uuid4())
    assert result["status"] == "MATCHED"
    observed = result["orders"][0]
    assert observed["order_state"] == "PART_TRADED_QUEUEING"
    assert observed["submit_state"] == description and observed["active"] is True
    assert observed["ledger_filled_lots"] == 1 and observed["fill_gap_lots"] == 0
    assert observed["ownership"] == "EXTERNAL_NOT_OWNED"
    assert observed["reservation_release"] == "NOT_AUTHORIZED"
    assert result["reconciliation"] == "UNRECONCILED"
    assert result["execution"] == {"order_sending": False, "cancel_sending": False}


@pytest.mark.parametrize(
    "reported,fill_volume,gap",
    [
        (order(VolumeTotalOriginal=3, VolumeTraded=2, VolumeTotal=1, OrderStatus="5"), 1, 1),
        (order(VolumeTraded=1, VolumeTotal=1, OrderStatus="1"), 2, -1),
    ],
)
def test_cancellation_and_cumulative_counts_do_not_erase_signed_fill_gaps(
    postgres_engine: Engine,
    clean_database: None,
    reported: dict[str, Any],
    fill_volume: int,
    gap: int,
) -> None:
    del clean_database
    ledger, parent, _ = _parent(
        postgres_engine, fills=(trade(Volume=fill_volume),), latest_orders=(reported,)
    )
    result = ledger.check_orders(parent, request_id=uuid4())
    assert result["status"] == "DIFFERENCES"
    observed = result["orders"][0]
    assert observed["fill_gap_lots"] == gap
    assert observed["ledger_filled_lots"] == fill_volume
    if reported["OrderStatus"] == "5":
        assert observed["order_state"] == "CANCELED" and observed["active"] is False
    assert observed["reservation_release"] == "NOT_AUTHORIZED"


def test_duplicate_receipts_and_new_query_fills_cannot_fill_their_own_ledger_gap(
    postgres_engine: Engine, clean_database: None
) -> None:
    del clean_database
    first, second = trade(Volume=1), trade("T2", Volume=1)
    ledger, parent, entry = _parent(
        postgres_engine,
        fills=(first,),
        duplicate_source_fills=(first,),
        new_fills=(second,),
        latest_orders=(order(),),
    )
    assert entry["fill_count"] == 1 and entry["duplicate_count"] == 1
    result = ledger.check_orders(parent, request_id=uuid4())
    assert result["status"] == "DIFFERENCES"
    observed = result["orders"][0]
    assert observed["reported_traded_lots"] == 2
    assert observed["ledger_filled_lots"] == 1 and observed["fill_gap_lots"] == 1
    assert len(observed["ledger_fill_ids"]) == len(observed["unrecorded_fill_ids"]) == 1
    assert observed["ledger_fill_ids"] != observed["unrecorded_fill_ids"]
    assert ledger.get(UUID(entry["entry_id"])) == entry


def test_exchange_id_leading_zeros_and_reused_order_ref_in_other_sessions_remain_distinct(
    postgres_engine: Engine, clean_database: None
) -> None:
    del clean_database
    ledger, parent, _ = _parent(
        postgres_engine,
        fills=(trade(OrderSysID="001", Volume=1), trade("T2", OrderSysID="1", Volume=1)),
        latest_orders=(
            order(OrderSysID="001", VolumeTotalOriginal=1, VolumeTraded=1),
            order(OrderSysID="1", SessionID=3, VolumeTotalOriginal=1, VolumeTraded=1),
        ),
    )
    result = ledger.check_orders(parent, request_id=uuid4())
    assert result["status"] == "MATCHED"
    assert {row["order_sys_id"] for row in result["orders"]} == {"001", "1"}
    assert {tuple(row["client_identity"]) for row in result["orders"]} == {
        (1, 2, "0001"),
        (1, 3, "0001"),
    }
    assert all(
        row["ledger_filled_lots"] == 1 and row["fill_gap_lots"] == 0 for row in result["orders"]
    )


def test_empty_complete_order_query_does_not_hide_an_unlinked_recorded_fill(
    postgres_engine: Engine, clean_database: None
) -> None:
    del clean_database
    ledger, parent, _ = _parent(postgres_engine, fills=(trade(),), latest_orders=())
    assert ledger.get_check(parent)["status"] == "MATCHED"
    result = ledger.check_orders(parent, request_id=uuid4())
    assert result["status"] == "UNKNOWN" and result["orders"] == []
    assert len(result["unlinked_fills"]) == 1
    assert "RECORDED_FILL_WITHOUT_ORDER" in {item["code"] for item in result["problems"]}


def test_distinct_states_in_one_query_are_not_resolved_by_last_arrival_or_maximum_count(
    postgres_engine: Engine, clean_database: None
) -> None:
    del clean_database
    ledger, parent, _ = _parent(
        postgres_engine,
        fills=(trade(),),
        latest_orders=(order(VolumeTraded=1, VolumeTotal=1, OrderStatus="1"), order()),
    )
    result = ledger.check_orders(parent, request_id=uuid4())
    assert result["status"] == "UNKNOWN"
    assert "ORDER_OBSERVATIONS_AMBIGUOUS" in {item["code"] for item in result["problems"]}
    observed = result["orders"][0]
    assert len(observed["observations"]) == 2 and observed["active"] is None
    assert {item["reported_traded_lots"] for item in observed["observations"]} == {1, 2}


@pytest.mark.parametrize(
    "previous,current,problem",
    [
        (
            order(VolumeTraded=1, VolumeTotal=1, OrderStatus="5"),
            order(VolumeTraded=1, VolumeTotal=1, OrderStatus="1"),
            "ORDER_TERMINAL_STATE_CHANGED",
        ),
        (
            order(VolumeTotalOriginal=3, VolumeTraded=2, VolumeTotal=1, OrderStatus="1"),
            order(VolumeTotalOriginal=3, VolumeTraded=1, VolumeTotal=2, OrderStatus="1"),
            "ORDER_CUMULATIVE_VOLUME_REGRESSED",
        ),
    ],
)
def test_terminal_reopening_or_decreasing_cumulative_volume_retains_conflicting_history(
    postgres_engine: Engine,
    clean_database: None,
    previous: dict[str, Any],
    current: dict[str, Any],
    problem: str,
) -> None:
    del clean_database
    ledger, parent, _ = _parent(
        postgres_engine,
        fills=(trade(Volume=1),),
        source_orders=(previous,),
        latest_orders=(current,),
    )
    result = ledger.check_orders(parent, request_id=uuid4())
    assert result["status"] == "UNKNOWN"
    assert problem in {item["code"] for item in result["problems"]}
    assert result["orders"][0]["active"] is None
    assert len(result["orders"][0]["observations"]) == 2


def test_missing_previous_order_is_not_reinterpreted_as_cancelled_or_absent_risk(
    postgres_engine: Engine, clean_database: None
) -> None:
    del clean_database
    ledger, parent, _ = _parent(
        postgres_engine,
        fills=(trade(Volume=1),),
        source_orders=(order(VolumeTraded=1, VolumeTotal=1, OrderStatus="1"),),
        latest_orders=(),
    )
    result = ledger.check_orders(parent, request_id=uuid4())
    assert result["status"] == "UNKNOWN"
    assert "PREVIOUS_ORDER_MISSING_FROM_QUERY" in {item["code"] for item in result["problems"]}
    observed = result["orders"][0]
    assert observed["order_state"] == "PART_TRADED_QUEUEING"
    assert observed["seen_in_query"] is False and observed["active"] is None
    assert observed["reservation_release"] == "NOT_AUTHORIZED"


def test_entries_created_after_fixed_parent_never_pollute_its_order_comparison(
    postgres_engine: Engine, clean_database: None
) -> None:
    del clean_database
    first = trade()
    ledger, parent, entry = _parent(
        postgres_engine, fills=(first,), source_orders=(order(),), latest_orders=(order(),)
    )
    fixed_parent = ledger.get_check(parent)
    later = ledger_query(
        postgres_engine,
        trades=(first, trade("T2", OrderSysID="O2", Volume=1)),
        orders=(
            order(),
            order(OrderSysID="O2", OrderRef="0002", VolumeTotalOriginal=1, VolumeTraded=1),
        ),
    )
    newer = ledger.ingest(UUID(entry["baseline_id"]), later, request_id=uuid4())
    assert newer["fill_count"] == 2
    command = uuid4()
    result = ledger.check_orders(parent, request_id=command)
    assert result["status"] == "MATCHED"
    assert result["entry_id"] == entry["entry_id"]
    assert [row["order_sys_id"] for row in result["orders"]] == ["O1"]
    assert result["orders"][0]["ledger_filled_lots"] == 2
    assert ledger.get_check(parent) == fixed_parent
    assert BrokerLedger(postgres_engine).get_order_check(command) == result


@pytest.mark.parametrize(
    "changes", [{"OrderSysID": ""}, {"VolumeTraded": None}, {"OrderStatus": None}]
)
def test_unknown_order_fields_retain_original_observation_without_zero_filling(
    postgres_engine: Engine, clean_database: None, changes: dict[str, Any]
) -> None:
    del clean_database
    reported = order(**changes)
    ledger, parent, _ = _parent(postgres_engine, fills=(), latest_orders=(reported,))
    result = ledger.check_orders(parent, request_id=uuid4())
    assert result["status"] == "UNKNOWN" and result["orders"] == []
    unresolved = result["unresolved_observations"]
    assert len(unresolved) == 1
    assert unresolved[0]["reported_fields"]["OrderRef"] == "0001"
    for key, value in changes.items():
        assert unresolved[0]["reported_fields"][key] == value


@pytest.mark.parametrize(
    "cause", ["parent_failed", "missing_field", "nonconserving_volume", "conflicting_status"]
)
def test_incomplete_or_conflicting_evidence_never_confirms_order_inactivity(
    postgres_engine: Engine, clean_database: None, cause: str
) -> None:
    del clean_database
    reported = {
        "parent_failed": (order(),),
        "missing_field": (order(), order(VolumeTraded=None)),
        "nonconserving_volume": (order(), order(VolumeTraded=1, VolumeTotal=0)),
        "conflicting_status": (order(VolumeTraded=1, VolumeTotal=1),),
    }[cause]
    ledger, parent, _ = _parent(
        postgres_engine,
        fills=(trade(),),
        latest_orders=reported,
        latest_failure="QUERY_TIMEOUT" if cause == "parent_failed" else None,
    )
    if cause == "parent_failed":
        assert ledger.get_check(parent)["status"] == "UNKNOWN"
    result = ledger.check_orders(parent, request_id=uuid4())
    assert result["status"] == "UNKNOWN"
    assert result["orders"][0]["order_state"] == "FILLED"
    assert result["orders"][0]["active"] is None
    assert result["orders"][0]["reservation_release"] == "NOT_AUTHORIZED"
    if cause in {"missing_field", "nonconserving_volume"}:
        assert result["unresolved_observations"][0]["reported_fields"] == reported[1]
    if cause == "conflicting_status":
        assert "ORDER_STATUS_QUANTITY_CONFLICT" in {item["code"] for item in result["problems"]}


@pytest.mark.parametrize("instrument", ["rb2701", "rb2610C3100"])
def test_unfilled_order_without_exact_confirmed_futures_metadata_stays_unknown(
    postgres_engine: Engine, clean_database: None, instrument: str
) -> None:
    del clean_database
    ledger, parent, _ = _parent(
        postgres_engine,
        fills=(),
        latest_orders=(
            order(InstrumentID=instrument, VolumeTraded=0, VolumeTotal=2, OrderStatus="3"),
        ),
    )
    result = ledger.check_orders(parent, request_id=uuid4())
    assert result["status"] == "UNKNOWN"
    observed = result["orders"][0]
    assert observed["symbol"] == instrument.upper()
    assert observed["order_state"] == "NO_TRADE_QUEUEING" and observed["active"] is None
    assert observed["ledger_filled_lots"] == 0 and observed["fill_gap_lots"] == 0
    assert "ORDER_INSTRUMENT_NOT_CONFIRMED" in {item["code"] for item in result["problems"]}


def test_order_review_is_idempotent_immutable_and_checks_its_fixed_parent_evidence(
    postgres_engine: Engine, clean_database: None
) -> None:
    del clean_database
    ledger, parent, entry = _parent(postgres_engine, fills=(), latest_orders=())
    command = uuid4()
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(lambda _: ledger.check_orders(parent, request_id=command), range(2))
        )
    assert results[0] == results[1] == ledger.get_order_check(command)
    other_parent = uuid4()
    ledger.compare(UUID(entry["entry_id"]), ledger_query(postgres_engine), request_id=other_parent)
    with pytest.raises(ValueError, match="bound"):
        ledger.check_orders(other_parent, request_id=command)
    with pytest.raises(ValueError, match="already has"):
        ledger.check_orders(parent, request_id=uuid4())
    with pytest.raises(DBAPIError, match="immutable"):
        with postgres_engine.begin() as connection:
            connection.execute(
                text("DELETE FROM broker_order_checks WHERE check_id=:id"), {"id": command}
            )
    with postgres_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(
            text("UPDATE broker_position_checks SET sha256=repeat('0',64) WHERE check_id=:id"),
            {"id": parent},
        )
    with pytest.raises(ValueError, match="damaged"):
        BrokerLedger(postgres_engine).get_order_check(command)
    with postgres_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(
            text("DELETE FROM broker_position_checks WHERE check_id=:id"), {"id": parent}
        )
    with pytest.raises(ValueError, match="parent evidence is missing"):
        ledger.get_order_check(command)

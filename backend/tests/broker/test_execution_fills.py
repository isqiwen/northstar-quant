"""Retained synthetic CTP trades through the real single core and local account/OMS."""

from datetime import UTC, datetime

import pytest

from northstar_quant.accounting.ledger import BrokerLedger
from northstar_quant.broker.events import BrokerEvent
from northstar_quant.broker.execution_fills import apply_pending, apply_stream, verify_all
from tests.accounting.test_ledger import trade
from tests.broker.test_execution_reports import receiving as receiving


def execution(sequence, *, trade_id="t1", **changes):
    return BrokerEvent(
        sequence,
        "TD",
        "OnRtnTrade",
        None,
        None,
        datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        0,
        trade(trade_id, **{"Price": "100", "Volume": 1, "OrderSysID": "sys1", **changes}),
    )


def test_native_fill_and_unknown_fee_follow_account_prefix_and_do_not_duplicate(
    live_engine, receiving
):
    adapter, order, stream_id, accept, report, _ = receiving
    accept(report(3))
    accept(execution(4))
    state = adapter.journal.get(order.order_id)
    assert state["filled_lots"] == 1 and state["fee_pending_lots"] == 1
    assert state["reservation"]["reserved_fee"] == "6"
    assert state["reservation"]["reserved_margin"] == "202"
    assert state["requires_reconciliation"]
    assert verify_all(live_engine) == 1 and adapter.journal.verify_all() == 1
    accept(execution(5))
    assert adapter.journal.get(order.order_id) == state
    assert verify_all(live_engine) == 1
    assert BrokerLedger(live_engine).stream_progress(stream_id)["through_sequence"] == 5
    with live_engine.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT count(*) FROM ctp_fill_receipts").scalar_one() == 2
        )
    accept(report(6, state="5", traded=1))
    state = adapter.journal.get(order.order_id)
    assert state["status"] == "CANCELED" and state["reservation"]["reserved_margin"] == "0"
    assert state["reservation"]["reserved_fee"] == "2"
    assert state["requires_reconciliation"]
    assert adapter.journal.verify_all() == 1 and verify_all(live_engine) == 1


def test_trade_before_order_identity_stays_raw_then_catches_up_once(live_engine, receiving):
    adapter, order, stream_id, accept, report, _ = receiving
    accept(execution(3))
    assert adapter.journal.get(order.order_id)["filled_lots"] == 0
    assert verify_all(live_engine) == 0
    accept(report(4, state="1", traded=1))
    assert adapter.journal.get(order.order_id)["filled_lots"] == 1
    assert verify_all(live_engine) == 1
    assert apply_pending(live_engine, stream_id, 4) == 0
    assert apply_stream(live_engine, stream_id, 3) is not None
    assert adapter.journal.get(order.order_id)["filled_lots"] == 1


def test_account_failure_rolls_back_fill_association_but_retains_raw_source(
    live_engine, receiving, monkeypatch
):
    adapter, order, stream_id, accept, report, _ = receiving
    accept(report(3))
    original = BrokerLedger.advance_stream

    def fail(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if kwargs.get("transaction") is not None:
            raise RuntimeError("after account write")
        return result

    monkeypatch.setattr(BrokerLedger, "advance_stream", fail)
    with pytest.raises(RuntimeError, match="after account write"):
        accept(execution(4))
    assert adapter.journal.get(order.order_id)["filled_lots"] == 0
    assert verify_all(live_engine) == 0
    assert BrokerLedger(live_engine).stream_progress(stream_id)["through_sequence"] == 3
    with live_engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT count(*) FROM broker_stream_events WHERE stream_id=? AND sequence=4",
                (stream_id.hex,),
            ).scalar_one()
            == 1
        )
    monkeypatch.setattr(BrokerLedger, "advance_stream", original)
    assert apply_stream(live_engine, stream_id, 4) is not None
    assert adapter.journal.get(order.order_id)["filled_lots"] == 1
    assert verify_all(live_engine) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"InvestorID": "other"},
        {"InstrumentID": "ag2610"},
        {"Direction": "1"},
        {"OffsetFlag": "3"},
        {"HedgeFlag": "2"},
    ],
)
def test_conflicting_fixed_order_fields_stay_raw_without_local_fill(
    live_engine, receiving, changes
):
    adapter, order, stream_id, accept, report, _ = receiving
    accept(report(3))
    with pytest.raises(ValueError):
        accept(execution(4, **changes))
    assert adapter.journal.get(order.order_id)["filled_lots"] == 0
    assert verify_all(live_engine) == 0
    with live_engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT count(*) FROM broker_stream_events WHERE stream_id=? AND sequence=4",
                (stream_id.hex,),
            ).scalar_one()
            == 1
        )


def test_conflicting_duplicate_trade_preserves_original_fill_and_raw_conflict(
    live_engine, receiving
):
    adapter, order, stream_id, accept, report, _ = receiving
    accept(report(3))
    accept(execution(4))
    with pytest.raises(ValueError, match="conflicting facts"):
        accept(execution(5, Price="101"))
    assert adapter.journal.get(order.order_id)["filled_lots"] == 1
    assert verify_all(live_engine) == 1
    with live_engine.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT count(*) FROM ctp_fill_receipts").scalar_one() == 1
        )
        assert (
            connection.exec_driver_sql(
                "SELECT count(*) FROM broker_stream_events WHERE stream_id=? AND sequence=5",
                (stream_id.hex,),
            ).scalar_one()
            == 1
        )


def test_original_receipt_is_required_after_restart_even_if_oms_fill_survives(
    live_engine, receiving
):
    adapter, order, _, accept, report, _ = receiving
    accept(report(3))
    accept(execution(4))
    with live_engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER immutable_ctp_fill_receipts_DELETE")
        connection.exec_driver_sql("DELETE FROM ctp_fill_receipts")
    assert adapter.journal.verify_all() == 1
    with pytest.raises(ValueError, match="original account receipt"):
        verify_all(live_engine)


def test_matched_execution_account_prefix_and_pending_fee_restore_together(
    live_engine, receiving, tmp_path
):
    from uuid import uuid4

    from northstar_quant.apps.live.maintenance import backup, restore
    from northstar_quant.data_management.files import SourceFiles
    from northstar_quant.execution.journal import OrderJournal
    from northstar_quant.live.storage import open_store

    adapter, order, stream_id, accept, report, _ = receiving
    accept(execution(3))
    accept(report(4, state="1", traded=1))
    accept(execution(5))
    expected = adapter.journal.get(order.order_id)
    destination = tmp_path / "fills-backup"
    evidence = backup(live_engine, SourceFiles(tmp_path / "archive"), destination)
    assert evidence["evidence"]["ctp_fills_count"] == 1
    target = open_store(tmp_path / "restored.sqlite")
    try:
        result = restore(target, tmp_path / "restored-sources", destination)
        assert result["execution"] == "RECONCILIATION_REQUIRED"
        assert verify_all(target) == 1
        reopened = OrderJournal(target, uuid4())
        assert reopened.get(order.order_id) == expected
        assert reopened.get(order.order_id)["fee_pending_lots"] == 1
        assert apply_pending(target, stream_id, 5) == 0
        assert reopened.verify_all() == 1
    finally:
        target.dispose()

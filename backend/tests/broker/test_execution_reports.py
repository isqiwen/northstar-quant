"""Copied CTP reports through the actual Live core and SQLite, never a broker connection."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from northstar_quant.broker.events import BrokerEvent
from northstar_quant.broker.execution_reports import apply_stream, verify_all
from northstar_quant.broker.order_transport import CtpExecution
from northstar_quant.live.streams import LiveStreams
from tests.broker.test_order_transport import instrument, session
from tests.execution.test_journal import fill, request
from tests.live.test_market import OPEN
from tests.live.test_streams import logins, prepare, start


@pytest.fixture
def receiving(live_engine, tmp_path, monkeypatch):
    library, query, config, calls = prepare(live_engine, tmp_path, monkeypatch)
    streams, identifier = LiveStreams(live_engine, library), uuid4()
    try:
        start(streams, query, config, identifier)
        assert calls["ready"].wait(3)
        logins(calls["accept"])
        binding = streams.get(identifier)["binding"]
        adapter = CtpExecution(live_engine, uuid4(), replace(session(), trading_day=OPEN.date()))
        order = replace(request(), contract_id=UUID(binding["contract_id"]))
        sent = []
        adapter.submit(
            order,
            uuid4(),
            instrument(order),
            Decimal(100),
            admit=lambda c: None,
            send=lambda _, fields, __, deadline: sent.append(fields) or 0,
            check_owner=lambda: None,
        )

        def report(sequence, *, state="3", traded=0, submit="3", **changes):
            return BrokerEvent(
                sequence,
                "TD",
                "OnRtnOrder",
                None,
                None,
                datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                0,
                {
                    **sent[0],
                    "FrontID": 7,
                    "SessionID": 99,
                    "TradingDay": OPEN.strftime("%Y%m%d"),
                    "OrderSysID": "sys1",
                    "OrderStatus": state,
                    "OrderSubmitStatus": submit,
                    "VolumeTraded": traded,
                    "VolumeTotal": order.quantity_lots - traded,
                    **changes,
                },
            )

        yield adapter, order, identifier, calls["accept"], report, library
    finally:
        streams.close()


def test_full_client_identity_and_partial_cancel_report_wait_for_fills(live_engine, receiving):
    adapter, order, stream_id, accept, report, _ = receiving
    accept(report(3, FrontID=8))
    assert adapter.journal.get(order.order_id)["status"] == "UNKNOWN"
    assert verify_all(live_engine) == 0
    accept(report(4))
    assert adapter.journal.get(order.order_id)["status"] == "ACCEPTED"
    accept(report(5, state="1", traded=1))
    assert adapter.journal.get(order.order_id)["status"] == "UNKNOWN"
    accept(report(6, state="5", traded=1))
    assert adapter.journal.get(order.order_id)["reservation"]["reserved_margin"] == "303"
    assert verify_all(live_engine) == 3
    # Same received prefix cannot duplicate either the report or exchange identity.
    apply_stream(live_engine, stream_id, 4)
    apply_stream(live_engine, stream_id, 6)
    assert verify_all(live_engine) == 3
    adapter.journal.fill(fill(order, 1), post_account=lambda c, f: None)
    assert adapter.journal.get(order.order_id)["status"] == "CANCELED"
    assert adapter.journal.get(order.order_id)["reservation"]["reserved_margin"] == "0"
    assert adapter.journal.verify_all() == 1
    with live_engine.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT count(*) FROM ctp_exchange_orders").scalar_one() == 1
        )


@pytest.mark.parametrize(
    "changes",
    [
        dict(LimitPrice="101"),
        dict(CombOffsetFlag="3"),
        dict(VolumeTotal=4),
        dict(InvestorID="654321"),
    ],
)
def test_mismatched_report_remains_raw_without_advancing_local_order(
    live_engine, receiving, changes
):
    adapter, order, stream_id, accept, report, _ = receiving
    with pytest.raises(ValueError):
        accept(report(3, **changes))
    assert adapter.journal.get(order.order_id)["status"] == "UNKNOWN"
    assert verify_all(live_engine) == 0
    with live_engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT count(*) FROM broker_stream_events WHERE stream_id=? AND sequence=3",
                (stream_id.hex,),
            ).scalar_one()
            == 1
        )


def test_exchange_identity_conflict_rolls_back_report_and_preserves_source(live_engine, receiving):
    adapter, order, stream_id, accept, report, _ = receiving
    accept(report(3))
    with pytest.raises(ValueError, match="exchange identity changed"):
        accept(report(4, OrderSysID="different"))
    assert adapter.journal.get(order.order_id)["status"] == "ACCEPTED"
    assert verify_all(live_engine) == 1
    assert adapter.journal.verify_all() == 1
    with live_engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT count(*) FROM broker_stream_events WHERE stream_id=?", (stream_id.hex,)
            ).scalar_one()
            == 4
        )


def test_unknown_submission_then_acceptance_is_not_an_invented_rejection(live_engine, receiving):
    adapter, order, _, accept, report, _ = receiving
    accept(report(3, state="a", submit="0", OrderSysID=""))
    assert adapter.journal.get(order.order_id)["status"] == "UNKNOWN"
    accept(report(4))
    assert adapter.journal.get(order.order_id)["status"] == "ACCEPTED"
    assert verify_all(live_engine) == 2 and adapter.journal.verify_all() == 1


@pytest.mark.parametrize("damage", ["exchange", "parent", "source"])
def test_recovery_rejects_receipt_with_missing_identity_or_changed_evidence(
    live_engine, receiving, damage
):
    _, _, stream_id, accept, report, _ = receiving
    accept(report(3))
    assert verify_all(live_engine) == 1
    # Model offline corruption, bypassing the normal immutable write interface.
    with live_engine.begin() as connection:
        if damage == "exchange":
            connection.exec_driver_sql("DROP TRIGGER immutable_ctp_exchange_orders_DELETE")
            connection.exec_driver_sql("DELETE FROM ctp_exchange_orders")
        elif damage == "parent":
            triggers = (
                connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND tbl_name='execution_order_events'"
                )
                .scalars()
                .all()
            )
            for name in triggers:
                connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
            connection.exec_driver_sql(
                "UPDATE execution_order_events SET document='{}' WHERE kind='BROKER_REPORT'"
            )
        else:
            triggers = (
                connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND tbl_name='broker_stream_events'"
                )
                .scalars()
                .all()
            )
            for name in triggers:
                connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
            connection.exec_driver_sql(
                "UPDATE broker_stream_events SET event_hash=? WHERE stream_id=? AND sequence=3",
                ("0" * 64, stream_id.hex),
            )
    with pytest.raises(ValueError):
        verify_all(live_engine)


def test_received_order_identity_survives_joint_sqlite_restore(live_engine, receiving, tmp_path):
    from northstar_quant.apps.live.maintenance import backup, restore
    from northstar_quant.data_management.files import SourceFiles
    from northstar_quant.live.storage import open_store

    adapter, order, _, accept, report, _ = receiving
    accept(report(3))
    destination = tmp_path / "backup"
    evidence = backup(live_engine, SourceFiles(tmp_path / "archive"), destination)
    assert evidence["evidence"]["ctp_receipts_count"] == 1
    target = open_store(tmp_path / "restored.sqlite")
    try:
        restored = restore(target, tmp_path / "restored-sources", destination)
        assert restored["execution"] == "RECONCILIATION_REQUIRED"
        assert verify_all(target) == 1
        from northstar_quant.execution.journal import OrderJournal

        assert OrderJournal(target, uuid4()).get(order.order_id) == adapter.journal.get(
            order.order_id
        )
    finally:
        target.dispose()


def rejection(engine, order, sequence, callback, *, error=31, **changes):
    import json

    kind = "INSERT" if callback.endswith("OrderInsert") else "CANCEL"
    with engine.connect() as connection:
        saved = (
            connection.exec_driver_sql(
                "SELECT * FROM ctp_requests WHERE order_id=? AND kind=? "
                "ORDER BY sequence DESC LIMIT 1",
                (order.order_id, kind),
            )
            .mappings()
            .one()
        )
    return BrokerEvent(
        sequence,
        "TD",
        callback,
        None if callback.startswith("OnErrRtn") else saved["sequence"] + 100_000,
        None if callback.startswith("OnErrRtn") else True,
        datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        error,
        {**json.loads(saved["document"]), "RequestID": saved["sequence"] + 100_000, **changes},
    )


@pytest.mark.parametrize("callback", ["OnRspOrderInsert", "OnErrRtnOrderInsert"])
def test_explicit_insert_rejection_releases_only_unfilled_order(live_engine, receiving, callback):
    adapter, order, _, accept, _, _ = receiving
    accept(rejection(live_engine, order, 3, callback))
    saved = adapter.journal.get(order.order_id)
    assert saved["status"] == "REJECTED"
    assert saved["reservation"]["reserved_margin"] == "0"
    assert verify_all(live_engine) == adapter.journal.verify_all() == 1


def test_cancel_rejection_is_bound_to_attempt_and_does_not_close_order(live_engine, receiving):
    adapter, order, _, accept, report, _ = receiving
    accept(report(3))
    first = uuid4()
    sent = []
    adapter.cancel(
        order.order_id,
        first,
        admit=lambda c: None,
        send=lambda *args: sent.append(args) or 0,
        check_owner=lambda: None,
    )
    accept(rejection(live_engine, order, 4, "OnRspOrderAction"))
    saved = adapter.journal.get(order.order_id)
    assert saved["status"] == "ACCEPTED"
    assert saved["reservation"]["reserved_margin"] == "303"
    adapter.cancel(
        order.order_id,
        uuid4(),
        admit=lambda c: None,
        send=lambda *args: sent.append(args) or 0,
        check_owner=lambda: None,
    )
    assert len(sent) == 2
    assert verify_all(live_engine) == 2 and adapter.journal.verify_all() == 1


@pytest.mark.parametrize("changes", [dict(OrderRef="9999"), dict(InvestorID="654321")])
def test_rejection_with_wrong_request_fields_cannot_release_reserve(
    live_engine, receiving, changes
):
    adapter, order, _, accept, _, _ = receiving
    with pytest.raises(ValueError, match="differs"):
        accept(rejection(live_engine, order, 3, "OnRspOrderInsert", **changes))
    assert adapter.journal.get(order.order_id)["reservation"]["reserved_margin"] == "303"
    assert verify_all(live_engine) == 0


def test_success_response_without_order_fact_does_not_assume_acceptance(live_engine, receiving):
    adapter, order, _, accept, _, _ = receiving
    accept(rejection(live_engine, order, 3, "OnRspOrderInsert", error=0))
    assert adapter.journal.get(order.order_id)["status"] == "UNKNOWN"
    assert verify_all(live_engine) == 0


def test_error_return_without_attempt_identity_remains_unresolved(live_engine, receiving):
    adapter, order, _, accept, _, _ = receiving
    accept(rejection(live_engine, order, 3, "OnErrRtnOrderInsert", RequestID=0))
    assert adapter.journal.get(order.order_id)["status"] == "UNKNOWN"
    assert adapter.journal.get(order.order_id)["reservation"]["reserved_margin"] == "303"
    assert verify_all(live_engine) == 0

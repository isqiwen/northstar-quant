
import pytest
from sqlalchemy.orm import Session
from tests.helpers.durable_submission import RecordingBroker, order_request

from northstar_quant.foundation.common.order_identity import build_order_ref
from northstar_quant.foundation.db.models import OrderRecord
from northstar_quant.foundation.db.repositories import (
    prepare_order_submission,
    update_order_statuses,
)
from northstar_quant.trading_execution.orders.durable_submission import (
    DurableBrokerAdapter,
)

def test_confirmed_terminal_without_broker_order_id_is_replayed(postgresql_engine):
    engine = postgresql_engine
    broker = RecordingBroker()

    with Session(engine, future=True) as session:
        row, _created = prepare_order_submission(
            session,
            order_request(instrument_id="rb2601"),
            broker="ctp",
            account="DU123456",
        )
        row.status = "Filled"
        row.client_id = 7
        row.perm_id = 101
        row.filled_qty = 2.0
        row.remaining_qty = 0.0
        session.commit()

        replay = DurableBrokerAdapter(broker, session).submit_order(
            order_request(instrument_id="rb2601")
        )

    assert replay.replayed is True
    assert replay.status == "FILLED"
    assert replay.broker_order_id == ""
    assert replay.client_id == 7
    assert replay.perm_id == 101
    assert broker.submit_count == 0


def test_unknown_submission_recovers_by_order_ref_without_resubmission(postgresql_engine):
    engine = postgresql_engine
    failing_broker = RecordingBroker(error=TimeoutError("ack lost"))

    with Session(engine, future=True) as session:
        durable = DurableBrokerAdapter(failing_broker, session)
        with pytest.raises(TimeoutError):
            durable.submit_order(order_request())

        updated = update_order_statuses(
            session,
            [
                {
                    "broker_order_id": "broker-recovered",
                    "account": "DU123456",
                    "order_ref": build_order_ref("plan-1", 1),
                    "status": "Filled",
                    "qty": 2.0,
                    "filled_qty": 2.0,
                    "remaining_qty": 0.0,
                }
            ],
            broker="ctp",
            account="DU123456",
        )
        assert updated == 1

        healthy_broker = RecordingBroker()
        replay = DurableBrokerAdapter(healthy_broker, session).submit_order(order_request())

    assert replay.replayed is True
    assert replay.status == "FILLED"
    assert replay.broker_order_id == "broker-recovered"
    assert healthy_broker.submit_count == 0


def test_order_recovery_rejects_mismatched_instrument_identity(postgresql_engine):
    engine = postgresql_engine

    with Session(engine, future=True) as session:
        order = OrderRecord(
            strategy_id="core",
            symbol="RB2405",
            side="BUY",
            qty=2.0,
            broker="ctp",
            account="DU123456",
            instrument_id="rb2601",
            order_ref=build_order_ref("plan-1", 1),
            status="SubmissionUnknown",
        )
        session.add(order)
        session.commit()

        with pytest.raises(
            RuntimeError,
            match="BROKER_ORDER_IDENTITY_MISMATCH",
        ):
            update_order_statuses(
                session,
                [
                    {
                        "broker_order_id": "broker-wrong-contract",
                        "account": "DU123456",
                        "order_ref": build_order_ref("plan-1", 1),
                        "instrument_id": "rb2602",
                        "symbol": "RB2405",
                        "status": "Filled",
                    }
                ],
                broker="ctp",
                account="DU123456",
            )
        session.rollback()
        assert session.get(OrderRecord, order.id).status == "SubmissionUnknown"


def test_order_recovery_uses_client_id_when_order_id_is_reused(postgresql_engine):
    engine = postgresql_engine

    with Session(engine, future=True) as session:
        client_one = OrderRecord(
            strategy_id="core",
            symbol="RB2405",
            side="BUY",
            qty=1.0,
            broker="ctp",
            account="DU123456",
            broker_order_id="42",
            client_id=1,
            status="Submitted",
        )
        client_two = OrderRecord(
            strategy_id="core",
            symbol="I2405",
            side="BUY",
            qty=1.0,
            broker="ctp",
            account="DU123456",
            broker_order_id="42",
            client_id=2,
            status="Submitted",
        )
        session.add_all([client_one, client_two])
        session.commit()

        assert (
            update_order_statuses(
                session,
                [
                    {
                        "broker_order_id": "42",
                        "account": "DU123456",
                        "client_id": 2,
                        "symbol": "I2405",
                        "status": "Filled",
                        "qty": 1.0,
                        "filled_qty": 1.0,
                        "remaining_qty": 0.0,
                    }
                ],
                broker="ctp",
                account="DU123456",
            )
            == 1
        )

        assert session.get(OrderRecord, client_one.id).status == "Submitted"
        assert session.get(OrderRecord, client_two.id).status == "Filled"


def test_late_working_status_cannot_regress_terminal_fill_progress(postgresql_engine):
    engine = postgresql_engine

    with Session(engine, future=True) as session:
        order = OrderRecord(
            strategy_id="core",
            symbol="RB2405",
            side="BUY",
            qty=2.0,
            broker="ctp",
            account="DU123456",
            broker_order_id="42",
            client_id=7,
            instrument_id="rb2601",
            status="Filled",
            filled_qty=2.0,
            remaining_qty=0.0,
        )
        session.add(order)
        session.commit()

        update_order_statuses(
            session,
            [
                {
                    "broker_order_id": "42",
                    "account": "DU123456",
                    "client_id": 7,
                    "instrument_id": "rb2601",
                    "symbol": "RB2405",
                    "side": "BUY",
                    "qty": 2.0,
                    "status": "Submitted",
                    "filled_qty": 0.0,
                    "remaining_qty": 2.0,
                }
            ],
            broker="ctp",
            account="DU123456",
        )
        refreshed = session.get(OrderRecord, order.id)

        assert refreshed.status == "Filled"
        assert refreshed.filled_qty == 2.0
        assert refreshed.remaining_qty == 0.0


def test_stale_cancelled_snapshot_cannot_regress_fill_progress(postgresql_engine):
    engine = postgresql_engine

    with Session(engine, future=True) as session:
        order = OrderRecord(
            strategy_id="core",
            symbol="RB2405",
            side="BUY",
            qty=2.0,
            broker="ctp",
            account="DU123456",
            broker_order_id="42",
            client_id=7,
            instrument_id="rb2601",
            status="Cancelled",
            filled_qty=1.0,
            remaining_qty=1.0,
        )
        session.add(order)
        session.commit()

        update_order_statuses(
            session,
            [
                {
                    "broker_order_id": "42",
                    "account": "DU123456",
                    "client_id": 7,
                    "instrument_id": "rb2601",
                    "symbol": "RB2405",
                    "side": "BUY",
                    "qty": 2.0,
                    "status": "Cancelled",
                    "filled_qty": 0.0,
                    "remaining_qty": 2.0,
                }
            ],
            broker="ctp",
            account="DU123456",
        )
        refreshed = session.get(OrderRecord, order.id)

        assert refreshed.status == "Cancelled"
        assert refreshed.filled_qty == 1.0
        assert refreshed.remaining_qty == 1.0


def test_any_broker_status_rejects_progress_above_order_quantity(postgresql_engine):
    engine = postgresql_engine

    with Session(engine, future=True) as session:
        order = OrderRecord(
            strategy_id="core",
            symbol="RB2405",
            side="BUY",
            qty=2.0,
            broker="ctp",
            account="DU123456",
            broker_order_id="42",
            client_id=7,
            instrument_id="rb2601",
            status="Submitted",
        )
        session.add(order)
        session.commit()

        with pytest.raises(RuntimeError, match="BROKER_ORDER_PROGRESS_INVALID"):
            update_order_statuses(
                session,
                [
                    {
                        "broker_order_id": "42",
                        "account": "DU123456",
                        "client_id": 7,
                        "instrument_id": "rb2601",
                        "symbol": "RB2405",
                        "side": "BUY",
                        "qty": 2.0,
                        "status": "Cancelled",
                        "filled_qty": 3.0,
                        "remaining_qty": 0.0,
                    }
                ],
                broker="ctp",
                account="DU123456",
            )

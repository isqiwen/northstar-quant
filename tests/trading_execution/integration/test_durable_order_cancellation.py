from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from tests.helpers.durable_submission import RecordingBroker, order_request

from northstar_quant.platform.common.order_identity import build_order_ref
from northstar_quant.platform.common.time import utc_now
from northstar_quant.platform.db.models import CancelRecord, OrderRecord
from northstar_quant.platform.db.repositories import (
    finalize_order_cancel_request,
    prepare_order_cancel,
    prepare_order_submission,
    try_acquire_execution_lease,
    update_order_statuses,
    update_pending_cancel_statuses,
)
from northstar_quant.trading_execution.orders.durable_submission import (
    DurableBrokerAdapter,
    SubmissionLease,
    SubmissionRecoveryRequired,
)

def test_terminal_order_rejects_duplicate_cancel_without_new_blocker(postgresql_engine):
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
            status="Filled",
            filled_qty=2.0,
            remaining_qty=0.0,
        )
        session.add(order)
        session.commit()

        with pytest.raises(
            RuntimeError,
            match="CANCEL_NOT_REQUIRED_ORDER_FINAL",
        ):
            prepare_order_cancel(
                session,
                broker="ctp",
                account="DU123456",
                broker_order_id="42",
                reason="late_duplicate",
                local_order_id=order.id,
                client_id=7,
            )

        assert list(session.scalars(select(CancelRecord))) == []


def test_cancel_intent_uses_client_id_when_order_id_is_reused(postgresql_engine):
    engine = postgresql_engine

    class _ClientTwoBroker(RecordingBroker):
        def get_client_id(self) -> int:
            return 2

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

        broker = _ClientTwoBroker()
        assert DurableBrokerAdapter(broker, session).cancel_order("42") is True
        cancel = session.scalar(select(CancelRecord))

        assert broker.cancel_count == 1
        assert cancel is not None
        assert cancel.order_id == client_two.id


def test_stale_fencing_token_cannot_claim_prepared_order(postgresql_engine):
    engine = postgresql_engine
    lease_now = utc_now()

    with Session(engine, future=True) as session:
        first_token = try_acquire_execution_lease(
            session,
            resource_key="live-submit:ctp:DU123456",
            owner_token="owner-1",  # secret-scan: allow; reason: disposable test fixture
            ttl_seconds=30,
            now=lease_now,
        )
        assert first_token == 1
        prepare_order_submission(
            session,
            order_request(),
            broker="ctp",
            account="DU123456",
        )
        second_token = try_acquire_execution_lease(
            session,
            resource_key="live-submit:ctp:DU123456",
            owner_token="owner-2",  # secret-scan: allow; reason: disposable test fixture
            ttl_seconds=30,
            now=lease_now + timedelta(seconds=31),
        )
        assert second_token == 2

        durable = DurableBrokerAdapter(
            RecordingBroker(),
            session,
            lease=SubmissionLease(
                resource_key="live-submit:ctp:DU123456",
                owner_token="owner-1",  # secret-scan: allow; reason: disposable test fixture
                fencing_token=first_token,
                ttl_seconds=30,
            ),
        )
        with pytest.raises(
            SubmissionRecoveryRequired,
            match="EXECUTION_LEASE_LOST",
        ):
            durable.submit_order(order_request())


def test_cancel_intent_is_durable_before_broker_call_and_recovers_terminal(
    postgresql_engine,
):
    engine = postgresql_engine

    def assert_cancel_is_prepared(_broker_order_id: str) -> None:
        with Session(engine, future=True) as observer:
            cancel_row = observer.scalar(select(CancelRecord))
            assert cancel_row is not None
            assert cancel_row.status == "CancelPrepared"

    broker = RecordingBroker(on_cancel=assert_cancel_is_prepared)
    with Session(engine, future=True) as session:
        durable = DurableBrokerAdapter(broker, session)
        durable.submit_order(order_request())
        assert durable.cancel_order("broker-42") is True

        cancel_row = session.scalar(select(CancelRecord))
        order_row = session.scalar(select(OrderRecord))
        assert cancel_row is not None
        assert cancel_row.status == "PendingCancel"
        assert order_row is not None
        assert order_row.status == "PendingCancel"

        updated = update_pending_cancel_statuses(
            session,
            [
                {
                    "broker_order_id": "broker-42",
                    "account": "DU123456",
                    "status": "Cancelled",
                }
            ],
            broker="ctp",
            account="DU123456",
        )
        refreshed_cancel = session.get(CancelRecord, cancel_row.id)

    assert broker.cancel_count == 1
    assert updated == 1
    assert refreshed_cancel is not None
    assert refreshed_cancel.status == "Cancelled"


def test_cancel_exception_is_not_reissued_before_completed_order_recovery(
    postgresql_engine,
):
    engine = postgresql_engine

    with Session(engine, future=True) as session:
        DurableBrokerAdapter(RecordingBroker(), session).submit_order(order_request())
        cancel_broker = RecordingBroker(
            cancel_error=TimeoutError("cancel acknowledgement lost")
        )
        durable = DurableBrokerAdapter(cancel_broker, session)

        with pytest.raises(TimeoutError, match="acknowledgement lost"):
            durable.cancel_order("broker-42")
        with pytest.raises(
            SubmissionRecoveryRequired,
            match="CANCEL_RECOVERY_REQUIRED",
        ):
            durable.cancel_order("broker-42")

        cancel_row = session.scalar(select(CancelRecord))
        assert cancel_row is not None
        assert cancel_row.status == "CancelPrepared"

        updated = update_pending_cancel_statuses(
            session,
            [
                {
                    "broker_order_id": "broker-42",
                    "account": "DU123456",
                    "status": "Cancelled",
                }
            ],
            broker="ctp",
            account="DU123456",
        )

    assert cancel_broker.cancel_count == 1
    assert updated == 1


def test_cancel_reject_is_durable_and_does_not_claim_cancellation(postgresql_engine):
    engine = postgresql_engine

    class _RejectingCancelBroker(RecordingBroker):
        def cancel_order(self, broker_order_id: str) -> bool:
            self.cancel_count += 1
            return False

    with Session(engine, future=True) as session:
        broker = _RejectingCancelBroker()
        durable = DurableBrokerAdapter(broker, session)
        durable.submit_order(order_request())

        assert durable.cancel_order("broker-42") is False
        cancel = session.scalar(select(CancelRecord))
        order = session.scalar(select(OrderRecord))

    assert broker.cancel_count == 1
    assert cancel is not None
    assert cancel.status == "CancelRequestFailed"
    assert order is not None
    assert order.status == "ACCEPTED"


def test_cancel_terminal_recovery_rejects_mismatched_instrument(postgresql_engine):
    engine = postgresql_engine

    with Session(engine, future=True) as session:
        order = OrderRecord(
            strategy_id="core",
            symbol="RB2405",
            side="BUY",
            qty=2.0,
            broker="ctp",
            account="DU123456",
            broker_order_id="broker-42",
            order_ref=build_order_ref("plan-1", 1),
            instrument_id="rb2601",
            status="PendingCancel",
        )
        session.add(order)
        session.flush()
        cancel = CancelRecord(
            order_id=order.id,
            broker="ctp",
            broker_order_id="broker-42",
            account="DU123456",
            status="PendingCancel",
        )
        session.add(cancel)
        session.commit()

        with pytest.raises(
            RuntimeError,
            match="BROKER_ORDER_IDENTITY_MISMATCH",
        ):
            update_pending_cancel_statuses(
                session,
                [
                    {
                        "broker_order_id": "broker-42",
                        "account": "DU123456",
                        "order_ref": build_order_ref("plan-1", 1),
                        "instrument_id": "rb2602",
                        "symbol": "RB2405",
                        "status": "Cancelled",
                    }
                ],
                broker="ctp",
                account="DU123456",
            )

        session.rollback()
        assert session.get(CancelRecord, cancel.id).status == "PendingCancel"


def test_late_cancel_ack_cannot_downgrade_reconciled_terminal_state(postgresql_engine):
    engine = postgresql_engine

    with Session(engine, future=True) as setup:
        order = OrderRecord(
            strategy_id="core",
            symbol="RB2405",
            side="BUY",
            qty=2.0,
            broker="ctp",
            account="DU123456",
            broker_order_id="broker-42",
            order_ref=build_order_ref("plan-1", 1),
            instrument_id="rb2601",
            status="Submitted",
        )
        setup.add(order)
        setup.flush()
        cancel = CancelRecord(
            order_id=order.id,
            broker="ctp",
            broker_order_id="broker-42",
            account="DU123456",
            status="CancelPrepared",
        )
        setup.add(cancel)
        setup.commit()
        order_id = order.id
        cancel_id = cancel.id

    terminal_row = {
        "broker_order_id": "broker-42",
        "account": "DU123456",
        "order_ref": build_order_ref("plan-1", 1),
        "instrument_id": "rb2601",
        "symbol": "RB2405",
        "status": "Cancelled",
    }
    with Session(engine, future=True) as stale_session:
        assert stale_session.get(CancelRecord, cancel_id) is not None
        assert stale_session.get(OrderRecord, order_id) is not None

        with Session(engine, future=True) as reconciler:
            assert (
                update_order_statuses(
                    reconciler,
                    [terminal_row],
                    broker="ctp",
                    account="DU123456",
                )
                == 1
            )
            assert (
                update_pending_cancel_statuses(
                    reconciler,
                    [terminal_row],
                    broker="ctp",
                    account="DU123456",
                )
                == 1
            )

        finalize_order_cancel_request(
            stale_session,
            cancel_id=cancel_id,
            accepted=True,
        )

    with Session(engine, future=True) as observer:
        assert observer.get(CancelRecord, cancel_id).status == "Cancelled"
        assert observer.get(OrderRecord, order_id).status == "Cancelled"

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from northstar_quant.common.order_identity import build_order_ref
from northstar_quant.common.time import utc_now
from northstar_quant.db.base import Base
from northstar_quant.db.models import CancelRecord, OrderRecord
from northstar_quant.db.repositories import (
    finalize_order_cancel_request,
    prepare_order_cancel,
    prepare_order_submission,
    try_acquire_execution_lease,
    update_order_statuses,
    update_pending_cancel_statuses,
)
from northstar_quant.execution.broker_base import BrokerAdapter
from northstar_quant.execution.models import OrderRequest, OrderResult
from northstar_quant.live.durable_submission import (
    DurableBrokerAdapter,
    SubmissionLease,
    SubmissionRecoveryRequired,
)


def _order(**overrides) -> OrderRequest:
    values = {
        "strategy_id": "core",
        "symbol": "RB2405",
        "side": "BUY",
        "qty": 2.0,
        "account": "DU123456",
        "order_type": "LMT",
        "limit_price": 500.0,
        "run_id": "run-1",
        "batch_id": "batch-1",
        "plan_id": "plan-1",
        "attempt_no": 1,
    }
    values.update(overrides)
    return OrderRequest(**values)


class _RecordingBroker(BrokerAdapter):
    def __init__(
        self,
        *,
        on_submit=None,
        on_cancel=None,
        error: Exception | None = None,
        cancel_error: Exception | None = None,
    ) -> None:
        self.on_submit = on_submit
        self.on_cancel = on_cancel
        self.error = error
        self.cancel_error = cancel_error
        self.submit_count = 0
        self.cancel_count = 0

    def submit_order(self, order: OrderRequest) -> OrderResult:
        self.submit_count += 1
        if self.on_submit is not None:
            self.on_submit(order)
        if self.error is not None:
            raise self.error
        return OrderResult(
            accepted=True,
            broker_order_id="broker-42",
            status="Submitted",
            submitted_at=utc_now(),
        )

    def get_name(self) -> str:
        return "ctp"

    def get_account(self) -> str:
        return "DU123456"

    def cancel_order(self, broker_order_id: str) -> bool:
        self.cancel_count += 1
        if self.on_cancel is not None:
            self.on_cancel(broker_order_id)
        if self.cancel_error is not None:
            raise self.cancel_error
        return True


def test_order_intent_is_committed_before_broker_call_and_replay_is_idempotent(
    tmp_path,
):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'durable.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)

    def assert_durable_before_submit(_order_request: OrderRequest) -> None:
        with Session(engine, future=True) as observer:
            row = observer.scalar(select(OrderRecord))
            assert row is not None
            assert row.status == "Submitting"
            assert row.broker_order_id is None
            assert row.submission_started_at is not None

    broker = _RecordingBroker(on_submit=assert_durable_before_submit)
    with Session(engine, future=True) as session:
        durable = DurableBrokerAdapter(broker, session)
        first = durable.submit_order(_order())
        replay = durable.submit_order(_order())
        row = session.scalar(select(OrderRecord))

    assert broker.submit_count == 1
    assert first.replayed is False
    assert replay.replayed is True
    assert replay.broker_order_id == "broker-42"
    assert row is not None
    assert row.status == "Submitted"
    assert row.broker_acknowledged_at is not None


def test_concurrent_sessions_create_only_one_order_intent(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'concurrent-intent.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    barrier = Barrier(2)

    def prepare(_worker: int) -> tuple[int, bool]:
        with Session(engine, future=True) as session:
            barrier.wait()
            row, created = prepare_order_submission(
                session,
                _order(),
                broker="ctp",
                account="DU123456",
            )
            return row.id, created

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(prepare, (1, 2)))

    assert len({row_id for row_id, _created in results}) == 1
    assert sum(1 for _row_id, created in results if created) == 1


def test_final_instrument_payload_is_persisted_before_broker_call(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'instrument-payload.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)

    class _InstrumentBroker(_RecordingBroker):
        def prepare_order(self, order: OrderRequest) -> OrderRequest:
                return replace(
                    order,
                    instrument_id="rb2601",
                    exchange_id="SHFE",
                    currency="CNY",
            )

    def assert_instrument_is_durable(_order_request: OrderRequest) -> None:
        with Session(engine, future=True) as observer:
            row = observer.scalar(select(OrderRecord))
            assert row is not None
            assert row.instrument_id == "rb2601"
            assert row.exchange_id == "SHFE"
            assert row.request_fingerprint

    broker = _InstrumentBroker(on_submit=assert_instrument_is_durable)
    with Session(engine, future=True) as session:
        DurableBrokerAdapter(broker, session).submit_order(
            _order(reference_price=500.0)
        )


def test_submission_exception_stays_unknown_and_cannot_be_retried(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'unknown.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    broker = _RecordingBroker(error=TimeoutError("connection lost"))

    with Session(engine, future=True) as session:
        durable = DurableBrokerAdapter(broker, session)
        with pytest.raises(TimeoutError, match="connection lost"):
            durable.submit_order(_order())

        row = session.scalar(select(OrderRecord))
        assert row is not None
        assert row.status == "SubmissionUnknown"
        assert "TimeoutError" in str(row.last_submission_error)

        with pytest.raises(
            SubmissionRecoveryRequired,
            match="SUBMISSION_RECOVERY_REQUIRED",
        ):
            durable.submit_order(_order())

    assert broker.submit_count == 1


def test_same_idempotency_key_with_different_payload_fails_closed(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'conflict.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    broker = _RecordingBroker()

    with Session(engine, future=True) as session:
        durable = DurableBrokerAdapter(broker, session)
        durable.submit_order(_order())
        with pytest.raises(RuntimeError, match="IDEMPOTENCY_CONFLICT"):
            durable.submit_order(_order(qty=3.0))

    assert broker.submit_count == 1


def test_chase_restart_restores_persisted_price_and_quantity(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'chase-restart.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    broker = _RecordingBroker()

    with Session(engine, future=True) as session:
        durable = DurableBrokerAdapter(broker, session)
        durable.submit_order(_order(qty=2.0, limit_price=500.0))

        restored = durable.restore_order_attempt(
            _order(qty=1.0, limit_price=505.0)
        )
        replay = durable.submit_order(restored)

    assert restored.qty == 2.0
    assert restored.limit_price == 500.0
    assert replay.replayed is True
    assert broker.submit_count == 1


def test_durable_adapter_lists_all_persisted_plan_attempts(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'plan-attempts.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)

    with Session(engine, future=True) as session:
        prepare_order_submission(
            session,
            _order(
                attempt_no=1,
                order_type="LMT",
                limit_price=500.0,
                execution_policy_fingerprint="policy-v1",
            ),
            broker="ctp",
            account="DU123456",
        )
        prepare_order_submission(
            session,
            _order(
                attempt_no=2,
                order_type="MKT",
                limit_price=None,
                execution_policy_fingerprint="policy-v1",
            ),
            broker="ctp",
            account="DU123456",
        )
        attempts = DurableBrokerAdapter(
            _RecordingBroker(),
            session,
        ).list_order_plan_attempts(_order())

    assert [
        (
            row["attempt_no"],
            row["order_type"],
            row["execution_policy_fingerprint"],
        )
        for row in attempts
    ] == [
        (1, "LMT", "policy-v1"),
        (2, "MKT", "policy-v1"),
    ]


def test_confirmed_terminal_without_broker_order_id_is_replayed(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'terminal-without-order-id.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    broker = _RecordingBroker()

    with Session(engine, future=True) as session:
        row, _created = prepare_order_submission(
            session,
            _order(instrument_id="rb2601"),
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
            _order(instrument_id="rb2601")
        )

    assert replay.replayed is True
    assert replay.status == "Filled"
    assert replay.broker_order_id == ""
    assert replay.client_id == 7
    assert replay.perm_id == 101
    assert broker.submit_count == 0


def test_unknown_submission_recovers_by_order_ref_without_resubmission(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'recovery.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    failing_broker = _RecordingBroker(error=TimeoutError("ack lost"))

    with Session(engine, future=True) as session:
        durable = DurableBrokerAdapter(failing_broker, session)
        with pytest.raises(TimeoutError):
            durable.submit_order(_order())

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

        healthy_broker = _RecordingBroker()
        replay = DurableBrokerAdapter(healthy_broker, session).submit_order(_order())

    assert replay.replayed is True
    assert replay.status == "Filled"
    assert replay.broker_order_id == "broker-recovered"
    assert healthy_broker.submit_count == 0


def test_order_recovery_rejects_mismatched_instrument_identity(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'identity-mismatch.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)

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


def test_order_recovery_uses_client_id_when_order_id_is_reused(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'client-order-id.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)

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


def test_late_working_status_cannot_regress_terminal_fill_progress(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'terminal-progress.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)

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


def test_stale_cancelled_snapshot_cannot_regress_fill_progress(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'cancelled-progress.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)

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


def test_any_broker_status_rejects_progress_above_order_quantity(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'invalid-progress.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)

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


def test_terminal_order_rejects_duplicate_cancel_without_new_blocker(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'terminal-cancel.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)

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


def test_cancel_intent_uses_client_id_when_order_id_is_reused(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'cancel-client-order-id.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)

    class _ClientTwoBroker(_RecordingBroker):
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


def test_stale_fencing_token_cannot_claim_prepared_order(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'fencing.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    lease_now = utc_now()

    with Session(engine, future=True) as session:
        first_token = try_acquire_execution_lease(
            session,
            resource_key="live-submit:ctp:DU123456",
            owner_token="owner-1",
            ttl_seconds=30,
            now=lease_now,
        )
        assert first_token == 1
        prepare_order_submission(
            session,
            _order(),
            broker="ctp",
            account="DU123456",
        )
        second_token = try_acquire_execution_lease(
            session,
            resource_key="live-submit:ctp:DU123456",
            owner_token="owner-2",
            ttl_seconds=30,
            now=lease_now + timedelta(seconds=31),
        )
        assert second_token == 2

        durable = DurableBrokerAdapter(
            _RecordingBroker(),
            session,
            lease=SubmissionLease(
                resource_key="live-submit:ctp:DU123456",
                owner_token="owner-1",
                fencing_token=first_token,
                ttl_seconds=30,
            ),
        )
        with pytest.raises(
            SubmissionRecoveryRequired,
            match="EXECUTION_LEASE_LOST",
        ):
            durable.submit_order(_order())


def test_cancel_intent_is_durable_before_broker_call_and_recovers_terminal(
    tmp_path,
):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'cancel.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)

    def assert_cancel_is_prepared(_broker_order_id: str) -> None:
        with Session(engine, future=True) as observer:
            cancel_row = observer.scalar(select(CancelRecord))
            assert cancel_row is not None
            assert cancel_row.status == "CancelPrepared"

    broker = _RecordingBroker(on_cancel=assert_cancel_is_prepared)
    with Session(engine, future=True) as session:
        durable = DurableBrokerAdapter(broker, session)
        durable.submit_order(_order())
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
    tmp_path,
):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'cancel-unknown.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)

    with Session(engine, future=True) as session:
        DurableBrokerAdapter(_RecordingBroker(), session).submit_order(_order())
        cancel_broker = _RecordingBroker(
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


def test_cancel_terminal_recovery_rejects_mismatched_instrument(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'cancel-identity.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)

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


def test_late_cancel_ack_cannot_downgrade_reconciled_terminal_state(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'cancel-cas.db').as_posix()}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)

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

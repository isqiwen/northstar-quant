from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from tests.helpers.durable_submission import RecordingBroker, order_request

import northstar_quant.trading_execution.orders.durable_submission as durable_submission_module
from northstar_quant.foundation.db.models import OrderRecord
from northstar_quant.foundation.db.repositories import (
    prepare_order_submission,
)
from northstar_quant.trading_execution.execution.models import OrderRequest
from northstar_quant.trading_execution.orders.durable_submission import (
    DurableBrokerAdapter,
    SubmissionRecoveryRequired,
)

def test_order_intent_is_committed_before_broker_call_and_replay_is_idempotent(
    postgresql_engine,
):
    engine = postgresql_engine

    def assert_durable_before_submit(_order_request: OrderRequest) -> None:
        with Session(engine, future=True) as observer:
            row = observer.scalar(select(OrderRecord))
            assert row is not None
            assert row.status == "Submitting"
            assert row.broker_order_id is None
            assert row.submission_started_at is not None

    broker = RecordingBroker(on_submit=assert_durable_before_submit)
    with Session(engine, future=True) as session:
        durable = DurableBrokerAdapter(broker, session)
        first = durable.submit_order(order_request())
        replay = durable.submit_order(order_request())
        row = session.scalar(select(OrderRecord))

    assert broker.submit_count == 1
    assert first.replayed is False
    assert replay.replayed is True
    assert replay.broker_order_id == "broker-42"
    assert row is not None
    assert row.status == "ACCEPTED"
    assert row.broker_acknowledged_at is not None


def test_concurrent_sessions_create_only_one_order_intent(postgresql_engine):
    engine = postgresql_engine
    barrier = Barrier(2)

    def prepare(_worker: int) -> tuple[int, bool]:
        with Session(engine, future=True) as session:
            barrier.wait()
            row, created = prepare_order_submission(
                session,
                order_request(),
                broker="ctp",
                account="DU123456",
            )
            return row.id, created

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(prepare, (1, 2)))

    assert len({row_id for row_id, _created in results}) == 1
    assert sum(1 for _row_id, created in results if created) == 1


def test_final_instrument_payload_is_persisted_before_broker_call(postgresql_engine):
    engine = postgresql_engine

    class _InstrumentBroker(RecordingBroker):
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
            order_request(reference_price=500.0)
        )


@pytest.mark.parametrize(
    ("failure", "failure_name"),
    [
        (TimeoutError("transport acknowledgement unavailable"), "TimeoutError"),
        (ConnectionError("transport acknowledgement unavailable"), "ConnectionError"),
    ],
)
def test_timeout_or_network_partition_stays_unknown_and_cannot_be_retried(
    postgresql_engine,
    failure: Exception,
    failure_name: str,
):
    engine = postgresql_engine
    broker = RecordingBroker(error=failure)

    with Session(engine, future=True) as session:
        durable = DurableBrokerAdapter(broker, session)
        with pytest.raises(type(failure), match="transport acknowledgement unavailable"):
            durable.submit_order(order_request())

        row = session.scalar(select(OrderRecord))
        assert row is not None
        assert row.status == "SubmissionUnknown"
        assert failure_name in str(row.last_submission_error)

        with pytest.raises(
            SubmissionRecoveryRequired,
            match="SUBMISSION_RECOVERY_REQUIRED",
        ):
            durable.submit_order(order_request())

    assert broker.submit_count == 1


def test_database_unavailable_prevents_any_broker_submission(monkeypatch):
    broker = RecordingBroker()

    def raise_database_unavailable(*_args, **_kwargs):
        raise OperationalError(
            "INSERT order_records",
            {},
            ConnectionError("database unavailable"),
        )

    monkeypatch.setattr(
        durable_submission_module,
        "prepare_order_submission",
        raise_database_unavailable,
    )

    with pytest.raises(OperationalError, match="database unavailable"):
        DurableBrokerAdapter(broker, Session()).submit_order(order_request())

    assert broker.submit_count == 0


def test_same_idempotency_key_with_different_payload_fails_closed(postgresql_engine):
    engine = postgresql_engine
    broker = RecordingBroker()

    with Session(engine, future=True) as session:
        durable = DurableBrokerAdapter(broker, session)
        durable.submit_order(order_request())
        with pytest.raises(RuntimeError, match="IDEMPOTENCY_CONFLICT"):
            durable.submit_order(order_request(qty=3.0))

    assert broker.submit_count == 1


def test_chase_restart_restores_persisted_price_and_quantity(postgresql_engine):
    engine = postgresql_engine
    broker = RecordingBroker()

    with Session(engine, future=True) as session:
        durable = DurableBrokerAdapter(broker, session)
        durable.submit_order(order_request(qty=2.0, limit_price=500.0))

        restored = durable.restore_order_attempt(
            order_request(qty=1.0, limit_price=505.0)
        )
        replay = durable.submit_order(restored)

    assert restored.qty == 2.0
    assert restored.limit_price == 500.0
    assert replay.replayed is True
    assert broker.submit_count == 1


def test_durable_adapter_lists_all_persisted_plan_attempts(postgresql_engine):
    engine = postgresql_engine

    with Session(engine, future=True) as session:
        prepare_order_submission(
            session,
            order_request(
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
            order_request(
                attempt_no=2,
                order_type="MKT",
                limit_price=None,
                execution_policy_fingerprint="policy-v1",
            ),
            broker="ctp",
            account="DU123456",
        )
        attempts = DurableBrokerAdapter(
            RecordingBroker(),
            session,
        ).list_order_plan_attempts(order_request())

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

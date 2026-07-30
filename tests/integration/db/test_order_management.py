from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from northstar_quant.db.models import CancelRecord, OrderRecord
from northstar_quant.execution.broker_base import BrokerAdapter
from northstar_quant.execution.models import OrderRequest, OrderResult
from northstar_quant.live.durable_submission import DurableBrokerAdapter
from northstar_quant.live.order_management import cancel_stale_orders


class _FakeBroker:
    def __init__(self):
        self.canceled: list[str] = []

    def cancel_order(self, broker_order_id: str) -> bool:
        self.canceled.append(broker_order_id)
        return True

    def get_name(self) -> str:
        return "paper"

    def get_account(self) -> str:
        return "paper-account"


def test_cancel_stale_orders_writes_cancel_record(postgresql_engine):
    engine = postgresql_engine
    stale_time = datetime.now(UTC) - timedelta(days=1)

    with Session(engine, future=True) as session:
        session.add(
            OrderRecord(
                profile_id="cn_futures_daily_live",
                strategy_id="core_portfolio",
                symbol="RB2405",
                side="BUY",
                qty=10.0,
                broker="paper",
                account="paper-account",
                run_id="run-cancel-001",
                broker_order_id="paper-123",
                status="Submitted",
                submitted_at=stale_time,
            )
        )
        session.commit()

        result = cancel_stale_orders(session, _FakeBroker())
        cancel_row = session.scalar(
            select(CancelRecord).where(CancelRecord.broker_order_id == "paper-123")
        )
        order_row = session.scalar(
            select(OrderRecord).where(OrderRecord.broker_order_id == "paper-123")
        )

    assert result["stale_order_count"] == 1
    assert result["cancel_record_count"] == 1
    assert result["cancel_batch_id"] is not None
    assert result["canceled_order_ids"] == ["paper-123"]
    assert result["cancel_requested_order_ids"] == ["paper-123"]
    assert cancel_row is not None
    assert cancel_row.broker == "paper"
    assert cancel_row.profile_id == "cn_futures_daily_live"
    assert cancel_row.run_id == "run-cancel-001"
    assert cancel_row.account == "paper-account"
    assert cancel_row.reason == "stale_order_timeout"
    assert order_row is not None
    assert order_row.status == "PendingCancel"


def test_cancel_stale_orders_is_scoped_to_current_broker_client(postgresql_engine):
    class _ClientBroker(BrokerAdapter):
        def __init__(self) -> None:
            self.cancelled: list[str] = []

        def submit_order(self, order: OrderRequest) -> OrderResult:
            raise AssertionError(f"本测试不应提交订单：{order}")

        def get_name(self) -> str:
            return "ctp"

        def get_account(self) -> str:
            return "DU123456"

        def get_client_id(self) -> int:
            return 2

        def cancel_order(self, broker_order_id: str) -> bool:
            self.cancelled.append(broker_order_id)
            return True

    engine = postgresql_engine
    stale_time = datetime.now(UTC) - timedelta(days=1)

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
            submitted_at=stale_time,
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
            submitted_at=stale_time,
        )
        session.add_all([client_one, client_two])
        session.commit()

        delegate = _ClientBroker()
        result = cancel_stale_orders(
            session,
            DurableBrokerAdapter(delegate, session),
        )

        assert result["stale_order_count"] == 1
        assert delegate.cancelled == ["42"]
        assert session.get(OrderRecord, client_one.id).status == "Submitted"
        assert session.get(OrderRecord, client_two.id).status == "PendingCancel"


def test_cancel_stale_orders_never_crosses_broker_or_account(postgresql_engine):
    engine = postgresql_engine
    stale_time = datetime.now(UTC) - timedelta(days=1)
    broker = _FakeBroker()

    with Session(engine, future=True) as session:
        session.add_all(
            [
                OrderRecord(
                    strategy_id="test",
                    symbol="RB2405",
                    side="BUY",
                    qty=1.0,
                    broker="ctp",
                    account="paper-account",
                    broker_order_id="ctp-1",
                    status="Submitted",
                    submitted_at=stale_time,
                ),
                OrderRecord(
                    strategy_id="test",
                    symbol="I2405",
                    side="BUY",
                    qty=1.0,
                    broker="paper",
                    account="other-paper-account",
                    broker_order_id="paper-other",
                    status="Submitted",
                    submitted_at=stale_time,
                ),
            ]
        )
        session.commit()

        result = cancel_stale_orders(session, broker)

    assert result["stale_order_count"] == 0
    assert broker.canceled == []

from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from northstar_quant.db.base import Base
from northstar_quant.db.models import CancelRecord, OrderRecord
from northstar_quant.live.order_management import cancel_stale_orders


class _FakeBroker:
    def __init__(self):
        self.canceled: list[str] = []

    def cancel_order(self, broker_order_id: str) -> bool:
        self.canceled.append(broker_order_id)
        return True

    def get_name(self) -> str:
        return "paper"


def test_cancel_stale_orders_writes_cancel_record(tmp_path):
    db_path = tmp_path / "cancel-ledger.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    stale_time = datetime.now(UTC) - timedelta(days=1)

    with Session(engine, future=True) as session:
        session.add(
            OrderRecord(
                profile_id="us_etf_daily",
                strategy_id="core_portfolio",
                symbol="SPY",
                side="BUY",
                qty=10.0,
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
    assert cancel_row is not None
    assert cancel_row.broker == "paper"
    assert cancel_row.profile_id == "us_etf_daily"
    assert cancel_row.run_id == "run-cancel-001"
    assert cancel_row.account == "paper-account"
    assert cancel_row.reason == "stale_order_timeout"
    assert order_row is not None
    assert order_row.status == "Canceled"

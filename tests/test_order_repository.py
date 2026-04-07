from datetime import UTC

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from northstar_quant.db.base import Base
from northstar_quant.db.models import OrderRecord
from northstar_quant.db.repositories import save_order_result


def test_save_order_result_persists_order_semantic(tmp_path):
    db_path = tmp_path / "orders.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)

    with Session(engine, future=True) as session:
        save_order_result(
            session=session,
            strategy_id="intraday_breakout",
            symbol="AAPL",
            side="BUY",
            qty=100,
            target_weight=None,
            order_semantic="entry",
            broker_order_id="paper-123",
            status="accepted",
        )

        row = session.scalar(
            select(OrderRecord).where(OrderRecord.broker_order_id == "paper-123")
        )

    assert row is not None
    assert row.order_semantic == "entry"
    assert row.target_weight is None
    assert row.submitted_at is not None
    assert row.submitted_at.tzinfo is UTC

from datetime import UTC
from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from northstar_quant.db.base import Base
from northstar_quant.db.models import OrderRecord
from northstar_quant.db.repositories import save_order_result
from northstar_quant.execution.models import OrderRequest, OrderResult


def test_save_order_result_persists_full_order_audit_context(tmp_path):
    db_path = tmp_path / "orders.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    submitted_at = datetime(2024, 3, 4, 15, 35, tzinfo=UTC)

    with Session(engine, future=True) as session:
        save_order_result(
            session=session,
            order=OrderRequest(
                strategy_id="intraday_breakout",
                symbol="AAPL",
                side="BUY",
                qty=100,
                profile_id="us_stock_intraday_1m",
                target_weight=None,
                order_type="LMT",
                limit_price=101.25,
                order_semantic="entry",
                account="paper-account",
                reason="breakout_entry",
                reference_price=101.0,
                reference_price_source="broker_snapshot",
                planned_trade_value=10100.0,
                run_id="run-001",
                batch_id="batch-001",
                plan_id="plan-001",
                execution_planner_id="direct_execution_intent",
            ),
            result=OrderResult(
                accepted=True,
                broker_order_id="paper-123",
                status="Submitted",
                submitted_at=submitted_at,
            ),
        )

        row = session.scalar(
            select(OrderRecord).where(OrderRecord.broker_order_id == "paper-123")
        )

    assert row is not None
    assert row.profile_id == "us_stock_intraday_1m"
    assert row.order_semantic == "entry"
    assert row.target_weight is None
    assert row.order_type == "LMT"
    assert row.limit_price == 101.25
    assert row.reason == "breakout_entry"
    assert row.account == "paper-account"
    assert row.reference_price == 101.0
    assert row.reference_price_source == "broker_snapshot"
    assert row.planned_trade_value == 10100.0
    assert row.execution_planner_id == "direct_execution_intent"
    assert row.run_id == "run-001"
    assert row.batch_id == "batch-001"
    assert row.plan_id == "plan-001"
    assert row.submitted_at == submitted_at
    assert row.submitted_at.tzinfo is UTC

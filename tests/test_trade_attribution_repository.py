from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from northstar_quant.db.base import Base
from northstar_quant.db.models import OrderRecord, TradeAttributionRecord
from northstar_quant.db.repositories import save_fill_snapshots
from northstar_quant.execution.models import FillSnapshot


def test_save_fill_snapshots_creates_buy_trade_attribution(tmp_path):
    db_path = tmp_path / "buy-attribution.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    filled_at = datetime(2024, 3, 4, 15, 36, tzinfo=UTC)

    with Session(engine, future=True) as session:
        session.add(
            OrderRecord(
                profile_id="us_etf_daily",
                strategy_id="core_portfolio",
                symbol="SPY",
                side="BUY",
                qty=100.0,
                order_type="MKT",
                account="paper-account",
                reference_price=100.0,
                reference_price_source="broker_snapshot",
                execution_planner_id="bar_close_rebalance",
                run_id="run-001",
                batch_id="batch-001",
                plan_id="plan-001",
                broker_order_id="paper-123",
                status="Submitted",
                submitted_at=filled_at,
            )
        )
        session.commit()

        count = save_fill_snapshots(
            session,
            [
                FillSnapshot(
                    broker_order_id="paper-123",
                    symbol="SPY",
                    qty=100.0,
                    price=100.5,
                    side="BUY",
                    filled_at=filled_at,
                )
            ],
        )
        row = session.scalar(
            select(TradeAttributionRecord).where(
                TradeAttributionRecord.broker_order_id == "paper-123"
            )
        )

    assert count == 1
    assert row is not None
    assert row.run_id == "run-001"
    assert row.plan_id == "plan-001"
    assert row.reference_price == 100.0
    assert row.reference_price_source == "broker_snapshot"
    assert row.actual_notional == 10050.0
    assert row.reference_notional == 10000.0
    assert row.implementation_shortfall == 50.0
    assert row.implementation_shortfall_bps == 50.0


def test_save_fill_snapshots_creates_sell_trade_attribution_with_correct_sign(tmp_path):
    db_path = tmp_path / "sell-attribution.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    filled_at = datetime(2024, 3, 4, 15, 36, tzinfo=UTC)

    with Session(engine, future=True) as session:
        session.add(
            OrderRecord(
                profile_id="us_etf_daily",
                strategy_id="core_portfolio",
                symbol="QQQ",
                side="SELL",
                qty=50.0,
                order_type="LMT",
                limit_price=200.0,
                account="paper-account",
                execution_planner_id="bar_close_rebalance",
                run_id="run-002",
                batch_id="batch-002",
                plan_id="plan-002",
                broker_order_id="paper-456",
                status="Submitted",
                submitted_at=filled_at,
            )
        )
        session.commit()

        save_fill_snapshots(
            session,
            [
                FillSnapshot(
                    broker_order_id="paper-456",
                    symbol="QQQ",
                    qty=50.0,
                    price=199.0,
                    side="SELL",
                    filled_at=filled_at,
                )
            ],
        )
        row = session.scalar(
            select(TradeAttributionRecord).where(
                TradeAttributionRecord.broker_order_id == "paper-456"
            )
        )

    assert row is not None
    assert row.reference_price == 200.0
    assert row.reference_price_source == "order_limit"
    assert row.actual_notional == 9950.0
    assert row.reference_notional == 10000.0
    assert row.implementation_shortfall == 50.0
    assert row.implementation_shortfall_bps == 50.0

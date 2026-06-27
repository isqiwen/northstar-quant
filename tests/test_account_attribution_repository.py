from datetime import UTC, datetime
import json

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from northstar_quant.db.base import Base
from northstar_quant.db.models import (
    AccountAttributionRecord,
    AccountSnapshotRecord,
    PositionSnapshotRecord,
    TradeAttributionRecord,
)
from northstar_quant.db.repositories import save_account_attribution_for_snapshot


def test_save_account_attribution_for_snapshot_splits_price_and_rebalance_pnl(tmp_path):
    db_path = tmp_path / "account-attribution.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    start_asof = datetime(2024, 3, 4, 15, 30, tzinfo=UTC)
    end_asof = datetime(2024, 3, 4, 15, 45, tzinfo=UTC)

    with Session(engine, future=True) as session:
        session.add_all(
            [
                PositionSnapshotRecord(
                    account="paper-account",
                    symbol="SPY",
                    qty=100.0,
                    market_price=100.0,
                    market_value=10000.0,
                    asof=start_asof,
                    snapshot_batch_id="pos-start",
                ),
                PositionSnapshotRecord(
                    account="paper-account",
                    symbol="SPY",
                    qty=120.0,
                    market_price=102.0,
                    market_value=12240.0,
                    asof=end_asof,
                    snapshot_batch_id="pos-end",
                ),
                AccountSnapshotRecord(
                    run_id="run-start",
                    profile_id="us_etf_daily",
                    broker="paper",
                    account="paper-account",
                    position_snapshot_batch_id="pos-start",
                    position_count=1,
                    cash_balance=90000.0,
                    net_liquidation=100000.0,
                    gross_position_value=10000.0,
                    net_position_value=10000.0,
                    available_funds=90000.0,
                    gross_exposure=0.10,
                    net_exposure=0.10,
                    account_values_json=json.dumps(
                        {
                            "Dividends": 0.0,
                            "InterestAccruals": 0.0,
                            "Commissions": 0.0,
                            "WithholdingTax": 0.0,
                            "FundsTransfer": 0.0,
                            "CashInLieu": 0.0,
                        }
                    ),
                    asof=start_asof,
                ),
                AccountSnapshotRecord(
                    run_id="run-end",
                    profile_id="us_etf_daily",
                    broker="paper",
                    account="paper-account",
                    position_snapshot_batch_id="pos-end",
                    position_count=1,
                    cash_balance=88027.0,
                    net_liquidation=100267.0,
                    gross_position_value=12240.0,
                    net_position_value=12240.0,
                    available_funds=88027.0,
                    gross_exposure=0.1221,
                    net_exposure=0.1221,
                    account_values_json=json.dumps(
                        {
                            "Dividends": 30.0,
                            "InterestAccruals": 5.0,
                            "Commissions": -3.0,
                            "WithholdingTax": -2.0,
                            "FundsTransfer": 10.0,
                            "CashInLieu": 7.0,
                        }
                    ),
                    asof=end_asof,
                ),
                TradeAttributionRecord(
                    broker_order_id="paper-123",
                    run_id="run-end",
                    batch_id="batch-001",
                    plan_id="plan-001",
                    profile_id="us_etf_daily",
                    account="paper-account",
                    strategy_id="core_portfolio",
                    execution_planner_id="bar_close_rebalance",
                    symbol="SPY",
                    side="BUY",
                    qty=20.0,
                    fill_price=101.0,
                    reference_price=100.5,
                    reference_price_source="broker_snapshot",
                    actual_notional=2020.0,
                    reference_notional=2010.0,
                    implementation_shortfall=10.0,
                    implementation_shortfall_bps=49.75124378109453,
                    attributed_at=datetime(2024, 3, 4, 15, 35, tzinfo=UTC),
                ),
            ]
        )
        session.commit()

        ending_snapshot = session.scalar(
            select(AccountSnapshotRecord).where(AccountSnapshotRecord.run_id == "run-end")
        )
        assert ending_snapshot is not None

        save_account_attribution_for_snapshot(session, ending_snapshot)
        row = session.scalar(
            select(AccountAttributionRecord).where(
                AccountAttributionRecord.end_account_snapshot_id == ending_snapshot.id
            )
        )

    assert row is not None
    assert row.run_id == "run-end"
    assert row.start_position_snapshot_batch_id == "pos-start"
    assert row.end_position_snapshot_batch_id == "pos-end"
    assert row.starting_equity == 100000.0
    assert row.ending_equity == 100267.0
    assert row.equity_change == 267.0
    assert row.starting_cash == 90000.0
    assert row.ending_cash == 88027.0
    assert row.cash_change == -1973.0
    assert row.price_pnl == 200.0
    assert row.rebalance_pnl == 20.0
    assert row.execution_shortfall == 10.0
    assert row.dividend_cash_flow == 30.0
    assert row.interest_cash_flow == 5.0
    assert row.fee_cash_flow == -3.0
    assert row.tax_cash_flow == -2.0
    assert row.funding_cash_flow == 10.0
    assert row.corporate_action_cash_flow == 7.0
    assert row.other_non_trade_cash_flow == 0.0
    assert row.total_non_trade_cash_flow == 47.0
    assert row.traded_notional == 2020.0
    assert row.fill_count == 1
    assert row.residual_pnl == 0.0

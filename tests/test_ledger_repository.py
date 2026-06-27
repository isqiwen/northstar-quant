import json
from datetime import UTC, date, datetime

import polars as pl
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from northstar_quant.common.enums import StrategyOutputType
from northstar_quant.db.base import Base
from northstar_quant.db.models import (
    AccountSnapshotRecord,
    ExecutionPlanRecord,
    StrategyRunRecord,
    StrategySnapshotRecord,
    WorkingOrderSnapshotRecord,
)
from northstar_quant.db.repositories import (
    save_account_snapshot,
    save_execution_plan_records,
    save_strategy_run_snapshot,
    save_working_order_snapshots,
)
from northstar_quant.execution.models import BrokerStateSnapshot, PositionSnapshot, RebalanceOrderPlan


def test_save_strategy_run_snapshot_persists_strategy_ledger_rows(tmp_path):
    db_path = tmp_path / "strategy-ledger.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)

    output_frame = pl.DataFrame(
        [
            {"date": date(2024, 3, 1), "symbol": "SPY", "signal_value": 1.0, "target_weight": 0.6},
            {"date": date(2024, 3, 1), "symbol": "QQQ", "signal_value": 0.5, "target_weight": 0.4},
        ]
    )
    market_df = pl.DataFrame(
        [
            {"date": date(2024, 2, 29), "symbol": "SPY", "close": 500.0},
            {"date": date(2024, 3, 1), "symbol": "QQQ", "close": 400.0},
        ]
    )
    signal_df = pl.DataFrame(
        [
            {"date": date(2024, 3, 1), "symbol": "SPY", "close": 500.0},
            {"date": date(2024, 3, 1), "symbol": "QQQ", "close": 400.0},
        ]
    )

    with Session(engine, future=True) as session:
        save_strategy_run_snapshot(
            session,
            run_id="run-123",
            profile_id="us_etf_daily",
            pipeline_strategy_id="portfolio",
            output_type=StrategyOutputType.TARGET_WEIGHT,
            time_column="date",
            output_frame=output_frame,
            selected_strategy_ids=["etf_rotation", "momentum"],
            strategy_params={"etf_rotation": {"lookback": 90}, "momentum": {"window": 120}},
            risk_limits={"max_single_weight": 0.35, "min_cash_buffer": 0.02},
            market_data_frame=market_df,
            signal_data_frame=signal_df,
        )

        run_row = session.scalar(
            select(StrategyRunRecord).where(StrategyRunRecord.run_id == "run-123")
        )
        snapshot_rows = list(
            session.scalars(
                select(StrategySnapshotRecord)
                .where(StrategySnapshotRecord.run_id == "run-123")
                .order_by(StrategySnapshotRecord.symbol.asc())
            )
        )

    assert run_row is not None
    assert run_row.profile_id == "us_etf_daily"
    assert run_row.pipeline_strategy_id == "portfolio"
    assert run_row.output_type == StrategyOutputType.TARGET_WEIGHT.value
    assert run_row.snapshot_count == 2
    assert json.loads(run_row.selected_strategy_ids_json or "[]") == ["etf_rotation", "momentum"]
    assert json.loads(run_row.strategy_params_json or "{}")["etf_rotation"]["lookback"] == 90
    assert run_row.market_data_asof.date() == date(2024, 3, 1)
    assert run_row.signal_data_asof.date() == date(2024, 3, 1)
    assert run_row.output_asof.date() == date(2024, 3, 1)

    assert len(snapshot_rows) == 2
    assert snapshot_rows[0].symbol == "QQQ"
    assert snapshot_rows[0].target_weight == 0.4
    assert snapshot_rows[1].symbol == "SPY"
    assert snapshot_rows[1].signal_value == 1.0


def test_save_account_snapshot_persists_account_ledger_row(tmp_path):
    db_path = tmp_path / "account-ledger.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    asof = datetime(2024, 3, 4, 21, 0, tzinfo=UTC)
    snapshot = BrokerStateSnapshot(
        positions=[
            PositionSnapshot(
                symbol="SPY",
                qty=100.0,
                market_price=500.0,
                market_value=50000.0,
                account="paper-account",
                asof=asof,
                snapshot_batch_id="batch-001",
            )
        ],
        account_values={
            "Account": "paper-account",
            "NetLiquidation": 100000.0,
            "GrossPositionValue": 50000.0,
            "CashBalance": 50000.0,
            "AvailableFunds": 48000.0,
            "RealizedPnL": 1250.0,
            "UnrealizedPnL": 300.0,
        },
        asof=asof,
    )

    with Session(engine, future=True) as session:
        save_account_snapshot(
            session,
            broker="paper",
            snapshot=snapshot,
            run_id="run-abc",
            profile_id="us_etf_daily",
        )
        row = session.scalar(
            select(AccountSnapshotRecord).where(AccountSnapshotRecord.run_id == "run-abc")
        )

    assert row is not None
    assert row.broker == "paper"
    assert row.account == "paper-account"
    assert row.position_snapshot_batch_id == "batch-001"
    assert row.position_count == 1
    assert row.cash_balance == 50000.0
    assert row.net_liquidation == 100000.0
    assert row.gross_position_value == 50000.0
    assert row.net_position_value == 50000.0
    assert row.available_funds == 48000.0
    assert row.gross_exposure == 0.5
    assert row.net_exposure == 0.5
    assert row.realized_pnl == 1250.0
    assert row.unrealized_pnl == 300.0
    assert json.loads(row.account_values_json or "{}")["NetLiquidation"] == 100000.0
    assert row.asof == asof


def test_save_execution_plan_records_persists_execution_ledger_rows(tmp_path):
    db_path = tmp_path / "execution-ledger.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    plans = [
        RebalanceOrderPlan(
            symbol="SPY",
            side="BUY",
            qty=100.0,
            target_weight=0.5,
            current_qty=0.0,
            target_qty=100.0,
            latest_price=500.0,
            execution_reference_price=501.0,
            estimated_trade_value=50100.0,
            strategy_id="core_portfolio",
            reason="1d_rebalance",
            order_type="MKT",
        ),
        RebalanceOrderPlan(
            symbol="QQQ",
            side="SELL",
            qty=50.0,
            target_weight=0.2,
            current_qty=80.0,
            target_qty=30.0,
            latest_price=400.0,
            execution_reference_price=399.5,
            estimated_trade_value=19975.0,
            strategy_id="core_portfolio",
            order_semantic="reduce",
            reason="trim_position",
            order_type="LMT",
            limit_price=399.0,
        ),
    ]

    with Session(engine, future=True) as session:
        count = save_execution_plan_records(
            session,
            plans,
            run_id="run-plan-001",
            batch_id="batch-plan-001",
            profile_id="us_etf_daily",
            execution_planner_id="bar_close_rebalance",
        )
        rows = list(
            session.scalars(
                select(ExecutionPlanRecord)
                .where(ExecutionPlanRecord.run_id == "run-plan-001")
                .order_by(ExecutionPlanRecord.symbol.asc())
            )
        )

    assert count == 2
    assert len(rows) == 2
    assert rows[0].symbol == "QQQ"
    assert rows[0].plan_id == "batch-plan-001-0002-qqq"
    assert rows[0].order_semantic == "reduce"
    assert rows[0].limit_price == 399.0
    assert rows[1].symbol == "SPY"
    assert rows[1].execution_reference_price == 501.0


def test_save_working_order_snapshots_persists_open_order_batch(tmp_path):
    db_path = tmp_path / "working-order-ledger.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    observed_at = datetime(2024, 3, 4, 21, 5, tzinfo=UTC)

    with Session(engine, future=True) as session:
        result = save_working_order_snapshots(
            session,
            [
                {
                    "broker_order_id": "paper-001",
                    "symbol": "SPY",
                    "side": "BUY",
                    "qty": 100.0,
                    "filled_qty": 20.0,
                    "remaining_qty": 80.0,
                    "avg_fill_price": 500.5,
                    "status": "PartiallyFilled",
                    "order_type": "LMT",
                    "limit_price": 501.0,
                    "submitted_at": observed_at,
                }
            ],
            broker="paper",
            run_id="run-open-001",
            profile_id="us_etf_daily",
            default_account="paper-account",
            observed_at=observed_at,
        )
        row = session.scalar(
            select(WorkingOrderSnapshotRecord).where(
                WorkingOrderSnapshotRecord.broker_order_id == "paper-001"
            )
        )

    assert result["count"] == 1
    assert result["snapshot_batch_id"] is not None
    assert row is not None
    assert row.run_id == "run-open-001"
    assert row.account == "paper-account"
    assert row.open_order_snapshot_batch_id == result["snapshot_batch_id"]
    assert row.remaining_qty == 80.0
    assert row.status == "PartiallyFilled"
    assert row.observed_at == observed_at

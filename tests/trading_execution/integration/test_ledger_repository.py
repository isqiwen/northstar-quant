import json
from datetime import UTC, date, datetime

import polars as pl
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from northstar_quant.foundation.common.enums import StrategyOutputType
from northstar_quant.foundation.db.models import (
    AccountSnapshotRecord,
    ExecutionPlanRecord,
    LedgerAdjustmentRecord,
    SettlementRecord,
    StrategyRunRecord,
    StrategySnapshotRecord,
    WorkingOrderSnapshotRecord,
)
from northstar_quant.foundation.db.repositories import (
    save_account_snapshot,
    save_execution_plan_records,
    record_controlled_ledger_adjustment,
    save_settlement_record,
    save_strategy_run_snapshot,
    save_working_order_snapshots,
)
from northstar_quant.trading_execution.execution.models import BrokerStateSnapshot, PositionSnapshot, RebalanceOrderPlan


def test_save_strategy_run_snapshot_persists_strategy_ledger_rows(postgresql_engine):
    engine = postgresql_engine

    output_frame = pl.DataFrame(
        [
            {"date": date(2024, 3, 1), "symbol": "RB2405", "signal_value": 1.0, "target_weight": 0.6},
            {"date": date(2024, 3, 1), "symbol": "I2405", "signal_value": 0.5, "target_weight": 0.4},
        ]
    )
    market_df = pl.DataFrame(
        [
            {"date": date(2024, 2, 29), "symbol": "RB_CONT", "close": 3600.0},
            {"date": date(2024, 3, 1), "symbol": "I_CONT", "close": 800.0},
        ]
    )
    signal_df = pl.DataFrame(
        [
            {"date": date(2024, 3, 1), "symbol": "RB_CONT", "close": 3600.0},
            {"date": date(2024, 3, 1), "symbol": "I_CONT", "close": 800.0},
        ]
    )

    with Session(engine, future=True) as session:
        save_strategy_run_snapshot(
            session,
            run_id="run-123",
            profile_id="cn_futures_daily_live",
            pipeline_strategy_id="portfolio",
            output_type=StrategyOutputType.TARGET_WEIGHT,
            time_column="date",
            output_frame=output_frame,
            selected_strategy_ids=["futures_trend"],
            strategy_params={"futures_trend": {"lookback_days": 60}},
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
    assert run_row.profile_id == "cn_futures_daily_live"
    assert run_row.pipeline_strategy_id == "portfolio"
    assert run_row.output_type == StrategyOutputType.TARGET_WEIGHT.value
    assert run_row.snapshot_count == 2
    assert json.loads(run_row.selected_strategy_ids_json or "[]") == ["futures_trend"]
    assert json.loads(run_row.strategy_params_json or "{}")["futures_trend"]["lookback_days"] == 60
    assert run_row.market_data_asof.date() == date(2024, 3, 1)
    assert run_row.signal_data_asof.date() == date(2024, 3, 1)
    assert run_row.output_asof.date() == date(2024, 3, 1)

    assert len(snapshot_rows) == 2
    assert snapshot_rows[0].symbol == "I2405"
    assert snapshot_rows[0].target_weight == 0.4
    assert snapshot_rows[1].symbol == "RB2405"
    assert snapshot_rows[1].signal_value == 1.0


def test_save_account_snapshot_persists_account_ledger_row(postgresql_engine):
    engine = postgresql_engine
    asof = datetime(2024, 3, 4, 21, 0, tzinfo=UTC)
    snapshot = BrokerStateSnapshot(
        positions=[
            PositionSnapshot(
                symbol="RB2405",
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
            profile_id="cn_futures_daily_live",
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


def test_save_execution_plan_records_persists_execution_ledger_rows(postgresql_engine):
    engine = postgresql_engine
    plans = [
        RebalanceOrderPlan(
            symbol="RB2405",
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
            symbol="I2405",
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
            profile_id="cn_futures_daily_live",
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
    assert rows[0].symbol == "I2405"
    assert rows[0].plan_id == "batch-plan-001-0002-i2405"
    assert rows[0].order_semantic == "reduce"
    assert rows[0].limit_price == 399.0
    assert rows[1].symbol == "RB2405"
    assert rows[1].execution_reference_price == 501.0


def test_save_working_order_snapshots_persists_open_order_batch(postgresql_engine):
    engine = postgresql_engine
    observed_at = datetime(2024, 3, 4, 21, 5, tzinfo=UTC)

    with Session(engine, future=True) as session:
        result = save_working_order_snapshots(
            session,
            [
                {
                    "broker_order_id": "paper-001",
                    "symbol": "RB2405",
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
            profile_id="cn_futures_daily_live",
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


def test_settlement_and_controlled_adjustment_are_append_only_and_idempotent(
    postgresql_engine,
):
    engine = postgresql_engine
    settled_at = datetime(2026, 8, 22, 7, tzinfo=UTC)

    with Session(engine, future=True) as session:
        settlement = save_settlement_record(
            session,
            settlement_id="ctp-settlement-20260821",
            settlement_date=date(2026, 8, 21),
            broker="ctp_sim",
            account="sim-ledger",
            profile_id="cn_futures_daily_trend_simulated",
            account_snapshot_id=None,
            cash_balance=98_500.0,
            margin=12_000.0,
            realized_pnl=400.0,
            unrealized_pnl=100.0,
            fee=-15.0,
            currency="CNY",
            evidence={"source": "ctp_sim", "settlement_ref": "20260821"},
            settled_at=settled_at,
        )
        replay = save_settlement_record(
            session,
            settlement_id="ctp-settlement-20260821",
            settlement_date=date(2026, 8, 21),
            broker="ctp_sim",
            account="sim-ledger",
            profile_id="cn_futures_daily_trend_simulated",
            account_snapshot_id=None,
            cash_balance=98_500.0,
            margin=12_000.0,
            realized_pnl=400.0,
            unrealized_pnl=100.0,
            fee=-15.0,
            currency="CNY",
            evidence={"source": "ctp_sim", "settlement_ref": "20260821"},
            settled_at=settled_at,
        )
        adjustment = record_controlled_ledger_adjustment(
            session,
            adjustment_id="adj-20260822-001",
            broker="ctp_sim",
            account="sim-ledger",
            profile_id="cn_futures_daily_trend_simulated",
            amount=-3.5,
            currency="CNY",
            reason="broker fee correction",
            approver_id="risk-owner",
            evidence={"ticket": "OPS-42", "broker_notice": "fee correction"},
            occurred_at=settled_at,
        )
        assert replay.id == settlement.id
        assert session.scalar(select(SettlementRecord)) is not None
        assert session.scalar(select(LedgerAdjustmentRecord)) is not None

        with pytest.raises(RuntimeError, match="SETTLEMENT_IDENTITY_MISMATCH"):
            save_settlement_record(
                session,
                settlement_id="ctp-settlement-20260821",
                settlement_date=date(2026, 8, 21),
                broker="ctp_sim",
                account="sim-ledger",
                profile_id="cn_futures_daily_trend_simulated",
                account_snapshot_id=None,
                cash_balance=98_501.0,
                margin=12_000.0,
                realized_pnl=400.0,
                unrealized_pnl=100.0,
                fee=-15.0,
                currency="CNY",
                evidence={"source": "ctp_sim", "settlement_ref": "20260821"},
                settled_at=settled_at,
            )
        with pytest.raises(ValueError, match="approver_id"):
            record_controlled_ledger_adjustment(
                session,
                adjustment_id="adj-20260822-002",
                broker="ctp_sim",
                account="sim-ledger",
                profile_id="cn_futures_daily_trend_simulated",
                amount=1.0,
                currency="CNY",
                reason="unapproved",
                approver_id="",
                evidence={"ticket": "OPS-43"},
                occurred_at=settled_at,
            )

    assert adjustment.amount == -3.5

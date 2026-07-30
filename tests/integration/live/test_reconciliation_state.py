from datetime import UTC, datetime

import polars as pl
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from northstar_quant.db.models import (
    AccountSnapshotRecord,
    BrokerSyncLog,
    OrderRecord,
    PositionSnapshotBatchRecord,
)
from northstar_quant.execution.models import (
    BrokerStateSnapshot,
    PositionSnapshot,
)
from northstar_quant.live.reconciliation import (
    analyze_position_drift,
    reconcile_broker_state,
)


class _FakeBroker:
    def __init__(self, snapshot: BrokerStateSnapshot) -> None:
        self.snapshot = snapshot

    def sync_state(self) -> BrokerStateSnapshot:
        return self.snapshot

    def get_name(self) -> str:
        return "ctp"

    def get_account(self) -> str:
        return "DU123456"


def test_reconcile_persists_empty_position_batch_and_explicit_snapshot_account(postgresql_engine):
    engine = postgresql_engine
    asof = datetime(2026, 7, 23, 2, 30, tzinfo=UTC)
    snapshot = BrokerStateSnapshot(
        positions=[],
        account_values={"NetLiquidation": 100000.0},
        account="DU123456",
        asof=asof,
    )

    with Session(engine, future=True) as session:
        result = reconcile_broker_state(
            session,
            _FakeBroker(snapshot),
            run_id="run-empty",
            profile_id="trend-live",
        )
        batch = session.scalar(select(PositionSnapshotBatchRecord))
        account_snapshot = session.scalar(select(AccountSnapshotRecord))

    assert result["positions_synced"] == 0
    assert batch is not None
    assert batch.position_count == 0
    assert batch.broker == "ctp"
    assert batch.account == "DU123456"
    assert account_snapshot is not None
    assert account_snapshot.account == "DU123456"
    assert account_snapshot.position_snapshot_batch_id == batch.snapshot_batch_id


def test_reconcile_rejects_state_errors_even_when_complete_flag_is_true(postgresql_engine):
    engine = postgresql_engine
    snapshot = BrokerStateSnapshot(
        account="DU123456",
        state_complete=True,
        state_errors=["持仓查询超时"],
    )

    with Session(engine, future=True) as session:
        with pytest.raises(RuntimeError, match="持仓查询超时"):
            reconcile_broker_state(session, _FakeBroker(snapshot))

        assert session.scalar(select(PositionSnapshotBatchRecord)) is None


def test_position_drift_uses_scoped_account_equity_instead_of_net_position_value(
    postgresql_engine,
):
    engine = postgresql_engine
    asof = datetime(2026, 7, 23, 2, 30, tzinfo=UTC)
    snapshot = BrokerStateSnapshot(
        positions=[
            PositionSnapshot(
                symbol="RB2405",
                qty=1,
                market_price=100.0,
                market_value=100.0,
                account="DU123456",
                asof=asof,
            ),
            PositionSnapshot(
                symbol="CU2405",
                qty=-1,
                market_price=100.0,
                market_value=-100.0,
                account="DU123456",
                asof=asof,
            ),
        ],
        account_values={"NetLiquidation": 1_000.0},
        account="DU123456",
        asof=asof,
    )
    targets = pl.DataFrame(
        {
            "symbol": ["RB2405", "CU2405"],
            "target_weight": [0.1, -0.1],
        }
    )

    with Session(engine, future=True) as session:
        reconcile_broker_state(
            session,
            _FakeBroker(snapshot),
            run_id="run-drift",
            profile_id="trend-live",
        )
        result = analyze_position_drift(
            session,
            targets,
            {"RB2405": 100.0, "CU2405": 100.0},
            broker="ctp",
            account="DU123456",
            profile_id="trend-live",
        )

    assert result["summary"]["equity"] == 1_000.0
    assert result["summary"]["total_abs_weight_diff"] == pytest.approx(0.0)


def test_reconcile_rolls_back_all_state_rows_when_later_identity_check_fails(
    postgresql_engine,
):
    engine = postgresql_engine
    asof = datetime(2026, 7, 23, 2, 30, tzinfo=UTC)
    snapshot = BrokerStateSnapshot(
        positions=[
            PositionSnapshot(
                symbol="RB2405",
                qty=1,
                account="DU123456",
                asof=asof,
            )
        ],
        completed_orders=[
            {
                "broker_order_id": "bad-identity",
                "account": "DU123456",
                "symbol": "CU2405",
                "side": "BUY",
                "qty": 1.0,
                "status": "Cancelled",
            }
        ],
        account="DU123456",
        asof=asof,
    )

    with Session(engine, future=True) as session:
        session.add(
            OrderRecord(
                strategy_id="test",
                symbol="RB2405",
                side="BUY",
                qty=1.0,
                broker="ctp",
                account="DU123456",
                broker_order_id="bad-identity",
                status="Submitted",
                submitted_at=asof,
            )
        )
        session.commit()

        with pytest.raises(RuntimeError, match="IDENTITY_MISMATCH"):
            reconcile_broker_state(
                session,
                _FakeBroker(snapshot),
                run_id="atomic-run",
                profile_id="trend-live",
            )

        assert session.scalar(select(PositionSnapshotBatchRecord)) is None
        assert session.scalar(select(AccountSnapshotRecord)) is None
        sync_log = session.scalar(select(BrokerSyncLog))

    assert sync_log is not None
    assert sync_log.status == "failed"

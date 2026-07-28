from datetime import UTC, datetime

import polars as pl
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from tests.postgresql import postgresql_test_url

from northstar_quant.db.base import Base
from northstar_quant.db.models import (
    AccountSnapshotRecord,
    BrokerSyncLog,
    CancelRecord,
    FillRecord,
    OrderRecord,
    PositionSnapshotBatchRecord,
    TradeAttributionRecord,
)
from northstar_quant.db.repositories import list_execution_recovery_blockers
from northstar_quant.execution.models import (
    BrokerStateSnapshot,
    FillSnapshot,
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


def test_reconcile_persists_empty_position_batch_and_explicit_snapshot_account(tmp_path):
    engine = create_engine(
        postgresql_test_url(tmp_path / "empty-reconciliation.db"),
        future=True,
    )
    Base.metadata.create_all(bind=engine)
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


def test_reconcile_rejects_state_errors_even_when_complete_flag_is_true(tmp_path):
    engine = create_engine(
        postgresql_test_url(tmp_path / "state-errors.db"),
        future=True,
    )
    Base.metadata.create_all(bind=engine)
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
    tmp_path,
):
    engine = create_engine(
        postgresql_test_url(tmp_path / "drift-equity.db"),
        future=True,
    )
    Base.metadata.create_all(bind=engine)
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
    tmp_path,
):
    engine = create_engine(
        postgresql_test_url(tmp_path / "atomic-reconciliation.db"),
        future=True,
    )
    Base.metadata.create_all(bind=engine)
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


def test_reconcile_recovers_completed_order_and_cancel_terminal_states(tmp_path):
    db_path = tmp_path / "completed-order-reconciliation.db"
    engine = create_engine(postgresql_test_url(db_path), future=True)
    Base.metadata.create_all(bind=engine)
    asof = datetime(2026, 7, 23, 2, 30, tzinfo=UTC)
    snapshot = BrokerStateSnapshot(
        open_orders=[
            {
                "broker_order_id": "11",
                "account": "DU123456",
                "symbol": "I2405",
                "side": "BUY",
                "qty": 1.0,
                "filled_qty": 0.0,
                "remaining_qty": 1.0,
                "status": "Submitted",
            }
        ],
        completed_orders=[
            {
                "broker_order_id": "10",
                "account": "DU123456",
                "symbol": "RB2405",
                "side": "BUY",
                "qty": 2.0,
                "filled_qty": 0.0,
                "remaining_qty": 2.0,
                "status": "Cancelled",
            }
        ],
        account_values={
            "Account": "DU123456",
            "NetLiquidation": 100000.0,
            "CashBalance": 100000.0,
        },
        account="DU123456",
        asof=asof,
    )

    with Session(engine, future=True) as session:
        completed_order = OrderRecord(
            strategy_id="core_portfolio",
            symbol="RB2405",
            side="BUY",
            qty=2.0,
            broker="ctp",
            account="DU123456",
            broker_order_id="10",
            status="PendingCancel",
            submitted_at=asof,
        )
        open_order = OrderRecord(
            strategy_id="core_portfolio",
            symbol="I2405",
            side="BUY",
            qty=1.0,
            broker="ctp",
            account="DU123456",
            broker_order_id="11",
            status="PendingSubmit",
            submitted_at=asof,
        )
        other_account_order = OrderRecord(
            strategy_id="core_portfolio",
            symbol="RB2405",
            side="BUY",
            qty=2.0,
            broker="ctp",
            account="DU-OTHER",
            broker_order_id="10",
            status="PendingCancel",
            submitted_at=asof,
        )
        session.add_all([completed_order, open_order, other_account_order])
        session.flush()
        session.add_all(
            [
                CancelRecord(
                    order_id=completed_order.id,
                    broker="ctp",
                    broker_order_id="10",
                    account="DU123456",
                    status="PendingCancel",
                    requested_at=asof,
                ),
                CancelRecord(
                    order_id=other_account_order.id,
                    broker="ctp",
                    broker_order_id="10",
                    account="DU-OTHER",
                    status="PendingCancel",
                    requested_at=asof,
                ),
            ]
        )
        session.commit()

        result = reconcile_broker_state(session, _FakeBroker(snapshot))

        refreshed_completed = session.get(OrderRecord, completed_order.id)
        refreshed_open = session.get(OrderRecord, open_order.id)
        refreshed_other = session.get(OrderRecord, other_account_order.id)
        cancel_rows = list(
            session.scalars(
                select(CancelRecord).order_by(CancelRecord.id.asc())
            )
        )

    assert result["open_orders_count"] == 1
    assert result["completed_orders_count"] == 1
    assert result["updated_order_statuses"] == 2
    assert result["updated_cancel_statuses"] == 1
    assert refreshed_completed is not None
    assert refreshed_completed.status == "Cancelled"
    assert refreshed_open is not None
    assert refreshed_open.status == "Submitted"
    assert refreshed_other is not None
    assert refreshed_other.status == "PendingCancel"
    assert [row.status for row in cancel_rows] == ["Cancelled", "PendingCancel"]


def test_reconcile_does_not_finalize_cancel_from_non_terminal_order_state(tmp_path):
    db_path = tmp_path / "pending-cancel-reconciliation.db"
    engine = create_engine(postgresql_test_url(db_path), future=True)
    Base.metadata.create_all(bind=engine)
    asof = datetime(2026, 7, 23, 2, 30, tzinfo=UTC)
    snapshot = BrokerStateSnapshot(
        open_orders=[
            {
                "broker_order_id": "12",
                "account": "DU123456",
                "symbol": "IWM",
                "status": "PendingCancel",
            }
        ],
        account_values={
            "Account": "DU123456",
            "NetLiquidation": 100000.0,
            "CashBalance": 100000.0,
        },
        account="DU123456",
        asof=asof,
    )

    with Session(engine, future=True) as session:
        order = OrderRecord(
            strategy_id="core_portfolio",
            symbol="IWM",
            side="BUY",
            qty=1.0,
            broker="ctp",
            account="DU123456",
            broker_order_id="12",
            status="PendingCancel",
            submitted_at=asof,
        )
        session.add(order)
        session.flush()
        cancel = CancelRecord(
            order_id=order.id,
            broker="ctp",
            broker_order_id="12",
            account="DU123456",
            status="PendingCancel",
            requested_at=asof,
        )
        session.add(cancel)
        session.commit()

        result = reconcile_broker_state(session, _FakeBroker(snapshot))
        refreshed_cancel = session.get(CancelRecord, cancel.id)

    assert result["updated_cancel_statuses"] == 0
    assert refreshed_cancel is not None
    assert refreshed_cancel.status == "PendingCancel"


def test_reconcile_recovers_idless_completed_order_fill_and_failed_cancel(
    tmp_path,
):
    engine = create_engine(
        postgresql_test_url(tmp_path / "completed-strong-identity.db"),
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    asof = datetime(2026, 7, 23, 2, 30, tzinfo=UTC)
    snapshot = BrokerStateSnapshot(
        completed_orders=[
            {
                "broker_order_id": None,
                "account": "DU123456",
                "client_id": None,
                "order_ref": "NSQ-crash-window",
                "perm_id": 1001,
                "instrument_id": "rb2601",
                "symbol": "RB2405",
                "side": "BUY",
                "qty": 2.0,
                "filled_qty": 2.0,
                "remaining_qty": 0.0,
                "status": "Filled",
                "order_type": "LMT",
                "limit_price": 500.0,
            }
        ],
        fills=[
            FillSnapshot(
                broker_order_id="77",
                symbol="RB2405",
                qty=2.0,
                price=499.5,
                side="BUY",
                filled_at=asof,
                account="DU123456",
                exec_id="exec-crash-window",
                order_ref="NSQ-crash-window",
                perm_id=1001,
                client_id=7,
                instrument_id="rb2601",
            )
        ],
        account_values={
            "Account": "DU123456",
            "NetLiquidation": 100000.0,
            "CashBalance": 100000.0,
        },
        account="DU123456",
        asof=asof,
    )

    with Session(engine, future=True) as session:
        order = OrderRecord(
            strategy_id="core_portfolio",
            symbol="RB2405",
            side="BUY",
            qty=2.0,
            order_type="LMT",
            limit_price=500.0,
            broker="ctp",
            account="DU123456",
            order_ref="NSQ-crash-window",
            instrument_id="rb2601",
            reference_price=500.0,
            status="SubmissionUnknown",
        )
        session.add(order)
        session.flush()
        cancel = CancelRecord(
            order_id=order.id,
            broker="ctp",
            broker_order_id="77",
            account="DU123456",
            status="CancelRequestFailed",
            requested_at=asof,
        )
        session.add(cancel)
        session.commit()

        assert list_execution_recovery_blockers(
            session,
            broker="ctp",
            account="DU123456",
        )
        result = reconcile_broker_state(session, _FakeBroker(snapshot))
        refreshed_order = session.get(OrderRecord, order.id)
        refreshed_cancel = session.get(CancelRecord, cancel.id)
        fill = session.scalar(
            select(FillRecord).where(
                FillRecord.exec_id == "exec-crash-window"
            )
        )
        assert fill is not None
        attribution = session.scalar(
            select(TradeAttributionRecord).where(
                TradeAttributionRecord.fill_id == fill.id
            )
        )
        blockers = list_execution_recovery_blockers(
            session,
            broker="ctp",
            account="DU123456",
        )

        assert result["updated_order_statuses"] == 1
        assert result["updated_cancel_statuses"] == 1
        assert refreshed_order is not None
        assert refreshed_order.status == "Filled"
        assert refreshed_order.perm_id == 1001
        assert refreshed_order.filled_qty == 2.0
        assert refreshed_order.remaining_qty == 0.0
        assert refreshed_cancel is not None
        assert refreshed_cancel.status == "Filled"
        assert fill.order_id == order.id
        assert attribution is not None
        assert attribution.order_id == order.id
        assert blockers == []


def test_idless_completed_order_cannot_finalize_legacy_cancel_by_similarity(
    tmp_path,
):
    engine = create_engine(
        postgresql_test_url(tmp_path / "completed-no-legacy-guess.db"),
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    asof = datetime(2026, 7, 23, 2, 30, tzinfo=UTC)
    snapshot = BrokerStateSnapshot(
        completed_orders=[
            {
                "broker_order_id": None,
                "account": "DU123456",
                "client_id": 7,
                "order_ref": "NSQ-another-order",
                "perm_id": 2002,
                "instrument_id": "rb2601",
                "symbol": "RB2405",
                "side": "BUY",
                "qty": 1.0,
                "filled_qty": 0.0,
                "remaining_qty": 1.0,
                "status": "Cancelled",
                "order_type": "LMT",
                "limit_price": 500.0,
            }
        ],
        account_values={
            "Account": "DU123456",
            "NetLiquidation": 100000.0,
            "CashBalance": 100000.0,
        },
        account="DU123456",
        asof=asof,
    )

    with Session(engine, future=True) as session:
        legacy_order = OrderRecord(
            strategy_id="legacy",
            symbol="RB2405",
            side="BUY",
            qty=1.0,
            order_type="LMT",
            limit_price=500.0,
            broker="ctp",
            account="DU123456",
            broker_order_id="88",
            client_id=7,
            status="PendingCancel",
        )
        session.add(legacy_order)
        session.flush()
        legacy_cancel = CancelRecord(
            order_id=legacy_order.id,
            broker="ctp",
            broker_order_id="88",
            account="DU123456",
            status="PendingCancel",
            requested_at=asof,
        )
        session.add(legacy_cancel)
        session.commit()

        result = reconcile_broker_state(session, _FakeBroker(snapshot))
        refreshed_cancel = session.get(CancelRecord, legacy_cancel.id)

        assert result["updated_cancel_statuses"] == 0
        assert refreshed_cancel is not None
        assert refreshed_cancel.status == "PendingCancel"

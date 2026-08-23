from sqlalchemy import select

import northstar_quant.trading_execution.broker.ctp_sim_broker as ctp_sim_broker
from northstar_quant.foundation.config.settings import Settings, get_settings
from northstar_quant.foundation.db.models import FillRecord, OrderRecord, PositionSnapshotRecord
from northstar_quant.trading_execution.broker.ctp_sim_broker import CtpSimBrokerAdapter
from northstar_quant.trading_execution.execution.models import OrderRequest
from northstar_quant.trading_execution.orders.durable_submission import DurableBrokerAdapter
from northstar_quant.trading_execution.reconciliation.reconciliation import reconcile_broker_state
from tests.helpers.ctp_sim_submission import create_test_ctp_sim_submission_authority


def test_ctp_sim_disconnect_recovery_reconciles_order_fill_and_position(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    settings = Settings(
        _env_file=None,
        storage_dir=tmp_path / "storage",
        downloads_dir=tmp_path / "storage" / "downloads",
        reports_dir=tmp_path / "reports",
        log_dir=tmp_path / "logs",
        ctp_sim_state_path=tmp_path / "storage" / "ctp_sim_state.json",
        ctp_sim_account="ctp-sim-recovery",
        default_cash=1000000,
    )
    monkeypatch.setattr(ctp_sim_broker, "get_settings", lambda: settings)
    try:
        submission_authority = create_test_ctp_sim_submission_authority()
        broker = CtpSimBrokerAdapter(submission_authority=submission_authority)
        broker.connect()
        broker.seed_market_quotes({"RB2610": 3100.0})
        with postgresql_session_factory() as session:
            durable = DurableBrokerAdapter(
                broker,
                session,
                ctp_sim_submission_authority=submission_authority,
            )
            result = durable.submit_order(
                OrderRequest(
                    strategy_id="ctp-sim-recovery",
                    profile_id="cn_futures_daily_trend_simulated",
                    symbol="RB2610",
                    side="BUY",
                    qty=2.0,
                    plan_id="ctp-sim-recovery-plan",
                    batch_id="ctp-sim-recovery-batch",
                    run_id="ctp-sim-recovery-run",
                    account="ctp-sim-recovery",
                    reference_price=3100.0,
                    reference_price_source="ctp_sim_market_data",
                    planned_trade_value=62000.0,
                    instrument_id="rb2610",
                    exchange_id="SHFE",
                    ctp_offset="open",
                    volume_multiple=10,
                    margin_rate=0.1,
                    required_margin=6200.0,
                )
            )
            assert result.status == "ACCEPTED"
        broker.disconnect()

        recovered = CtpSimBrokerAdapter(
            submission_authority=create_test_ctp_sim_submission_authority(),
        )
        recovered.connect()
        snapshot = recovered.sync_state()
        with postgresql_session_factory() as session:
            result = reconcile_broker_state(
                session,
                recovered,
                snapshot=snapshot,
                run_id="ctp-sim-recovery-sync",
                profile_id="cn_futures_daily_trend_simulated",
            )
            order = session.scalar(
                select(OrderRecord).where(
                    OrderRecord.plan_id == "ctp-sim-recovery-plan"
                )
            )
            fill = session.scalar(
                select(FillRecord).where(
                    FillRecord.exec_id == "CTPSIM-EXEC-00000001"
                )
            )
            position = session.scalar(
                select(PositionSnapshotRecord)
                .where(PositionSnapshotRecord.symbol == "RB2610")
                .order_by(PositionSnapshotRecord.id.desc())
            )

        assert result["fills_synced"] == 1
        assert order is not None
        assert order.status == "Filled"
        assert order.ctp_offset == "open"
        assert order.required_margin == 6200.0
        assert fill is not None
        assert fill.order_id == order.id
        assert fill.ctp_offset == "open"
        assert position is not None
        assert position.instrument_id == "rb2610"
        assert position.exchange_id == "SHFE"
        assert position.long_today_qty == 2.0
        assert position.long_yesterday_qty == 0.0
    finally:
        get_settings.cache_clear()


def test_ctp_sim_cancel_pending_recovers_as_cancelled_without_a_fill(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    settings = Settings(
        _env_file=None,
        storage_dir=tmp_path / "storage",
        downloads_dir=tmp_path / "storage" / "downloads",
        reports_dir=tmp_path / "reports",
        log_dir=tmp_path / "logs",
        ctp_sim_state_path=tmp_path / "storage" / "ctp_sim_state.json",
        ctp_sim_account="ctp-sim-cancel-recovery",
        default_cash=1000000,
    )
    monkeypatch.setattr(ctp_sim_broker, "get_settings", lambda: settings)
    try:
        submission_authority = create_test_ctp_sim_submission_authority()
        broker = CtpSimBrokerAdapter(submission_authority=submission_authority)
        broker.connect()
        broker.seed_market_quotes({"RB2610": 3100.0})
        with postgresql_session_factory() as session:
            durable = DurableBrokerAdapter(
                broker,
                session,
                ctp_sim_submission_authority=submission_authority,
            )
            result = durable.submit_order(
                OrderRequest(
                    strategy_id="ctp-sim-cancel-recovery",
                    profile_id="cn_futures_daily_trend_simulated",
                    symbol="RB2610",
                    side="BUY",
                    qty=2.0,
                    order_type="LMT",
                    limit_price=3000.0,
                    plan_id="ctp-sim-cancel-recovery-plan",
                    batch_id="ctp-sim-cancel-recovery-batch",
                    run_id="ctp-sim-cancel-recovery-run",
                    account="ctp-sim-cancel-recovery",
                    reference_price=3100.0,
                    reference_price_source="ctp_sim_market_data",
                    planned_trade_value=62000.0,
                    instrument_id="rb2610",
                    exchange_id="SHFE",
                    ctp_offset="open",
                    volume_multiple=10,
                    margin_rate=0.1,
                    required_margin=6200.0,
                )
            )
            assert durable.cancel_order(result.broker_order_id) is True
        broker.disconnect()

        recovered = CtpSimBrokerAdapter(
            submission_authority=create_test_ctp_sim_submission_authority(),
        )
        recovered.connect()
        snapshot = recovered.sync_state()
        with postgresql_session_factory() as session:
            reconcile_broker_state(
                session,
                recovered,
                snapshot=snapshot,
                run_id="ctp-sim-cancel-recovery-sync",
                profile_id="cn_futures_daily_trend_simulated",
            )
            order = session.scalar(
                select(OrderRecord).where(
                    OrderRecord.plan_id == "ctp-sim-cancel-recovery-plan"
                )
            )

        assert snapshot.fills == []
        assert snapshot.open_orders == []
        assert snapshot.completed_orders[0]["status"] == "Cancelled"
        assert order is not None
        assert order.status == "Cancelled"
        assert order.filled_qty == 0.0
        assert order.remaining_qty == 2.0
    finally:
        get_settings.cache_clear()

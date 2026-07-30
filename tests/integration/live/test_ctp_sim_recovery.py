from sqlalchemy import select

from northstar_quant.config.settings import get_settings
from northstar_quant.db.models import FillRecord, OrderRecord, PositionSnapshotRecord
from northstar_quant.execution.ctp_sim_broker import CtpSimBrokerAdapter
from northstar_quant.execution.models import OrderRequest
from northstar_quant.live.durable_submission import DurableBrokerAdapter
from northstar_quant.live.reconciliation import reconcile_broker_state


def test_ctp_sim_disconnect_recovery_reconciles_order_fill_and_position(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
):
    monkeypatch.setenv(
        "NORTHSTAR_CTP_SIM_STATE_PATH",
        str(tmp_path / "ctp_sim_state.json"),
    )
    monkeypatch.setenv("NORTHSTAR_CTP_SIM_ACCOUNT", "ctp-sim-recovery")
    monkeypatch.setenv("NORTHSTAR_DEFAULT_CASH", "1000000")
    get_settings.cache_clear()
    try:
        broker = CtpSimBrokerAdapter()
        broker.connect()
        broker.seed_market_quotes({"RB2610": 3100.0})
        with postgresql_session_factory() as session:
            durable = DurableBrokerAdapter(broker, session)
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
            assert result.status == "Submitted"
        broker.disconnect()

        recovered = CtpSimBrokerAdapter()
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

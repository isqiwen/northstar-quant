"""Paper 适配器到账本与报告的基础设施闭环测试。

该测试使用显式测试订单，不经过期货策略 planner；它验证的是本地基础设施，而不是
连续合约可交易性或真实期货成交语义。
"""

from pathlib import Path

import northstar_quant.trading_execution.broker.paper_broker as paper_broker
from northstar_quant.platform.config.settings import Settings, get_settings
from northstar_quant.trading_execution.execution.models import OrderRequest
from northstar_quant.trading_execution.broker.paper_broker import PaperBrokerAdapter
from northstar_quant.trading_execution.orders.durable_submission import DurableBrokerAdapter
from northstar_quant.trading_execution.reconciliation.reconciliation import reconcile_broker_state
from northstar_quant.application import reporting as report_builder


def test_paper_order_reconcile_attribution_and_report_infrastructure(
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
        default_cash=100000,
        paper_account="paper-e2e",
        paper_fill_price_mode="reference",
    )
    monkeypatch.setattr(paper_broker, "get_settings", lambda: settings)
    try:
        broker = PaperBrokerAdapter()
        testing_session = postgresql_session_factory

        with testing_session() as session:
            DurableBrokerAdapter(broker, session).submit_order(
                OrderRequest(
                    strategy_id="paper-infrastructure-test",
                    symbol="RB_TEST",
                    side="BUY",
                    qty=10.0,
                    plan_id="paper-e2e-plan-1",
                    reference_price=100.0,
                    account="paper-e2e",
                )
            )
            first = reconcile_broker_state(
                session,
                broker,
                snapshot=broker.sync_state(),
                run_id="paper-e2e-1",
                profile_id="paper-infrastructure-test",
            )

        with testing_session() as session:
            DurableBrokerAdapter(broker, session).submit_order(
                OrderRequest(
                    strategy_id="paper-infrastructure-test",
                    symbol="RB_TEST",
                    side="BUY",
                    qty=1.0,
                    plan_id="paper-e2e-plan-2",
                    reference_price=110.0,
                    account="paper-e2e",
                )
            )
            second = reconcile_broker_state(
                session,
                broker,
                snapshot=broker.sync_state(),
                run_id="paper-e2e-2",
                profile_id="paper-infrastructure-test",
            )

        monkeypatch.setattr(report_builder, "SessionLocal", testing_session)
        monkeypatch.setattr(report_builder, "get_settings", lambda: settings)
        attribution = report_builder.latest_live_account_attribution_summary(
            profile_id="paper-infrastructure-test",
            account="paper-e2e",
        )
        report_path = report_builder.build_markdown_report(
            report_type="daily",
            strategy_id="paper-infrastructure-test",
            metrics={"基础设施闭环": "通过"},
            period_label="paper-e2e",
            profile_id="paper-infrastructure-test",
            live_account_attribution=attribution,
        )

        assert first["positions_synced"] == 1
        assert second["positions_synced"] == 1
        assert second["account_attribution_id"] is not None
        assert attribution is not None
        assert attribution["equity_change"] == 100.0
        assert attribution["price_pnl"] == 100.0
        assert Path(report_path).exists()
    finally:
        get_settings.cache_clear()

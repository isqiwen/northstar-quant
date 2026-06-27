from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from northstar_quant.db.base import Base
from northstar_quant.db.models import (
    AccountAttributionRecord,
    AnomalyEventRecord,
    RunHealthRecord,
    TradeAttributionRecord,
)
from northstar_quant.live import service as live_service


def test_recent_trade_attributions_returns_serializable_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "trade-attribution-service.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    monkeypatch.setattr(live_service, "SessionLocal", testing_session)

    with testing_session() as session:
        session.add(
            TradeAttributionRecord(
                broker_order_id="paper-123",
                run_id="run-001",
                batch_id="batch-001",
                plan_id="plan-001",
                profile_id="us_etf_daily",
                account="paper-account",
                strategy_id="core_portfolio",
                execution_planner_id="bar_close_rebalance",
                symbol="SPY",
                side="BUY",
                qty=100.0,
                fill_price=100.5,
                reference_price=100.0,
                reference_price_source="broker_snapshot",
                actual_notional=10050.0,
                reference_notional=10000.0,
                implementation_shortfall=50.0,
                implementation_shortfall_bps=50.0,
                order_semantic="rebalance",
                reason="1d_rebalance",
                attributed_at=datetime(2024, 3, 4, 15, 36, tzinfo=UTC),
            )
        )
        session.commit()

    rows = live_service.recent_trade_attributions(limit=5, profile_id="us_etf_daily")

    assert len(rows) == 1
    assert rows[0]["profile_id"] == "us_etf_daily"
    assert rows[0]["account"] == "paper-account"
    assert rows[0]["symbol"] == "SPY"
    assert rows[0]["implementation_shortfall_bps"] == 50.0
    assert rows[0]["attributed_at"] == "2024-03-04T15:36:00+00:00"


def test_recent_account_attributions_returns_serializable_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "account-attribution-service.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    monkeypatch.setattr(live_service, "SessionLocal", testing_session)

    with testing_session() as session:
        session.add(
            AccountAttributionRecord(
                run_id="run-002",
                profile_id="us_etf_daily",
                broker="paper",
                account="paper-account",
                start_asof=datetime(2024, 3, 4, 15, 30, tzinfo=UTC),
                end_asof=datetime(2024, 3, 4, 15, 45, tzinfo=UTC),
                starting_equity=100000.0,
                ending_equity=100267.0,
                equity_change=267.0,
                starting_cash=90000.0,
                ending_cash=88027.0,
                cash_change=-1973.0,
                price_pnl=200.0,
                rebalance_pnl=20.0,
                execution_shortfall=10.0,
                dividend_cash_flow=30.0,
                interest_cash_flow=5.0,
                fee_cash_flow=-3.0,
                tax_cash_flow=-2.0,
                funding_cash_flow=10.0,
                corporate_action_cash_flow=7.0,
                other_non_trade_cash_flow=0.0,
                total_non_trade_cash_flow=47.0,
                traded_notional=2020.0,
                fill_count=1,
                residual_pnl=0.0,
            )
        )
        session.commit()

    rows = live_service.recent_account_attributions(limit=5, account="paper-account")

    assert len(rows) == 1
    assert rows[0]["account"] == "paper-account"
    assert rows[0]["equity_change"] == 267.0
    assert rows[0]["dividend_cash_flow"] == 30.0
    assert rows[0]["funding_cash_flow"] == 10.0
    assert rows[0]["corporate_action_cash_flow"] == 7.0
    assert rows[0]["total_non_trade_cash_flow"] == 47.0
    assert rows[0]["end_asof"] == "2024-03-04T15:45:00+00:00"


def test_recent_anomaly_events_returns_serializable_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "anomaly-events-service.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    monkeypatch.setattr(live_service, "SessionLocal", testing_session)

    with testing_session() as session:
        session.add(
            AnomalyEventRecord(
                account_attribution_id=1,
                profile_id="us_etf_daily",
                account="paper-account",
                run_id="run-003",
                report_type="daily",
                alert_code="execution_shortfall",
                alert_tag="执行异常",
                severity="warning",
                summary="执行损耗达到 60.00",
                report_path="/tmp/daily_report.md",
                detected_at=datetime(2024, 3, 4, 15, 45, tzinfo=UTC),
            )
        )
        session.commit()

    rows = live_service.recent_anomaly_events(limit=5, profile_id="us_etf_daily")

    assert len(rows) == 1
    assert rows[0]["profile_id"] == "us_etf_daily"
    assert rows[0]["alert_code"] == "execution_shortfall"
    assert rows[0]["alert_tag"] == "执行异常"
    assert rows[0]["detected_at"] == "2024-03-04T15:45:00+00:00"


def test_recent_run_health_and_soak_summary_return_serializable_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "run-health-service.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    monkeypatch.setattr(live_service, "SessionLocal", testing_session)
    monkeypatch.setattr(
        live_service,
        "utc_now",
        lambda: datetime(2024, 3, 8, 16, 0, tzinfo=UTC),
    )

    with testing_session() as session:
        session.add_all(
            [
                RunHealthRecord(
                    run_id="paper-run-001",
                    profile_id="us_etf_daily",
                    mode="paper_soak",
                    broker="paper",
                    account="paper-account",
                    preflight_can_trade=True,
                    blocking_failure_count=0,
                    warning_count=0,
                    target_symbol_count=3,
                    target_weight_sum=0.98,
                    execution_plan_count=2,
                    planned_trade_value=6000.0,
                    plan_consistency_issue_count=0,
                    open_order_count=0,
                    partial_fill_count=1,
                    fills_seen_count=2,
                    execution_shortfall=20.0,
                    execution_shortfall_bps=12.0,
                    residual_pnl=5.0,
                    anomaly_count_trailing_7d=1,
                    anomaly_count_prev_7d=3,
                    anomaly_trend="down",
                    created_at=datetime(2024, 3, 8, 15, 45, tzinfo=UTC),
                ),
                RunHealthRecord(
                    run_id="shadow-run-001",
                    profile_id="us_etf_daily",
                    mode="shadow_run",
                    broker="ibkr",
                    account="paper-account",
                    preflight_can_trade=False,
                    blocking_failure_count=1,
                    warning_count=0,
                    target_symbol_count=3,
                    target_weight_sum=0.98,
                    execution_plan_count=0,
                    planned_trade_value=0.0,
                    plan_consistency_issue_count=0,
                    open_order_count=1,
                    partial_fill_count=0,
                    fills_seen_count=0,
                    execution_shortfall=None,
                    execution_shortfall_bps=None,
                    residual_pnl=15.0,
                    anomaly_count_trailing_7d=1,
                    anomaly_count_prev_7d=3,
                    anomaly_trend="down",
                    created_at=datetime(2024, 3, 8, 15, 30, tzinfo=UTC),
                ),
                AnomalyEventRecord(
                    account_attribution_id=1,
                    profile_id="us_etf_daily",
                    account="paper-account",
                    run_id="run-previous",
                    report_type="daily",
                    alert_code="residual_pnl",
                    alert_tag="账本异常",
                    severity="warning",
                    summary="residual",
                    detected_at=datetime(2024, 3, 3, 15, 45, tzinfo=UTC),
                ),
                AnomalyEventRecord(
                    account_attribution_id=2,
                    profile_id="us_etf_daily",
                    account="paper-account",
                    run_id="run-prev-window-1",
                    report_type="daily",
                    alert_code="execution_shortfall",
                    alert_tag="执行异常",
                    severity="warning",
                    summary="shortfall",
                    detected_at=datetime(2024, 2, 27, 15, 45, tzinfo=UTC),
                ),
                AnomalyEventRecord(
                    account_attribution_id=3,
                    profile_id="us_etf_daily",
                    account="paper-account",
                    run_id="run-prev-window-2",
                    report_type="daily",
                    alert_code="funding_cash_flow",
                    alert_tag="资金异常",
                    severity="warning",
                    summary="funding",
                    detected_at=datetime(2024, 2, 25, 15, 45, tzinfo=UTC),
                ),
            ]
        )
        session.commit()

    rows = live_service.recent_run_health(limit=5, profile_id="us_etf_daily")
    summary = live_service.soak_summary(
        days=28,
        limit=5,
        profile_id="us_etf_daily",
        account="paper-account",
    )

    assert len(rows) == 2
    assert rows[0]["run_id"] == "paper-run-001"
    assert rows[0]["mode"] == "paper_soak"
    assert rows[0]["partial_fill_count"] == 1
    assert summary["run_count"] == 2
    assert summary["blocked_run_count"] == 1
    assert summary["partial_fill_run_count"] == 1
    assert summary["anomaly_events_recent_7d"] == 1
    assert summary["anomaly_events_prev_7d"] == 2
    assert summary["anomaly_trend"] == "down"

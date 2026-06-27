from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from northstar_quant.config.settings import get_settings
from northstar_quant.db.base import Base
from northstar_quant.db.models import AccountAttributionRecord, AnomalyEventRecord
from northstar_quant.reporting import report_builder


def test_daily_report_includes_latest_account_attribution(tmp_path, monkeypatch):
    db_path = tmp_path / "report-builder.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    monkeypatch.setattr(report_builder, "SessionLocal", testing_session)

    settings = get_settings().model_copy()
    object.__setattr__(settings, "project_root", Path(__file__).resolve().parents[1])
    object.__setattr__(settings, "reports_dir", tmp_path / "reports")
    object.__setattr__(settings, "report_benchmark_symbol", "SPY")
    monkeypatch.setattr(report_builder, "get_settings", lambda: settings)

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
                execution_shortfall=2.0,
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

    summary = report_builder.latest_live_account_attribution_summary(profile_id="us_etf_daily")

    assert summary is not None
    assert summary["account"] == "paper-account"
    assert summary["funding_cash_flow"] == 10.0
    assert summary["corporate_action_cash_flow"] == 7.0
    assert summary["alert_tag_summary"] == ""
    assert summary["alert_items"] == []
    assert summary["alert_lines"] == []

    report_path = report_builder.build_markdown_report(
        report_type="daily",
        strategy_id="portfolio",
        metrics={"total_return": 0.12},
        period_label="2024-03-04",
        benchmark_symbol="SPY",
        live_account_attribution=summary,
    )

    content = Path(report_path).read_text(encoding="utf-8")
    assert "# Northstar Quant 日报" in content
    assert "## 三、异常关注" in content
    assert "暂无异常告警。" in content
    assert "## 四、当日复盘结论" in content
    assert "本期账户权益变动 267.00" in content
    assert "## 五、最新实盘归因" in content
    assert "资金划转：10.0" in content
    assert "公司行为现金流：7.0" in content
    assert "非交易现金流合计：47.0" in content


def test_daily_report_emits_alert_lines_when_thresholds_are_breached(tmp_path, monkeypatch):
    db_path = tmp_path / "report-builder-alert.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    monkeypatch.setattr(report_builder, "SessionLocal", testing_session)

    settings = get_settings().model_copy()
    object.__setattr__(settings, "project_root", Path(__file__).resolve().parents[1])
    object.__setattr__(settings, "reports_dir", tmp_path / "reports")
    object.__setattr__(settings, "report_benchmark_symbol", "SPY")
    monkeypatch.setattr(report_builder, "get_settings", lambda: settings)

    with testing_session() as session:
        session.add(
            AccountAttributionRecord(
                run_id="run-004",
                profile_id="us_etf_daily",
                broker="paper",
                account="paper-account",
                start_asof=datetime(2024, 3, 4, 15, 30, tzinfo=UTC),
                end_asof=datetime(2024, 3, 4, 15, 45, tzinfo=UTC),
                starting_equity=100000.0,
                ending_equity=100300.0,
                equity_change=300.0,
                starting_cash=90000.0,
                ending_cash=88100.0,
                cash_change=-1900.0,
                price_pnl=200.0,
                rebalance_pnl=20.0,
                execution_shortfall=60.0,
                dividend_cash_flow=0.0,
                interest_cash_flow=0.0,
                fee_cash_flow=0.0,
                tax_cash_flow=0.0,
                funding_cash_flow=0.0,
                corporate_action_cash_flow=0.0,
                other_non_trade_cash_flow=0.0,
                total_non_trade_cash_flow=0.0,
                traded_notional=2000.0,
                fill_count=1,
                residual_pnl=80.0,
            )
        )
        session.commit()

    summary = report_builder.latest_live_account_attribution_summary(profile_id="us_etf_daily")

    assert summary is not None
    assert summary["alert_tag_summary"] == "[执行异常][账本异常]"
    assert summary["alert_items"][0]["tag"] == "执行异常"
    assert summary["alert_items"][1]["tag"] == "账本异常"
    assert len(summary["alert_lines"]) == 2
    assert "[执行异常] 执行损耗达到 60.00" in summary["alert_lines"][0]
    assert "[账本异常] 未解释剩余达到 80.00" in summary["alert_lines"][1]

    report_path = report_builder.build_markdown_report(
        report_type="daily",
        strategy_id="portfolio",
        metrics={"total_return": 0.12},
        period_label="2024-03-04",
        benchmark_symbol="SPY",
        live_account_attribution=summary,
    )

    content = Path(report_path).read_text(encoding="utf-8")
    assert "# Northstar Quant 日报 [执行异常][账本异常]" in content
    assert "[执行异常] 执行损耗达到 60.00" in content
    assert "[账本异常] 未解释剩余达到 80.00" in content

    alert_message = report_builder.build_daily_alert_notification(report_path, summary)
    assert alert_message is not None
    assert "日报检测到异常归因 [执行异常][账本异常]。" in alert_message
    assert "画像：us_etf_daily" in alert_message
    assert "账户：paper-account" in alert_message
    assert "[执行异常] 执行损耗达到 60.00" in alert_message
    subject = report_builder.build_report_email_subject(
        report_type="daily",
        report_path=report_path,
        live_account_attribution=summary,
    )
    assert subject == "Northstar Quant - 日报 [执行异常][账本异常] - portfolio_daily_report"


def test_daily_report_emits_funding_alerts_for_large_cash_flows(tmp_path, monkeypatch):
    db_path = tmp_path / "report-builder-funding-alert.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    monkeypatch.setattr(report_builder, "SessionLocal", testing_session)

    settings = get_settings().model_copy()
    object.__setattr__(settings, "project_root", Path(__file__).resolve().parents[1])
    object.__setattr__(settings, "reports_dir", tmp_path / "reports")
    object.__setattr__(settings, "report_benchmark_symbol", "SPY")
    monkeypatch.setattr(report_builder, "get_settings", lambda: settings)

    with testing_session() as session:
        session.add(
            AccountAttributionRecord(
                run_id="run-005",
                profile_id="us_etf_daily",
                broker="paper",
                account="paper-account",
                start_asof=datetime(2024, 3, 4, 15, 30, tzinfo=UTC),
                end_asof=datetime(2024, 3, 4, 15, 45, tzinfo=UTC),
                starting_equity=100000.0,
                ending_equity=103500.0,
                equity_change=3500.0,
                starting_cash=90000.0,
                ending_cash=93500.0,
                cash_change=3500.0,
                price_pnl=0.0,
                rebalance_pnl=0.0,
                execution_shortfall=0.0,
                dividend_cash_flow=0.0,
                interest_cash_flow=0.0,
                fee_cash_flow=0.0,
                tax_cash_flow=0.0,
                funding_cash_flow=2500.0,
                corporate_action_cash_flow=1500.0,
                other_non_trade_cash_flow=0.0,
                total_non_trade_cash_flow=4000.0,
                traded_notional=0.0,
                fill_count=0,
                residual_pnl=0.0,
            )
        )
        session.commit()

    summary = report_builder.latest_live_account_attribution_summary(profile_id="us_etf_daily")

    assert summary is not None
    assert summary["alert_tag_summary"] == "[资金异常]"
    assert len(summary["alert_items"]) == 2
    assert summary["alert_items"][0]["tag"] == "资金异常"
    assert summary["alert_items"][1]["tag"] == "资金异常"
    assert "[资金异常] 资金划转达到 2,500.00" in summary["alert_lines"][0]
    assert "[资金异常] 公司行为现金流达到 1,500.00" in summary["alert_lines"][1]

    report_path = report_builder.build_markdown_report(
        report_type="daily",
        strategy_id="portfolio",
        metrics={"total_return": 0.12},
        period_label="2024-03-04",
        benchmark_symbol="SPY",
        live_account_attribution=summary,
    )

    content = Path(report_path).read_text(encoding="utf-8")
    assert "# Northstar Quant 日报 [资金异常]" in content
    assert "[资金异常] 资金划转达到 2,500.00" in content
    assert "[资金异常] 公司行为现金流达到 1,500.00" in content


def test_daily_report_includes_run_health_section(tmp_path, monkeypatch):
    settings = get_settings().model_copy()
    object.__setattr__(settings, "project_root", Path(__file__).resolve().parents[1])
    object.__setattr__(settings, "reports_dir", tmp_path / "reports")
    object.__setattr__(settings, "report_benchmark_symbol", "SPY")
    monkeypatch.setattr(report_builder, "get_settings", lambda: settings)

    report_path = report_builder.build_markdown_report(
        report_type="daily",
        strategy_id="portfolio",
        metrics={"total_return": 0.12},
        period_label="2024-03-04",
        benchmark_symbol="SPY",
        run_health_days=28,
        run_health_summaries=[
            {
                "mode_label": "Paper Soak（本地仿真账户）",
                "summary_lines": [
                    "近 28 天共运行 18 次，preflight 通过 18 次，阻止 0 次，通过率 100%。",
                    "target 与 execution plan 不一致的运行有 0 次，open order 干扰 1 次，partial fill 干扰 2 次。",
                ],
            },
            {
                "mode_label": "Shadow Run（只建计划不下单）",
                "summary_lines": [
                    "近 28 天共运行 18 次，preflight 通过 17 次，阻止 1 次，通过率 94%。",
                ],
            },
        ],
    )

    content = Path(report_path).read_text(encoding="utf-8")
    assert "## 六、最近 28 天运行健康" in content
    assert "### Paper Soak（本地仿真账户）" in content
    assert "### Shadow Run（只建计划不下单）" in content
    assert "target 与 execution plan 不一致的运行有 0 次" in content


def test_record_daily_anomaly_events_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "report-builder-anomaly-events.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    monkeypatch.setattr(report_builder, "SessionLocal", testing_session)

    settings = get_settings().model_copy()
    object.__setattr__(settings, "project_root", Path(__file__).resolve().parents[1])
    object.__setattr__(settings, "reports_dir", tmp_path / "reports")
    object.__setattr__(settings, "report_benchmark_symbol", "SPY")
    monkeypatch.setattr(report_builder, "get_settings", lambda: settings)

    with testing_session() as session:
        session.add(
            AccountAttributionRecord(
                run_id="run-006",
                profile_id="us_etf_daily",
                broker="paper",
                account="paper-account",
                start_asof=datetime(2024, 3, 4, 15, 30, tzinfo=UTC),
                end_asof=datetime(2024, 3, 4, 15, 45, tzinfo=UTC),
                starting_equity=100000.0,
                ending_equity=100300.0,
                equity_change=300.0,
                starting_cash=90000.0,
                ending_cash=88100.0,
                cash_change=-1900.0,
                price_pnl=200.0,
                rebalance_pnl=20.0,
                execution_shortfall=60.0,
                dividend_cash_flow=0.0,
                interest_cash_flow=0.0,
                fee_cash_flow=0.0,
                tax_cash_flow=0.0,
                funding_cash_flow=0.0,
                corporate_action_cash_flow=0.0,
                other_non_trade_cash_flow=0.0,
                total_non_trade_cash_flow=0.0,
                traded_notional=2000.0,
                fill_count=1,
                residual_pnl=80.0,
            )
        )
        session.commit()

    summary = report_builder.latest_live_account_attribution_summary(profile_id="us_etf_daily")
    assert summary is not None

    first = report_builder.record_daily_anomaly_events("/tmp/report.md", summary)
    second = report_builder.record_daily_anomaly_events("/tmp/report.md", summary)

    assert first == {"deleted": 0, "created": 2}
    assert second == {"deleted": 2, "created": 2}

    with testing_session() as session:
        rows = list(
            session.scalars(
                select(AnomalyEventRecord).order_by(
                    AnomalyEventRecord.detected_at.desc(),
                    AnomalyEventRecord.id.desc(),
                )
            )
        )

    assert len(rows) == 2
    assert {row.alert_code for row in rows} == {"execution_shortfall", "residual_pnl"}

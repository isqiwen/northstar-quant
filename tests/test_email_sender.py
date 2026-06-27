from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from northstar_quant.config.settings import get_settings
from northstar_quant.db.base import Base
from northstar_quant.db.models import AccountAttributionRecord
from northstar_quant.reporting import email_sender, report_builder


class _DummySMTP:
    sent_messages = []

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def login(self, username: str, password: str) -> None:
        return None

    def send_message(self, msg) -> None:
        type(self).sent_messages.append(msg)


def test_send_report_email_includes_daily_recap_in_body(tmp_path, monkeypatch):
    db_path = tmp_path / "report-email.db"
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
    object.__setattr__(settings, "report_recipients", "ops@example.com")
    object.__setattr__(settings, "smtp_host", "smtp.example.com")
    object.__setattr__(settings, "smtp_port", 465)
    object.__setattr__(settings, "smtp_sender", "northstar@example.com")
    object.__setattr__(settings, "smtp_use_ssl", True)
    object.__setattr__(settings, "report_email_attach_pdf", False)
    monkeypatch.setattr(report_builder, "get_settings", lambda: settings)
    monkeypatch.setattr(email_sender, "get_settings", lambda: settings)
    _DummySMTP.sent_messages.clear()
    monkeypatch.setattr(email_sender.smtplib, "SMTP_SSL", _DummySMTP)

    with testing_session() as session:
        session.add(
            AccountAttributionRecord(
                run_id="run-003",
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
                residual_pnl=-27.0,
            )
        )
        session.commit()

    summary = report_builder.latest_live_account_attribution_summary(profile_id="us_etf_daily")
    report_path = report_builder.build_markdown_report(
        report_type="daily",
        strategy_id="portfolio",
        metrics={"total_return": 0.12},
        period_label="2024-03-04",
        benchmark_symbol="SPY",
        live_account_attribution=summary,
    )

    result = email_sender.send_report_via_email(report_path, attach_pdf=False)

    assert result["sent"] is True
    assert result["subject"] == "Northstar Quant - portfolio_daily_report"
    assert len(_DummySMTP.sent_messages) == 1
    plain_body = _DummySMTP.sent_messages[0].get_body(preferencelist=("plain",))
    assert plain_body is not None
    body_text = plain_body.get_content()
    assert "## 三、异常关注" in body_text
    assert "[执行异常] 执行损耗达到 60.00" in body_text
    assert "[账本异常] 未解释剩余达到 -27.00" in body_text
    assert "## 四、当日复盘结论" in body_text
    assert "本期账户权益变动 300.00" in body_text


def test_send_report_email_uses_tagged_subject_when_provided(tmp_path, monkeypatch):
    report_path = tmp_path / "portfolio_daily_report.md"
    report_path.write_text("# Northstar Quant 日报 [执行异常]\n", encoding="utf-8")

    settings = get_settings().model_copy()
    object.__setattr__(settings, "report_recipients", "ops@example.com")
    object.__setattr__(settings, "smtp_host", "smtp.example.com")
    object.__setattr__(settings, "smtp_port", 465)
    object.__setattr__(settings, "smtp_sender", "northstar@example.com")
    object.__setattr__(settings, "smtp_use_ssl", True)
    object.__setattr__(settings, "report_email_attach_pdf", False)
    monkeypatch.setattr(email_sender, "get_settings", lambda: settings)
    _DummySMTP.sent_messages.clear()
    monkeypatch.setattr(email_sender.smtplib, "SMTP_SSL", _DummySMTP)

    result = email_sender.send_report_via_email(
        report_path,
        subject="Northstar Quant - 日报 [执行异常] - portfolio_daily_report",
        attach_pdf=False,
    )

    assert result["sent"] is True
    assert result["subject"] == "Northstar Quant - 日报 [执行异常] - portfolio_daily_report"

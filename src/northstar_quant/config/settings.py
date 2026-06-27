"""全局配置模型。

这里统一管理 Northstar Quant 的所有运行时配置。为了便于个人长期维护，
所有环境变量都从这里读取，业务模块不要直接硬编码地址、令牌、券商参数。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """应用运行时配置。"""

    model_config = SettingsConfigDict(
        env_prefix="NORTHSTAR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="Northstar Quant")
    env: str = Field(default="dev")
    timezone: str = Field(default="Asia/Shanghai")
    project_root: Path = Field(default=_PROJECT_ROOT)
    default_profile_id: str = Field(default="cn_etf_daily")
    profile_config_dir: Path = Field(default=Path("configs/profiles"))

    storage_dir: Path = Field(default=Path("storage"))
    downloads_dir: Path = Field(default=Path("storage/downloads"))
    reports_dir: Path = Field(default=Path("reports"))

    # 数据库配置。正式环境建议使用 PostgreSQL。
    database_url: str = Field(default="sqlite:///storage/northstar.db")

    # 券商与账户配置。
    broker: str = Field(default="paper")
    live_trading_enabled: bool = Field(default=False)
    kill_switch_enabled: bool = Field(default=False)
    default_cash: float = Field(default=100000.0)
    rebalance_min_trade_value: float = Field(default=500.0)
    paper_fill_price_mode: str = Field(default="close")

    # IBKR 连接参数。
    ibkr_host: str = Field(default="127.0.0.1")
    ibkr_port: int = Field(default=7497)
    ibkr_client_id: int = Field(default=7)
    ibkr_account: str | None = Field(default=None)
    ibkr_readonly: bool = Field(default=False)
    ibkr_poll_interval_seconds: int = Field(default=15)
    order_timeout_seconds: int = Field(default=300)
    limit_price_offset_bps: float = Field(default=15.0)
    limit_chase_max_steps: int = Field(default=3)
    limit_chase_sleep_seconds: int = Field(default=2)
    limit_chase_per_step_timeout_seconds: int = Field(default=20)
    limit_chase_fallback_mode: str = Field(default='market')

    # 交易日历配置。默认使用上交所日历。
    exchange_calendar: str = Field(default="XSHG")

    # 告警相关。你说不想用 Telegram，这里默认改成企业微信机器人。
    alert_mode: str = Field(default="console")
    wecom_webhook: str | None = Field(default=None)
    wecom_mentioned_mobile_list: str | None = Field(default=None)
    telegram_bot_token: str | None = Field(default=None)
    telegram_chat_id: str | None = Field(default=None)
    telegram_message_thread_id: int | None = Field(default=None)

    # 邮件报告配置。后续周报 / 月报可以直接复用。
    smtp_host: str | None = Field(default=None)
    smtp_port: int = Field(default=465)
    smtp_username: str | None = Field(default=None)
    smtp_password: str | None = Field(default=None)
    smtp_sender: str | None = Field(default=None)
    smtp_use_ssl: bool = Field(default=True)
    report_recipients: str | None = Field(default=None)
    report_email_subject_prefix: str = Field(default="Northstar Quant")
    report_email_attach_pdf: bool = Field(default=True)
    report_recap_execution_shortfall_alert_bps: float = Field(default=20.0)
    report_recap_residual_abs_alert: float = Field(default=10.0)
    report_recap_residual_ratio_alert: float = Field(default=0.05)
    report_recap_funding_abs_alert: float = Field(default=1000.0)
    report_recap_funding_ratio_alert: float = Field(default=0.01)
    live_preflight_max_state_age_seconds: int = Field(default=120)
    live_preflight_intraday_data_max_age_minutes: int = Field(default=120)
    live_preflight_daily_data_max_age_days: int = Field(default=4)
    live_preflight_weekly_data_max_age_days: int = Field(default=10)
    live_preflight_allow_valuation_price_fallback: bool = Field(default=False)

    # 报告与执行控制。
    report_benchmark_symbol: str = Field(default="510300.SS")
    etf_rotation_lookback_days: int = Field(default=126)
    etf_rotation_top_n: int = Field(default=3)
    trading_currency: str = Field(default="CNY")

    # 日频调度器配置。
    scheduler_timezone: str = Field(default="Asia/Shanghai")
    shadow_run_cron: str = Field(default="20 15 * * 1-5")
    rebalance_cron: str = Field(default="35 15 * * 1-5")
    broker_sync_cron: str = Field(default="0,15,30,45 9-16 * * 1-5")
    daily_report_cron: str = Field(default="45 16 * * 1-5")
    weekly_report_cron: str = Field(default="0 17 * * 5")
    monthly_report_cron: str = Field(default="0 17 28-31 * *")

    # Dashboard 配置。
    dashboard_host: str = Field(default="127.0.0.1")
    dashboard_port: int = Field(default=8501)

    def model_post_init(self, __context: object) -> None:
        project_root = Path(self.project_root)
        if not project_root.is_absolute():
            project_root = _PROJECT_ROOT / project_root
        project_root = project_root.resolve()
        object.__setattr__(self, "project_root", project_root)

        for field_name in ("profile_config_dir", "storage_dir", "downloads_dir", "reports_dir"):
            value = Path(getattr(self, field_name))
            if not value.is_absolute():
                value = project_root / value
            object.__setattr__(self, field_name, value)

        if self.database_url.startswith("sqlite:///"):
            db_path = Path(self.database_url.removeprefix("sqlite:///"))
            if not db_path.is_absolute():
                db_path = (project_root / db_path).resolve()
                object.__setattr__(self, "database_url", f"sqlite:///{db_path.as_posix()}")


@lru_cache
def get_settings() -> Settings:
    """返回全局单例配置对象。"""

    return Settings()

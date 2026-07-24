"""全局配置模型。

这里统一管理 Northstar Quant 的所有运行时配置。为了便于个人长期维护，
所有环境变量都从这里读取，业务模块不要直接硬编码地址、令牌、券商参数。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
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
    default_profile_id: str = Field(default="cn_futures_daily_trend_offline")
    profile_config_dir: Path = Field(default=Path("configs/profiles"))

    storage_dir: Path = Field(default=Path("storage"))
    downloads_dir: Path = Field(default=Path("storage/downloads"))
    reports_dir: Path = Field(default=Path("reports"))

    # 数据库配置。正式环境建议使用 PostgreSQL。
    database_url: str = Field(default="sqlite:///storage/northstar.db")

    # 券商与账户配置。
    broker: Literal["paper", "ctp"] = Field(default="paper")
    live_trading_enabled: bool = Field(default=False)
    kill_switch_enabled: bool = Field(default=False)
    default_cash: float = Field(default=100000.0, gt=0)
    rebalance_min_trade_value: float = Field(default=500.0, ge=0)
    paper_fill_price_mode: Literal["close", "reference", "limit"] = Field(default="close")
    paper_account: str = Field(default="paper-account", min_length=1)

    # CTP 合约映射。CTP 交易适配器尚未实现，不能据此直接下单。
    ctp_contract_mapping_path: Path = Field(default=Path("configs/instruments/ctp.yaml"))
    order_timeout_seconds: int = Field(default=300, gt=0)
    limit_price_offset_bps: float = Field(default=15.0, ge=0)
    limit_chase_max_steps: int = Field(default=3, ge=1, le=20)
    limit_chase_sleep_seconds: float = Field(default=2.0, ge=0.2)
    limit_chase_per_step_timeout_seconds: int = Field(default=20, gt=0)
    limit_chase_fallback_mode: Literal["cancel", "market"] = Field(default="cancel")
    execution_lease_ttl_seconds: int = Field(default=120, ge=30, le=3600)

    # 交易日历配置。期货夜盘须由期货数据/会话配置进一步约束，不能只依赖本默认值。
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
    live_preflight_max_state_age_seconds: int = Field(default=120, gt=0)
    live_preflight_intraday_data_max_age_minutes: int = Field(default=120, gt=0)
    live_preflight_daily_data_max_age_days: int = Field(default=4, gt=0)
    live_preflight_weekly_data_max_age_days: int = Field(default=10, gt=0)
    live_preflight_allow_valuation_price_fallback: bool = Field(default=False)

    # 报告与执行控制。
    report_benchmark_symbol: str = Field(default="RB_CONT")
    futures_trend_lookback_days: int = Field(default=60)
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
    dashboard_port: int = Field(default=8501, ge=1, le=65535)

    @field_validator(
        "broker",
        "paper_fill_price_mode",
        "limit_chase_fallback_mode",
        mode="before",
    )
    @classmethod
    def _normalize_choice(cls, value: object) -> object:
        """统一环境变量中枚举型配置的大小写与空白。"""

        return value.strip().lower() if isinstance(value, str) else value

    def model_post_init(self, __context: object) -> None:
        project_root = Path(self.project_root)
        if not project_root.is_absolute():
            project_root = _PROJECT_ROOT / project_root
        project_root = project_root.resolve()
        object.__setattr__(self, "project_root", project_root)

        for field_name in (
            "profile_config_dir",
            "storage_dir",
            "downloads_dir",
            "reports_dir",
            "ctp_contract_mapping_path",
        ):
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

    return load_settings()


def load_settings() -> Settings:
    """重新读取一次运行时配置，不使用进程缓存。"""

    return Settings()

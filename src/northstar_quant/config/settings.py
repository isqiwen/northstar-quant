"""全局配置模型。

这里统一管理 Northstar Quant 的所有运行时配置。为了便于个人长期维护，
所有环境变量都从这里读取，业务模块不要直接硬编码地址、令牌、券商参数。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_LOCAL_ACCOUNT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def normalize_local_state_account(value: str) -> str:
    """规范本地模拟账户标识，避免它作为路径片段时发生路径穿越。"""

    normalized = value.strip()
    if not _LOCAL_ACCOUNT_ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            "本地 Paper/CTP 模拟账户只能使用 1-64 位字母、数字、下划线或连字符，"
            "且必须以字母或数字开头。"
        )
    return normalized


class Settings(BaseSettings):
    """应用运行时配置。"""

    model_config = SettingsConfigDict(
        env_prefix="NORTHSTAR_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    app_name: str = Field(default="Northstar Quant")
    env: str = Field(default="dev")
    timezone: str = Field(default="Asia/Shanghai")
    project_root: Path = Field(default=_PROJECT_ROOT)
    default_profile_id: str = Field(default="cn_futures_daily_trend_offline")
    profile_config_dir: Path = Field(default=Path("configs/profiles"))

    storage_dir: Path = Field(default=Path("storage"))
    # 未显式配置时在 model_post_init 中派生为 storage_dir / "downloads"。
    downloads_dir: Path = Field(default=Path("storage/downloads"))
    reports_dir: Path = Field(default=Path("reports"))
    # None 表示沿用 configs/app.yaml 的 logging.directory。
    log_dir: Path | None = Field(default=None)

    # 数据库配置。项目只支持 PostgreSQL；凭据必须通过本地 .env 注入。
    database_url: str = Field(
        default="postgresql+psycopg://northstar@127.0.0.1:5432/northstar"
    )

    # 券商与账户配置。
    broker: Literal["paper", "ctp_sim", "ctp"] = Field(default="paper")
    live_trading_enabled: bool = Field(default=False)
    kill_switch_enabled: bool = Field(default=False)
    default_cash: float = Field(default=100000.0, gt=0)
    rebalance_min_trade_value: float = Field(default=500.0, ge=0)
    paper_fill_price_mode: Literal["close", "reference", "limit"] = Field(default="close")
    paper_account: str = Field(default="paper-account", min_length=1)
    # 未显式配置时在 model_post_init 中派生为
    # storage_dir / "brokers" / "paper" / paper_account / "state.json"。
    paper_state_path: Path = Field(default=Path("storage/brokers/paper/state.json"))

    # ctp_sim 是隔离的本地语义仿真，不连接交易前置；真实 CTP 适配器仍未实现。
    ctp_sim_account: str = Field(default="ctp-sim-account", min_length=1)
    # 未显式配置时在 model_post_init 中派生为
    # storage_dir / "brokers" / "ctp_sim" / ctp_sim_account / "state.json"。
    ctp_sim_state_path: Path = Field(default=Path("storage/brokers/ctp_sim/state.json"))
    ctp_sim_contract_mapping_path: Path = Field(
        default=Path("configs/instruments/ctp_sim.yaml")
    )
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
    alert_mode: Literal["console", "wecom", "telegram"] = Field(default="console")
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
    daily_target_max_age_days: int = Field(default=4, gt=0)
    # 逗号分隔的真实交易数据提供器白名单；默认空值使非 paper 路径失败关闭。
    approved_live_data_providers: str = Field(default="")

    # 盘中实时风控。结论过期、账户状态缺失或阈值超限时，真实订单失败关闭。
    runtime_risk_gate_max_age_seconds: int = Field(default=90, gt=0)
    runtime_risk_max_state_age_seconds: int = Field(default=30, gt=0)
    runtime_risk_max_quote_age_seconds: int = Field(default=15, gt=0)
    runtime_risk_max_margin_ratio: float = Field(default=0.75, gt=0, le=1)
    runtime_risk_min_available_funds_ratio: float = Field(default=0.15, ge=0, lt=1)
    runtime_risk_max_quote_spread_bps: float = Field(default=100.0, gt=0)
    runtime_risk_max_open_orders: int = Field(default=50, ge=0)

    # 报告与执行控制。
    report_benchmark_symbol: str = Field(default="RB_CONT")
    futures_trend_lookback_days: int = Field(default=60)
    trading_currency: str = Field(default="CNY")

    # 低频策略、盘中执行与实时风控的独立调度配置。
    scheduler_timezone: str = Field(default="Asia/Shanghai")
    shadow_run_cron: str = Field(default="20 15 * * 1-5")
    daily_signal_cron: str = Field(default="20 15 * * 1-5")
    execution_cron: str = Field(default="5 9,21 * * 1-5")
    runtime_risk_cron: str = Field(default="*/1 * * * 1-5")
    broker_sync_cron: str = Field(default="0,15,30,45 9-16 * * 1-5")
    daily_report_cron: str = Field(default="45 16 * * 1-5")
    weekly_report_cron: str = Field(default="0 17 * * 5")
    monthly_report_cron: str = Field(default="0 17 24-31 * *")
    yearly_report_cron: str = Field(default="15 17 * 12 *")

    # Dashboard 配置。
    dashboard_host: str = Field(default="127.0.0.1")
    dashboard_port: int = Field(default=8501, ge=1, le=65535)

    @field_validator(
        "broker",
        "paper_fill_price_mode",
        "limit_chase_fallback_mode",
        "alert_mode",
        mode="before",
    )
    @classmethod
    def _normalize_choice(cls, value: object) -> object:
        """统一环境变量中枚举型配置的大小写与空白。"""

        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("database_url")
    @classmethod
    def _require_postgresql(cls, value: str) -> str:
        """拒绝 SQLite 等非 PostgreSQL 数据库，避免测试与运行语义分叉。"""

        normalized = value.strip()
        if not normalized.startswith("postgresql+psycopg://"):
            raise ValueError(
                "NORTHSTAR_DATABASE_URL 必须使用 postgresql+psycopg://，"
                "本项目不再支持 SQLite。"
            )
        return normalized

    @field_validator("paper_account", "ctp_sim_account")
    @classmethod
    def _validate_local_state_account(cls, value: str) -> str:
        """限制本地账户标识，避免其参与状态路径时产生路径穿越。"""

        return normalize_local_state_account(value)

    def model_post_init(self, __context: object) -> None:
        project_root = Path(self.project_root)
        if not project_root.is_absolute():
            project_root = _PROJECT_ROOT / project_root
        project_root = project_root.resolve()
        object.__setattr__(self, "project_root", project_root)

        for field_name in (
            "profile_config_dir",
            "storage_dir",
            "reports_dir",
            "ctp_sim_contract_mapping_path",
            "ctp_contract_mapping_path",
        ):
            value = Path(getattr(self, field_name))
            if not value.is_absolute():
                value = project_root / value
            object.__setattr__(self, field_name, value)

        if "downloads_dir" in self.model_fields_set:
            downloads_dir = Path(self.downloads_dir)
            if not downloads_dir.is_absolute():
                downloads_dir = project_root / downloads_dir
        else:
            downloads_dir = self.storage_dir / "downloads"
        object.__setattr__(self, "downloads_dir", downloads_dir)

        paper_state_path = _resolve_local_state_path(
            self.paper_state_path
            if "paper_state_path" in self.model_fields_set
            else self.storage_dir / "brokers" / "paper" / self.paper_account / "state.json",
            project_root=project_root,
            storage_dir=self.storage_dir,
            setting_name="NORTHSTAR_PAPER_STATE_PATH",
        )
        object.__setattr__(self, "paper_state_path", paper_state_path)

        ctp_sim_state_path = _resolve_local_state_path(
            self.ctp_sim_state_path
            if "ctp_sim_state_path" in self.model_fields_set
            else self.storage_dir / "brokers" / "ctp_sim" / self.ctp_sim_account / "state.json",
            project_root=project_root,
            storage_dir=self.storage_dir,
            setting_name="NORTHSTAR_CTP_SIM_STATE_PATH",
        )
        object.__setattr__(self, "ctp_sim_state_path", ctp_sim_state_path)

        if self.log_dir is not None:
            log_dir = Path(self.log_dir)
            if not log_dir.is_absolute():
                log_dir = project_root / log_dir
            object.__setattr__(self, "log_dir", log_dir)


def _resolve_local_state_path(
    value: str | Path,
    *,
    project_root: Path,
    storage_dir: Path,
    setting_name: str,
) -> Path:
    """将本地模拟状态限制在 storage 根内，使部署白名单与运行时一致。"""

    state_path = Path(value)
    if not state_path.is_absolute():
        state_path = project_root / state_path
    resolved_state_path = state_path.resolve()
    resolved_storage_dir = storage_dir.resolve()
    try:
        resolved_state_path.relative_to(resolved_storage_dir)
    except ValueError as exc:
        raise ValueError(
            f"{setting_name} 必须位于 NORTHSTAR_STORAGE_DIR 内；"
            "如需迁移本地模拟状态，请调整 NORTHSTAR_STORAGE_DIR。"
        ) from exc
    if resolved_state_path == resolved_storage_dir:
        raise ValueError(f"{setting_name} 必须指向 NORTHSTAR_STORAGE_DIR 内的状态文件。")
    return resolved_state_path

@lru_cache
def get_settings() -> Settings:
    """返回全局单例配置对象。"""

    return load_settings()


def load_settings() -> Settings:
    """重新读取一次运行时配置，不使用进程缓存。"""

    return Settings()

"""全局配置模型。

这里统一管理 Northstar Quant 的所有运行时配置。为了便于个人长期维护，
所有环境变量都从这里读取，业务模块不要直接硬编码地址、令牌、券商参数。
"""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import re
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from northstar_quant.config.app_runtime import load_app_config
from northstar_quant.config.environment_file import (
    ENVIRONMENT_FILE_AUXILIARY_KEYS,
    validate_active_environment_file,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_LOCAL_ACCOUNT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_NTFY_TOPIC_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_NTFY_TOKEN_PATTERN = re.compile(r"^tk_[A-Za-z0-9]{29}$")
_NTFY_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_NTFY_PUBLIC_SERVICE_HOSTS = frozenset({"ntfy.sh"})

# 这四个字段保留在模型中，便于测试显式构造 Settings；运行时真源始终是 YAML。
ENV_DISABLED_FIELDS = frozenset({"storage_dir", "downloads_dir", "reports_dir", "log_dir"})
LEGACY_RUNTIME_PATH_ENV_VARS = frozenset(
    f"NORTHSTAR_{field.upper()}" for field in ENV_DISABLED_FIELDS
)


class _ExcludedSettingsFieldsSource(PydanticBaseSettingsSource):
    """从一个设置来源中删除只允许 YAML/显式构造的字段。"""

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        source: PydanticBaseSettingsSource,
    ) -> None:
        super().__init__(settings_cls)
        self._source = source

    def get_field_value(
        self,
        field: FieldInfo,
        field_name: str,
    ) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return {
            field_name: value
            for field_name, value in self._source().items()
            if field_name not in ENV_DISABLED_FIELDS
        }


class _RejectLegacyRuntimePathEnvironmentSource(PydanticBaseSettingsSource):
    """阻止旧输出路径变量悄悄覆盖可审计的 YAML 配置。"""

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        *sources: PydanticBaseSettingsSource,
    ) -> None:
        super().__init__(settings_cls)
        self._sources = sources

    def get_field_value(
        self,
        field: FieldInfo,
        field_name: str,
    ) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        seen: set[str] = set()
        for source in self._sources:
            env_vars = getattr(source, "env_vars", {})
            if not isinstance(env_vars, dict):
                continue
            seen.update(
                str(name).upper()
                for name in env_vars
                if str(name).upper() in LEGACY_RUNTIME_PATH_ENV_VARS
            )

        if seen:
            names = "、".join(sorted(seen))
            raise ValueError(
                f"不再接受运行输出路径环境变量：{names}。"
                "请从 OS/.env 中删除这些变量，并改为在 configs/app.yaml 的 "
                "runtime 段中完整配置 storage_dir、downloads_dir、reports_dir、log_dir。"
            )
        return {}


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
        # 只允许 load_settings() 显式传入项目根目录下经过结构校验的活动 .env。
        # 直接构造 Settings 主要用于隔离测试，不能悄悄读取调用目录中的文件。
        env_file=None,
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """保留敏感环境变量，同时让运行输出路径只由 YAML 决定。"""

        return (
            init_settings,
            _RejectLegacyRuntimePathEnvironmentSource(
                settings_cls,
                env_settings,
                dotenv_settings,
            ),
            _ExcludedSettingsFieldsSource(settings_cls, env_settings),
            _ExcludedSettingsFieldsSource(settings_cls, dotenv_settings),
            _ExcludedSettingsFieldsSource(settings_cls, file_secret_settings),
        )

    app_name: str = Field(default="Northstar Quant")
    env: str = Field(default="dev")
    timezone: str = Field(default="Asia/Shanghai")
    project_root: Path = Field(default=_PROJECT_ROOT)
    default_profile_id: str = Field(default="cn_futures_daily_trend_offline")
    profile_config_dir: Path = Field(default=Path("configs/profiles"))

    # 运行输出路径由 configs/app.yaml 的 runtime 段提供。保留这些字段只供测试
    # 显式构造 Settings 使用；它们绝不接受环境变量或 .env 注入。
    storage_dir: Path = Field(default=Path("storage"))
    # YAML 中的 null 在 model_post_init 中表示从最终 storage_dir 派生为
    # storage_dir / "downloads"。字段本身在设置对象完成构造后始终是 Path。
    downloads_dir: Path = Field(default=Path("storage/downloads"))
    reports_dir: Path = Field(default=Path("reports"))
    log_dir: Path = Field(default=Path("logs"))

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

    # 即时告警只支持私有部署 ntfy；console 始终保留为本地审计回退。
    alert_mode: Literal["console", "ntfy"] = Field(default="console")
    ntfy_base_url: str | None = Field(default=None)
    ntfy_topic: str | None = Field(default=None)
    ntfy_token: str | None = Field(default=None)
    ntfy_timeout_seconds: float = Field(default=10.0, gt=0, le=30)

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

    @field_validator("ntfy_base_url")
    @classmethod
    def _validate_ntfy_base_url(cls, value: str | None) -> str | None:
        """仅允许私有 HTTPS ntfy 服务；本机开发可使用 loopback HTTP。"""

        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        if not normalized:
            return None

        parsed = urlparse(normalized)
        if not parsed.scheme or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError(
                "NORTHSTAR_NTFY_BASE_URL 必须是无查询参数的完整服务地址。"
            )
        if parsed.username or parsed.password:
            raise ValueError(
                "NORTHSTAR_NTFY_BASE_URL 不得内嵌凭据；请使用 NORTHSTAR_NTFY_TOKEN。"
            )
        if parsed.scheme not in {"https", "http"}:
            raise ValueError(
                "NORTHSTAR_NTFY_BASE_URL 只允许 HTTPS 或本机 loopback HTTP。"
            )
        if parsed.scheme == "http" and parsed.hostname not in _NTFY_LOOPBACK_HOSTS:
            raise ValueError(
                "NORTHSTAR_NTFY_BASE_URL 仅允许 HTTPS；本机开发可使用 loopback HTTP。"
            )
        hostname = (parsed.hostname or "").lower()
        if hostname in _NTFY_PUBLIC_SERVICE_HOSTS or hostname.endswith(".ntfy.sh"):
            raise ValueError(
                "NORTHSTAR_NTFY_BASE_URL 必须指向私有部署的 ntfy，不能使用公共 ntfy.sh。"
            )
        return normalized

    @field_validator("ntfy_topic")
    @classmethod
    def _validate_ntfy_topic(cls, value: str | None) -> str | None:
        """限制 topic 为 ntfy 支持的安全路径片段。"""

        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if not _NTFY_TOPIC_PATTERN.fullmatch(normalized):
            raise ValueError(
                "NORTHSTAR_NTFY_TOPIC 只能使用 1-64 位字母、数字、下划线或连字符。"
            )
        return normalized

    @field_validator("ntfy_token")
    @classmethod
    def _normalize_ntfy_token(cls, value: str | None) -> str | None:
        """规范可选令牌，空白值保持为未配置。"""

        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if not _NTFY_TOKEN_PATTERN.fullmatch(normalized):
            raise ValueError(
                "NORTHSTAR_NTFY_TOKEN 必须是 ntfy 生成的 32 位 tk_ 访问令牌。"
            )
        return normalized

    def model_post_init(self, __context: object) -> None:
        project_root = Path(self.project_root)
        if not project_root.is_absolute():
            project_root = _PROJECT_ROOT / project_root
        project_root = project_root.resolve()
        object.__setattr__(self, "project_root", project_root)

        for field_name in (
            "profile_config_dir",
            "ctp_sim_contract_mapping_path",
            "ctp_contract_mapping_path",
        ):
            value = Path(getattr(self, field_name))
            if not value.is_absolute():
                value = project_root / value
            object.__setattr__(self, field_name, value)

        # 无论调用方是否显式传入测试/嵌入式路径，均先校验唯一活动配置。
        # 否则 app.yaml 缺失或遗留 app.local.yaml 时可绕过失败关闭边界。
        runtime_paths = load_app_config(project_root).runtime
        configured_storage_dir = (
            self.storage_dir
            if "storage_dir" in self.model_fields_set
            else runtime_paths.storage_dir
        )
        configured_downloads_dir = (
            self.downloads_dir
            if "downloads_dir" in self.model_fields_set
            else runtime_paths.downloads_dir
        )
        configured_reports_dir = (
            self.reports_dir
            if "reports_dir" in self.model_fields_set
            else runtime_paths.reports_dir
        )
        configured_log_dir = (
            self.log_dir if "log_dir" in self.model_fields_set else runtime_paths.log_dir
        )

        storage_dir = _resolve_runtime_setting_path(
            configured_storage_dir,
            project_root,
        )
        reports_dir = _resolve_runtime_setting_path(
            configured_reports_dir,
            project_root,
        )
        log_dir = _resolve_runtime_setting_path(
            configured_log_dir,
            project_root,
        )
        downloads_dir = (
            storage_dir / "downloads"
            if configured_downloads_dir is None
            else _resolve_runtime_setting_path(configured_downloads_dir, project_root)
        )

        object.__setattr__(self, "storage_dir", storage_dir)
        object.__setattr__(self, "reports_dir", reports_dir)
        object.__setattr__(self, "log_dir", log_dir)
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


def _resolve_runtime_setting_path(value: str | Path, project_root: Path) -> Path:
    """将 YAML 或测试显式传入的运行输出路径解析为绝对路径。"""

    path = Path(value)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


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
            f"{setting_name} 必须位于 runtime.storage_dir 内；"
            "如需迁移本地模拟状态，请调整 configs/app.yaml 的 runtime.storage_dir。"
        ) from exc
    if resolved_state_path == resolved_storage_dir:
        raise ValueError(f"{setting_name} 必须指向 runtime.storage_dir 内的状态文件。")
    return resolved_state_path

@lru_cache
def get_settings() -> Settings:
    """返回全局单例配置对象。"""

    return load_settings()


def load_settings() -> Settings:
    """重新读取唯一活动 `.env` 与 `configs/app.yaml`，不使用进程缓存。"""

    env_file = _default_env_file()
    validate_active_environment_file(
        env_file,
        expected_keys=active_environment_file_keys(),
        retired_keys=LEGACY_RUNTIME_PATH_ENV_VARS,
    )
    return Settings(_env_file=env_file)  # type: ignore[call-arg]


def active_environment_file_keys() -> frozenset[str]:
    """构造 `.env.example` 与活动 `.env` 共用的完整键集合。"""

    env_prefix = str(Settings.model_config["env_prefix"])
    settings_keys = {
        f"{env_prefix}{field_name.upper()}"
        for field_name in Settings.model_fields
        if field_name not in ENV_DISABLED_FIELDS
    }
    return frozenset(settings_keys | ENVIRONMENT_FILE_AUXILIARY_KEYS)


def _default_env_file() -> Path:
    """让全局设置读取其项目根目录下的 .env，而非调用进程的当前目录。"""

    configured_root = os.getenv("NORTHSTAR_PROJECT_ROOT")
    project_root = Path(configured_root) if configured_root else _PROJECT_ROOT
    if not project_root.is_absolute():
        project_root = _PROJECT_ROOT / project_root
    return project_root.resolve() / ".env"

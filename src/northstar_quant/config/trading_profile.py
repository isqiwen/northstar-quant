"""交易画像加载与解析。"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from northstar_quant.common.enums import (
    AssetType,
    DataFrequency,
    Market,
    RebalanceFrequency,
    StrategyFamily,
    StringEnum,
)
from northstar_quant.common.types import TradingDimensions
from northstar_quant.config.settings import get_settings
from northstar_quant.config.yaml_loader import load_yaml


def _parse_enum(enum_cls: type[StringEnum], value: str | StringEnum) -> StringEnum:
    if isinstance(value, enum_cls):
        return value
    return enum_cls.parse(str(value))


@dataclass(frozen=True, slots=True)
class ProfileDownloadConfig:
    """交易画像中的数据下载配置。"""

    enabled: bool = False
    provider: str = "local"
    symbols: tuple[str, ...] = ()
    start_date: str | None = None
    end_date: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProfileDataConfig:
    """交易画像中的数据集配置。"""

    provider: str = "local"
    dataset_id: str = "core"
    path: str = ""
    price_field: str = "close"
    adjusted: bool = True
    download: ProfileDownloadConfig = field(default_factory=ProfileDownloadConfig)


@dataclass(frozen=True, slots=True)
class ProfileStrategyConfig:
    """交易画像中的策略配置。"""

    strategy_id: str
    strategy_family: StrategyFamily | None = None
    capital_weight: float = 1.0
    enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProfileLifecycleConfig:
    """交易画像的生命周期角色。"""

    role: str = "experimental"
    line_id: str = "default"

    @property
    def is_production(self) -> bool:
        return self.role == "production"


@dataclass(frozen=True, slots=True)
class ProfileExecutionConfig:
    """交易画像中的执行政策。"""

    long_only: bool = True
    rebalance_min_trade_value: float | None = None
    rebalance_weight_tolerance: float = 0.0


@dataclass(frozen=True, slots=True)
class ProfileVersionConfig:
    """交易画像中的版本锚点。"""

    profile: str = "v1"
    benchmark: str = "v1"
    strategy_params: str = "v1"
    execution_policy: str = "v1"
    risk_policy: str = "v1"


@dataclass(frozen=True, slots=True)
class TradingProfile:
    """统一描述交易类型、数据集与策略组合的交易画像。"""

    profile_id: str
    name: str
    market: Market
    asset_type: AssetType
    data_frequency: DataFrequency
    rebalance_frequency: RebalanceFrequency
    strategy_family: StrategyFamily
    currency: str
    timezone: str
    calendar: str
    universe_id: str
    benchmark_symbol: str
    data: ProfileDataConfig
    strategies: tuple[ProfileStrategyConfig, ...] = ()
    lifecycle: ProfileLifecycleConfig = field(default_factory=ProfileLifecycleConfig)
    execution: ProfileExecutionConfig = field(default_factory=ProfileExecutionConfig)
    versions: ProfileVersionConfig = field(default_factory=ProfileVersionConfig)
    risk: dict[str, Any] = field(default_factory=dict)
    schedule: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def enabled_strategies(self) -> tuple[ProfileStrategyConfig, ...]:
        return tuple(strategy for strategy in self.strategies if strategy.enabled)

    @property
    def dimensions(self) -> TradingDimensions:
        return TradingDimensions(
            market=self.market,
            asset_type=self.asset_type,
            data_frequency=self.data_frequency,
            rebalance_frequency=self.rebalance_frequency,
            strategy_family=self.strategy_family,
        )

    @property
    def dimension_key(self) -> str:
        return self.dimensions.key

    @property
    def is_production(self) -> bool:
        return self.lifecycle.is_production

    def strategy_dimensions(self, strategy: ProfileStrategyConfig) -> TradingDimensions:
        return TradingDimensions(
            market=self.market,
            asset_type=self.asset_type,
            data_frequency=self.data_frequency,
            rebalance_frequency=self.rebalance_frequency,
            strategy_family=strategy.strategy_family or self.strategy_family,
        )


def resolve_profile_id(profile_id: str | None = None) -> str:
    """解析交易画像 ID；为空时回退到全局默认画像。"""

    return profile_id or get_production_profile_id()


def get_profile_config_dir(config_dir: str | Path | None = None) -> Path:
    """返回交易画像配置目录。"""

    if config_dir is not None:
        path = Path(config_dir)
        if path.is_absolute():
            return path
        return get_settings().project_root / path
    return get_settings().profile_config_dir


def get_profile_config_path(profile_id: str | None = None, config_dir: str | Path | None = None) -> Path:
    """返回某个交易画像对应的 YAML 路径。"""

    resolved_profile_id = resolve_profile_id(profile_id)
    return get_profile_config_dir(config_dir) / f"{resolved_profile_id}.yaml"


def list_trading_profiles(
    config_dir: str | Path | None = None,
    *,
    role: str | None = None,
) -> list[str]:
    """列出当前可用的交易画像 ID。"""

    profile_dir = get_profile_config_dir(config_dir)
    if not profile_dir.exists():
        return []
    profiles = sorted(path.stem for path in profile_dir.glob("*.yaml"))
    if role is None:
        return profiles
    normalized_role = str(role).strip().lower()
    return [
        profile_id
        for profile_id in profiles
        if load_trading_profile(profile_id, config_dir).lifecycle.role == normalized_role
    ]


def list_production_profiles(config_dir: str | Path | None = None) -> list[str]:
    """列出标记为 production 的交易画像。"""

    return list_trading_profiles(config_dir, role="production")


def get_production_profile_id(config_dir: str | Path | None = None) -> str:
    """返回唯一 production profile。"""

    production_profiles = list_production_profiles(config_dir)
    if len(production_profiles) == 1:
        return production_profiles[0]
    default_profile_id = get_settings().default_profile_id
    if not production_profiles and default_profile_id:
        return default_profile_id
    if len(production_profiles) > 1:
        joined = ", ".join(production_profiles)
        raise ValueError(f"当前存在多个 production 画像：{joined}")
    raise ValueError("当前没有标记为 production 的交易画像。")


def ensure_production_profile(profile: TradingProfile, *, context: str) -> TradingProfile:
    """确保给定画像可用于实盘主线。"""

    if profile.is_production:
        return profile
    raise ValueError(
        f"{context} 仅允许使用 production 画像；当前 {profile.profile_id} 的角色为 {profile.lifecycle.role}。"
    )


@lru_cache(maxsize=None)
def load_trading_profile(
    profile_id: str | None = None,
    config_dir: str | Path | None = None,
) -> TradingProfile:
    """从 YAML 读取交易画像。"""

    resolved_profile_id = resolve_profile_id(profile_id)
    path = get_profile_config_path(resolved_profile_id, config_dir)
    if not path.exists():
        available_profiles = ", ".join(list_trading_profiles(config_dir)) or "无"
        raise FileNotFoundError(
            f"交易画像配置不存在：{path}。当前可用画像：{available_profiles}"
        )

    raw = load_yaml(path)
    data_raw = raw.get("data", {}) or {}
    download_raw = data_raw.get("download", {}) or {}
    strategies_raw = raw.get("strategies", []) or []
    lifecycle_raw = raw.get("lifecycle", {}) or {}
    execution_raw = raw.get("execution", {}) or {}
    versions_raw = raw.get("versions", {}) or {}

    market = _parse_enum(Market, raw.get("market", "CN"))
    asset_type = _parse_enum(AssetType, raw.get("asset_type", "ETF"))
    data_frequency = _parse_enum(DataFrequency, raw.get("data_frequency", "1d"))
    rebalance_frequency = _parse_enum(
        RebalanceFrequency,
        raw.get("rebalance_frequency", data_frequency.value),
    )

    download_config = ProfileDownloadConfig(
        enabled=bool(download_raw.get("enabled", False)),
        provider=str(download_raw.get("provider", data_raw.get("provider", "local"))),
        symbols=tuple(str(symbol) for symbol in (download_raw.get("symbols", []) or [])),
        start_date=(
            str(download_raw["start_date"])
            if download_raw.get("start_date") is not None
            else None
        ),
        end_date=(
            str(download_raw["end_date"])
            if download_raw.get("end_date") is not None
            else None
        ),
        options=dict(download_raw.get("options", {}) or {}),
    )
    data_config = ProfileDataConfig(
        provider=str(data_raw.get("provider", "local")),
        dataset_id=str(data_raw.get("dataset_id", "core")),
        path=str(
            data_raw.get(
                "path",
                f"{market.value.lower()}/"
                f"{asset_type.value.lower()}/"
                f"{data_frequency.value.lower()}/core.parquet",
            )
        ),
        price_field=str(
            data_raw.get(
                "price_field",
                "adjusted_close" if data_frequency in {DataFrequency.D1, DataFrequency.W1} else "close",
            )
        ),
        adjusted=bool(data_raw.get("adjusted", True)),
        download=download_config,
    )
    lifecycle_config = ProfileLifecycleConfig(
        role=str(lifecycle_raw.get("role", "experimental")).strip().lower(),
        line_id=str(
            lifecycle_raw.get(
                "line_id",
                raw.get("profile_id", resolved_profile_id),
            )
        ),
    )
    execution_config = ProfileExecutionConfig(
        long_only=bool(execution_raw.get("long_only", True)),
        rebalance_min_trade_value=(
            float(execution_raw["rebalance_min_trade_value"])
            if execution_raw.get("rebalance_min_trade_value") is not None
            else None
        ),
        rebalance_weight_tolerance=float(execution_raw.get("rebalance_weight_tolerance", 0.0) or 0.0),
    )
    version_config = ProfileVersionConfig(
        profile=str(versions_raw.get("profile", "v1")),
        benchmark=str(versions_raw.get("benchmark", "v1")),
        strategy_params=str(versions_raw.get("strategy_params", "v1")),
        execution_policy=str(versions_raw.get("execution_policy", "v1")),
        risk_policy=str(versions_raw.get("risk_policy", "v1")),
    )

    strategy_configs = tuple(
        ProfileStrategyConfig(
            strategy_id=str(item["strategy_id"]),
            strategy_family=(
                _parse_enum(StrategyFamily, item["strategy_family"])
                if item.get("strategy_family") is not None
                else None
            ),
            capital_weight=float(item.get("capital_weight", 1.0)),
            enabled=bool(item.get("enabled", True)),
            params=dict(item.get("params", {}) or {}),
        )
        for item in strategies_raw
    )

    default_strategy_family = (
        strategy_configs[0].strategy_family.value
        if strategy_configs and strategy_configs[0].strategy_family is not None
        else StrategyFamily.TREND_FOLLOWING.value
    )

    metadata = {
        key: value
        for key, value in raw.items()
        if key
        not in {
            "profile_id",
            "name",
            "market",
            "asset_type",
            "data_frequency",
            "rebalance_frequency",
            "strategy_family",
            "currency",
            "timezone",
            "calendar",
            "universe_id",
            "benchmark_symbol",
            "data",
            "strategies",
            "lifecycle",
            "execution",
            "versions",
            "risk",
            "schedule",
        }
    }

    return TradingProfile(
        profile_id=str(raw.get("profile_id", resolved_profile_id)),
        name=str(raw.get("name", resolved_profile_id)),
        market=market,
        asset_type=asset_type,
        data_frequency=data_frequency,
        rebalance_frequency=rebalance_frequency,
        strategy_family=_parse_enum(
            StrategyFamily,
            raw.get("strategy_family", default_strategy_family),
        ),
        currency=str(raw.get("currency", get_settings().trading_currency)).upper(),
        timezone=str(raw.get("timezone", get_settings().timezone)),
        calendar=str(raw.get("calendar", get_settings().exchange_calendar)),
        universe_id=str(raw.get("universe_id", resolved_profile_id)),
        benchmark_symbol=str(raw.get("benchmark_symbol", get_settings().report_benchmark_symbol)),
        data=data_config,
        strategies=strategy_configs,
        lifecycle=lifecycle_config,
        execution=execution_config,
        versions=version_config,
        risk=dict(raw.get("risk", {}) or {}),
        schedule=dict(raw.get("schedule", {}) or {}),
        metadata=metadata,
    )

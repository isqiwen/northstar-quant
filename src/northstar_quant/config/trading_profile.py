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

    return profile_id or get_settings().default_profile_id


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


def list_trading_profiles(config_dir: str | Path | None = None) -> list[str]:
    """列出当前可用的交易画像 ID。"""

    profile_dir = get_profile_config_dir(config_dir)
    if not profile_dir.exists():
        return []
    return sorted(path.stem for path in profile_dir.glob("*.yaml"))


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

    market = _parse_enum(Market, raw.get("market", "US"))
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
        risk=dict(raw.get("risk", {}) or {}),
        schedule=dict(raw.get("schedule", {}) or {}),
        metadata=metadata,
    )

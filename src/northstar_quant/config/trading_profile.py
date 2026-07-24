"""交易画像加载与解析。"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar, cast

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


EnumValue = TypeVar("EnumValue", bound=StringEnum)


def _parse_enum(enum_cls: type[EnumValue], value: str | EnumValue) -> EnumValue:
    if isinstance(value, enum_cls):
        return value
    return cast(EnumValue, enum_cls.parse(str(value)))


def _parse_bool(value: object, *, field_name: str) -> bool:
    """严格解析配置布尔值，避免字符串 ``"false"`` 被当成真值。"""

    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError(f"配置字段 {field_name} 必须是明确的布尔值")


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
    live_trading_eligible: bool = False
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
    order_qty_step: float | None = None
    buy_qty_step: float | None = None
    sell_qty_step: float | None = None


@dataclass(frozen=True, slots=True)
class ProfileBacktestConfig:
    """交易画像中的低频回测与撮合模拟政策。"""

    engine: str = "weight_return"
    initial_cash: float = 100_000.0
    commission_bps: float = 0.0
    min_commission: float = 0.0
    slippage_bps: float = 0.0
    lot_size: int = 1
    execution_delay_sessions: int = 1
    sellable_after_sessions: int = 0


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
    backtest: ProfileBacktestConfig = field(default_factory=ProfileBacktestConfig)
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
    """解析交易画像 ID；为空时使用安全的全局默认画像。"""

    return profile_id or get_settings().default_profile_id


def get_profile_config_dir(config_dir: str | Path | None = None) -> Path:
    """返回交易画像配置目录。"""

    if config_dir is not None:
        path = Path(config_dir)
        if path.is_absolute():
            return path
        return get_settings().project_root / path
    return get_settings().profile_config_dir


_DIRECTORY_ALLOWED_ROLES = {
    "offline": frozenset({"research", "experimental"}),
    "simulated": frozenset({"simulated"}),
    "live": frozenset({"production"}),
}


def _discover_profile_paths(profile_dir: Path) -> dict[str, Path]:
    """递归发现交易画像，并以 YAML 内的 ``profile_id`` 建立唯一索引。"""

    if not profile_dir.exists():
        return {}

    paths_by_id: dict[str, Path] = {}
    for path in sorted(profile_dir.rglob("*.yaml")):
        raw = load_yaml(path)
        profile_id = str(raw.get("profile_id") or "").strip()
        if not profile_id:
            raise ValueError(f"交易画像配置缺少 profile_id：{path}")
        lifecycle_raw = raw.get("lifecycle", {}) or {}
        lifecycle_role = str(lifecycle_raw.get("role", "experimental")).strip().lower()
        _validate_profile_directory(
            path,
            profile_dir,
            profile_id=profile_id,
            lifecycle_role=lifecycle_role,
        )
        previous_path = paths_by_id.get(profile_id)
        if previous_path is not None:
            raise ValueError(
                f"交易画像 profile_id 重复：{profile_id}，"
                f"同时出现在 {previous_path} 和 {path}"
            )
        paths_by_id[profile_id] = path
    return paths_by_id


def get_profile_config_path(profile_id: str | None = None, config_dir: str | Path | None = None) -> Path:
    """按 YAML 内的 ``profile_id`` 返回对应的配置路径。"""

    resolved_profile_id = resolve_profile_id(profile_id)
    profile_dir = get_profile_config_dir(config_dir)
    return _discover_profile_paths(profile_dir).get(
        resolved_profile_id,
        profile_dir / f"{resolved_profile_id}.yaml",
    )


def list_trading_profiles(
    config_dir: str | Path | None = None,
    *,
    role: str | None = None,
) -> list[str]:
    """列出当前可用的交易画像 ID。"""

    profile_dir = get_profile_config_dir(config_dir)
    profiles = sorted(_discover_profile_paths(profile_dir))
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


def _validate_profile_directory(
    path: Path,
    profile_dir: Path,
    *,
    profile_id: str,
    lifecycle_role: str,
) -> None:
    """校验画像路径、ID、类别后缀与生命周期角色的一致性。"""

    relative_path = path.relative_to(profile_dir)
    if len(relative_path.parts) != 2:
        raise ValueError(
            f"交易画像 {profile_id} 必须直接位于类别目录中："
            f"{', '.join(sorted(_DIRECTORY_ALLOWED_ROLES))}"
        )
    directory_name = relative_path.parts[0]
    allowed_roles = _DIRECTORY_ALLOWED_ROLES.get(directory_name)
    if allowed_roles is None:
        raise ValueError(
            f"交易画像 {profile_id} 位于不支持的目录 {directory_name}；"
            f"仅支持：{', '.join(sorted(_DIRECTORY_ALLOWED_ROLES))}"
        )
    if lifecycle_role not in allowed_roles:
        raise ValueError(
            f"交易画像 {profile_id} 的 lifecycle.role={lifecycle_role} 不允许位于目录 "
            f"{directory_name}；允许角色：{', '.join(sorted(allowed_roles))}"
        )
    if path.stem != profile_id:
        raise ValueError(
            f"交易画像文件名必须与 profile_id 一致：{path.name} != {profile_id}.yaml"
        )
    required_suffix = f"_{directory_name}"
    if not profile_id.endswith(required_suffix):
        raise ValueError(
            f"交易画像 {profile_id} 必须以类别后缀 {required_suffix} 结尾"
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
    backtest_raw = raw.get("backtest", {}) or {}
    versions_raw = raw.get("versions", {}) or {}

    configured_profile_id = str(raw.get("profile_id") or "").strip()
    if configured_profile_id != resolved_profile_id:
        raise ValueError(
            f"交易画像路径索引与内容不一致：请求 {resolved_profile_id}，"
            f"配置声明 {configured_profile_id or '空'}"
        )
    lifecycle_role = str(lifecycle_raw.get("role", "experimental")).strip().lower()
    _validate_profile_directory(
        path,
        get_profile_config_dir(config_dir),
        profile_id=configured_profile_id,
        lifecycle_role=lifecycle_role,
    )

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
        live_trading_eligible=_parse_bool(
            data_raw.get("live_trading_eligible", False),
            field_name="data.live_trading_eligible",
        ),
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
        order_qty_step=(
            float(execution_raw["order_qty_step"])
            if execution_raw.get("order_qty_step") is not None
            else None
        ),
        buy_qty_step=(
            float(execution_raw["buy_qty_step"])
            if execution_raw.get("buy_qty_step") is not None
            else None
        ),
        sell_qty_step=(
            float(execution_raw["sell_qty_step"])
            if execution_raw.get("sell_qty_step") is not None
            else None
        ),
    )
    backtest_engine = str(backtest_raw.get("engine", "weight_return")).strip().lower()
    if backtest_engine not in {"weight_return", "daily_stateful"}:
        raise ValueError(
            "配置字段 backtest.engine 仅支持 weight_return / daily_stateful"
        )
    backtest_config = ProfileBacktestConfig(
        engine=backtest_engine,
        initial_cash=float(backtest_raw.get("initial_cash", 100_000.0)),
        commission_bps=float(backtest_raw.get("commission_bps", 0.0)),
        min_commission=float(backtest_raw.get("min_commission", 0.0)),
        slippage_bps=float(backtest_raw.get("slippage_bps", 0.0)),
        lot_size=_parse_positive_int(
            backtest_raw.get("lot_size", 1),
            field_name="backtest.lot_size",
            minimum=1,
        ),
        execution_delay_sessions=_parse_positive_int(
            backtest_raw.get("execution_delay_sessions", 1),
            field_name="backtest.execution_delay_sessions",
            minimum=1,
        ),
        sellable_after_sessions=_parse_positive_int(
            backtest_raw.get("sellable_after_sessions", 0),
            field_name="backtest.sellable_after_sessions",
            minimum=0,
        ),
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
            "backtest",
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
        backtest=backtest_config,
        versions=version_config,
        risk=dict(raw.get("risk", {}) or {}),
        schedule=dict(raw.get("schedule", {}) or {}),
        metadata=metadata,
    )


def _parse_positive_int(value: object, *, field_name: str, minimum: int) -> int:
    """严格读取画像中的整数，避免 YAML 小数或布尔值静默进入数量规则。"""

    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"配置字段 {field_name} 必须是大于等于 {minimum} 的整数")
    return value

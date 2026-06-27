"""策略注册表。"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from northstar_quant.common.enums import AssetType, DataFrequency, Market, StrategyFamily, StrategyOutputType
from northstar_quant.config.settings import get_settings
from northstar_quant.config.trading_profile import TradingProfile
from northstar_quant.config.yaml_loader import load_yaml
from northstar_quant.strategies.base import StrategyBase
from northstar_quant.strategies.etf_rotation import ETFDailyRotationStrategy
from northstar_quant.strategies.intraday_breakout import IntradayBreakoutStrategy
from northstar_quant.strategies.momentum import MomentumRotationStrategy

StrategyFactory = Callable[..., StrategyBase]


@dataclass(frozen=True, slots=True)
class StrategyDefinition:
    """策略注册元数据。"""

    strategy_id: str
    factory: StrategyFactory
    strategy_family: StrategyFamily
    output_type: StrategyOutputType
    supported_markets: tuple[Market, ...] = ()
    supported_asset_types: tuple[AssetType, ...] = ()
    supported_data_frequencies: tuple[DataFrequency, ...] = ()


_REGISTRY: dict[str, StrategyDefinition] = {}


def _resolve_config_dir(config_dir: str | Path = "configs/strategy") -> Path:
    path = Path(config_dir)
    if path.is_absolute():
        return path
    return get_settings().project_root / path


def register_strategy(
    strategy_id: str,
    factory: StrategyFactory,
    *,
    strategy_family: StrategyFamily,
    output_type: StrategyOutputType | None = None,
    supported_markets: tuple[Market, ...] = (),
    supported_asset_types: tuple[AssetType, ...] = (),
    supported_data_frequencies: tuple[DataFrequency, ...] = (),
    replace: bool = False,
) -> None:
    """注册策略工厂。"""

    if strategy_id in _REGISTRY and not replace:
        raise ValueError(f"策略已注册：{strategy_id}")
    _REGISTRY[strategy_id] = StrategyDefinition(
        strategy_id=strategy_id,
        factory=factory,
        strategy_family=strategy_family,
        output_type=output_type or getattr(factory, "output_type", StrategyOutputType.TARGET_WEIGHT),
        supported_markets=supported_markets,
        supported_asset_types=supported_asset_types,
        supported_data_frequencies=supported_data_frequencies,
    )


def list_registered_strategies() -> list[str]:
    """列出当前已注册的策略 ID。"""

    return sorted(_REGISTRY)


def get_strategy_definition(strategy_id: str) -> StrategyDefinition:
    """获取某个策略 ID 对应的定义。"""

    try:
        return _REGISTRY[strategy_id]
    except KeyError as exc:
        available = ", ".join(list_registered_strategies()) or "无"
        raise KeyError(f"未注册的策略：{strategy_id}。当前可用策略：{available}") from exc


def get_strategy_factory(strategy_id: str) -> StrategyFactory:
    """获取某个策略 ID 对应的工厂。"""

    return get_strategy_definition(strategy_id).factory


def _strategy_config_path(strategy_id: str, config_dir: str | Path = "configs/strategy") -> Path:
    return _resolve_config_dir(config_dir) / f"{strategy_id}.yaml"


def _normalize_strategy_config(raw: dict[str, Any]) -> dict[str, Any]:
    payload = dict(raw.get("strategy", raw) or {})
    if "id" in payload and "strategy_id" not in payload:
        payload["strategy_id"] = payload.pop("id")
    return payload


def load_strategy_config(strategy_id: str, config_dir: str | Path = "configs/strategy") -> dict[str, Any]:
    """读取并规范化策略配置。"""

    path = _strategy_config_path(strategy_id, config_dir)
    if not path.exists():
        return {}
    return _normalize_strategy_config(load_yaml(path))


def _filter_factory_kwargs(factory: StrategyFactory, params: dict[str, Any]) -> dict[str, Any]:
    signature = inspect.signature(factory)
    supported: dict[str, Any] = {}
    for name, parameter in signature.parameters.items():
        if name == "self":
            continue
        if parameter.kind in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        } and name in params:
            supported[name] = params[name]
    return supported


def build_strategy(
    strategy_id: str,
    *,
    params: dict[str, Any] | None = None,
    config_dir: str | Path = "configs/strategy",
) -> StrategyBase:
    """根据注册表和 YAML 默认配置构建策略实例。"""

    factory = get_strategy_factory(strategy_id)
    merged = load_strategy_config(strategy_id, config_dir=config_dir)
    merged.update(params or {})
    factory_kwargs = _filter_factory_kwargs(factory, merged)
    strategy = factory(**factory_kwargs)
    if (
        getattr(strategy, "supported_data_frequencies", ())
        and strategy_id in _REGISTRY
    ):
        definition = get_strategy_definition(strategy_id)
        if definition.output_type != strategy.output_type:
            raise ValueError(
                f"策略 {strategy_id} 的 output_type 与注册表不一致："
                f"{strategy.output_type.value} != {definition.output_type.value}"
            )
        unsupported = set(definition.supported_data_frequencies).difference(strategy.supported_data_frequencies)
        if unsupported:
            unsupported_text = ", ".join(sorted(item.value for item in unsupported))
            raise ValueError(
                f"策略 {strategy_id} 注册表声明了未被策略基类支持的频率：{unsupported_text}"
            )
    return strategy


def build_profile_strategies(profile: TradingProfile) -> list[tuple[StrategyBase, float]]:
    """根据交易画像构建启用中的策略及其资本权重。"""

    built: list[tuple[StrategyBase, float]] = []
    for strategy_config in profile.enabled_strategies:
        definition = get_strategy_definition(strategy_config.strategy_id)
        if (
            strategy_config.strategy_family is not None
            and strategy_config.strategy_family != definition.strategy_family
        ):
            raise ValueError(
                f"画像 {profile.profile_id} 中策略 {strategy_config.strategy_id} 的 strategy_family="
                f"{strategy_config.strategy_family.value} 与注册表中的 "
                f"{definition.strategy_family.value} 不一致"
            )
        if definition.supported_markets and profile.market not in definition.supported_markets:
            raise ValueError(
                f"策略 {strategy_config.strategy_id} 不支持市场 {profile.market.value}，"
                f"仅支持 {', '.join(item.value for item in definition.supported_markets)}"
            )
        if definition.supported_asset_types and profile.asset_type not in definition.supported_asset_types:
            raise ValueError(
                f"策略 {strategy_config.strategy_id} 不支持资产类型 {profile.asset_type.value}，"
                f"仅支持 {', '.join(item.value for item in definition.supported_asset_types)}"
            )
        if (
            definition.supported_data_frequencies
            and profile.data_frequency not in definition.supported_data_frequencies
        ):
            raise ValueError(
                f"策略 {strategy_config.strategy_id} 不支持数据频率 {profile.data_frequency.value}，"
                f"仅支持 {', '.join(item.value for item in definition.supported_data_frequencies)}"
            )
        built.append(
            (
                build_strategy(strategy_config.strategy_id, params=strategy_config.params),
                float(strategy_config.capital_weight),
            )
        )
    return built


register_strategy(
    "etf_rotation",
    ETFDailyRotationStrategy,
    strategy_family=StrategyFamily.MOMENTUM_ROTATION,
    supported_markets=(Market.US, Market.CN),
    supported_asset_types=(AssetType.ETF,),
    supported_data_frequencies=(DataFrequency.D1,),
)
register_strategy(
    "momentum",
    MomentumRotationStrategy,
    strategy_family=StrategyFamily.CROSS_SECTIONAL_SELECTION,
    supported_markets=(Market.US, Market.CN),
    supported_asset_types=(AssetType.ETF, AssetType.EQUITY),
    supported_data_frequencies=(DataFrequency.D1, DataFrequency.W1),
)
register_strategy(
    "intraday_breakout",
    IntradayBreakoutStrategy,
    strategy_family=StrategyFamily.INTRADAY_BREAKOUT,
    supported_markets=(Market.US, Market.CN),
    supported_asset_types=(AssetType.EQUITY,),
    supported_data_frequencies=(DataFrequency.M1, DataFrequency.M5, DataFrequency.M15),
)

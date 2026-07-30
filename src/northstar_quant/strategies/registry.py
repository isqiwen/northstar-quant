"""策略注册表。"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from northstar_quant.common.enums import AssetType, DataFrequency, Market, StrategyFamily, StrategyOutputType
from northstar_quant.config.settings import get_settings
from northstar_quant.config.trading_profile import ProfileStrategyConfig, TradingProfile
from northstar_quant.config.yaml_loader import load_yaml
from northstar_quant.strategies.base import StrategyBase
from northstar_quant.strategies.futures_trend import FuturesTrendStrategy

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

# 这些字段属于现有策略 YAML 的说明或研究元数据，不会传给策略构造器。
_STRATEGY_CONFIG_METADATA_FIELDS = frozenset(
    {
        "commission_bps",
        "min_liquidity_cny",
        "name",
        "rebalance_frequency",
        "rebalance_interval",
        "risk",
        "slippage_bps",
        "strategy_id",
        "universe",
        "weighting",
    }
)


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
    resolved_output_type = output_type
    if resolved_output_type is None:
        factory_output_type = getattr(factory, "output_type", StrategyOutputType.TARGET_WEIGHT)
        if not isinstance(factory_output_type, StrategyOutputType):
            raise TypeError(f"策略 {strategy_id} 的 output_type 必须是 StrategyOutputType")
        resolved_output_type = factory_output_type
    _REGISTRY[strategy_id] = StrategyDefinition(
        strategy_id=strategy_id,
        factory=factory,
        strategy_family=strategy_family,
        output_type=resolved_output_type,
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


def _factory_keyword_parameters(factory: StrategyFactory) -> tuple[set[str], bool]:
    signature = inspect.signature(factory)
    supported: set[str] = set()
    accepts_extra_kwargs = False
    for name, parameter in signature.parameters.items():
        if name == "self":
            continue
        if parameter.kind == inspect.Parameter.VAR_KEYWORD:
            accepts_extra_kwargs = True
        elif parameter.kind in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }:
            supported.add(name)
    return supported, accepts_extra_kwargs


def _raise_for_unknown_fields(
    strategy_id: str,
    fields: set[str],
    *,
    supported_fields: set[str],
    field_label: str,
) -> None:
    unknown_fields = sorted(fields.difference(supported_fields))
    if not unknown_fields:
        return
    raise ValueError(
        f"策略 {strategy_id} 包含不支持的{field_label}：{', '.join(unknown_fields)}"
    )


def build_strategy(
    strategy_id: str,
    *,
    params: dict[str, Any] | None = None,
    config_dir: str | Path = "configs/strategy",
) -> StrategyBase:
    """根据注册表和 YAML 默认配置构建策略实例。"""

    factory = get_strategy_factory(strategy_id)
    supported_params, accepts_extra_kwargs = _factory_keyword_parameters(factory)
    yaml_config = load_strategy_config(strategy_id, config_dir=config_dir)
    profile_params = dict(params or {})

    if not accepts_extra_kwargs:
        _raise_for_unknown_fields(
            strategy_id,
            set(yaml_config),
            supported_fields=supported_params | set(_STRATEGY_CONFIG_METADATA_FIELDS),
            field_label="配置字段",
        )
        _raise_for_unknown_fields(
            strategy_id,
            set(profile_params),
            supported_fields=supported_params,
            field_label="策略参数",
        )

    factory_kwargs = {
        key: value
        for key, value in yaml_config.items()
        if key in supported_params
        or (accepts_extra_kwargs and key not in _STRATEGY_CONFIG_METADATA_FIELDS)
    }
    factory_kwargs.update(profile_params)
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


def build_profile_strategy(
    profile: TradingProfile,
    strategy_config: ProfileStrategyConfig,
) -> StrategyBase:
    """校验画像兼容性并构建单个策略。"""

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
        and profile.strategy_data_frequency not in definition.supported_data_frequencies
    ):
        raise ValueError(
            f"策略 {strategy_config.strategy_id} 不支持信号频率 "
            f"{profile.strategy_data_frequency.value}，"
            f"仅支持 {', '.join(item.value for item in definition.supported_data_frequencies)}"
        )
    return build_strategy(strategy_config.strategy_id, params=strategy_config.params)


def build_profile_strategies(profile: TradingProfile) -> list[tuple[StrategyBase, float]]:
    """根据交易画像构建启用中的策略及其资本权重。"""

    return [
        (
            build_profile_strategy(profile, strategy_config),
            float(strategy_config.capital_weight),
        )
        for strategy_config in profile.enabled_strategies
    ]


register_strategy(
    "futures_trend",
    FuturesTrendStrategy,
    strategy_family=StrategyFamily.TREND_FOLLOWING,
    supported_markets=(Market.CN,),
    supported_asset_types=(AssetType.FUTURES,),
    supported_data_frequencies=(DataFrequency.D1,),
)

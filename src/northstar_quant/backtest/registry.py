"""按交易五维选择回测器的注册表。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import polars as pl

from northstar_quant.backtest.event_engine import BacktestResult, run_event_backtest
from northstar_quant.backtest.simulation import (
    SimulationBacktesterBase,
    periods_per_year_for_frequency,
)
from northstar_quant.common.enums import AssetType, DataFrequency, Market, RebalanceFrequency, StrategyFamily
from northstar_quant.config.trading_profile import TradingProfile

TargetBacktester = Callable[[TradingProfile, pl.DataFrame, pl.DataFrame], BacktestResult]
SimulationBacktesterFactory = Callable[[], SimulationBacktesterBase]


@dataclass(frozen=True, slots=True)
class TargetBacktesterDefinition:
    """目标持仓回测器注册元数据。"""

    backtester_id: str
    backtester: TargetBacktester
    supported_markets: tuple[Market, ...] = ()
    supported_asset_types: tuple[AssetType, ...] = ()
    supported_data_frequencies: tuple[DataFrequency, ...] = ()
    supported_rebalance_frequencies: tuple[RebalanceFrequency, ...] = ()
    supported_strategy_families: tuple[StrategyFamily, ...] = ()
    supported_backtest_engines: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SimulationBacktesterDefinition:
    """策略仿真回测器注册元数据。"""

    backtester_id: str
    backtester_factory: SimulationBacktesterFactory
    supported_markets: tuple[Market, ...] = ()
    supported_asset_types: tuple[AssetType, ...] = ()
    supported_data_frequencies: tuple[DataFrequency, ...] = ()
    supported_rebalance_frequencies: tuple[RebalanceFrequency, ...] = ()
    supported_strategy_families: tuple[StrategyFamily, ...] = ()


_TARGET_BACKTESTERS: dict[str, TargetBacktesterDefinition] = {}
_SIMULATION_BACKTESTERS: dict[str, SimulationBacktesterDefinition] = {}


def register_target_backtester(
    backtester_id: str,
    backtester: TargetBacktester,
    *,
    supported_markets: tuple[Market, ...] = (),
    supported_asset_types: tuple[AssetType, ...] = (),
    supported_data_frequencies: tuple[DataFrequency, ...] = (),
    supported_rebalance_frequencies: tuple[RebalanceFrequency, ...] = (),
    supported_strategy_families: tuple[StrategyFamily, ...] = (),
    supported_backtest_engines: tuple[str, ...] = (),
    replace: bool = False,
) -> None:
    """注册目标持仓回测器。"""

    if backtester_id in _TARGET_BACKTESTERS and not replace:
        raise ValueError(f"目标持仓回测器已注册：{backtester_id}")
    _TARGET_BACKTESTERS[backtester_id] = TargetBacktesterDefinition(
        backtester_id=backtester_id,
        backtester=backtester,
        supported_markets=supported_markets,
        supported_asset_types=supported_asset_types,
        supported_data_frequencies=supported_data_frequencies,
        supported_rebalance_frequencies=supported_rebalance_frequencies,
        supported_strategy_families=supported_strategy_families,
        supported_backtest_engines=supported_backtest_engines,
    )


def register_simulation_backtester(
    backtester_id: str,
    backtester_factory: SimulationBacktesterFactory,
    *,
    supported_markets: tuple[Market, ...] = (),
    supported_asset_types: tuple[AssetType, ...] = (),
    supported_data_frequencies: tuple[DataFrequency, ...] = (),
    supported_rebalance_frequencies: tuple[RebalanceFrequency, ...] = (),
    supported_strategy_families: tuple[StrategyFamily, ...] = (),
    replace: bool = False,
) -> None:
    """注册策略仿真回测器。"""

    if backtester_id in _SIMULATION_BACKTESTERS and not replace:
        raise ValueError(f"策略仿真回测器已注册：{backtester_id}")
    _SIMULATION_BACKTESTERS[backtester_id] = SimulationBacktesterDefinition(
        backtester_id=backtester_id,
        backtester_factory=backtester_factory,
        supported_markets=supported_markets,
        supported_asset_types=supported_asset_types,
        supported_data_frequencies=supported_data_frequencies,
        supported_rebalance_frequencies=supported_rebalance_frequencies,
        supported_strategy_families=supported_strategy_families,
    )


def list_target_backtesters() -> list[str]:
    """列出已注册的目标持仓回测器。"""

    return sorted(_TARGET_BACKTESTERS)


def list_simulation_backtesters() -> list[str]:
    """列出已注册的策略仿真回测器。"""

    return sorted(_SIMULATION_BACKTESTERS)


def _matches(
    *,
    supported_markets: tuple[Market, ...],
    supported_asset_types: tuple[AssetType, ...],
    supported_data_frequencies: tuple[DataFrequency, ...],
    supported_rebalance_frequencies: tuple[RebalanceFrequency, ...],
    supported_strategy_families: tuple[StrategyFamily, ...],
    profile: TradingProfile,
    supported_backtest_engines: tuple[str, ...] = (),
) -> bool:
    """判断一个回测器是否完整匹配画像维度。

    注册项中的空元组表示该维度不设限制；非空时每一项都必须匹配。这样可以防止把
    股票、分钟线或不同策略输出的回测器误用于当前国内期货日线研究。
    """

    return (
        (not supported_markets or profile.market in supported_markets)
        and (not supported_asset_types or profile.asset_type in supported_asset_types)
        and (
            not supported_data_frequencies
            or profile.data_frequency in supported_data_frequencies
        )
        and (
            not supported_rebalance_frequencies
            or profile.rebalance_frequency in supported_rebalance_frequencies
        )
        and (
            not supported_strategy_families
            or profile.strategy_family in supported_strategy_families
        )
        and (
            not supported_backtest_engines
            or profile.backtest.engine in supported_backtest_engines
        )
    )


def resolve_target_backtester(profile: TradingProfile) -> TargetBacktesterDefinition:
    """按五维为目标持仓回测选择实现。"""

    matches = [
        definition
        for definition in _TARGET_BACKTESTERS.values()
        if _matches(
            supported_markets=definition.supported_markets,
            supported_asset_types=definition.supported_asset_types,
            supported_data_frequencies=definition.supported_data_frequencies,
            supported_rebalance_frequencies=definition.supported_rebalance_frequencies,
            supported_strategy_families=definition.supported_strategy_families,
            supported_backtest_engines=definition.supported_backtest_engines,
            profile=profile,
        )
    ]
    if not matches:
        raise LookupError(f"未找到适用于画像 {profile.dimension_key} 的目标持仓回测器")
    if len(matches) > 1:
        matched_ids = ", ".join(sorted(item.backtester_id for item in matches))
        raise LookupError(
            f"画像 {profile.dimension_key} 匹配到多个目标持仓回测器：{matched_ids}"
        )
    return matches[0]


def resolve_simulation_backtester(profile: TradingProfile) -> SimulationBacktesterDefinition:
    """按五维为策略仿真回测选择实现。"""

    matches = [
        definition
        for definition in _SIMULATION_BACKTESTERS.values()
        if _matches(
            supported_markets=definition.supported_markets,
            supported_asset_types=definition.supported_asset_types,
            supported_data_frequencies=definition.supported_data_frequencies,
            supported_rebalance_frequencies=definition.supported_rebalance_frequencies,
            supported_strategy_families=definition.supported_strategy_families,
            profile=profile,
        )
    ]
    if not matches:
        raise LookupError(f"未找到适用于画像 {profile.dimension_key} 的策略仿真回测器")
    if len(matches) > 1:
        matched_ids = ", ".join(sorted(item.backtester_id for item in matches))
        raise LookupError(
            f"画像 {profile.dimension_key} 匹配到多个策略仿真回测器：{matched_ids}"
        )
    return matches[0]


def run_target_backtest(
    profile: TradingProfile,
    market_df: pl.DataFrame,
    targets: pl.DataFrame,
) -> BacktestResult:
    """根据画像运行目标权重回测。

    调用前的策略目标应已按信号日生成；所选回测器负责使用画像中允许的频率和引擎。
    当前注册实现是连续合约的 ``weight_return`` 研究，不等同于保证金账户逐笔成交。
    """

    definition = resolve_target_backtester(profile)
    return definition.backtester(profile, market_df, targets)


def run_simulation_backtest(
    profile: TradingProfile,
    *,
    strategy_name: str,
    symbol: str = "RB_CONT",
) -> dict:
    """根据画像运行策略仿真回测。"""

    definition = resolve_simulation_backtester(profile)
    return definition.backtester_factory().run(
        profile,
        strategy_name=strategy_name,
        symbol=symbol,
    )


def _run_bar_event_backtest(
    profile: TradingProfile,
    market_df: pl.DataFrame,
    targets: pl.DataFrame,
) -> BacktestResult:
    """运行连续合约收益型事件回测。

    ``periods_per_year`` 只用于把周期收益换算为年化统计，不会制造额外交易日。实际
    成交延迟、换月、保证金和流动性约束不在这个轻量研究引擎中模拟。
    """

    return run_event_backtest(
        market_df,
        targets,
        periods_per_year=periods_per_year_for_frequency(profile.data_frequency),
    )


register_target_backtester(
    "continuous_futures_research_backtest",
    _run_bar_event_backtest,
    supported_markets=(Market.CN,),
    supported_asset_types=(AssetType.FUTURES,),
    supported_data_frequencies=(DataFrequency.D1,),
    supported_rebalance_frequencies=(RebalanceFrequency.D1,),
    supported_strategy_families=(StrategyFamily.TREND_FOLLOWING,),
    supported_backtest_engines=("weight_return",),
)

"""按交易五维选择回测器的注册表。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import polars as pl

from northstar_quant.backtest.event_engine import BacktestResult, run_event_backtest
from northstar_quant.backtest.intraday_event_engine import run_intraday_event_backtest
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
) -> bool:
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
            profile=profile,
        )
    ]
    if not matches:
        raise LookupError(f"未找到适用于画像 {profile.dimension_key} 的目标持仓回测器")
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
    return matches[0]


def run_target_backtest(
    profile: TradingProfile,
    market_df: pl.DataFrame,
    targets: pl.DataFrame,
) -> BacktestResult:
    """根据画像运行目标持仓回测。"""

    definition = resolve_target_backtester(profile)
    return definition.backtester(profile, market_df, targets)


def run_simulation_backtest(
    profile: TradingProfile,
    *,
    strategy_name: str,
    symbol: str = "SPY",
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
    return run_event_backtest(
        market_df,
        targets,
        periods_per_year=periods_per_year_for_frequency(profile.data_frequency),
    )


def _run_intraday_event_backtest(
    profile: TradingProfile,
    market_df: pl.DataFrame,
    targets: pl.DataFrame,
) -> BacktestResult:
    return run_intraday_event_backtest(
        market_df,
        targets,
        periods_per_year=periods_per_year_for_frequency(profile.data_frequency),
    )


def _build_backtrader_bar_simulation_backtester() -> SimulationBacktesterBase:
    from northstar_quant.backtest.backtrader_runner import BacktraderBarSimulationBacktester

    return BacktraderBarSimulationBacktester()


def _build_intraday_signal_simulation_backtester() -> SimulationBacktesterBase:
    from northstar_quant.backtest.intraday_runner import IntradaySignalSimulationBacktester

    return IntradaySignalSimulationBacktester()


register_target_backtester(
    "bar_event_backtest",
    _run_bar_event_backtest,
    supported_markets=(Market.US,),
    supported_asset_types=(AssetType.ETF, AssetType.EQUITY),
    supported_data_frequencies=(DataFrequency.D1, DataFrequency.W1),
    supported_rebalance_frequencies=(RebalanceFrequency.D1, RebalanceFrequency.W1),
    supported_strategy_families=(
        StrategyFamily.MOMENTUM_ROTATION,
        StrategyFamily.CROSS_SECTIONAL_SELECTION,
        StrategyFamily.TREND_FOLLOWING,
        StrategyFamily.MEAN_REVERSION,
    ),
)
register_target_backtester(
    "intraday_event_backtest",
    _run_intraday_event_backtest,
    supported_markets=(Market.US,),
    supported_asset_types=(AssetType.EQUITY,),
    supported_data_frequencies=(DataFrequency.M1, DataFrequency.M5, DataFrequency.M15, DataFrequency.H1),
    supported_rebalance_frequencies=(
        RebalanceFrequency.M1,
        RebalanceFrequency.M5,
        RebalanceFrequency.M15,
        RebalanceFrequency.H1,
    ),
    supported_strategy_families=(StrategyFamily.INTRADAY_BREAKOUT,),
)
register_simulation_backtester(
    "backtrader_bar_simulation",
    _build_backtrader_bar_simulation_backtester,
    supported_markets=(Market.US,),
    supported_asset_types=(AssetType.ETF, AssetType.EQUITY),
    supported_data_frequencies=(DataFrequency.D1, DataFrequency.W1),
    supported_rebalance_frequencies=(RebalanceFrequency.D1, RebalanceFrequency.W1),
    supported_strategy_families=(
        StrategyFamily.MOMENTUM_ROTATION,
        StrategyFamily.CROSS_SECTIONAL_SELECTION,
        StrategyFamily.TREND_FOLLOWING,
        StrategyFamily.MEAN_REVERSION,
    ),
)
register_simulation_backtester(
    "intraday_signal_simulation",
    _build_intraday_signal_simulation_backtester,
    supported_markets=(Market.US,),
    supported_asset_types=(AssetType.EQUITY,),
    supported_data_frequencies=(DataFrequency.M1, DataFrequency.M5, DataFrequency.M15),
    supported_rebalance_frequencies=(RebalanceFrequency.M1, RebalanceFrequency.M5, RebalanceFrequency.M15),
    supported_strategy_families=(StrategyFamily.INTRADAY_BREAKOUT,),
)

"""按交易五维选择回测器的注册表。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import polars as pl

from northstar_quant.research.backtest.event_engine import run_event_backtest
from northstar_quant.research.backtest.models import (
    BacktestEngine,
    BacktestEngineSemantics,
    BacktestResult,
    engine_semantics,
)
from northstar_quant.research.backtest.futures_actual_adapter import run_actual_futures_backtest
from northstar_quant.research.backtest.futures_intraday_adapter import (
    run_actual_futures_intraday_replay,
)
from northstar_quant.research.backtest.metrics import periods_per_year_for_frequency
from northstar_quant.platform.common.enums import AssetType, DataFrequency, Market, RebalanceFrequency, StrategyFamily
from northstar_quant.platform.config.trading_profile import TradingProfile

TargetBacktester = Callable[[TradingProfile, pl.DataFrame, pl.DataFrame], BacktestResult]


@dataclass(frozen=True, slots=True)
class TargetBacktesterDefinition:
    """目标持仓回测器注册元数据。"""

    backtester_id: str
    engine: BacktestEngine
    backtester: TargetBacktester
    supported_markets: tuple[Market, ...] = ()
    supported_asset_types: tuple[AssetType, ...] = ()
    supported_data_frequencies: tuple[DataFrequency, ...] = ()
    supported_rebalance_frequencies: tuple[RebalanceFrequency, ...] = ()
    supported_strategy_families: tuple[StrategyFamily, ...] = ()

    @property
    def semantics(self) -> BacktestEngineSemantics:
        """由 engine 固定的真实性声明，不能由注册调用方伪造。"""

        return engine_semantics(self.engine)


_TARGET_BACKTESTERS: dict[str, TargetBacktesterDefinition] = {}
_TARGET_BACKTESTERS_SEALED = False


def register_target_backtester(
    backtester_id: str,
    engine: BacktestEngine,
    backtester: TargetBacktester,
    *,
    supported_markets: tuple[Market, ...] = (),
    supported_asset_types: tuple[AssetType, ...] = (),
    supported_data_frequencies: tuple[DataFrequency, ...] = (),
    supported_rebalance_frequencies: tuple[RebalanceFrequency, ...] = (),
    supported_strategy_families: tuple[StrategyFamily, ...] = (),
    replace: bool = False,
) -> None:
    """注册启动期内置目标持仓回测器。

    模块加载完成后注册表会封存。运行时替换任何内置 adapter 会让一个任意 callable
    伪装成已声明的成交真实性，因此一律拒绝。
    """

    if _TARGET_BACKTESTERS_SEALED:
        raise RuntimeError("目标持仓回测器注册表已封存，禁止运行时注册或替换")
    if backtester_id in _TARGET_BACKTESTERS and not replace:
        raise ValueError(f"目标持仓回测器已注册：{backtester_id}")
    _TARGET_BACKTESTERS[backtester_id] = TargetBacktesterDefinition(
        backtester_id=backtester_id,
        engine=BacktestEngine.parse(engine),
        backtester=backtester,
        supported_markets=supported_markets,
        supported_asset_types=supported_asset_types,
        supported_data_frequencies=supported_data_frequencies,
        supported_rebalance_frequencies=supported_rebalance_frequencies,
        supported_strategy_families=supported_strategy_families,
    )


def list_target_backtesters() -> list[str]:
    """列出已注册的目标持仓回测器。"""

    return sorted(_TARGET_BACKTESTERS)


def _matches(
    *,
    supported_markets: tuple[Market, ...],
    supported_asset_types: tuple[AssetType, ...],
    supported_data_frequencies: tuple[DataFrequency, ...],
    supported_rebalance_frequencies: tuple[RebalanceFrequency, ...],
    supported_strategy_families: tuple[StrategyFamily, ...],
    profile: TradingProfile,
    engine: BacktestEngine,
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
        and profile.backtest.engine == engine.value
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
            engine=definition.engine,
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


def run_target_backtest(
    profile: TradingProfile,
    market_df: pl.DataFrame,
    targets: pl.DataFrame,
) -> BacktestResult:
    """根据画像运行目标权重回测。

    调用前的策略目标应已按信号日生成；所选回测器负责使用画像中允许的频率和引擎。
    当前三种实现分别服务于连续收益研究、实际合约逐日回测和分钟订单回放。
    """

    definition = resolve_target_backtester(profile)
    result = definition.backtester(profile, market_df, targets)
    if result.engine is not definition.engine:
        raise RuntimeError(
            "回测器返回的 engine 与注册 descriptor 不一致："
            f"{result.engine.value} != {definition.engine.value}"
        )
    return result


def _run_bar_event_backtest(
    profile: TradingProfile,
    market_df: pl.DataFrame,
    targets: pl.DataFrame,
) -> BacktestResult:
    """运行连续合约收益型事件回测。

    ``periods_per_year`` 只用于把周期收益换算为年化统计，不会制造额外交易日。画像
    中的资金、佣金、滑点和信号延迟会传入收益型引擎；换月、保证金和流动性约束仍不
    在这个轻量研究引擎中模拟。
    """

    return run_event_backtest(
        market_df,
        targets,
        periods_per_year=periods_per_year_for_frequency(profile.data_frequency),
        initial_cash=profile.backtest.initial_cash,
        commission_bps=profile.backtest.commission_bps,
        min_commission=profile.backtest.min_commission,
        slippage_bps=profile.backtest.slippage_bps,
        execution_delay_sessions=profile.backtest.execution_delay_sessions,
        lot_size=profile.backtest.lot_size,
        sellable_after_sessions=profile.backtest.sellable_after_sessions,
    )


register_target_backtester(
    "continuous_futures_research_backtest",
    BacktestEngine.WEIGHT_RETURN,
    _run_bar_event_backtest,
    supported_markets=(Market.CN,),
    supported_asset_types=(AssetType.FUTURES,),
    supported_data_frequencies=(DataFrequency.D1,),
    supported_rebalance_frequencies=(RebalanceFrequency.D1,),
    supported_strategy_families=(StrategyFamily.TREND_FOLLOWING,),
)

register_target_backtester(
    "actual_futures_daily_backtest",
    BacktestEngine.FUTURES_DAILY,
    run_actual_futures_backtest,
    supported_markets=(Market.CN,),
    supported_asset_types=(AssetType.FUTURES,),
    supported_data_frequencies=(DataFrequency.D1,),
    supported_rebalance_frequencies=(RebalanceFrequency.D1,),
    supported_strategy_families=(StrategyFamily.TREND_FOLLOWING,),
)

register_target_backtester(
    "actual_futures_intraday_replay_backtest",
    BacktestEngine.FUTURES_INTRADAY_REPLAY,
    run_actual_futures_intraday_replay,
    supported_markets=(Market.CN,),
    supported_asset_types=(AssetType.FUTURES,),
    supported_data_frequencies=(DataFrequency.M1,),
    supported_rebalance_frequencies=(RebalanceFrequency.D1,),
    supported_strategy_families=(StrategyFamily.TREND_FOLLOWING,),
)

_TARGET_BACKTESTERS_SEALED = True

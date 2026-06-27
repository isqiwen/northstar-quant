"""执行计划器注册表。"""

from __future__ import annotations

from collections.abc import Callable
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

import polars as pl

from northstar_quant.common.enums import (
    AssetType,
    DataFrequency,
    Market,
    RebalanceFrequency,
    StrategyFamily,
    StrategyOutputType,
)
from northstar_quant.common.time import ensure_utc
from northstar_quant.config.trading_profile import TradingProfile
from northstar_quant.execution.intent_planner import build_execution_intent_plan
from northstar_quant.execution.models import (
    BrokerStateSnapshot,
    FillSnapshot,
    PositionSnapshot,
    RebalanceOrderPlan,
)
from northstar_quant.execution.rebalance import build_rebalance_plan

ExecutionPlanner = Callable[
    [TradingProfile, pl.DataFrame, list[PositionSnapshot], dict[str, float], float | None],
    list[RebalanceOrderPlan],
]

_FINAL_ORDER_STATUSES = {
    "filled",
    "cancelled",
    "apicancelled",
    "inactive",
    "rejected",
    "unknownterminal",
}


@dataclass(frozen=True, slots=True)
class ExecutionPlannerDefinition:
    """执行计划器注册元数据。"""

    planner_id: str
    planner: ExecutionPlanner
    supported_output_types: tuple[StrategyOutputType, ...] = ()
    supported_markets: tuple[Market, ...] = ()
    supported_asset_types: tuple[AssetType, ...] = ()
    supported_data_frequencies: tuple[DataFrequency, ...] = ()
    supported_rebalance_frequencies: tuple[RebalanceFrequency, ...] = ()
    supported_strategy_families: tuple[StrategyFamily, ...] = ()


_REGISTRY: dict[str, ExecutionPlannerDefinition] = {}


def register_execution_planner(
    planner_id: str,
    planner: ExecutionPlanner,
    *,
    supported_output_types: tuple[StrategyOutputType, ...] = (),
    supported_markets: tuple[Market, ...] = (),
    supported_asset_types: tuple[AssetType, ...] = (),
    supported_data_frequencies: tuple[DataFrequency, ...] = (),
    supported_rebalance_frequencies: tuple[RebalanceFrequency, ...] = (),
    supported_strategy_families: tuple[StrategyFamily, ...] = (),
    replace: bool = False,
) -> None:
    """注册执行计划器。"""

    if planner_id in _REGISTRY and not replace:
        raise ValueError(f"执行计划器已注册：{planner_id}")
    _REGISTRY[planner_id] = ExecutionPlannerDefinition(
        planner_id=planner_id,
        planner=planner,
        supported_output_types=supported_output_types,
        supported_markets=supported_markets,
        supported_asset_types=supported_asset_types,
        supported_data_frequencies=supported_data_frequencies,
        supported_rebalance_frequencies=supported_rebalance_frequencies,
        supported_strategy_families=supported_strategy_families,
    )


def list_execution_planners() -> list[str]:
    """列出当前已注册的执行计划器。"""

    return sorted(_REGISTRY)


def _matches(
    definition: ExecutionPlannerDefinition,
    profile: TradingProfile,
    output_type: StrategyOutputType,
) -> bool:
    return (
        (not definition.supported_output_types or output_type in definition.supported_output_types)
        and (not definition.supported_markets or profile.market in definition.supported_markets)
        and (not definition.supported_asset_types or profile.asset_type in definition.supported_asset_types)
        and (
            not definition.supported_data_frequencies
            or profile.data_frequency in definition.supported_data_frequencies
        )
        and (
            not definition.supported_rebalance_frequencies
            or profile.rebalance_frequency in definition.supported_rebalance_frequencies
        )
        and (
            not definition.supported_strategy_families
            or profile.strategy_family in definition.supported_strategy_families
        )
    )


def resolve_execution_planner(
    profile: TradingProfile,
    output_type: StrategyOutputType,
) -> ExecutionPlannerDefinition:
    """根据画像与输出类型选择执行计划器。"""

    matches = [
        definition
        for definition in _REGISTRY.values()
        if _matches(definition, profile, output_type)
    ]
    if not matches:
        raise LookupError(
            f"未找到适用于画像 {profile.dimension_key} 且输出类型为 {output_type.value} 的执行计划器"
        )
    return matches[0]


def _signed_qty(side: str, qty: float) -> float:
    normalized_side = str(side).upper()
    if normalized_side == "BUY":
        return abs(float(qty))
    if normalized_side == "SELL":
        return -abs(float(qty))
    return 0.0


def _remaining_order_qty(row: dict) -> float:
    remaining_qty = row.get("remaining_qty")
    if remaining_qty is not None:
        return max(float(remaining_qty), 0.0)

    total_qty = row.get("qty")
    if total_qty is None:
        return 0.0

    filled_qty = float(row.get("filled_qty", 0.0) or 0.0)
    return max(float(total_qty) - filled_qty, 0.0)


def _is_working_order(row: dict) -> bool:
    status = str(row.get("status") or "").strip().lower()
    if not status:
        return True
    return status not in _FINAL_ORDER_STATUSES


def _fill_affects_planning(fill: FillSnapshot, snapshot_asof: datetime) -> bool:
    return ensure_utc(fill.filled_at) > snapshot_asof


def project_broker_state_positions(broker_state: BrokerStateSnapshot) -> list[PositionSnapshot]:
    """把券商状态投影成用于计划计算的净持仓。

    计划视图会合并三部分信息：
    - 当前真实持仓
    - 仍在挂单簿上的 working orders
    - 在持仓快照之后发生、但尚未来得及反映进 positions 的成交
    """

    snapshot_asof = ensure_utc(broker_state.asof)
    qty_by_symbol: dict[str, float] = defaultdict(float)

    for item in broker_state.positions:
        symbol = str(item.symbol or "").strip()
        if not symbol:
            continue
        qty_by_symbol[symbol] += float(item.qty or 0.0)

    for row in broker_state.open_orders:
        if not _is_working_order(row):
            continue
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        remaining_qty = _remaining_order_qty(row)
        if remaining_qty <= 0:
            continue
        qty_by_symbol[symbol] += _signed_qty(str(row.get("side") or ""), remaining_qty)

    for fill in broker_state.fills:
        symbol = str(fill.symbol or "").strip()
        if not symbol:
            continue
        if not _fill_affects_planning(fill, snapshot_asof):
            continue
        qty_by_symbol[symbol] += _signed_qty(fill.side, float(fill.qty or 0.0))

    return [
        PositionSnapshot(symbol=symbol, qty=qty, asof=snapshot_asof)
        for symbol, qty in sorted(qty_by_symbol.items())
        if abs(qty) > 1e-8
    ]


def build_execution_plan(
    profile: TradingProfile,
    output: pl.DataFrame,
    output_type: StrategyOutputType,
    broker_state: BrokerStateSnapshot,
    latest_prices: dict[str, float],
    *,
    equity: float | None = None,
) -> list[RebalanceOrderPlan]:
    """按交易画像和输出类型构建执行计划。"""

    definition = resolve_execution_planner(profile, output_type)
    planning_positions = project_broker_state_positions(broker_state)
    plans = definition.planner(profile, output, planning_positions, latest_prices, equity)
    for plan in plans:
        if not plan.reason:
            plan.reason = f"{profile.rebalance_frequency.value}_rebalance"
    return plans


def _build_bar_close_rebalance_plan(
    profile: TradingProfile,
    targets: pl.DataFrame,
    positions: list[PositionSnapshot],
    latest_prices: dict[str, float],
    equity: float | None = None,
) -> list[RebalanceOrderPlan]:
    plans = build_rebalance_plan(
        targets,
        positions,
        latest_prices,
        equity,
        rebalance_min_trade_value=profile.execution.rebalance_min_trade_value,
        rebalance_weight_tolerance=profile.execution.rebalance_weight_tolerance,
        long_only=profile.execution.long_only,
    )
    for plan in plans:
        plan.reason = f"{profile.rebalance_frequency.value}_rebalance"
        plan.strategy_id = "core_portfolio"
    return plans


def _build_direct_execution_intent_plan(
    profile: TradingProfile,
    intents: pl.DataFrame,
    positions: list[PositionSnapshot],
    latest_prices: dict[str, float],
    equity: float | None = None,
) -> list[RebalanceOrderPlan]:
    del profile
    return build_execution_intent_plan(intents, positions, latest_prices, equity)


register_execution_planner(
    "bar_close_rebalance",
    _build_bar_close_rebalance_plan,
    supported_output_types=(StrategyOutputType.TARGET_WEIGHT,),
    supported_markets=(Market.US, Market.CN),
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
register_execution_planner(
    "direct_execution_intent",
    _build_direct_execution_intent_plan,
    supported_output_types=(StrategyOutputType.EXECUTION_INTENT,),
    supported_markets=(Market.US, Market.CN),
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

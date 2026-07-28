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
from northstar_quant.common.order_status import is_final_order_status
from northstar_quant.common.time import ensure_utc
from northstar_quant.config.ctp_contract_mapping import load_ctp_contract_registry
from northstar_quant.config.trading_profile import TradingProfile
from northstar_quant.execution.models import (
    BrokerStateSnapshot,
    FillSnapshot,
    PositionSnapshot,
    RebalanceOrderPlan,
)

ExecutionPlanner = Callable[
    [TradingProfile, pl.DataFrame, list[PositionSnapshot], dict[str, float], float | None],
    list[RebalanceOrderPlan],
]


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
    """判断计划器是否同时满足输出语义与画像五维。

    空的 supported 元组表示不限制该维度；非空代表必须精确匹配。执行计划会改变订单
    方向和数量，因此匹配不到或匹配多个时必须失败，而不能任选一个计划器。
    """

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
    if len(matches) > 1:
        matched_ids = ", ".join(sorted(item.planner_id for item in matches))
        raise LookupError(
            f"画像 {profile.dimension_key} 且输出类型为 {output_type.value} "
            f"匹配到多个执行计划器：{matched_ids}"
        )
    return matches[0]


def _signed_qty(side: str, qty: float) -> float:
    """把 BUY/SELL 数量转换为净持仓方向；未知方向返回 0，避免放大脏数据。"""

    normalized_side = str(side).upper()
    if normalized_side == "BUY":
        return abs(float(qty))
    if normalized_side == "SELL":
        return -abs(float(qty))
    return 0.0


def _remaining_order_qty(row: dict) -> float:
    """计算工作订单尚未成交的数量，优先使用券商明确提供的 remaining_qty。"""

    remaining_qty = row.get("remaining_qty")
    if remaining_qty is not None:
        return max(float(remaining_qty), 0.0)

    total_qty = row.get("qty")
    if total_qty is None:
        return 0.0

    filled_qty = float(row.get("filled_qty", 0.0) or 0.0)
    return max(float(total_qty) - filled_qty, 0.0)


def _is_working_order(row: dict) -> bool:
    return not is_final_order_status(row.get("status"))


def _fill_affects_planning(fill: FillSnapshot, snapshot_asof: datetime) -> bool:
    if fill.filled_at is None:
        raise ValueError(
            "BROKER_FILL_TIMESTAMP_REQUIRED: 成交缺少 filled_at，"
            "无法判断是否已反映在持仓快照中。"
        )
    return ensure_utc(fill.filled_at) > snapshot_asof


def project_broker_state_positions(broker_state: BrokerStateSnapshot) -> list[PositionSnapshot]:
    """把券商状态投影成用于计划计算的净持仓。

    计划视图会合并三部分信息：
    - 当前真实持仓
    - 仍在挂单簿上的 working orders
    - 在持仓快照之后发生、但尚未来得及反映进 positions 的成交

    因此投影值是“若全部已知挂单按剩余数量成交后的预期净持仓”，用于避免再次下达
    同方向订单。它不是券商官方持仓，也不能用于账务或保证金结算。
    """

    if broker_state.asof is None:
        raise ValueError(
            "BROKER_STATE_TIMESTAMP_REQUIRED: 券商状态缺少 asof，"
            "无法安全投影计划持仓。"
        )
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
    """按交易画像和输出类型构建“计划”，而不是提交订单。

    期货路径先拒绝连续合约画像，再校验 CTP 映射；即使映射存在，也会在 CTP 报单、
    保证金、开平仓与回报状态机完成前明确停止。其他输出类型由已匹配计划器将目标权重
    与投影后持仓、最新价格和权益换算为 ``RebalanceOrderPlan``，后续仍必须经过风控。
    """

    if profile.asset_type == AssetType.FUTURES:
        futures = profile.futures
        if futures is None or futures.symbols_are_continuous or not futures.execution_allowed:
            raise ValueError(
                "FUTURES_CONTINUOUS_RESEARCH_ONLY: 连续合约研究画像不能生成实际订单计划。"
            )
        registry = load_ctp_contract_registry(futures.ctp_contract_mapping_path)
        if "symbol" not in output.columns:
            raise ValueError("CTP_CONTRACT_SYMBOL_REQUIRED: 策略输出缺少 symbol 列。")
        for symbol in output.get_column("symbol").unique().to_list():
            registry.resolve_data_symbol(str(symbol))
        raise NotImplementedError(
            "CTP_EXECUTION_ADAPTER_REQUIRED: CTP 合约映射已校验，但 CTP 连接、"
            "保证金、开平仓与回报状态机尚未实现。"
        )
    if output_type == StrategyOutputType.TRADE_PLAN:
        raise ValueError(
            "TradePlan 必须先经风险层完成仓位计算和审批，当前不能直接生成券商执行计划。"
        )
    definition = resolve_execution_planner(profile, output_type)
    planning_positions = project_broker_state_positions(broker_state)
    plans = definition.planner(profile, output, planning_positions, latest_prices, equity)
    plans = _apply_rebalance_tolerance(
        plans,
        tolerance=profile.execution.rebalance_weight_tolerance,
        equity=equity,
    )
    for plan in plans:
        if not plan.reason:
            plan.reason = f"{profile.rebalance_frequency.value}_rebalance"
    return plans


def _apply_rebalance_tolerance(
    plans: list[RebalanceOrderPlan],
    *,
    tolerance: float,
    equity: float | None,
) -> list[RebalanceOrderPlan]:
    """过滤目标与当前权重差异落在容忍带内的计划。

    计划缺少目标权重、当前数量、可信价格或账户权益时无法证明落在容忍带内，因此保留
    计划交给后续风控，而不是猜测并静默删除。
    """

    if tolerance <= 0:
        return plans
    if equity is None or equity <= 0:
        return plans
    filtered: list[RebalanceOrderPlan] = []
    for plan in plans:
        price = plan.execution_reference_price or plan.latest_price
        if (
            plan.target_weight is None
            or plan.current_qty is None
            or price is None
            or price <= 0
        ):
            filtered.append(plan)
            continue
        current_weight = float(plan.current_qty) * float(price) / float(equity)
        if abs(float(plan.target_weight) - current_weight) + 1e-12 >= tolerance:
            filtered.append(plan)
    return filtered

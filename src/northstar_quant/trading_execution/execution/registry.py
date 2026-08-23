"""执行计划器注册表。"""

from __future__ import annotations

from collections.abc import Callable
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import math

import polars as pl

from northstar_quant.foundation.common.enums import (
    AssetType,
    CtpOffset,
    DataFrequency,
    Market,
    OrderSemantic,
    RebalanceFrequency,
    StrategyFamily,
    StrategyOutputType,
)
from northstar_quant.foundation.common.order_status import is_final_order_status
from northstar_quant.foundation.common.time import ensure_utc
from northstar_quant.trading_execution.broker.ctp_contract_mapping import (
    CtpContractMapping,
    CtpContractRegistry,
    load_ctp_contract_registry,
)
from northstar_quant.foundation.config.trading_profile import TradingProfile
from northstar_quant.trading_execution.execution.models import (
    BrokerStateSnapshot,
    FillSnapshot,
    FuturesExecutionRule,
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
    broker_name: str | None = None,
    futures_rules: dict[str, FuturesExecutionRule] | None = None,
) -> list[RebalanceOrderPlan]:
    """按交易画像和输出类型构建“计划”，而不是提交订单。

    期货路径拒绝连续合约行情画像和连续序列目标；订单计划只能接收已经在组合边界
    解析完成的具体月份合约。目标权重按动态保证金率换算为整数手数，减仓和反手会生成明确的
    OPEN/CLOSE/CLOSE_TODAY/CLOSE_YESTERDAY 计划。
    """

    if profile.asset_type == AssetType.FUTURES:
        futures = profile.futures
        if futures is None or futures.symbols_are_continuous or not futures.execution_allowed:
            raise ValueError(
                "FUTURES_CONTINUOUS_RESEARCH_ONLY: 连续合约研究画像不能生成实际订单计划。"
            )
        normalized_broker = str(broker_name or "").strip().lower()
        if normalized_broker not in {"ctp_sim", "ctp"}:
            raise ValueError(
                "CTP_BROKER_REQUIRED: 期货执行计划必须明确使用 ctp_sim 或 ctp。"
            )
        registry = load_ctp_contract_registry(
            futures.ctp_contract_mapping_path,
            expected_broker=normalized_broker,
        )
        if output_type != StrategyOutputType.TARGET_WEIGHT:
            raise ValueError("期货日线执行当前只接受 target_weight 输出。")
        if any(_is_working_order(row) for row in broker_state.open_orders):
            raise ValueError(
                "FUTURES_WORKING_ORDERS_PRESENT: 存在未完成期货订单，"
                "必须先完成对账，禁止重复规划。"
            )
        if broker_state.asof is None:
            raise ValueError("BROKER_STATE_TIMESTAMP_REQUIRED: 期货计划缺少状态时间戳。")
        snapshot_asof = ensure_utc(broker_state.asof)
        if any(_fill_affects_planning(fill, snapshot_asof) for fill in broker_state.fills):
            raise ValueError(
                "FUTURES_POSITION_RECONCILIATION_REQUIRED: 持仓快照后仍有新成交，"
                "必须刷新今昨仓明细后再规划。"
            )
        plans = _futures_daily_target_weight_planner(
            profile,
            output,
            broker_state.positions,
            latest_prices,
            equity,
            registry=registry,
            futures_rules=futures_rules or {},
        )
        for plan in plans:
            if not plan.reason:
                plan.reason = f"{profile.rebalance_frequency.value}_rebalance"
        return plans
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


def _mapping_for_strategy_symbol(
    registry: CtpContractRegistry,
    symbol: str,
) -> CtpContractMapping:
    normalized = str(symbol or "").strip().upper()
    if normalized.endswith("_CONT"):
        raise ValueError(
            "FUTURES_CONTINUOUS_CONTRACT_FORBIDDEN: 连续研究序列不能生成实际订单计划；"
            "必须先在授权的 Contract Master 组合边界解析为具体月份合约。"
        )
    return registry.resolve_data_symbol(normalized)


def _finite_positive(value: float | None, *, message: str) -> float:
    if value is None or not math.isfinite(float(value)) or float(value) <= 0:
        raise ValueError(message)
    return float(value)


def _position_buckets(position: PositionSnapshot) -> tuple[float, float, float, float]:
    buckets = (
        position.long_today_qty,
        position.long_yesterday_qty,
        position.short_today_qty,
        position.short_yesterday_qty,
    )
    if any(value is None for value in buckets):
        raise ValueError(
            "CTP_POSITION_DETAIL_REQUIRED: 期货减仓或反手前必须提供多空今昨仓明细。"
        )
    long_today, long_yesterday, short_today, short_yesterday = (
        float(value or 0.0) for value in buckets
    )
    if min(long_today, long_yesterday, short_today, short_yesterday) < 0:
        raise ValueError("CTP_POSITION_DETAIL_INVALID: 今昨仓数量不能为负数。")
    net_qty = long_today + long_yesterday - short_today - short_yesterday
    if not math.isclose(net_qty, float(position.qty), abs_tol=1e-8):
        raise ValueError(
            "CTP_POSITION_DETAIL_INVALID: 今昨仓明细与净持仓数量不一致。"
        )
    if (long_today + long_yesterday) > 0 and (short_today + short_yesterday) > 0:
        raise ValueError(
            "CTP_HEDGE_POSITION_UNSUPPORTED: 当前执行计划器不支持同合约双向持仓。"
        )
    return long_today, long_yesterday, short_today, short_yesterday


def _append_futures_plan(
    plans: list[RebalanceOrderPlan],
    *,
    mapping: CtpContractMapping,
    side: str,
    qty: float,
    target_weight: float,
    current_qty: float,
    target_qty: float,
    price: float,
    margin_rate: float,
    ctp_offset: CtpOffset,
    order_semantic: OrderSemantic,
) -> None:
    if qty <= 1e-8:
        return
    notional = float(qty) * price * mapping.volume_multiple
    plans.append(
        RebalanceOrderPlan(
            symbol=mapping.data_symbol,
            side=side,
            qty=float(qty),
            target_weight=target_weight,
            current_qty=current_qty,
            target_qty=target_qty,
            latest_price=price,
            execution_reference_price=price,
            estimated_trade_value=notional,
            order_semantic=order_semantic.value,
            reason="futures_daily_margin_rebalance",
            instrument_id=mapping.instrument_id,
            exchange_id=mapping.exchange_id,
            ctp_offset=ctp_offset.value,
            volume_multiple=mapping.volume_multiple,
            margin_rate=margin_rate,
            required_margin=(
                notional * margin_rate
                if ctp_offset == CtpOffset.OPEN
                else 0.0
            ),
        )
    )


def _append_close_plans(
    plans: list[RebalanceOrderPlan],
    *,
    mapping: CtpContractMapping,
    position: PositionSnapshot,
    side: str,
    close_qty: float,
    target_weight: float,
    target_qty: float,
    price: float,
    margin_rate: float,
    semantic: OrderSemantic,
) -> None:
    long_today, long_yesterday, short_today, short_yesterday = _position_buckets(
        position
    )
    today_available = long_today if side == "SELL" else short_today
    yesterday_available = long_yesterday if side == "SELL" else short_yesterday
    if close_qty > today_available + yesterday_available + 1e-8:
        raise ValueError("CTP_CLOSE_POSITION_EXCEEDED: 平仓数量超过对应方向持仓。")

    if mapping.exchange_id not in {"SHFE", "INE"}:
        _append_futures_plan(
            plans,
            mapping=mapping,
            side=side,
            qty=close_qty,
            target_weight=target_weight,
            current_qty=position.qty,
            target_qty=target_qty,
            price=price,
            margin_rate=margin_rate,
            ctp_offset=CtpOffset.CLOSE,
            order_semantic=semantic,
        )
        return

    close_yesterday = min(close_qty, yesterday_available)
    close_today = close_qty - close_yesterday
    _append_futures_plan(
        plans,
        mapping=mapping,
        side=side,
        qty=close_yesterday,
        target_weight=target_weight,
        current_qty=position.qty,
        target_qty=target_qty,
        price=price,
        margin_rate=margin_rate,
        ctp_offset=CtpOffset.CLOSE_YESTERDAY,
        order_semantic=semantic,
    )
    _append_futures_plan(
        plans,
        mapping=mapping,
        side=side,
        qty=close_today,
        target_weight=target_weight,
        current_qty=position.qty,
        target_qty=target_qty,
        price=price,
        margin_rate=margin_rate,
        ctp_offset=CtpOffset.CLOSE_TODAY,
        order_semantic=semantic,
    )


def _futures_daily_target_weight_planner(
    profile: TradingProfile,
    output: pl.DataFrame,
    positions: list[PositionSnapshot],
    latest_prices: dict[str, float],
    equity: float | None,
    *,
    registry: CtpContractRegistry,
    futures_rules: dict[str, FuturesExecutionRule],
) -> list[RebalanceOrderPlan]:
    """把日线风险预算权重转换为具体合约的整数手数和开平计划。"""

    resolved_equity = _finite_positive(
        equity,
        message="FUTURES_ACCOUNT_EQUITY_REQUIRED: 期货计划需要正的账户权益。",
    )
    if "symbol" not in output.columns or "target_weight" not in output.columns:
        raise ValueError("FUTURES_TARGET_COLUMNS_REQUIRED: 输出必须包含 symbol/target_weight。")

    target_by_symbol: dict[str, float] = {}
    mapping_by_symbol: dict[str, CtpContractMapping] = {}
    for row in output.select("symbol", "target_weight").to_dicts():
        mapping = _mapping_for_strategy_symbol(registry, str(row["symbol"]))
        target_weight = float(row["target_weight"])
        if not math.isfinite(target_weight):
            raise ValueError("FUTURES_TARGET_WEIGHT_INVALID: 目标权重必须是有限数。")
        if mapping.data_symbol in target_by_symbol:
            raise ValueError(
                f"FUTURES_TARGET_DUPLICATED: {mapping.data_symbol} 被多个策略 symbol 映射。"
            )
        target_by_symbol[mapping.data_symbol] = target_weight
        mapping_by_symbol[mapping.data_symbol] = mapping

    position_by_symbol: dict[str, PositionSnapshot] = {}
    for position in positions:
        symbol = str(position.symbol or "").strip().upper()
        if not symbol or abs(float(position.qty)) <= 1e-8:
            continue
        mapping = registry.resolve_data_symbol(symbol)
        if symbol in position_by_symbol:
            raise ValueError(f"CTP_POSITION_DUPLICATED: {symbol} 出现多条持仓。")
        position_by_symbol[symbol] = position
        mapping_by_symbol[symbol] = mapping
        target_by_symbol.setdefault(symbol, 0.0)

    plans: list[RebalanceOrderPlan] = []
    for symbol in sorted(target_by_symbol):
        mapping = mapping_by_symbol[symbol]
        rule = futures_rules.get(symbol)
        if rule is None:
            raise ValueError(f"FUTURES_DYNAMIC_RULE_REQUIRED: {symbol} 缺少保证金规则。")
        margin_rate = _finite_positive(
            rule.margin_rate,
            message=f"FUTURES_MARGIN_RATE_INVALID: {symbol} 保证金率无效。",
        )
        if margin_rate > 1:
            raise ValueError(f"FUTURES_MARGIN_RATE_INVALID: {symbol} 保证金率不能大于 1。")
        price = _finite_positive(
            latest_prices.get(symbol),
            message=f"FUTURES_EXECUTION_PRICE_REQUIRED: {symbol} 缺少有效执行价。",
        )
        target_weight = target_by_symbol[symbol]
        target_lots = math.floor(
            abs(resolved_equity * target_weight)
            / (price * mapping.volume_multiple * margin_rate)
            + 1e-12
        )
        target_qty = float(target_lots if target_weight >= 0 else -target_lots)
        if (
            rule.max_position_lots is not None
            and abs(target_qty) > int(rule.max_position_lots)
        ):
            raise ValueError(
                f"FUTURES_POSITION_LIMIT_EXCEEDED: {symbol} 目标 {abs(target_qty):.0f} 手"
                f"超过上限 {rule.max_position_lots} 手。"
            )

        current_position = position_by_symbol.get(symbol)
        current_qty = (
            float(current_position.qty)
            if current_position is not None
            else 0.0
        )
        current_weight = (
            current_qty
            * price
            * mapping.volume_multiple
            * margin_rate
            / resolved_equity
        )
        if (
            profile.execution.rebalance_weight_tolerance > 0
            and abs(target_weight - current_weight) + 1e-12
            < profile.execution.rebalance_weight_tolerance
        ):
            continue
        if math.isclose(current_qty, target_qty, abs_tol=1e-8):
            continue

        reverses = current_qty * target_qty < 0
        if current_qty != 0 and (
            reverses or abs(target_qty) < abs(current_qty)
        ):
            if current_position is None:
                raise RuntimeError("期货持仓计划内部状态不一致。")
            close_qty = (
                abs(current_qty)
                if reverses
                else abs(current_qty) - abs(target_qty)
            )
            _append_close_plans(
                plans,
                mapping=mapping,
                position=current_position,
                side="SELL" if current_qty > 0 else "BUY",
                close_qty=close_qty,
                target_weight=target_weight,
                target_qty=target_qty,
                price=price,
                margin_rate=margin_rate,
                semantic=(
                    OrderSemantic.REVERSE
                    if reverses
                    else OrderSemantic.EXIT
                    if target_qty == 0
                    else OrderSemantic.REDUCE
                ),
            )

        open_qty = (
            abs(target_qty)
            if reverses
            else max(abs(target_qty) - abs(current_qty), 0.0)
        )
        _append_futures_plan(
            plans,
            mapping=mapping,
            side="BUY" if target_qty > 0 else "SELL",
            qty=open_qty,
            target_weight=target_weight,
            current_qty=current_qty,
            target_qty=target_qty,
            price=price,
            margin_rate=margin_rate,
            ctp_offset=CtpOffset.OPEN,
            order_semantic=(
                OrderSemantic.REVERSE if reverses else OrderSemantic.ENTRY
            ),
        )
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


def _registered_futures_daily_planner(
    profile: TradingProfile,
    output: pl.DataFrame,
    positions: list[PositionSnapshot],
    latest_prices: dict[str, float],
    equity: float | None,
) -> list[RebalanceOrderPlan]:
    """注册表占位入口；期货计划必须经 build_execution_plan 注入动态规则。"""

    del profile, output, positions, latest_prices, equity
    raise RuntimeError("期货计划器必须通过 build_execution_plan 调用。")


register_execution_planner(
    "ctp_futures_daily_margin_rebalance",
    _registered_futures_daily_planner,
    supported_output_types=(StrategyOutputType.TARGET_WEIGHT,),
    supported_markets=(Market.CN,),
    supported_asset_types=(AssetType.FUTURES,),
    supported_data_frequencies=(DataFrequency.D1,),
    supported_rebalance_frequencies=(RebalanceFrequency.D1,),
    supported_strategy_families=(StrategyFamily.TREND_FOLLOWING,),
)

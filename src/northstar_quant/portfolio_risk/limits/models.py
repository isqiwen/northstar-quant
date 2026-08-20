"""风控配置模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
import math


@dataclass(slots=True)
class SymbolTradeState:
    """单标的、单个交易时点的实时可交易状态。

    此状态必须来自可信的行情或券商快照。未知值应保持 ``None``，由生产预交易门禁
    按“缺失即拒绝”处理，不能假设标的可交易。
    """

    is_suspended: bool = False
    limit_up_price: float | None = None
    limit_down_price: float | None = None


@dataclass(slots=True)
class RiskLimits:
    """统一风控限制。

    权重类字段用于策略和组合层；金额、数量、可用资金和交易状态字段用于订单提交前。
    同一对象可在研究、模拟和实盘路径复用，但实盘必须配置可信账户与行情状态，不能
    因字段为空而静默放宽检查。
    """

    max_single_weight: float = 0.35  # 单个 symbol 的绝对目标权重上限；不等于保证金比例。
    max_gross_exposure: float = 1.0  # 所有 symbol 绝对目标权重之和上限。
    min_cash_buffer: float = 0.02  # 总暴露缩放后必须预留的账户权益比例。
    min_order_notional: float | None = None  # 单笔委托名义金额下限；None 表示不设下限。
    max_order_notional: float | None = 50000.0  # 单笔委托名义金额上限；None 表示不设上限。
    max_order_qty: float = 10000.0  # 单笔委托数量上限；实际期货仍须受交易所限额约束。
    order_qty_step: float | None = None  # 买卖通用数量步长；None 时不执行步长校验。
    buy_qty_step: float | None = None  # 买入数量步长；提供时优先于通用步长。
    sell_qty_step: float | None = None  # 卖出数量步长；提供时优先于通用步长。
    enforce_available_cash: bool = False  # true 时买入前必须有足够可用资金或购买力。
    enforce_sellable_qty: bool = False  # true 时卖出前必须有足够可卖数量。
    enforce_tradeable_state: bool = False  # true 时缺少状态、停牌或不可交易都会拒绝。
    enforce_price_limit: bool = False  # true 时委托价不得突破可信涨跌停边界。
    long_only: bool = True  # true 时卖出数量不得超过当前多头持仓；缺少持仓状态即拒绝。

    def __post_init__(self) -> None:
        """拒绝会反转或静默放宽风控语义的非法阈值。"""

        for field_name in ("max_single_weight", "max_gross_exposure", "min_cash_buffer"):
            value = float(getattr(self, field_name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"风控字段 {field_name} 必须是非负有限数")
        if self.max_single_weight > 1:
            raise ValueError("风控字段 max_single_weight 不能大于 1")
        if self.min_cash_buffer >= 1:
            raise ValueError("风控字段 min_cash_buffer 必须小于 1")
        for field_name in ("min_order_notional", "max_order_notional"):
            value = getattr(self, field_name)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"风控字段 {field_name} 必须是非负有限数或 None")
        if (
            self.min_order_notional is not None
            and self.max_order_notional is not None
            and self.min_order_notional > self.max_order_notional
        ):
            raise ValueError("min_order_notional 不能大于 max_order_notional")
        if not math.isfinite(self.max_order_qty) or self.max_order_qty <= 0:
            raise ValueError("风控字段 max_order_qty 必须是正有限数")
        for field_name in ("order_qty_step", "buy_qty_step", "sell_qty_step"):
            value = getattr(self, field_name)
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError(f"风控字段 {field_name} 必须是正有限数或 None")
        for field_name in (
            "enforce_available_cash",
            "enforce_sellable_qty",
            "enforce_tradeable_state",
            "enforce_price_limit",
            "long_only",
        ):
            if not isinstance(getattr(self, field_name), bool):
                raise ValueError(f"风控字段 {field_name} 必须是布尔值")


@dataclass(slots=True)
class OrderRiskContext:
    """一次订单路由批次内的动态账户约束与预留量。

    同批次订单必须共享该对象：已提交但未完成的买入金额、卖出数量会先被预留，避免
    多笔订单各自通过检查后合计超出资金或可卖持仓。
    """

    available_cash: float | None = None
    position_qty_by_symbol: dict[str, float] = field(default_factory=dict)
    sellable_qty_by_symbol: dict[str, float] = field(default_factory=dict)
    trade_state_by_symbol: dict[str, SymbolTradeState] = field(default_factory=dict)
    reserved_buy_notional: float = 0.0
    reserved_sell_qty_by_symbol: dict[str, float] = field(default_factory=dict)
    unresolved_open_order_count: int = 0

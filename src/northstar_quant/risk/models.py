"""风控配置模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class RiskLimits:
    """统一风控限制。

    这些字段既可用于事件回测，也可用于实时下单前检查。
    后续你可以把它扩展为按市场、按策略、按账户的多层结构。
    """

    max_single_weight: float = 0.35
    max_gross_exposure: float = 1.0
    min_cash_buffer: float = 0.02
    max_order_notional: float = 50000.0
    max_order_qty: float = 10000.0

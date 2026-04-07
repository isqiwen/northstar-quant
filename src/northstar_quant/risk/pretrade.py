"""交易前风控。"""

from __future__ import annotations

from northstar_quant.execution.models import OrderRequest
from northstar_quant.risk.models import RiskLimits


def validate_order(order: OrderRequest, limits: RiskLimits) -> None:
    """验证单笔订单是否满足交易前约束。"""

    if order.qty <= 0:
        raise ValueError("订单数量必须大于 0")

    if order.qty > limits.max_order_qty:
        raise ValueError("订单数量超过风控上限")

    if order.target_weight is not None and abs(order.target_weight) > limits.max_single_weight:
        raise ValueError("目标权重超过单标的权重上限")

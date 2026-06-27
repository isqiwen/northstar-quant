"""限价单执行器。

个人日频系统并不需要复杂的算法执行器，但至少要具备：
1. 根据最新价格生成合理的限价
2. 可重复尝试追价
3. 超时后交给撤单逻辑处理
"""

from __future__ import annotations

from dataclasses import replace

from northstar_quant.config.settings import get_settings
from northstar_quant.execution.models import OrderRequest


def _calc_limit_price(side: str, reference_price: float, offset_bps: float) -> float:
    """根据买卖方向计算限价。"""

    ratio = offset_bps / 10000.0
    if side.upper() == 'BUY':
        return round(reference_price * (1 + ratio), 2)
    return round(reference_price * (1 - ratio), 2)


def build_limit_order(order: OrderRequest, reference_price: float, step: int = 0) -> OrderRequest:
    """把统一订单转换成限价单。

    这里的 reference_price 应该是执行参考价，
    优先使用 broker quote snapshot，而不是研究侧本地 bar close。

    step 越大，说明追价次数越多，偏移也越大。
    """

    settings = get_settings()
    offset_bps = settings.limit_price_offset_bps * (step + 1)
    return replace(
        order,
        order_type='LMT',
        limit_price=_calc_limit_price(order.side, reference_price, offset_bps),
        reference_price=reference_price,
    )

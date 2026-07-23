"""订单数量辅助函数的兼容入口。

新代码应直接从 :mod:`northstar_quant.common.quantity` 导入。这里保留重导出，
避免已有调用方因架构边界调整而立即失效。
"""

from __future__ import annotations

from northstar_quant.common.quantity import (
    resolve_qty_step,
    round_order_qty_down,
    round_qty_down_to_step,
)

__all__ = [
    "resolve_qty_step",
    "round_order_qty_down",
    "round_qty_down_to_step",
]

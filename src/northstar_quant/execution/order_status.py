"""订单状态分类器的兼容入口。"""

from northstar_quant.common.order_status import (
    CANCELLED_ORDER_STATUSES,
    FILLED_ORDER_STATUSES,
    FINAL_ORDER_STATUSES,
    REJECTED_ORDER_STATUSES,
    WORKING_ORDER_STATUSES,
    is_cancelled_order_status,
    is_filled_order_status,
    is_final_order_status,
    is_rejected_order_status,
    is_working_order_status,
    normalize_order_status,
)

__all__ = [
    "CANCELLED_ORDER_STATUSES",
    "FILLED_ORDER_STATUSES",
    "FINAL_ORDER_STATUSES",
    "REJECTED_ORDER_STATUSES",
    "WORKING_ORDER_STATUSES",
    "is_cancelled_order_status",
    "is_filled_order_status",
    "is_final_order_status",
    "is_rejected_order_status",
    "is_working_order_status",
    "normalize_order_status",
]

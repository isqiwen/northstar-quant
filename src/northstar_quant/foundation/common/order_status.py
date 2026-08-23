"""订单状态的统一分类规则。"""

from __future__ import annotations

FILLED_ORDER_STATUSES = frozenset({"filled"})
CANCELLED_ORDER_STATUSES = frozenset({"cancelled", "canceled", "apicancelled"})
REJECTED_ORDER_STATUSES = frozenset({"inactive", "rejected"})
WORKING_ORDER_STATUSES = frozenset(
    {
        "pending",
        "created",
        "pendingsubmit",
        "presubmitted",
        "submitting",
        "submitted",
        "accepted",
        "apipending",
        "pendingcancel",
        "partiallyfilled",
        "open",
    }
)
FINAL_ORDER_STATUSES = (
    FILLED_ORDER_STATUSES
    | CANCELLED_ORDER_STATUSES
    | REJECTED_ORDER_STATUSES
)


def normalize_order_status(value: object) -> str:
    """把不同券商的大小写和分隔符归一为稳定键。"""

    return "".join(
        character
        for character in str(value or "").lower()
        if character.isalnum()
    )


def is_filled_order_status(value: object) -> bool:
    return normalize_order_status(value) in FILLED_ORDER_STATUSES


def is_cancelled_order_status(value: object) -> bool:
    return normalize_order_status(value) in CANCELLED_ORDER_STATUSES


def is_rejected_order_status(value: object) -> bool:
    return normalize_order_status(value) in REJECTED_ORDER_STATUSES


def is_final_order_status(value: object) -> bool:
    return normalize_order_status(value) in FINAL_ORDER_STATUSES


def is_working_order_status(value: object) -> bool:
    return normalize_order_status(value) in WORKING_ORDER_STATUSES

"""P5-WP02 canonical broker-order lifecycle with fail-closed transitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class OrderLifecycleError(ValueError):
    pass


class BrokerOrderStatus(StrEnum):
    CREATED = "CREATED"
    SUBMITTING = "SUBMITTING"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


_FINAL = frozenset({BrokerOrderStatus.FILLED, BrokerOrderStatus.CANCELLED, BrokerOrderStatus.REJECTED})
_ALLOWED: dict[BrokerOrderStatus, frozenset[BrokerOrderStatus]] = {
    BrokerOrderStatus.CREATED: frozenset({BrokerOrderStatus.SUBMITTING, BrokerOrderStatus.REJECTED}),
    BrokerOrderStatus.SUBMITTING: frozenset({BrokerOrderStatus.ACCEPTED, BrokerOrderStatus.REJECTED, BrokerOrderStatus.UNKNOWN}),
    BrokerOrderStatus.ACCEPTED: frozenset({BrokerOrderStatus.PARTIALLY_FILLED, BrokerOrderStatus.FILLED, BrokerOrderStatus.CANCEL_PENDING, BrokerOrderStatus.REJECTED, BrokerOrderStatus.UNKNOWN}),
    BrokerOrderStatus.PARTIALLY_FILLED: frozenset({BrokerOrderStatus.PARTIALLY_FILLED, BrokerOrderStatus.FILLED, BrokerOrderStatus.CANCEL_PENDING, BrokerOrderStatus.UNKNOWN}),
    BrokerOrderStatus.CANCEL_PENDING: frozenset({BrokerOrderStatus.PARTIALLY_FILLED, BrokerOrderStatus.FILLED, BrokerOrderStatus.CANCELLED, BrokerOrderStatus.UNKNOWN}),
    BrokerOrderStatus.UNKNOWN: frozenset(),
    BrokerOrderStatus.FILLED: frozenset(),
    BrokerOrderStatus.CANCELLED: frozenset(),
    BrokerOrderStatus.REJECTED: frozenset(),
}
_BROKER_STATUS_MAP = {
    "created": BrokerOrderStatus.CREATED,
    "prepared": BrokerOrderStatus.CREATED,
    "submitting": BrokerOrderStatus.SUBMITTING,
    "submitted": BrokerOrderStatus.ACCEPTED,
    "accepted": BrokerOrderStatus.ACCEPTED,
    "partiallyfilled": BrokerOrderStatus.PARTIALLY_FILLED,
    "filled": BrokerOrderStatus.FILLED,
    "pendingcancel": BrokerOrderStatus.CANCEL_PENDING,
    "cancelled": BrokerOrderStatus.CANCELLED,
    "canceled": BrokerOrderStatus.CANCELLED,
    "rejected": BrokerOrderStatus.REJECTED,
    "inactive": BrokerOrderStatus.REJECTED,
    "unknown": BrokerOrderStatus.UNKNOWN,
    "submissionunknown": BrokerOrderStatus.UNKNOWN,
}


@dataclass(frozen=True, slots=True)
class BrokerOrderIdentity:
    client_order_id: str
    broker_order_id: str | None
    client_id: int | None
    perm_id: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.client_order_id, str) or not self.client_order_id.strip():
            raise OrderLifecycleError("client_order_id is required")
        if self.broker_order_id is not None and not self.broker_order_id.strip():
            raise OrderLifecycleError("broker_order_id cannot be blank")


def transition_order_status(*, current: BrokerOrderStatus, target: BrokerOrderStatus) -> BrokerOrderStatus:
    if not isinstance(current, BrokerOrderStatus) or not isinstance(target, BrokerOrderStatus):
        raise OrderLifecycleError("order states must be typed")
    if target is current:
        return current
    if target not in _ALLOWED[current]:
        raise OrderLifecycleError(f"invalid order transition: {current.value} -> {target.value}")
    return target


def canonicalize_broker_order_status(value: object) -> BrokerOrderStatus:
    key = "".join(character for character in str(value or "").lower() if character.isalnum())
    return _BROKER_STATUS_MAP.get(key, BrokerOrderStatus.UNKNOWN)


def apply_broker_order_status(
    *,
    current: object,
    broker_status: object,
) -> BrokerOrderStatus:
    """Apply one broker callback without allowing regressions or guessed recovery."""

    return transition_order_status(
        current=canonicalize_broker_order_status(current),
        target=canonicalize_broker_order_status(broker_status),
    )


__all__ = [
    "BrokerOrderIdentity",
    "BrokerOrderStatus",
    "OrderLifecycleError",
    "apply_broker_order_status",
    "canonicalize_broker_order_status",
    "transition_order_status",
]

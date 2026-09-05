"""Generate simulated fills; account facts are applied by Accounting."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID

from northstar_quant.accounting import FillFact
from northstar_quant.data.research import Market, ResearchBar
from northstar_quant.risk import Side
from northstar_quant.strategy import decimal_text


@dataclass(frozen=True, slots=True)
class PendingOrder:
    order_id: str
    observation_id: UUID
    submitted_at: datetime
    expires_at: datetime
    side: Side
    quantity_lots: int
    minimum_fill_price: Decimal
    maximum_fill_price: Decimal

    def __post_init__(self) -> None:
        if (
            not isinstance(self.order_id, str)
            or not 1 <= len(self.order_id) <= 256
            or not isinstance(self.observation_id, UUID)
            or not isinstance(self.side, Side)
            or type(self.quantity_lots) is not int
            or not 1 <= self.quantity_lots <= 1_000_000_000
        ):
            raise ValueError("order requires stable identity, side and positive integer lots")
        if (
            self.submitted_at.utcoffset() != timedelta(0)
            or self.expires_at.utcoffset() != timedelta(0)
            or self.expires_at <= self.submitted_at
        ):
            raise ValueError("order requires a positive UTC lifetime")
        if (
            not self.minimum_fill_price.is_finite()
            or not self.maximum_fill_price.is_finite()
            or not Decimal(0) < self.minimum_fill_price <= self.maximum_fill_price
        ):
            raise ValueError("order requires an exact positive fill-price interval")

    def to_dict(self) -> dict[str, object]:
        return {
            "order_id": self.order_id,
            "observation_id": str(self.observation_id),
            "submitted_at": self.submitted_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "side": self.side.value,
            "quantity_lots": self.quantity_lots,
            "minimum_fill_price": decimal_text(self.minimum_fill_price),
            "maximum_fill_price": decimal_text(self.maximum_fill_price),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> PendingOrder:
        try:
            quantity = value["quantity_lots"]
            if type(quantity) is not int:
                raise ValueError("order quantity must be an integer")
            return cls(
                str(value["order_id"]),
                UUID(str(value["observation_id"])),
                datetime.fromisoformat(str(value["submitted_at"])),
                datetime.fromisoformat(str(value["expires_at"])),
                Side(str(value["side"])),
                quantity,
                Decimal(str(value["minimum_fill_price"])),
                Decimal(str(value["maximum_fill_price"])),
            )
        except (KeyError, TypeError, ArithmeticError) as error:
            raise ValueError("invalid persisted pending order") from error


def simulate_fill(
    order: PendingOrder,
    bar: ResearchBar,
    market: Market,
    *,
    fee_per_lot: Decimal,
    slippage_ticks: int,
) -> FillFact | None:
    """Use a later completed/available bar and adverse slippage, filling all lots.

    This is the current explicit simulation model, not an external execution
    report. It does not change cash, positions or persistent order state.
    """

    if (
        not fee_per_lot.is_finite()
        or fee_per_lot < 0
        or type(slippage_ticks) is not int
        or slippage_ticks < 0
    ):
        raise ValueError("simulation costs require nonnegative fee and integer ticks")
    if (
        bar.observation_id == order.observation_id
        or bar.completed_at <= order.submitted_at
        or not order.submitted_at < bar.available_at < order.expires_at
    ):
        return None
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        direction = 1 if order.side is Side.BUY else -1
        price = bar.close + direction * slippage_ticks * market.price_tick
        if price <= 0 or not order.minimum_fill_price <= price <= order.maximum_fill_price:
            return None
        identity = hashlib.sha256(
            f"simulation:{order.order_id}:{bar.observation_id}".encode()
        ).hexdigest()
        return FillFact(
            identity,
            order.order_id,
            market.contract_id,
            bar.observation_id,
            bar.available_at,
            bar.trading_day,
            order.side,
            order.quantity_lots,
            price,
            order.quantity_lots * fee_per_lot,
        )

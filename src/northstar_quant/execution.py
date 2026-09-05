"""Causal completed-bar fills and one exact FIFO futures account."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID

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

    def to_dict(self) -> dict[str, object]:
        return {
            "order_id": self.order_id,
            "submitted_at": self.submitted_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "side": self.side.value,
            "quantity_lots": self.quantity_lots,
            "minimum_fill_price": decimal_text(self.minimum_fill_price),
            "maximum_fill_price": decimal_text(self.maximum_fill_price),
        }


@dataclass(frozen=True, slots=True)
class Fill:
    order_id: str
    observation_id: UUID
    filled_at: datetime
    side: Side
    quantity_lots: int
    price: Decimal
    fee: Decimal
    realized_pnl: Decimal
    position_lots: int
    cash: Decimal

    def to_dict(self) -> dict[str, object]:
        return {
            "order_id": self.order_id,
            "observation_id": str(self.observation_id),
            "filled_at": self.filled_at.isoformat(),
            "side": self.side.value,
            "quantity_lots": self.quantity_lots,
            "price": decimal_text(self.price),
            "fee": decimal_text(self.fee),
            "realized_pnl": decimal_text(self.realized_pnl),
            "position_lots": self.position_lots,
            "cash": decimal_text(self.cash),
        }


@dataclass(slots=True)
class _Lot:
    direction: int
    quantity: int
    entry_price: Decimal


class Account:
    """The shared simulated account; each fill updates cash before another decision."""

    def __init__(self, initial_cash: Decimal, market: Market) -> None:
        self.initial_cash = initial_cash
        self.market = market
        self.cash = initial_cash
        self.realized_pnl = Decimal(0)
        self.total_fees = Decimal(0)
        self.fills: list[Fill] = []
        self._lots: list[_Lot] = []
        self._filled_order_ids: set[str] = set()
        self._ledger_position = 0

    @property
    def position_lots(self) -> int:
        return sum(lot.direction * lot.quantity for lot in self._lots)

    def unrealized_pnl(self, mark: Decimal) -> Decimal:
        with localcontext() as context:
            context.prec = 96
            context.rounding = ROUND_HALF_EVEN
            return sum(
                (
                    lot.direction * (mark - lot.entry_price) * lot.quantity * self.market.multiplier
                    for lot in self._lots
                ),
                start=Decimal(0),
            )

    def equity(self, mark: Decimal) -> Decimal:
        with localcontext() as context:
            context.prec = 96
            context.rounding = ROUND_HALF_EVEN
            return self.cash + self.unrealized_pnl(mark)

    def execute(
        self,
        order: PendingOrder,
        bar: ResearchBar,
        *,
        fee_per_lot: Decimal,
        slippage_ticks: int,
    ) -> Fill | None:
        """Use only a later completed/available bar, with adverse tick slippage."""

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
            price = bar.close + direction * slippage_ticks * self.market.price_tick
            if price <= 0 or not order.minimum_fill_price <= price <= order.maximum_fill_price:
                return None
            if order.order_id in self._filled_order_ids:
                raise ValueError("an order cannot fill twice")
            quantity = order.quantity_lots
            realized = Decimal(0)
            while quantity and self._lots and self._lots[0].direction != direction:
                lot = self._lots[0]
                closed = min(quantity, lot.quantity)
                realized += (
                    lot.direction * (price - lot.entry_price) * closed * self.market.multiplier
                )
                lot.quantity -= closed
                quantity -= closed
                if lot.quantity == 0:
                    self._lots.pop(0)
            if quantity:
                self._lots.append(_Lot(direction, quantity, price))
            fee = order.quantity_lots * fee_per_lot
            self.total_fees += fee
            self.realized_pnl += realized
            self.cash += realized - fee
            fill = Fill(
                order.order_id,
                bar.observation_id,
                bar.available_at,
                order.side,
                order.quantity_lots,
                price,
                fee,
                realized,
                self.position_lots,
                self.cash,
            )
            self.fills.append(fill)
            self._filled_order_ids.add(order.order_id)
            self._ledger_position += direction * order.quantity_lots
            if (
                self.position_lots != self._ledger_position
                or self.cash != self.initial_cash + self.realized_pnl - self.total_fees
            ):
                raise RuntimeError("account ledger conservation failed")
            return fill

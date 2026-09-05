"""Apply identified single-contract fills to an exact FIFO account.

The caller supplies confirmed facts in its accepted ledger order. Accounting
does not infer fills from bars, require an order to fill all at once, or reject
a fill because a strategy would now reject it. This slice has no daily
settlement or broker opening-balance reconciliation yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID

from northstar_quant.data.research import Market
from northstar_quant.risk import Side
from northstar_quant.strategy import decimal_text


@dataclass(frozen=True, slots=True)
class FillFact:
    """One uniquely identified fill, not an order's cumulative filled quantity."""

    fill_id: str
    order_id: str
    contract_id: UUID
    observation_id: UUID | None
    filled_at: datetime
    trading_day: date
    side: Side
    quantity_lots: int
    price: Decimal
    fee: Decimal

    def __post_init__(self) -> None:
        for identity in (self.fill_id, self.order_id):
            if not isinstance(identity, str) or not 1 <= len(identity) <= 256:
                raise ValueError("fill requires bounded nonempty fill and order identities")
        if (
            not isinstance(self.contract_id, UUID)
            or self.observation_id is not None
            and not isinstance(self.observation_id, UUID)
            or not isinstance(self.side, Side)
        ):
            raise ValueError("fill requires a canonical contract and side")
        if (
            not isinstance(self.filled_at, datetime)
            or self.filled_at.utcoffset() != timedelta(0)
            or type(self.trading_day) is not date
        ):
            raise ValueError("fill requires UTC execution time and explicit trading day")
        if type(self.quantity_lots) is not int or not 1 <= self.quantity_lots <= 1_000_000_000:
            raise ValueError("fill quantity must be a positive integer number of lots")
        for name in ("price", "fee"):
            value = getattr(self, name)
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or (value <= 0 if name == "price" else value < 0)
                or len(value.as_tuple().digits) > 34
                or value.adjusted() > 33
            ):
                raise ValueError("fill requires bounded positive price and nonnegative fee")
            exponent = value.as_tuple().exponent
            if not isinstance(exponent, int) or exponent < -18:
                raise ValueError("fill money must use at most 18 decimal places")

    def to_dict(self) -> dict[str, object]:
        return {
            "fill_id": self.fill_id,
            "order_id": self.order_id,
            "contract_id": str(self.contract_id),
            "observation_id": None if self.observation_id is None else str(self.observation_id),
            "filled_at": self.filled_at.isoformat(),
            "trading_day": self.trading_day.isoformat(),
            "side": self.side.value,
            "quantity_lots": self.quantity_lots,
            "price": decimal_text(self.price),
            "fee": decimal_text(self.fee),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> FillFact:
        """Read fact fields, including from an AppliedFill's flat ledger record."""

        try:
            names = ("fill_id", "order_id", "contract_id", "filled_at", "trading_day", "side")
            if any(not isinstance(value[name], str) for name in names):
                raise ValueError("persisted fill identities and times must be strings")
            price, fee = value["price"], value["fee"]
            if not isinstance(price, str) or not isinstance(fee, str):
                raise ValueError("persisted fill money must be exact decimal strings")
            quantity = value["quantity_lots"]
            if type(quantity) is not int:
                raise ValueError("persisted fill quantity must be an integer")
            observation = value["observation_id"]
            if observation is not None and not isinstance(observation, str):
                raise ValueError("persisted observation identity must be a UUID string or null")
            return cls(
                str(value["fill_id"]),
                str(value["order_id"]),
                UUID(str(value["contract_id"])),
                None if observation is None else UUID(observation),
                datetime.fromisoformat(str(value["filled_at"])),
                date.fromisoformat(str(value["trading_day"])),
                Side(str(value["side"])),
                quantity,
                Decimal(price),
                Decimal(fee),
            )
        except (KeyError, TypeError, ArithmeticError) as error:
            raise ValueError("invalid persisted fill fact") from error


@dataclass(frozen=True, slots=True)
class AppliedFill:
    fact: FillFact
    realized_pnl: Decimal
    position_lots: int
    cash: Decimal
    total_fees: Decimal

    def to_dict(self) -> dict[str, object]:
        return {
            **self.fact.to_dict(),
            "realized_pnl": decimal_text(self.realized_pnl),
            "position_lots": self.position_lots,
            "cash": decimal_text(self.cash),
            "total_fees": decimal_text(self.total_fees),
        }


@dataclass(slots=True)
class _Lot:
    direction: int
    quantity: int
    entry_price: Decimal


class Account:
    """FIFO projection rebuilt from initial cash and accepted individual fills.

    A checkpoint is a comparison target, never an account constructor. Persistent
    callers rebuild from their verified ledger, apply the next fact and commit
    that fact and the new projection together under their account lock.
    """

    def __init__(self, initial_cash: Decimal, market: Market) -> None:
        if (
            not isinstance(initial_cash, Decimal)
            or not initial_cash.is_finite()
            or initial_cash <= 0
        ):
            raise ValueError("account initial cash must be a positive exact amount")
        if (
            not isinstance(market.multiplier, Decimal)
            or not market.multiplier.is_finite()
            or market.multiplier <= 0
        ):
            raise ValueError("account requires a positive exact contract multiplier")
        for value in (initial_cash, market.multiplier):
            exponent = value.as_tuple().exponent
            if (
                not isinstance(exponent, int)
                or exponent < -18
                or value.adjusted() > 33
                or len(value.as_tuple().digits) > 34
            ):
                raise ValueError("account economics exceed the bounded financial domain")
        self.initial_cash = initial_cash
        self.market = market
        self.cash = initial_cash
        self.realized_pnl = Decimal(0)
        self.total_fees = Decimal(0)
        self._fills: dict[str, AppliedFill] = {}
        self._lots: list[_Lot] = []
        self._ledger_position = 0

    @property
    def fill_count(self) -> int:
        return len(self._fills)

    @property
    def position_lots(self) -> int:
        return self._ledger_position

    def unrealized_pnl(self, mark: Decimal) -> Decimal:
        if not isinstance(mark, Decimal) or not mark.is_finite() or mark <= 0:
            raise ValueError("account mark must be a positive exact price")
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

    def apply(self, fact: FillFact) -> AppliedFill:
        if not isinstance(fact, FillFact) or fact.contract_id != self.market.contract_id:
            raise ValueError("fill fact belongs to a different contract")
        previous = self._fills.get(fact.fill_id)
        if previous is not None:
            if previous.fact != fact:
                raise ValueError("fill identity was reused with different facts")
            return previous
        with localcontext() as context:
            context.prec = 96
            context.rounding = ROUND_HALF_EVEN
            direction = 1 if fact.side is Side.BUY else -1
            quantity = fact.quantity_lots
            realized = Decimal(0)
            while quantity and self._lots and self._lots[0].direction != direction:
                lot = self._lots[0]
                closed = min(quantity, lot.quantity)
                realized += (
                    lot.direction * (fact.price - lot.entry_price) * closed * self.market.multiplier
                )
                lot.quantity -= closed
                quantity -= closed
                if lot.quantity == 0:
                    self._lots.pop(0)
            if quantity:
                if self._lots and self._lots[-1].entry_price == fact.price:
                    self._lots[-1].quantity += quantity
                else:
                    self._lots.append(_Lot(direction, quantity, fact.price))
            self.total_fees += fact.fee
            self.realized_pnl += realized
            self.cash += realized - fact.fee
            self._ledger_position += direction * fact.quantity_lots
            if (
                sum(lot.direction * lot.quantity for lot in self._lots) != self._ledger_position
                or self.cash != self.initial_cash + self.realized_pnl - self.total_fees
            ):
                raise RuntimeError("account ledger conservation failed")
            applied = AppliedFill(fact, realized, self.position_lots, self.cash, self.total_fees)
            self._fills[fact.fill_id] = applied
            return applied

    def checkpoint(self) -> dict[str, object]:
        """Bounded open-lot projection for comparison with a rebuilt ledger."""

        return {
            "contract_id": str(self.market.contract_id),
            "initial_cash": decimal_text(self.initial_cash),
            "cash": decimal_text(self.cash),
            "realized_pnl": decimal_text(self.realized_pnl),
            "total_fees": decimal_text(self.total_fees),
            "position_lots": self.position_lots,
            "fill_count": self.fill_count,
            "lots": [
                {
                    "direction": lot.direction,
                    "quantity_lots": lot.quantity,
                    "entry_price": decimal_text(lot.entry_price),
                }
                for lot in self._lots
            ],
        }

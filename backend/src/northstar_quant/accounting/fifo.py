"""Apply identified futures fills to an exact FIFO account.

The caller supplies confirmed facts in its accepted ledger order. Accounting
does not infer fills from bars, require an order to fill all at once, or reject
a fill because a strategy would now reject it. Settlement is an identified
account fact; broker opening-balance reconciliation belongs to the adapter.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID

from northstar_quant.accounting.amounts import decimal_text
from northstar_quant.accounting.positions import Position, PositionChange
from northstar_quant.accounting.settlement import AppliedSettlement, SettlementFact
from northstar_quant.execution.orders import Offset, Side
from northstar_quant.market_data import Market


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
    offset: Offset
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
            or not isinstance(self.offset, Offset)
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
            "offset": self.offset.value,
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
                Offset(str(value["offset"])),
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
    gross_position: Position

    def to_dict(self) -> dict[str, object]:
        return {
            **self.fact.to_dict(),
            "realized_pnl": decimal_text(self.realized_pnl),
            "position_lots": self.position_lots,
            "cash": decimal_text(self.cash),
            "total_fees": decimal_text(self.total_fees),
            "gross_position": self.gross_position.to_dict(),
        }


@dataclass(slots=True)
class _Lot:
    direction: int
    quantity: int
    entry_price: Decimal
    opened_on: date


@dataclass(frozen=True, slots=True)
class _Inventory:
    market: Market
    position: Position = Position()
    lots: tuple[_Lot, ...] = ()
    trading_day: date | None = None
    last_event_at: datetime | None = None


class Account:
    """One cash ledger with FIFO inventory per fixed contract.

    A checkpoint is a comparison target, never an account constructor. Persistent
    callers rebuild from their verified ledger, apply the next fact and commit
    that fact and the new projection together under their account lock.
    """

    def __init__(self, initial_cash: Decimal, markets: tuple[Market, ...]) -> None:
        if (
            not isinstance(initial_cash, Decimal)
            or not initial_cash.is_finite()
            or initial_cash <= 0
        ):
            raise ValueError("account initial cash must be a positive exact amount")
        if not markets or any(not isinstance(market, Market) for market in markets):
            raise ValueError("account requires fixed contract markets")
        if len({market.contract_id for market in markets}) != len(markets):
            raise ValueError("account contract identities must be unique")
        if any(
            not isinstance(market.contract_id, UUID)
            or not isinstance(market.currency, str)
            or len(market.currency) != 3
            or not all("A" <= c <= "Z" for c in market.currency)
            for market in markets
        ):
            raise ValueError("account markets require canonical contracts and currencies")
        if len({market.currency for market in markets}) != 1:
            raise ValueError("one futures account requires one currency; FX is not inferred")
        for value in (initial_cash, *(market.multiplier for market in markets)):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError("account requires positive exact economics")
            exponent = value.as_tuple().exponent
            if (
                not isinstance(exponent, int)
                or exponent < -18
                or value.adjusted() > 33
                or len(value.as_tuple().digits) > 34
            ):
                raise ValueError("account economics exceed the bounded financial domain")
        self.initial_cash = initial_cash
        self.markets = tuple(sorted(markets, key=lambda market: str(market.contract_id)))
        self._inventories = {market.contract_id: _Inventory(market) for market in self.markets}
        self.cash = initial_cash
        self.realized_pnl = Decimal(0)
        self.total_fees = Decimal(0)
        self._fills: dict[str, AppliedFill] = {}
        self._settlements: dict[str, AppliedSettlement] = {}
        self._last_fact_at: datetime | None = None
        self.settlement_pnl = Decimal(0)

    @property
    def fill_count(self) -> int:
        return len(self._fills)

    @property
    def applied_fills(self) -> tuple[AppliedFill, ...]:
        return tuple(self._fills.values())

    @property
    def applied_settlements(self) -> tuple[AppliedSettlement, ...]:
        return tuple(self._settlements.values())

    def market(self, contract_id: UUID) -> Market:
        try:
            return self._inventories[contract_id].market
        except KeyError:
            raise ValueError("account fact belongs to a different contract") from None

    def position(self, contract_id: UUID) -> Position:
        self.market(contract_id)
        return self._inventories[contract_id].position

    @property
    def last_fact_at(self) -> datetime | None:
        """Latest accepted account fact's availability in the owning ledger clock."""
        return self._last_fact_at

    def unrealized_pnl(self, marks: Mapping[UUID, Decimal]) -> Decimal:
        if any(key not in self._inventories for key in marks):
            raise ValueError("mark belongs to a different contract")
        with localcontext() as context:
            context.prec = 192
            context.rounding = ROUND_HALF_EVEN
            total = Decimal(0)
            for identity, inventory in self._inventories.items():
                mark = marks.get(identity)
                if mark is None:
                    if inventory.lots:
                        raise ValueError("every open contract requires an explicit mark")
                    continue
                if not isinstance(mark, Decimal) or not mark.is_finite() or mark <= 0:
                    raise ValueError("account mark must be a positive exact price")
                total += self.contract_unrealized_pnl(identity, mark)
            return total

    def contract_unrealized_pnl(self, contract_id: UUID, mark: Decimal) -> Decimal:
        market = self.market(contract_id)
        if not isinstance(mark, Decimal) or not mark.is_finite() or mark <= 0:
            raise ValueError("account mark must be a positive exact price")
        exponent = mark.as_tuple().exponent
        if (
            not isinstance(exponent, int)
            or exponent < -18
            or mark.adjusted() > 33
            or len(mark.as_tuple().digits) > 34
        ):
            raise ValueError("account mark exceeds the bounded financial domain")
        with localcontext() as context:
            context.prec = 192
            context.rounding = ROUND_HALF_EVEN
            return sum(
                (
                    lot.direction * (mark - lot.entry_price) * lot.quantity * market.multiplier
                    for lot in self._inventories[contract_id].lots
                ),
                Decimal(0),
            )

    def equity(self, marks: Mapping[UUID, Decimal]) -> Decimal:
        with localcontext() as context:
            context.prec = 192
            context.rounding = ROUND_HALF_EVEN
            return self.cash + self.unrealized_pnl(marks)

    def apply(self, fact: FillFact) -> AppliedFill:
        if not isinstance(fact, FillFact):
            raise ValueError("fill fact belongs to a different contract")
        market = self.market(fact.contract_id)
        inventory = self._inventories[fact.contract_id]
        previous = self._fills.get(fact.fill_id)
        if previous is not None:
            if previous.fact != fact:
                raise ValueError("fill identity was reused with different facts")
            return previous
        if self._last_fact_at is not None and fact.filled_at < self._last_fact_at:
            raise ValueError("account facts must follow accepted event order")
        if inventory.trading_day is not None and fact.trading_day != inventory.trading_day:
            raise ValueError("account requires settlement before a new trading day")
        position = inventory.position.apply(
            (
                PositionChange(
                    fact.contract_id,
                    fact.trading_day,
                    fact.side.value,
                    fact.offset.value,
                    fact.quantity_lots,
                    fact.filled_at,
                ),
            )
        )
        with localcontext() as context:
            context.prec = 192
            context.rounding = ROUND_HALF_EVEN
            direction = 1 if fact.side is Side.BUY else -1
            quantity = fact.quantity_lots
            realized = Decimal(0)
            lots = [replace(lot) for lot in inventory.lots]
            if fact.offset is Offset.OPEN:
                lots.append(_Lot(direction, quantity, fact.price, fact.trading_day))
            else:
                for lot in lots:
                    if lot.direction == direction or (
                        (lot.opened_on == fact.trading_day) != (fact.offset is Offset.CLOSE_TODAY)
                    ):
                        continue
                    closed = min(quantity, lot.quantity)
                    realized += (
                        lot.direction * (fact.price - lot.entry_price) * closed * market.multiplier
                    )
                    lot.quantity -= closed
                    quantity -= closed
                    if not quantity:
                        break
                lots = [lot for lot in lots if lot.quantity]
                if quantity:
                    raise RuntimeError("account lots disagree with gross positions")
            total_fees = self.total_fees + fact.fee
            realized_pnl = self.realized_pnl + realized
            cash = self.cash + realized - fact.fee
            if (
                sum(lot.quantity for lot in lots if lot.direction == 1)
                != position.long_today + position.long_yesterday
                or sum(lot.quantity for lot in lots if lot.direction == -1)
                != position.short_today + position.short_yesterday
                or cash != self.initial_cash + realized_pnl - total_fees
            ):
                raise RuntimeError("account ledger conservation failed")
            applied = AppliedFill(fact, realized, position.net_lots, cash, total_fees, position)
            self._inventories[fact.contract_id] = _Inventory(
                market, position, tuple(lots), fact.trading_day, fact.filled_at
            )
            self.total_fees, self.realized_pnl, self.cash = total_fees, realized_pnl, cash
            self._last_fact_at = fact.filled_at
            self._fills[fact.fill_id] = applied
            return applied

    def settle(self, fact: SettlementFact, *, at: datetime) -> AppliedSettlement:
        """Realize daily variation and roll quantity age, preserving open positions."""
        if not isinstance(fact, SettlementFact):
            raise ValueError("settlement belongs to a different contract")
        if not isinstance(at, datetime) or at.utcoffset() != timedelta(0) or at < fact.available_at:
            raise ValueError("settlement is not yet available to this account clock")
        market = self.market(fact.contract_id)
        inventory = self._inventories[fact.contract_id]
        previous = self._settlements.get(fact.settlement_id)
        if previous is not None:
            if previous.fact != fact:
                raise ValueError("settlement identity was reused with different facts")
            return previous
        if inventory.trading_day is not None and fact.trading_day != inventory.trading_day:
            raise ValueError("settlement does not match the current account trading day")
        if (
            self._last_fact_at is not None
            and fact.available_at < self._last_fact_at
            or inventory.last_event_at is not None
            and fact.settled_at < inventory.last_event_at
        ):
            raise ValueError("settlement precedes accepted account facts")
        with localcontext() as context:
            context.prec = 192
            context.rounding = ROUND_HALF_EVEN
            variation = sum(
                (
                    lot.direction
                    * (fact.price - lot.entry_price)
                    * lot.quantity
                    * market.multiplier
                    for lot in inventory.lots
                ),
                Decimal(0),
            )
            cash = self.cash + variation
            realized = self.realized_pnl + variation
            if cash != self.initial_cash + realized - self.total_fees:
                raise RuntimeError("settlement ledger conservation failed")
            position = Position(
                long_yesterday=inventory.position.long_today + inventory.position.long_yesterday,
                short_yesterday=inventory.position.short_today + inventory.position.short_yesterday,
            )
            lots = [replace(lot, entry_price=fact.price) for lot in inventory.lots]
            applied = AppliedSettlement(fact, variation, cash)
            self.cash, self.realized_pnl = cash, realized
            self.settlement_pnl += variation
            self._inventories[fact.contract_id] = _Inventory(
                market, position, tuple(lots), fact.next_trading_day, fact.settled_at
            )
            self._last_fact_at = fact.available_at
            self._settlements[fact.settlement_id] = applied
            return applied

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Stage one research event without copying the historical fill ledger.

        Only open lots and scalar projections are copied; new fill identities are
        removed on failure. Broker facts use their durable owner's transactions.
        """
        projection = (
            self.cash,
            self.realized_pnl,
            self.total_fees,
            self._inventories.copy(),
            self._last_fact_at,
            self.settlement_pnl,
        )
        count = len(self._fills)
        settlement_count = len(self._settlements)
        try:
            yield
        except BaseException:
            (
                self.cash,
                self.realized_pnl,
                self.total_fees,
                self._inventories,
                self._last_fact_at,
                self.settlement_pnl,
            ) = projection
            while len(self._fills) > count:
                self._fills.popitem()
            while len(self._settlements) > settlement_count:
                self._settlements.popitem()
            raise

    def checkpoint(self) -> dict[str, object]:
        """Comparison projection only; reconstruction always replays the ledger."""
        return {
            "currency": self.markets[0].currency,
            "initial_cash": decimal_text(self.initial_cash),
            "cash": decimal_text(self.cash),
            "realized_pnl": decimal_text(self.realized_pnl),
            "total_fees": decimal_text(self.total_fees),
            "fill_count": self.fill_count,
            "settlement_count": len(self._settlements),
            "settlement_pnl": decimal_text(self.settlement_pnl),
            "last_fact_at": None if self._last_fact_at is None else self._last_fact_at.isoformat(),
            "positions": {
                str(identity): {
                    "gross_position": inventory.position.to_dict(),
                    "last_event_at": None
                    if inventory.last_event_at is None
                    else inventory.last_event_at.isoformat(),
                    "trading_day": None
                    if inventory.trading_day is None
                    else inventory.trading_day.isoformat(),
                    "lots": [
                        {
                            "direction": lot.direction,
                            "quantity_lots": lot.quantity,
                            "entry_price": decimal_text(lot.entry_price),
                            "opened_on": lot.opened_on.isoformat(),
                        }
                        for lot in inventory.lots
                    ],
                }
                for identity, inventory in self._inventories.items()
            },
        }

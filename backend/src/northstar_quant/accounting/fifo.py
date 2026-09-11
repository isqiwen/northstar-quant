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
from northstar_quant.accounting.fees import AppliedFee, FeeFact
from northstar_quant.accounting.fills import AppliedFill, FillFact
from northstar_quant.accounting.positions import Position, PositionChange
from northstar_quant.accounting.settlement import AppliedSettlement, SettlementFact
from northstar_quant.execution.orders import Offset, Side
from northstar_quant.market_data import Market


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
        self._cash = initial_cash
        self.realized_pnl = Decimal(0)
        self.total_fees = Decimal(0)
        self._pending_fees: set[str] = set()
        self._fees: dict[str, AppliedFee] = {}
        self._fills: dict[str, AppliedFill] = {}
        self._settlements: dict[str, AppliedSettlement] = {}
        self._last_fact_at: datetime | None = None
        self.settlement_pnl = Decimal(0)

    @property
    def cash(self) -> Decimal:
        if self._pending_fees:
            raise ValueError("account cash requires confirmed fees for all accepted fills")
        return self._cash

    @property
    def pending_fee_fill_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._pending_fees))

    @property
    def applied_fees(self) -> tuple[AppliedFee, ...]:
        return tuple(self._fees.values())

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
        if self._last_fact_at is not None and fact.available_at < self._last_fact_at:
            raise ValueError("account facts must follow accepted event order")
        if inventory.last_event_at is not None and fact.filled_at < inventory.last_event_at:
            raise ValueError("fill precedes accepted contract executions; reconciliation required")
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
            known_fee = Decimal(0) if fact.fee is None else fact.fee
            total_fees = self.total_fees + known_fee
            realized_pnl = self.realized_pnl + realized
            cash = self._cash + realized - known_fee
            if (
                sum(lot.quantity for lot in lots if lot.direction == 1)
                != position.long_today + position.long_yesterday
                or sum(lot.quantity for lot in lots if lot.direction == -1)
                != position.short_today + position.short_yesterday
                or cash != self.initial_cash + realized_pnl - total_fees
            ):
                raise RuntimeError("account ledger conservation failed")
            if fact.fee is None:
                self._pending_fees.add(fact.fill_id)
            applied = AppliedFill(
                fact,
                realized,
                position.net_lots,
                None if self._pending_fees else cash,
                total_fees,
                position,
            )
            self._inventories[fact.contract_id] = _Inventory(
                market, position, tuple(lots), fact.trading_day, fact.filled_at
            )
            self.total_fees, self.realized_pnl, self._cash = total_fees, realized_pnl, cash
            self._last_fact_at = fact.available_at
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
            cash = self._cash + variation
            realized = self.realized_pnl + variation
            if cash != self.initial_cash + realized - self.total_fees:
                raise RuntimeError("settlement ledger conservation failed")
            position = Position(
                long_yesterday=inventory.position.long_today + inventory.position.long_yesterday,
                short_yesterday=inventory.position.short_today + inventory.position.short_yesterday,
            )
            lots = [replace(lot, entry_price=fact.price) for lot in inventory.lots]
            applied = AppliedSettlement(fact, variation, None if self._pending_fees else cash)
            self._cash, self.realized_pnl = cash, realized
            self.settlement_pnl += variation
            self._inventories[fact.contract_id] = _Inventory(
                market, position, tuple(lots), fact.next_trading_day, fact.settled_at
            )
            self._last_fact_at = fact.available_at
            self._settlements[fact.settlement_id] = applied
            return applied

    def confirm_fee(self, fact: FeeFact) -> AppliedFee:
        """Book one verified aggregate charge without inventing per-fill amounts."""
        if not isinstance(fact, FeeFact) or fact.currency != self.markets[0].currency:
            raise ValueError("fee belongs to a different account currency")
        previous = self._fees.get(fact.fee_id)
        if previous is not None:
            if previous.fact != fact:
                raise ValueError("fee identity was reused with different facts")
            return previous
        if not set(fact.fill_ids).issubset(self._pending_fees):
            raise ValueError("fee must cover exclusively accepted fills awaiting confirmed fees")
        if (
            self._last_fact_at is not None
            and fact.available_at < self._last_fact_at
            or any(
                self._fills[identity].fact.filled_at > fact.charged_at for identity in fact.fill_ids
            )
        ):
            raise ValueError("fee precedes accepted account or execution facts")
        with localcontext() as context:
            context.prec = 192
            context.rounding = ROUND_HALF_EVEN
            cash = self._cash - fact.amount
            total_fees = self.total_fees + fact.amount
            if cash != self.initial_cash + self.realized_pnl - total_fees:
                raise RuntimeError("fee ledger conservation failed")
            self._pending_fees.difference_update(fact.fill_ids)
            applied = AppliedFee(fact, None if self._pending_fees else cash, total_fees)
            self._cash, self.total_fees = cash, total_fees
            self._last_fact_at = fact.available_at
            self._fees[fact.fee_id] = applied
            return applied

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Stage one research event without copying the historical fill ledger.

        Only open lots and scalar projections are copied; new fill identities are
        removed on failure. Broker facts use their durable owner's transactions.
        """
        projection = (
            self._cash,
            self.realized_pnl,
            self.total_fees,
            self._inventories.copy(),
            self._last_fact_at,
            self.settlement_pnl,
            self._pending_fees.copy(),
        )
        count = len(self._fills)
        settlement_count = len(self._settlements)
        fee_count = len(self._fees)
        try:
            yield
        except BaseException:
            (
                self._cash,
                self.realized_pnl,
                self.total_fees,
                self._inventories,
                self._last_fact_at,
                self.settlement_pnl,
                self._pending_fees,
            ) = projection
            while len(self._fills) > count:
                self._fills.popitem()
            while len(self._settlements) > settlement_count:
                self._settlements.popitem()
            while len(self._fees) > fee_count:
                self._fees.popitem()
            raise

    def checkpoint(self) -> dict[str, object]:
        """Comparison projection only; reconstruction always replays the ledger."""
        return {
            "currency": self.markets[0].currency,
            "initial_cash": decimal_text(self.initial_cash),
            "cash": None if self._pending_fees else decimal_text(self._cash),
            "cash_before_pending_fees": decimal_text(self._cash),
            "pending_fee_fill_ids": list(self.pending_fee_fill_ids),
            "fee_count": len(self._fees),
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

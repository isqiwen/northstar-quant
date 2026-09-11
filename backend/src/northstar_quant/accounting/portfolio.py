"""Read-only portfolio views derived from the account ledger and explicit market values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from uuid import UUID

from northstar_quant.accounting.amounts import decimal_text
from northstar_quant.accounting.fifo import Account
from northstar_quant.accounting.positions import Position
from northstar_quant.accounting.terms import FuturesTerms
from northstar_quant.execution.orders import Side
from northstar_quant.market_data import Market


@dataclass(frozen=True, slots=True)
class PortfolioState:
    """The net account view consumed by the current single-contract risk policy."""

    observed_at: datetime
    equity: Decimal
    position_lots: int
    mark_price: Decimal


@dataclass(frozen=True, slots=True)
class AccountValuation:
    cash: Decimal
    position_lots: int
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_fees: Decimal
    equity: Decimal
    terms_id: str | None
    margin_used: Decimal | None
    long_lots: int
    short_lots: int
    net_exposure: Decimal
    gross_exposure: Decimal
    settlement_pnl: Decimal
    trade_realized_pnl: Decimal

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "cash": decimal_text(self.cash),
            "position_lots": self.position_lots,
            "realized_pnl": decimal_text(self.realized_pnl),
            "unrealized_pnl": decimal_text(self.unrealized_pnl),
            "total_fees": decimal_text(self.total_fees),
            "equity": decimal_text(self.equity),
            "long_lots": self.long_lots,
            "short_lots": self.short_lots,
            "net_exposure": decimal_text(self.net_exposure),
            "gross_exposure": decimal_text(self.gross_exposure),
            "settlement_pnl": decimal_text(self.settlement_pnl),
            "trade_realized_pnl": decimal_text(self.trade_realized_pnl),
        }
        if self.margin_used is not None:
            with localcontext() as context:
                context.prec = 192
                context.rounding = ROUND_HALF_EVEN
                result.update(
                    terms_id=self.terms_id,
                    margin_used=decimal_text(self.margin_used),
                    available=decimal_text(self.equity - self.margin_used),
                )
        return result


@dataclass(frozen=True, slots=True)
class HoldingValuation:
    market: Market
    position: Position
    mark: Decimal | None
    unrealized_pnl: Decimal
    net_exposure: Decimal
    gross_exposure: Decimal
    margin_used: Decimal | None
    terms_id: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_id": str(self.market.contract_id),
            "symbol": self.market.symbol,
            **self.position.to_dict(),
            "mark": None if self.mark is None else decimal_text(self.mark),
            "unrealized_pnl": decimal_text(self.unrealized_pnl),
            "net_exposure": decimal_text(self.net_exposure),
            "gross_exposure": decimal_text(self.gross_exposure),
            "margin_used": None if self.margin_used is None else decimal_text(self.margin_used),
            "terms_id": self.terms_id,
        }


@dataclass(frozen=True, slots=True)
class PortfolioValuation:
    cash: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    total_fees: Decimal
    equity: Decimal
    settlement_pnl: Decimal
    margin_used: Decimal | None
    net_exposure: Decimal
    gross_exposure: Decimal
    holdings: tuple[HoldingValuation, ...]

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            name: decimal_text(getattr(self, name))
            for name in (
                "cash",
                "realized_pnl",
                "unrealized_pnl",
                "total_fees",
                "equity",
                "settlement_pnl",
                "net_exposure",
                "gross_exposure",
            )
        }
        result["holdings"] = [holding.to_dict() for holding in self.holdings]
        if self.margin_used is not None:
            with localcontext() as context:
                context.prec = 192
                result.update(
                    margin_used=decimal_text(self.margin_used),
                    available=decimal_text(self.equity - self.margin_used),
                )
        return result


def value_portfolio(
    account: Account,
    marks: Mapping[UUID, Decimal],
    *,
    at: datetime,
    terms: Mapping[UUID, FuturesTerms] | None = None,
) -> PortfolioValuation:
    """One cash balance plus all marked inventory; no inferred FX or margin offsets.

    Missing open-contract marks or declared margin terms cannot silently hide a
    leg. Available excludes order reservations and is not a broker certificate.
    """
    if not isinstance(at, datetime) or at.utcoffset() != timedelta(0):
        raise ValueError("account valuation requires a UTC observation time")
    if account.last_fact_at is not None and at < account.last_fact_at:
        raise ValueError("valuation cannot precede accepted account facts")
    if terms is not None:
        for identity, value in terms.items():
            account.market(identity)
            if value.contract_id != identity:
                raise ValueError("valuation terms belong to another contract")
            value.require_available(at)
    unrealized = account.unrealized_pnl(marks)
    holdings = []
    with localcontext() as context:
        context.prec = 192
        context.rounding = ROUND_HALF_EVEN
        margin, net, gross = Decimal(0), Decimal(0), Decimal(0)
        for market in account.markets:
            position = account.position(market.contract_id)
            long = position.long_today + position.long_yesterday
            short = position.short_today + position.short_yesterday
            mark = marks.get(market.contract_id)
            effective = None if terms is None else terms.get(market.contract_id)
            if terms is not None and (long or short) and effective is None:
                raise ValueError("every open contract requires its effective margin terms")
            notional = Decimal(0) if mark is None else mark * market.multiplier
            used = None if terms is None else Decimal(0)
            if effective is not None and mark is not None:
                used = effective.margin(Side.BUY, mark, market.multiplier, long) + effective.margin(
                    Side.SELL, mark, market.multiplier, short
                )
            holding = HoldingValuation(
                market,
                position,
                mark,
                Decimal(0)
                if mark is None
                else account.contract_unrealized_pnl(market.contract_id, mark),
                (long - short) * notional,
                (long + short) * notional,
                used,
                None if effective is None else effective.terms_id,
            )
            holdings.append(holding)
            margin += used or Decimal(0)
            net += holding.net_exposure
            gross += holding.gross_exposure
        return PortfolioValuation(
            account.cash,
            account.realized_pnl,
            unrealized,
            account.total_fees,
            account.cash + unrealized,
            account.settlement_pnl,
            None if terms is None else margin,
            net,
            gross,
            tuple(holdings),
        )


def value_single_contract(
    account: Account,
    mark: Decimal,
    *,
    at: datetime,
    terms: FuturesTerms | None = None,
) -> AccountValuation:
    """The current single-contract report and sizing view of the shared account."""
    if len(account.markets) != 1:
        raise ValueError("single-contract valuation cannot omit other account holdings")
    market = account.markets[0]
    value = value_portfolio(
        account,
        {market.contract_id: mark},
        at=at,
        terms=None if terms is None else {market.contract_id: terms},
    )
    holding = value.holdings[0]
    position = holding.position
    with localcontext() as context:
        context.prec = 192
        context.rounding = ROUND_HALF_EVEN
        return AccountValuation(
            value.cash,
            position.net_lots,
            value.realized_pnl,
            value.unrealized_pnl,
            value.total_fees,
            value.equity,
            holding.terms_id,
            value.margin_used,
            position.long_today + position.long_yesterday,
            position.short_today + position.short_yesterday,
            value.net_exposure,
            value.gross_exposure,
            value.settlement_pnl,
            value.realized_pnl - value.settlement_pnl,
        )

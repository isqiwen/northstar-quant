"""Derived single-contract account valuation; no independent mutable balances."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from northstar_quant.accounting.amounts import decimal_text
from northstar_quant.accounting.fifo import Account
from northstar_quant.accounting.terms import FuturesTerms
from northstar_quant.execution.orders import Side


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

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "cash": decimal_text(self.cash),
            "position_lots": self.position_lots,
            "realized_pnl": decimal_text(self.realized_pnl),
            "unrealized_pnl": decimal_text(self.unrealized_pnl),
            "total_fees": decimal_text(self.total_fees),
            "equity": decimal_text(self.equity),
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


def value_account(
    account: Account,
    mark: Decimal,
    *,
    at: datetime,
    terms: FuturesTerms | None = None,
) -> AccountValuation:
    """Mark ledger-derived gross inventory; exclude order holds and spread offsets.

    Available here is equity less modeled margin, never a broker's Available
    certificate. Without effective terms no margin or available value is invented.
    """
    if not isinstance(at, datetime) or at.utcoffset() != timedelta(0):
        raise ValueError("account valuation requires a UTC observation time")
    if account.last_fact_at is not None and at < account.last_fact_at:
        raise ValueError("valuation cannot precede accepted account facts")
    if terms is not None:
        if terms.contract_id != account.market.contract_id:
            raise ValueError("valuation terms belong to another contract")
        terms.require_available(at)
    with localcontext() as context:
        context.prec = 192
        context.rounding = ROUND_HALF_EVEN
        unrealized = account.unrealized_pnl(mark)
        margin = None
        if terms is not None:
            position = account.position
            margin = terms.margin(
                Side.BUY,
                mark,
                account.market.multiplier,
                position.long_today + position.long_yesterday,
            ) + terms.margin(
                Side.SELL,
                mark,
                account.market.multiplier,
                position.short_today + position.short_yesterday,
            )
        return AccountValuation(
            account.cash,
            account.position_lots,
            account.realized_pnl,
            unrealized,
            account.total_fees,
            account.cash + unrealized,
            None if terms is None else terms.terms_id,
            margin,
        )

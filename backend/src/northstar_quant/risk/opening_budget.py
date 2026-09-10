"""Precheck broker opening budgets without orders, holds or execution authority."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Decimal, localcontext

from northstar_quant.accounting.amounts import decimal_text
from northstar_quant.execution.orders import Side

_MAX_FINANCIAL = Decimal("9" * 34)


@dataclass(frozen=True, slots=True)
class OpeningAccount:
    equity: Decimal
    available: Decimal
    current_margin: Decimal

    def __post_init__(self) -> None:
        for name in ("equity", "available", "current_margin"):
            _opening_decimal(getattr(self, name), name)
        if self.current_margin < 0:
            raise ValueError("current_margin cannot be negative")


@dataclass(frozen=True, slots=True)
class OpeningTerms:
    """Confirmed absolute rates; unknown or relative rates are not zero inputs."""

    price_tick: Decimal
    multiplier: Decimal
    long_margin_by_money: Decimal
    long_margin_by_volume: Decimal
    short_margin_by_money: Decimal
    short_margin_by_volume: Decimal
    open_fee_by_money: Decimal
    open_fee_by_volume: Decimal
    lower_limit: Decimal
    upper_limit: Decimal
    last_price: Decimal
    pre_settlement_price: Decimal
    min_limit_lots: int
    max_limit_lots: int

    def __post_init__(self) -> None:
        for name in (
            "price_tick",
            "multiplier",
            "lower_limit",
            "upper_limit",
            "last_price",
            "pre_settlement_price",
        ):
            value = getattr(self, name)
            _opening_decimal(value, name)
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        for name in (
            "long_margin_by_money",
            "long_margin_by_volume",
            "short_margin_by_money",
            "short_margin_by_volume",
            "open_fee_by_money",
            "open_fee_by_volume",
        ):
            value = getattr(self, name)
            _opening_decimal(value, name)
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        for name in ("min_limit_lots", "max_limit_lots"):
            _opening_lots(getattr(self, name), name)
        if self.min_limit_lots > self.max_limit_lots or self.lower_limit > self.upper_limit:
            raise ValueError("opening instrument limits are inconsistent")


@dataclass(frozen=True, slots=True)
class OpeningLimits:
    max_lots: int
    max_gross_notional: Decimal
    max_margin_fraction: Decimal
    max_adverse_price_move_fraction: Decimal

    def __post_init__(self) -> None:
        _opening_lots(self.max_lots, "max_lots")
        for name in (
            "max_gross_notional",
            "max_margin_fraction",
            "max_adverse_price_move_fraction",
        ):
            _opening_decimal(getattr(self, name), name)
        if (
            self.max_gross_notional <= 0
            or not Decimal(0) < self.max_margin_fraction <= Decimal(1)
            or not Decimal(0) < self.max_adverse_price_move_fraction < Decimal(1)
        ):
            raise ValueError("opening budgets require positive, bounded risk limits")


@dataclass(frozen=True, slots=True)
class OpeningCandidate:
    side: Side
    limit_price: Decimal
    lots: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.side, Side):
            raise ValueError("opening side must be BUY or SELL")
        _opening_decimal(self.limit_price, "limit_price")
        if self.limit_price <= 0:
            raise ValueError("limit_price must be positive")
        _opening_lots(self.lots, "lots")


def evaluate_opening_budget(
    account: OpeningAccount,
    terms: OpeningTerms,
    limits: OpeningLimits,
    candidate: OpeningCandidate,
) -> dict[str, object]:
    """Precheck one opening lot using confirmed facts, without creating an order.

    The caller owns empty-account scope, identities, freshness, unknown facts and
    rate applicability. This arithmetic never turns simulated fees or margin into
    broker facts. Existing margin is included in equity limits, but is not deducted
    again from the already reported Available. A SELL limit does not cap its fill
    price: daily upper limit bounds its notional and fee budgets instead. Both
    directions budget margin at max(daily upper limit, previous settlement),
    covering settlement-, last-, opening- and average-price margin on a first
    opening from flat without assuming the broker uses the eventual fill price.

    Money- and volume-based rates are additive. Margin and fee budgets round up
    separately to CNY cents; they are neither actual charges nor persisted holds.
    WITHIN_BUDGET remains a non-executable PRECHECK, never execution authorization.
    """
    with localcontext() as context:
        context.prec = 192
        context.rounding = ROUND_HALF_EVEN
        reasons: list[str] = []
        price, lots = candidate.limit_price, candidate.lots
        if lots != 1:
            reasons.append("ONLY_ONE_OPENING_LOT_SUPPORTED")
        if lots > limits.max_lots:
            reasons.append("MAX_LOTS_EXCEEDED")
        if not terms.min_limit_lots <= lots <= terms.max_limit_lots:
            reasons.append("INSTRUMENT_LOT_LIMIT")
        if price % terms.price_tick:
            reasons.append("PRICE_OFF_TICK")
        if not terms.lower_limit <= price <= terms.upper_limit:
            reasons.append("PRICE_OUTSIDE_DAILY_LIMITS")
        if (
            terms.last_price % terms.price_tick
            or terms.lower_limit % terms.price_tick
            or terms.upper_limit % terms.price_tick
            or not terms.lower_limit <= terms.last_price <= terms.upper_limit
        ):
            reasons.append("MARKET_PRICE_BOUNDS_NOT_USABLE")
        adverse = (
            price - terms.last_price if candidate.side is Side.BUY else terms.last_price - price
        )
        if adverse > terms.last_price * limits.max_adverse_price_move_fraction:
            reasons.append("ADVERSE_PRICE_LIMIT_EXCEEDED")
        reservation_price = price if candidate.side is Side.BUY else terms.upper_limit
        notional = reservation_price * terms.multiplier * lots
        margin_reference_price = max(terms.upper_limit, terms.pre_settlement_price)
        margin_notional = margin_reference_price * terms.multiplier * lots
        margin_money, margin_volume = (
            (terms.long_margin_by_money, terms.long_margin_by_volume)
            if candidate.side is Side.BUY
            else (terms.short_margin_by_money, terms.short_margin_by_volume)
        )
        margin = (margin_notional * margin_money + lots * margin_volume).quantize(
            Decimal("0.01"), rounding=ROUND_CEILING
        )
        fee = (notional * terms.open_fee_by_money + lots * terms.open_fee_by_volume).quantize(
            Decimal("0.01"), rounding=ROUND_CEILING
        )
        capital = margin + fee
        equity_after_fee = account.equity - fee
        total_margin = account.current_margin + margin
        if any(value > _MAX_FINANCIAL for value in (notional, margin, fee, capital, total_margin)):
            reasons.append("BUDGET_OUTSIDE_FINANCIAL_DOMAIN")
        if account.equity <= 0 or equity_after_fee <= 0:
            reasons.append("NONPOSITIVE_EQUITY_AFTER_FEES")
        if notional > limits.max_gross_notional:
            reasons.append("GROSS_NOTIONAL_LIMIT_EXCEEDED")
        if capital > account.available:
            reasons.append("INSUFFICIENT_AVAILABLE")
        if account.current_margin + capital > account.equity:
            reasons.append("INSUFFICIENT_EQUITY")
        if total_margin > equity_after_fee * limits.max_margin_fraction:
            reasons.append("MARGIN_FRACTION_EXCEEDED")
        return {
            "outcome": "REJECT" if reasons else "WITHIN_BUDGET",
            "reasons": reasons,
            "scope": "OPENING_BUDGET_PRECHECK",
            "side": candidate.side.value,
            "quantity_lots": lots,
            "requested_price": decimal_text(price),
            "reservation_price": decimal_text(reservation_price),
            "margin_reference_price": decimal_text(margin_reference_price),
            "notional": decimal_text(notional),
            "margin_budget": decimal_text(margin),
            "fee_budget": decimal_text(fee),
            "capital_budget": decimal_text(capital),
            "current_margin": decimal_text(account.current_margin),
            "total_margin_budget": decimal_text(total_margin),
            "equity_after_fee_budget": decimal_text(equity_after_fee),
            "available_after_budget": decimal_text(account.available - capital),
            "execution": {"order_sending": False, "cancel_sending": False},
        }


def _opening_decimal(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite exact Decimal")
    exponent = value.as_tuple().exponent
    if (
        not isinstance(exponent, int)
        or exponent < -18
        or value.adjusted() > 33
        or len(value.as_tuple().digits) > 34
    ):
        raise ValueError(f"{name} exceeds the bounded opening budget domain")


def _opening_lots(value: int, name: str) -> None:
    if type(value) is not int or not 1 <= value <= 1_000_000_000:
        raise ValueError(f"{name} must be a bounded positive integer")

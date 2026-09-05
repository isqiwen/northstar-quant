"""Simulated target sizing and separate, non-executable broker opening budgets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal, localcontext
from enum import StrEnum
from math import lcm

from northstar_quant.data.research import Market
from northstar_quant.strategy import StrategyIntent, decimal_text

_MAX_FINANCIAL = Decimal("9" * 34)


class Outcome(StrEnum):
    ALLOW = "ALLOW"
    REDUCE = "REDUCE"
    REJECT = "REJECT"
    UNKNOWN = "UNKNOWN"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


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


@dataclass(frozen=True, slots=True)
class PortfolioState:
    observed_at: datetime
    equity: Decimal
    position_lots: int
    mark_price: Decimal


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    max_lots: int
    max_gross_notional: Decimal
    max_margin_fraction: Decimal
    initial_margin_fraction: Decimal
    max_adverse_price_move_fraction: Decimal
    fee_per_lot: Decimal
    slippage_ticks: int = 0

    def __post_init__(self) -> None:
        if (
            type(self.max_lots) is not int
            or not 1 <= self.max_lots <= 1_000_000_000
            or type(self.slippage_ticks) is not int
            or self.slippage_ticks < 0
        ):
            raise ValueError("risk policy requires positive integer lots and nonnegative ticks")
        for value in (
            self.max_gross_notional,
            self.fee_per_lot,
            self.max_margin_fraction,
            self.initial_margin_fraction,
            self.max_adverse_price_move_fraction,
        ):
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError("risk policy amounts must be finite exact decimals")
        if (
            self.max_gross_notional <= 0
            or self.fee_per_lot < 0
            or not Decimal(0) < self.max_margin_fraction <= Decimal(1)
            or not Decimal(0) < self.initial_margin_fraction <= Decimal(1)
            or not Decimal(0) < self.max_adverse_price_move_fraction < Decimal(1)
        ):
            raise ValueError(
                "risk policy requires positive limits and bounded margin/price fractions"
            )


@dataclass(frozen=True, slots=True)
class RiskDecision:
    outcome: Outcome
    reason: str
    desired_position_lots: int
    approved_position_lots: int | None
    side: Side | None
    quantity_lots: int
    minimum_fill_price: Decimal | None
    maximum_fill_price: Decimal | None
    expires_at: datetime


def evaluate_risk(
    intent: StrategyIntent,
    state: PortfolioState,
    policy: RiskPolicy,
    market: Market,
) -> RiskDecision:
    """Authorize one target from current account truth, including execution fees.

    The trade-price interval protects equity, gross and initial-margin limits
    after fees and adverse slippage, marked back to the observed close. It is
    not an exchange limit order or a guarantee about later marks. A reversal
    first closes to flat.
    """

    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        if (
            not state.equity.is_finite()
            or not state.mark_price.is_finite()
            or state.mark_price <= 0
            or type(state.position_lots) is not int
            or abs(state.position_lots) > 1_000_000_000
            or state.observed_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("risk requires finite account values, integer lots and UTC time")
        return _evaluate(intent, state, policy, market)


def _evaluate(
    intent: StrategyIntent, state: PortfolioState, policy: RiskPolicy, market: Market
) -> RiskDecision:
    desired = int(
        (intent.target_fraction.copy_abs() * policy.max_lots).to_integral_value(
            rounding=ROUND_FLOOR
        )
    )
    if intent.target_fraction < 0:
        desired = -desired

    def no_action(outcome: Outcome, reason: str) -> RiskDecision:
        return RiskDecision(outcome, reason, desired, None, None, 0, None, None, intent.valid_until)

    if intent.contract_id != market.contract_id:
        return no_action(Outcome.UNKNOWN, "CONTRACT_MISMATCH")
    if state.observed_at != intent.generated_at:
        return no_action(Outcome.UNKNOWN, "ACCOUNT_NOT_CURRENT")
    if not intent.generated_at < intent.valid_until:
        return no_action(Outcome.UNKNOWN, "INTENT_EXPIRED")
    if state.equity <= 0:
        return no_action(Outcome.UNKNOWN, "NONPOSITIVE_EQUITY")

    current = state.position_lots
    reversal = current != 0 and desired != 0 and (current > 0) != (desired > 0)
    notional_capacity = min(
        policy.max_lots,
        int(
            (policy.max_gross_notional / (state.mark_price * market.multiplier)).to_integral_value(
                rounding=ROUND_FLOOR
            )
        ),
        int(
            (
                state.equity
                * policy.max_margin_fraction
                / (state.mark_price * market.multiplier * policy.initial_margin_fraction)
            ).to_integral_value(rounding=ROUND_FLOOR)
        ),
    )
    target = 0 if reversal else max(-notional_capacity, min(desired, notional_capacity))

    # For an increase, margin and fees both grow linearly in lots. Reductions
    # are assessed using their actual fee in the interval below.
    if abs(target) > abs(current):
        same_direction = current == 0 or (target > 0) == (current > 0)
        held = abs(current) if same_direction else -abs(current)
        cost = policy.fee_per_lot + policy.slippage_ticks * market.price_tick * market.multiplier
        capacity = int(
            (
                (state.equity + held * cost)
                * policy.max_margin_fraction
                / (
                    state.mark_price * market.multiplier * policy.initial_margin_fraction
                    + cost * policy.max_margin_fraction
                )
            ).to_integral_value(rounding=ROUND_FLOOR)
        )
        target = max(-max(0, capacity), min(target, max(0, capacity)))

    quantity = abs(target - current)
    if desired != 0 and target == 0 and current == 0:
        return no_action(Outcome.REJECT, "NO_PERMITTED_POSITION")
    if quantity == 0:
        return RiskDecision(
            Outcome.ALLOW,
            "TARGET_UNCHANGED",
            desired,
            target,
            None,
            0,
            None,
            None,
            intent.valid_until,
        )
    side = Side.BUY if target > current else Side.SELL
    interval = _authorize_price_interval(state, policy, market, target, side)
    if interval is None:
        return no_action(Outcome.REJECT, "NO_SAFE_FILL_PRICE")
    reducing = abs(target) < abs(current)
    reason = (
        "REVERSAL_REDUCED_TO_FLAT"
        if reversal
        else "RISK_REDUCING_TARGET"
        if reducing
        else "CAPPED_TO_LIMIT"
        if target != desired
        else "WITHIN_LIMITS"
    )
    return RiskDecision(
        Outcome.REDUCE if reducing else Outcome.ALLOW,
        reason,
        desired,
        target,
        side,
        quantity,
        interval[0],
        interval[1],
        intent.valid_until,
    )


def _authorize_price_interval(
    state: PortfolioState, policy: RiskPolicy, market: Market, target: int, side: Side
) -> tuple[Decimal, Decimal] | None:
    multiplier, tick = market.multiplier, market.price_tick
    direction = 1 if side is Side.BUY else -1
    price_offset = direction * policy.slippage_ticks * tick
    minimum, maximum = tick, _floor_to_tick(_MAX_FINANCIAL, tick)
    if side is Side.BUY:
        maximum = min(maximum, state.mark_price * (1 + policy.max_adverse_price_move_fraction))
    else:
        minimum = max(minimum, state.mark_price * (1 - policy.max_adverse_price_move_fraction))
    target_units = abs(target) * multiplier
    if target_units:
        maximum = min(maximum, policy.max_gross_notional / target_units + price_offset)
    minimum = max(minimum, tick + price_offset)

    fee = abs(target - state.position_lots) * policy.fee_per_lot
    # The public interval is a TRADE-price interval. Account truth is marked at
    # the observed close = trade_price - signed slippage. Transform both equity
    # and gross to that close before solving the linear policy inequalities.
    intercept = (
        state.equity
        - state.position_lots * multiplier * state.mark_price
        - fee
        - target * multiplier * price_offset
    )
    slope = state.position_lots * multiplier
    if slope > 0:
        minimum = max(minimum, _strictly_above_tick(-intercept / slope, tick))
    elif slope < 0:
        maximum = min(maximum, _strictly_below_tick(-intercept / slope, tick))
    elif intercept <= 0:
        return None

    coefficient = target_units * policy.initial_margin_fraction - policy.max_margin_fraction * slope
    right_hand_side = (
        policy.max_margin_fraction * intercept
        + target_units * policy.initial_margin_fraction * price_offset
    )
    if coefficient > 0:
        maximum = min(maximum, right_hand_side / coefficient)
    elif coefficient < 0:
        minimum = max(minimum, right_hand_side / coefficient)
    elif right_hand_side < 0:
        return None

    minimum = _ceiling_to_tick(max(minimum, tick), tick)
    maximum = _floor_to_tick(maximum, tick)
    if minimum <= 0 or maximum < minimum:
        return None
    low = _public_tick_bound(minimum, tick, round_up=True)
    high = _public_tick_bound(maximum, tick, round_up=False)
    if low is None or high is None or high < low:
        return None
    for price in (low, high):
        equity = intercept + slope * price
        gross = target_units * (price - price_offset)
        if equity <= 0 or gross > policy.max_gross_notional:
            return None
        if gross * policy.initial_margin_fraction > equity * policy.max_margin_fraction:
            return None
    return low, high


def _ceiling_to_tick(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def _floor_to_tick(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def _strictly_above_tick(value: Decimal, tick: Decimal) -> Decimal:
    rounded = _ceiling_to_tick(value, tick)
    return rounded + tick if rounded <= value else rounded


def _strictly_below_tick(value: Decimal, tick: Decimal) -> Decimal:
    rounded = _floor_to_tick(value, tick)
    return rounded - tick if rounded >= value else rounded


def _public_tick_bound(value: Decimal, tick: Decimal, *, round_up: bool) -> Decimal | None:
    """Move inward onto the joint tick and bounded public-decimal grids."""

    candidate = value
    for _ in range(2):
        integer_digits = max(1, candidate.copy_abs().adjusted() + 1)
        if integer_digits > 34:
            return None
        quantum = Decimal(1).scaleb(-min(18, 34 - integer_digits))
        tick_numerator, tick_denominator = tick.as_integer_ratio()
        quantum_numerator, quantum_denominator = quantum.as_integer_ratio()
        denominator = lcm(tick_denominator, quantum_denominator)
        tick_units = tick_numerator * (denominator // tick_denominator)
        quantum_units = quantum_numerator * (denominator // quantum_denominator)
        public_tick = Decimal(lcm(tick_units, quantum_units)) / Decimal(denominator)
        aligned = (
            _ceiling_to_tick(value, public_tick) if round_up else _floor_to_tick(value, public_tick)
        )
        aligned = Decimal(decimal_text(aligned))
        if aligned <= 0 or aligned > _MAX_FINANCIAL:
            return None
        if max(1, aligned.copy_abs().adjusted() + 1) == integer_digits:
            return aligned
        candidate = aligned
    return None

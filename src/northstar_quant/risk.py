"""Fee-aware futures sizing and exact tick-aligned fill authorization."""

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
        if (
            type(policy.max_lots) is not int
            or not 1 <= policy.max_lots <= 1_000_000_000
            or type(policy.slippage_ticks) is not int
            or policy.slippage_ticks < 0
            or not policy.max_gross_notional.is_finite()
            or policy.max_gross_notional <= 0
            or not policy.fee_per_lot.is_finite()
            or policy.fee_per_lot < 0
            or not Decimal(0) < policy.max_margin_fraction <= Decimal(1)
            or not Decimal(0) < policy.initial_margin_fraction <= Decimal(1)
            or not Decimal(0) < policy.max_adverse_price_move_fraction < Decimal(1)
        ):
            raise ValueError("risk policy requires bounded positive limits and nonnegative costs")
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

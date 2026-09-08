"""Compare reported account amounts without inventing identified cash flows."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from northstar_quant.accounting.amounts import decimal_text

ACCOUNT_AMOUNT_FIELDS: tuple[str, ...] = (
    "Balance",
    "Available",
    "PreBalance",
    "PreMargin",
    "Deposit",
    "Withdraw",
    "CurrMargin",
    "FrozenMargin",
    "FrozenCash",
    "FrozenCommission",
    "CashIn",
    "Commission",
    "CloseProfit",
    "PositionProfit",
    "WithdrawQuota",
    "Reserve",
)


def compare_account_amounts(
    previous: dict[str, str], current: dict[str, str], *, same_scope: bool
) -> dict[str, object]:
    """Compare two reported account observations, never apply totals as cash flows.

    The caller verifies source identities, time order and the same account,
    currency, business, trading day and settlement scope. False scope suppresses
    every delta. Missing or invalid fields suppress only their own deltas and
    dependent net deposits. Financial strings are bounded to 80 characters,
    34 significant digits, 18 fractional places and a maximum adjusted exponent
    of 33. Negative balances and P&L are evidence, not invalid starting capital.

    Returned signed changes do not establish transfer identities, trade coverage,
    actual refunds, a balance identity or permission to spend Available. Lower
    Commission/Deposit/Withdraw totals remain unresolved cumulative adjustments;
    decreasing CloseProfit or PositionProfit is an ordinary observed P&L change.
    """
    if (
        not isinstance(previous, dict)
        or not isinstance(current, dict)
        or type(same_scope) is not bool
    ):
        raise ValueError("account comparison requires two amount mappings and explicit scope")
    deltas: dict[str, str | None] = dict.fromkeys(ACCOUNT_AMOUNT_FIELDS)
    problems: list[str] = []
    net_deposit_delta: str | None = None
    if not same_scope:
        problems.append("ACCOUNT_SCOPE_CHANGED")
    else:
        with localcontext() as context:
            # Inputs can span 34 integer and 18 fractional places. Neither the
            # differences nor net deposits may round under an ambient context.
            context.prec = 96
            context.Emax, context.Emin, context.clamp = 96, -96, 0
            context.rounding = ROUND_HALF_EVEN
            for field in ACCOUNT_AMOUNT_FIELDS:
                amounts: list[Decimal | None] = []
                for side, observation in (("previous", previous), ("current", current)):
                    value = observation.get(field)
                    number: Decimal | None = None
                    code = "MISSING" if field not in observation else None
                    if code is None:
                        try:
                            number = _observed_account_amount(value)
                        except ValueError:
                            code = "INVALID"
                    if code is not None:
                        problems.append(f"ACCOUNT_{field.upper()}_{code}_{side.upper()}")
                    amounts.append(number)
                before, after = amounts
                if before is None or after is None:
                    continue
                delta = after - before
                deltas[field] = decimal_text(delta)
                if field in {"Commission", "Deposit", "Withdraw"} and delta < 0:
                    problems.append(f"CUMULATIVE_{field.upper()}_ADJUSTMENT_UNRESOLVED")
            deposit, withdraw = deltas["Deposit"], deltas["Withdraw"]
            if deposit is not None and withdraw is not None:
                net_deposit_delta = decimal_text(Decimal(deposit) - Decimal(withdraw))
    return {"deltas": deltas, "net_deposit_delta": net_deposit_delta, "problems": problems}


def _observed_account_amount(value: object) -> Decimal:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 80
        or re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value) is None
    ):
        raise ValueError("account amount must be bounded exact decimal text")
    try:
        number = Decimal(value)
        exponent = number.as_tuple().exponent
        if (
            not number.is_finite()
            or not isinstance(exponent, int)
            or exponent < -18
            or number.adjusted() > 33
            or len(number.as_tuple().digits) > 34
        ):
            raise ValueError("account amount is outside the bounded financial domain")
        return number
    except ArithmeticError as error:
        raise ValueError("account amount is not finite decimal text") from error

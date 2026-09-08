"""Synthetic account observations exercise money meaning without a broker or DB."""

from decimal import Inexact, Rounded, localcontext

import pytest

from northstar_quant.accounting.observations import compare_account_amounts

AMOUNTS = {
    "Balance": "100000",
    "Available": "99000",
    "PreBalance": "100000",
    "PreMargin": "0",
    "Deposit": "200",
    "Withdraw": "100",
    "CurrMargin": "500",
    "FrozenMargin": "480",
    "FrozenCash": "10",
    "FrozenCommission": "10",
    "CashIn": "0",
    "Commission": "20",
    "CloseProfit": "100",
    "PositionProfit": "-180",
    "WithdrawQuota": "98000",
    "Reserve": "0",
}


def test_reported_totals_compare_once_without_becoming_new_fees_or_cash_flows() -> None:
    current = {
        **AMOUNTS,
        "Balance": "100024.90",
        "Deposit": "300",
        "Withdraw": "125",
        "Commission": "22.10",
        "CloseProfit": "52",
    }
    result = compare_account_amounts(AMOUNTS, current, same_scope=True)
    assert result["deltas"]["Commission"] == "2.1"
    assert result["deltas"]["Balance"] == "24.9"
    assert result["deltas"]["CloseProfit"] == "-48"
    assert result["net_deposit_delta"] == "75"
    assert result["problems"] == []
    repeated = compare_account_amounts(current, current, same_scope=True)
    assert all(delta == "0" for delta in repeated["deltas"].values())
    assert repeated["net_deposit_delta"] == "0"
    assert current["Balance"] == "100024.90" and current["Commission"] == "22.10"


def test_negative_balances_and_normal_profit_decreases_are_retained() -> None:
    current = {
        **AMOUNTS,
        "Balance": "-100",
        "Available": "-1100",
        "CloseProfit": "-250",
        "PositionProfit": "-300",
        "CashIn": "-1.75",
    }
    result = compare_account_amounts(AMOUNTS, current, same_scope=True)
    assert result["deltas"]["Balance"] == "-100100"
    assert result["deltas"]["Available"] == "-100100"
    assert result["deltas"]["CloseProfit"] == "-350"
    assert result["deltas"]["PositionProfit"] == "-120"
    assert result["deltas"]["CashIn"] == "-1.75"
    assert result["problems"] == []


@pytest.mark.parametrize("field", ["Commission", "Deposit", "Withdraw"])
def test_lower_cumulative_total_is_signed_unresolved_adjustment_not_refund(field: str) -> None:
    result = compare_account_amounts(AMOUNTS, {**AMOUNTS, field: "0"}, same_scope=True)
    assert result["deltas"][field] == "-" + AMOUNTS[field]
    assert result["problems"] == [f"CUMULATIVE_{field.upper()}_ADJUSTMENT_UNRESOLVED"]


def test_missing_amount_suppresses_only_its_delta_and_dependent_net_deposits() -> None:
    current = {key: value for key, value in AMOUNTS.items() if key != "Deposit"}
    result = compare_account_amounts(AMOUNTS, current, same_scope=True)
    assert result["deltas"]["Deposit"] is None
    assert result["net_deposit_delta"] is None
    assert result["deltas"]["Commission"] == "0"
    assert result["problems"] == ["ACCOUNT_DEPOSIT_MISSING_CURRENT"]


def test_different_settlement_scope_never_subtracts_reset_counters() -> None:
    result = compare_account_amounts(AMOUNTS, {"Commission": "0"}, same_scope=False)
    assert all(delta is None for delta in result["deltas"].values())
    assert result["net_deposit_delta"] is None
    assert result["problems"] == ["ACCOUNT_SCOPE_CHANGED"]


@pytest.mark.parametrize(
    "invalid",
    [
        "NaN",
        "Infinity",
        "-Infinity",
        "1e9999999999999999999",
        "1e34",
        "1e-19",
        "9" * 81,
        "1_000",
        "12345678901234567.123456789012345678",
        0.1,
        None,
        True,
    ],
)
def test_invalid_or_unbounded_amount_cannot_silently_become_zero_or_rounded(
    invalid: object,
) -> None:
    result = compare_account_amounts({**AMOUNTS, "Commission": invalid}, AMOUNTS, same_scope=True)
    assert result["deltas"]["Commission"] is None
    assert result["deltas"]["Balance"] == "0"
    assert result["problems"] == ["ACCOUNT_COMMISSION_INVALID_PREVIOUS"]


def test_deltas_are_exact_across_large_and_subcent_values_under_hostile_decimal_context() -> None:
    previous = {**AMOUNTS, "Balance": "1e33", "Deposit": "-1e33", "Withdraw": "1e33"}
    current = {
        **AMOUNTS,
        "Balance": "0.000000000000000001",
        "Deposit": "0.000000000000000001",
        "Withdraw": "-0.000000000000000001",
    }
    with localcontext() as context:
        context.prec = 3
        context.Emax = 3
        context.Emin = -3
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        result = compare_account_amounts(previous, current, same_scope=True)
    assert result["deltas"]["Balance"] == "-" + "9" * 33 + "." + "9" * 18
    assert result["net_deposit_delta"] == "2" + "0" * 33 + ".000000000000000002"

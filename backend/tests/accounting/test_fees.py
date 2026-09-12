"""Confirmed executions survive unknown fees; balances stay unusable until covered."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from northstar_quant.accounting.fees import FeeFact
from northstar_quant.accounting.fifo import Account
from northstar_quant.accounting.fills import FillFact
from northstar_quant.accounting.portfolio import value_portfolio
from northstar_quant.accounting.settlement import SettlementFact
from tests.accounting.test_portfolio_account import AT, A, B, fills


def charge(*ids, identity="charge", amount="7.25", at=AT + timedelta(seconds=2)):
    return FeeFact(
        identity,
        tuple(sorted(ids)),
        Decimal(amount),
        "CNY",
        at,
        at,
        "synthetic verified coverage; not a CTP cumulative observation",
    )


def test_unknown_fee_keeps_positions_but_cannot_authorize_money_or_invent_zero():
    account = Account(Decimal(10000), (A, B))
    first, second = (replace(fill, fee=None) for fill in fills())
    applied = account.apply(first)
    assert applied.cash is None and applied.fact.fee is None
    assert applied.to_dict()["fee"] is None and applied.to_dict()["cash"] is None
    assert FillFact.from_dict(applied.to_dict()) == first
    account.apply(second)
    assert account.position(A.contract_id).long_today == 2
    assert account.position(B.contract_id).short_today == 3
    assert account.pending_fee_fill_ids == ("a", "b")
    assert account.checkpoint()["cash"] is None
    with pytest.raises(ValueError, match="confirmed fees"):
        account.equity({A.contract_id: Decimal(100), B.contract_id: Decimal(200)})
    with pytest.raises(ValueError, match="confirmed fees"):
        value_portfolio(account, {A.contract_id: Decimal(100), B.contract_id: Decimal(200)}, at=AT)
    fee = charge("a", "b")
    assert account.confirm_fee(fee).cash == Decimal("9992.75")
    assert account.cash == Decimal("9992.75") and account.total_fees == Decimal("7.25")
    assert account.pending_fee_fill_ids == ()
    # The aggregate charge never rewrites or allocates the original execution fee.
    assert all(fill.fact.fee is None for fill in account.applied_fills)
    before = account.checkpoint()
    account.confirm_fee(FeeFact.from_dict(fee.to_dict()))
    account.apply(first)
    assert account.checkpoint() == before
    rebuilt = Account(Decimal(10000), (A, B))
    for fill in account.applied_fills:
        rebuilt.apply(FillFact.from_dict(fill.to_dict()))
    for row in account.applied_fees:
        rebuilt.confirm_fee(FeeFact.from_dict(row.to_dict()))
    assert rebuilt.checkpoint() == before


def test_unknown_fee_does_not_discard_settlement_or_later_known_charge():
    account = Account(Decimal(10000), (A, B))
    first, second = fills()
    account.apply(replace(first, fee=None))
    account.apply(second)
    settlement = SettlementFact(
        "s",
        A.contract_id,
        AT.date(),
        AT.date() + timedelta(days=1),
        AT + timedelta(seconds=1),
        AT + timedelta(seconds=2),
        Decimal(110),
        "synthetic",
    )
    assert account.settle(settlement, at=settlement.available_at).cash is None
    assert account.position(A.contract_id).long_yesterday == 2
    assert account.total_fees == 3 and account.settlement_pnl == 200
    account.confirm_fee(charge("a", at=AT + timedelta(seconds=3)))
    assert account.cash == Decimal("10189.75")


def test_partial_fee_coverage_and_full_transaction_rollback():
    account = Account(Decimal(10000), (A, B))
    first, second = (replace(fill, fee=None) for fill in fills())
    account.apply(first)
    before = account.checkpoint()
    with pytest.raises(RuntimeError, match="rollback"):
        with account.transaction():
            account.apply(second)
            account.confirm_fee(charge("a"))
            raise RuntimeError("rollback")
    assert account.checkpoint() == before and account.applied_fees == ()
    account.apply(second)
    assert account.confirm_fee(charge("a")).cash is None
    assert account.pending_fee_fill_ids == ("b",)
    account.confirm_fee(charge("b", identity="confirmed-no-charge", amount="0"))
    assert account.cash == Decimal("9992.75")
    assert account.applied_fees[-1].fact.amount == 0


@pytest.mark.parametrize(
    "change",
    [
        {"fill_ids": ("missing",)},
        {"currency": "USD"},
        {"charged_at": AT - timedelta(seconds=1)},
        {"available_at": AT - timedelta(seconds=1), "charged_at": AT - timedelta(seconds=1)},
    ],
)
def test_wrong_coverage_time_or_currency_cannot_modify_balance(change):
    account = Account(Decimal(10000), (A, B))
    account.apply(replace(fills()[0], fee=None))
    before = account.checkpoint()
    with pytest.raises(ValueError):
        account.confirm_fee(replace(charge("a"), **change))
    assert account.checkpoint() == before


def test_charge_cannot_cover_known_fee_or_be_reused_for_different_amount():
    account = Account(Decimal(10000), (A, B))
    account.apply(fills()[0])
    with pytest.raises(ValueError, match="awaiting"):
        account.confirm_fee(charge("a"))
    account.apply(replace(fills()[1], fee=None))
    account.confirm_fee(charge("b"))
    with pytest.raises(ValueError, match="reused"):
        account.confirm_fee(charge("b", amount="1"))
    with pytest.raises(ValueError, match="awaiting"):
        account.confirm_fee(charge("b", identity="another-charge"))


def test_fee_revisions_preserve_history_apply_only_delta_and_rollback():
    account = Account(Decimal(10000), (A, B))
    fact = replace(fills()[0], fee=None)
    account.apply(fact)
    original = charge("a", amount="7")
    account.confirm_fee(original)
    refund = replace(charge("a", identity="refund", amount="2"), supersedes_fee_id="charge")
    before = account.checkpoint()
    with pytest.raises(RuntimeError):
        with account.transaction():
            account.confirm_fee(refund)
            assert account.cash == Decimal(9998)
            raise RuntimeError("abort")
    assert account.checkpoint() == before
    account.confirm_fee(refund)
    account.confirm_fee(refund)
    assert account.cash == Decimal(9998) and account.total_fees == 2
    assert account.applied_fees[0].fact == original
    with pytest.raises(ValueError, match="fork"):
        account.confirm_fee(replace(refund, fee_id="fork"))
    increase = replace(refund, fee_id="increase", amount=Decimal(12), supersedes_fee_id="refund")
    account.confirm_fee(increase)
    rebuilt = Account(Decimal(10000), (A, B))
    rebuilt.apply(fact)
    for row in account.applied_fees:
        rebuilt.confirm_fee(FeeFact.from_dict(row.to_dict()))
    assert rebuilt.checkpoint() == account.checkpoint()
    assert rebuilt.cash == Decimal(9988) and rebuilt.total_fees == 12


@pytest.mark.parametrize(
    "change",
    [
        {"supersedes_fee_id": "missing"},
        {"fill_ids": ("a", "b")},
        {"charged_at": AT},
        {"available_at": AT, "charged_at": AT},
    ],
)
def test_fee_revision_rejects_missing_coverage_and_regressive_evidence(change):
    account = Account(Decimal(10000), (A, B))
    account.apply(replace(fills()[0], fee=None))
    account.confirm_fee(charge("a"))
    before = account.checkpoint()
    revision = replace(
        charge("a", identity="revision"), **{"supersedes_fee_id": "charge", **change}
    )
    with pytest.raises(ValueError):
        account.confirm_fee(revision)
    assert account.checkpoint() == before

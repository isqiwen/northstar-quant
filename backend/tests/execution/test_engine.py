"""Replacement requests cannot release reservations or invent filled quantities."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from northstar_quant.execution.engine import ExecutionEngine
from northstar_quant.execution.orders import Offset, OrderBudget, PendingOrder, Side, reservation


def test_partial_facts_and_confirmed_cancel_control_the_working_order():
    at = datetime(2026, 9, 11, 1, tzinfo=UTC)
    order = PendingOrder(
        "order-1",
        uuid4(),
        at,
        at + timedelta(minutes=5),
        Side.BUY,
        Offset.OPEN,
        3,
        Decimal(90),
        Decimal(110),
        contract_id=UUID(int=1),
        budget=OrderBudget(
            Decimal(2),
            Decimal(100),
            Decimal(1100),
            Decimal(0),
        ),
    )
    engine = ExecutionEngine().record(order.observe(at=at, reason="CONFIRMED_SUBMISSION"))
    partial = order.record_fill(1, at=at + timedelta(seconds=1), reason="CONFIRMED_FILL")
    with pytest.raises(ValueError, match="unconfirmed fill"):
        engine.record(partial)
    engine = engine.record(partial, accepted_fill_lots=1)
    assert reservation(engine.pending_for(order.contract_id)) == {
        "reserved_fee": "4",
        "reserved_margin": "200",
        "reserved_close_lots": 0,
        "reserved_gross": "2200",
        "reserved_loss": "0",
    }
    desired = replace(order, order_id="order-2", submitted_at=at + timedelta(seconds=2))
    assert engine.plan(
        order.contract_id, desired, retained_budget=order.budget
    ).retained == engine.pending_for(order.contract_id)
    different = replace(desired, contract_id=UUID(int=2))
    with pytest.raises(ValueError, match="different contract"):
        engine.plan(order.contract_id, different, retained_budget=different.budget)
    desired = replace(desired, minimum_fill_price=Decimal(100))
    planned = engine.plan(order.contract_id, desired, retained_budget=order.budget)
    assert planned.cancel == engine.pending_for(order.contract_id) and planned.submit == desired
    assert reservation(engine.pending_for(order.contract_id))["reserved_margin"] == "200"
    with pytest.raises(ValueError, match="fixed terms"):
        engine.record(desired.observe(at=desired.submitted_at, reason="PREMATURE_REPLACEMENT"))
    cancelled = engine.pending_for(order.contract_id).cancel(
        at=desired.submitted_at, reason="CONFIRMED_CANCEL"
    )
    engine = engine.record(cancelled)
    assert reservation(engine.pending_for(order.contract_id))["reserved_margin"] == "0"
    engine = engine.record(desired.observe(at=desired.submitted_at, reason="CONFIRMED_SUBMISSION"))
    assert engine.pending_for(order.contract_id) == desired
    with pytest.raises(ValueError, match="backwards"):
        engine.record(order.observe(at=at, reason="LATE_SUBMISSION"))


def test_increased_cost_budget_requires_a_new_order_even_with_identical_price_bounds():
    at = datetime(2026, 9, 11, 1, tzinfo=UTC)
    order = PendingOrder(
        "order",
        uuid4(),
        at,
        at + timedelta(minutes=5),
        Side.SELL,
        Offset.CLOSE_TODAY,
        2,
        Decimal(90),
        Decimal(110),
        contract_id=UUID(int=1),
        budget=OrderBudget(
            Decimal(2),
            Decimal(0),
            Decimal(0),
            Decimal(0),
        ),
    )
    engine = ExecutionEngine().record(order.observe(at=at, reason="CONFIRMED_SUBMISSION"))
    candidate = replace(order, order_id="higher-fee", budget=replace(order.budget, fee=Decimal(3)))
    plan = engine.plan(order.contract_id, candidate, retained_budget=candidate.budget)
    assert plan.retained is None and plan.cancel == order and plan.submit == candidate
    assert reservation(engine.pending_for(order.contract_id))["reserved_close_lots"] == 2


def test_multi_contract_order_facts_keep_other_inventory_and_commitments_intact():
    from northstar_quant.accounting.fifo import Account
    from northstar_quant.accounting.fills import FillFact
    from northstar_quant.execution.history import OrderHistory
    from tests.accounting.test_portfolio_account import AT, A, B

    account = Account(Decimal(10000), (A, B))
    first = PendingOrder(
        "a-order",
        UUID(int=10),
        AT,
        AT + timedelta(minutes=5),
        Side.BUY,
        Offset.OPEN,
        2,
        Decimal(90),
        Decimal(110),
        contract_id=A.contract_id,
        budget=OrderBudget(Decimal(1), Decimal(110), Decimal(1100), Decimal(0)),
    )
    second = replace(first, order_id="b-order", contract_id=B.contract_id)
    engine, history = ExecutionEngine(), OrderHistory()
    for order in (first, second):
        update = order.observe(at=AT, reason="CONFIRMED_SUBMISSION")
        engine = engine.record(update)
        history.observe(update, at=AT)
    history.require_working(engine.working)
    with pytest.raises(ValueError, match="identity"):
        engine.record(
            replace(first, contract_id=UUID(int=99)).observe(at=AT, reason="WRONG_CONTRACT")
        )
    with pytest.raises(ValueError, match="fixed terms"):
        engine.record(
            replace(first, order_id="second-a").observe(at=AT, reason="DUPLICATE_CONTRACT")
        )
    partial_at = AT + timedelta(seconds=1)
    fact = FillFact(
        "b-fill",
        second.order_id,
        B.contract_id,
        None,
        partial_at,
        AT.date(),
        Side.BUY,
        Offset.OPEN,
        1,
        Decimal(100),
        Decimal(1),
        available_at=partial_at,
    )
    history.accept_fill(fact)
    account.apply(fact)
    partial = second.record_fill(1, at=partial_at, reason="CONFIRMED_FILL")
    engine = engine.record(partial, accepted_fill_lots=1)
    history.observe(partial, at=partial_at)
    history.require_working(engine.working)
    cancel_at = AT + timedelta(seconds=2)
    terminal = first.cancel(at=cancel_at, reason="CONFIRMED_CANCEL")
    engine = engine.record(terminal)
    history.observe(terminal, at=cancel_at)
    assert engine.working == (partial.order,)
    history.require_working(engine.working)
    assert account.position(A.contract_id).net_lots == 0
    assert account.position(B.contract_id).net_lots == 1 and account.cash == 9999
    assert reservation(engine.pending_for(B.contract_id))["reserved_margin"] == "110"
    with pytest.raises(ValueError, match="working authorized order"):
        history.accept_fill(replace(fact, fill_id="wrong-contract", contract_id=A.contract_id))
    with pytest.raises(ValueError, match="backwards"):
        engine.record(partial)
    recovered = ExecutionEngine(engine.working, engine.observed_at)
    assert recovered == engine and recovered.pending_for(A.contract_id) is None

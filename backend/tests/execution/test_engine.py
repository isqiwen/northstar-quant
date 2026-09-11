"""Replacement requests cannot release reservations or invent filled quantities."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from northstar_quant.execution.engine import ExecutionEngine
from northstar_quant.execution.orders import Offset, PendingOrder, Side, reservation


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
        fee_budget_per_lot=Decimal(2),
        margin_budget_per_lot=Decimal(100),
    )
    engine = ExecutionEngine().record(order.observe(at=at, reason="CONFIRMED_SUBMISSION"))
    partial = order.record_fill(1, at=at + timedelta(seconds=1), reason="CONFIRMED_FILL")
    with pytest.raises(ValueError, match="unconfirmed fill"):
        engine.record(partial)
    engine = engine.record(partial, accepted_fill_lots=1)
    assert reservation(engine.pending) == {
        "reserved_fee": "4",
        "reserved_margin": "200",
        "reserved_close_lots": 0,
    }
    desired = replace(order, order_id="order-2", submitted_at=at + timedelta(seconds=2))
    assert (
        engine.plan(desired, retained_budget=(Decimal(2), Decimal(100))).retained == engine.pending
    )
    desired = replace(desired, minimum_fill_price=Decimal(100))
    planned = engine.plan(desired, retained_budget=(Decimal(2), Decimal(100)))
    assert planned.cancel == engine.pending and planned.submit == desired
    assert reservation(engine.pending)["reserved_margin"] == "200"
    with pytest.raises(ValueError, match="fixed terms"):
        engine.record(desired.observe(at=desired.submitted_at, reason="PREMATURE_REPLACEMENT"))
    cancelled = engine.pending.cancel(at=desired.submitted_at, reason="CONFIRMED_CANCEL")
    engine = engine.record(cancelled)
    assert reservation(engine.pending)["reserved_margin"] == "0"
    engine = engine.record(desired.observe(at=desired.submitted_at, reason="CONFIRMED_SUBMISSION"))
    assert engine.pending == desired
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
        fee_budget_per_lot=Decimal(2),
        margin_budget_per_lot=Decimal(0),
    )
    engine = ExecutionEngine().record(order.observe(at=at, reason="CONFIRMED_SUBMISSION"))
    candidate = replace(order, order_id="higher-fee", fee_budget_per_lot=Decimal(3))
    plan = engine.plan(candidate, retained_budget=(Decimal(3), Decimal(0)))
    assert plan.retained is None and plan.cancel == order and plan.submit == candidate
    assert reservation(engine.pending)["reserved_close_lots"] == 2

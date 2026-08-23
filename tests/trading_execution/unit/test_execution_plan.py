import pytest

from northstar_quant.foundation.config.trading_profile import load_trading_profile
from northstar_quant.trading_execution.execution import (
    ExecutionPlan,
    ExecutionPlanError,
    build_approved_execution_plan,
)
from northstar_quant.trading_execution.execution.models import (
    BrokerStateSnapshot,
    FuturesExecutionRule,
    RebalanceOrderPlan,
)
from tests.helpers.approved_portfolio_target import build_approved_portfolio_target_fixture


def test_execution_plan_binds_approved_target_and_cannot_be_submitted():
    fixture = build_approved_portfolio_target_fixture()
    approved = fixture.approved_target
    now = approved.approved_at
    plan = ExecutionPlan(
        "plan-1", approved, BrokerStateSnapshot(asof=now), now,
        {"rb2610": FuturesExecutionRule(0.1)},
        (RebalanceOrderPlan("RB2610", "BUY", 1.0),), now,
    )

    assert plan.approved_target is approved
    assert plan.eligible_for_broker_order is False


def test_execution_plan_fails_closed_without_account_timestamp_or_contract_rules():
    fixture = build_approved_portfolio_target_fixture()
    approved = fixture.approved_target
    now = approved.approved_at
    with pytest.raises(ExecutionPlanError, match="account snapshot timestamp"):
        ExecutionPlan(
            "plan-1", approved, BrokerStateSnapshot(), now,
            {"rb2610": FuturesExecutionRule(0.1)},
            (RebalanceOrderPlan("RB2610", "BUY", 1.0),), now,
        )


def test_approved_target_is_bound_to_existing_futures_planner():
    fixture = build_approved_portfolio_target_fixture()
    approved = fixture.approved_target
    now = approved.approved_at

    plan = build_approved_execution_plan(
        plan_id="plan-1",
        approved_target=approved,
        profile=load_trading_profile("cn_futures_daily_trend_simulated"),
        account_snapshot=BrokerStateSnapshot(asof=now),
        latest_prices={"RB2610": 3100.0},
        market_snapshot_at=now,
        created_at=now,
        broker_name="ctp_sim",
        futures_rules={"RB2610": FuturesExecutionRule(0.1)},
        equity=100_000.0,
    )

    assert plan.orders[0].instrument_id == "rb2610"
    assert plan.orders[0].ctp_offset == "open"
    assert plan.eligible_for_broker_order is False

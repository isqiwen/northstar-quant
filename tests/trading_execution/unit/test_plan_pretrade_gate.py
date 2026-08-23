import pytest

from northstar_quant.portfolio_risk.limits.models import RiskLimits
from northstar_quant.trading_execution.broker.broker_base import BrokerAdapter
from northstar_quant.trading_execution.execution import ExecutionPlan
from northstar_quant.trading_execution.execution.models import BrokerStateSnapshot, FuturesExecutionRule, OrderRequest, OrderResult, RebalanceOrderPlan
from northstar_quant.trading_execution.execution.router import OrderRouter
from northstar_quant.trading_execution.live.plan_gate import PlanPreTradeGate
from northstar_quant.trading_execution.live.preflight import PreflightCheck, PreflightResult
from tests.helpers.approved_portfolio_target import build_approved_portfolio_target_fixture


def _gate(*, can_trade: bool = True) -> PlanPreTradeGate:
    fixture = build_approved_portfolio_target_fixture()
    approved = fixture.approved_target
    now = fixture.approved_target.approved_at
    plan = ExecutionPlan("plan-1", approved, BrokerStateSnapshot(asof=now), now, {"RB2610": FuturesExecutionRule(0.1)}, (RebalanceOrderPlan("RB2610", "BUY", 1.0, instrument_id="rb2610", exchange_id="SHFE", ctp_offset="open"),), now)
    checks = [] if can_trade else [PreflightCheck("kill_switch", "fail", True, "blocked")]
    return PlanPreTradeGate(plan, PreflightResult("profile-1", now, checks))


def _order(**updates: object) -> OrderRequest:
    values: dict[str, object] = {"strategy_id": "test", "symbol": "RB2610", "side": "BUY", "qty": 1.0, "plan_id": "plan-1", "instrument_id": "rb2610", "exchange_id": "SHFE", "ctp_offset": "open"}
    values.update(updates)
    return OrderRequest(**values)  # type: ignore[arg-type]


class _AcceptingBroker(BrokerAdapter):
    def __init__(self) -> None:
        self.orders: list[OrderRequest] = []

    def get_name(self) -> str:
        return "test"

    def submit_order(self, order: OrderRequest) -> OrderResult:
        self.orders.append(order)
        return OrderResult(True, "accepted-1", "ACCEPTED")


def test_plan_gate_allows_exact_item_once_and_rejects_replay():
    gate = _gate()
    gate(_order())
    with pytest.raises(PermissionError, match="ORDER_MISMATCH"):
        gate(_order())


def test_plan_gate_fails_closed_for_preflight_or_plan_mismatch():
    with pytest.raises(PermissionError, match="PREFLIGHT_BLOCKED"):
        _gate(can_trade=False)(_order())
    with pytest.raises(PermissionError, match="PLAN_ID_MISMATCH"):
        _gate()(_order(plan_id="other-plan"))


def test_plan_gate_is_enforced_immediately_before_broker_submission():
    broker = _AcceptingBroker()
    router = OrderRouter(broker, RiskLimits(max_order_notional=None), submission_guard=_gate())

    router.route(_order())

    with pytest.raises(PermissionError, match="ORDER_MISMATCH"):
        router.route(_order())

    assert broker.orders == [_order()]

import pytest

from northstar_quant.trading_execution.execution.models import OrderResult
from northstar_quant.trading_execution.orders import (
    BrokerOrderIdentity,
    BrokerOrderStatus,
    OrderLifecycleError,
    apply_broker_order_status,
    canonicalize_broker_order_status,
    transition_order_status,
)


def test_order_state_machine_handles_submit_fill_cancel_and_duplicate_callbacks():
    state = transition_order_status(current=BrokerOrderStatus.CREATED, target=BrokerOrderStatus.SUBMITTING)
    state = transition_order_status(current=state, target=BrokerOrderStatus.ACCEPTED)
    state = transition_order_status(current=state, target=BrokerOrderStatus.PARTIALLY_FILLED)
    assert transition_order_status(current=state, target=BrokerOrderStatus.PARTIALLY_FILLED) is state
    assert transition_order_status(current=state, target=BrokerOrderStatus.FILLED) is BrokerOrderStatus.FILLED
    assert BrokerOrderIdentity("client-1", "broker-1", 1, 2).broker_order_id == "broker-1"


def test_order_state_machine_rejects_terminal_regression_and_unknown_recovery():
    with pytest.raises(OrderLifecycleError, match="invalid"):
        transition_order_status(current=BrokerOrderStatus.FILLED, target=BrokerOrderStatus.CANCELLED)
    with pytest.raises(OrderLifecycleError, match="invalid"):
        transition_order_status(current=BrokerOrderStatus.UNKNOWN, target=BrokerOrderStatus.ACCEPTED)
    assert canonicalize_broker_order_status("unrecognised callback") is BrokerOrderStatus.UNKNOWN
    assert OrderResult(True, "broker-1", "Submitted").canonical_status is BrokerOrderStatus.ACCEPTED


def test_order_state_machine_rejects_out_of_order_broker_callback():
    assert (
        apply_broker_order_status(
            current="Submitted",
            broker_status="Submitted",
        )
        is BrokerOrderStatus.ACCEPTED
    )
    with pytest.raises(OrderLifecycleError, match="invalid"):
        apply_broker_order_status(
            current="Filled",
            broker_status="PartiallyFilled",
        )

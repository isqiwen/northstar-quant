from northstar_quant.platform.observability.monitoring import (
    ObservationState,
    observation_state_from_health,
)


def test_observability_unknown_status_never_becomes_healthy():
    assert observation_state_from_health("missing") is ObservationState.UNKNOWN
    assert observation_state_from_health("fail") is ObservationState.BLOCKED

from datetime import UTC, datetime

from northstar_quant.trading_execution.broker import (
    BrokerCapabilities,
    BrokerConnectionState,
    BrokerIdentity,
    BrokerMode,
    BrokerStatus,
)


def test_broker_status_fails_closed_when_connection_is_unknown():
    status = BrokerStatus(
        BrokerIdentity("paper", BrokerMode.PAPER, "paper-test", None),
        BrokerConnectionState.UNKNOWN,
        BrokerCapabilities(True, True, True, True, False),
        datetime(2026, 8, 22, tzinfo=UTC),
    )
    assert status.permits_new_risk is False


def test_broker_status_requires_a_typed_timezone_aware_contract():
    status = BrokerStatus(
        BrokerIdentity("ctp_sim", BrokerMode.CTP_SIM, "sim", 1),
        BrokerConnectionState.CONNECTED,
        BrokerCapabilities(True, True, True, True, True),
        datetime(2026, 8, 22, tzinfo=UTC),
    )
    assert status.permits_new_risk is True

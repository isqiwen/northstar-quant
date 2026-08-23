from typing import cast

import pytest

from northstar_quant.trading_execution.broker.ctp_broker import CtpBrokerAdapter
from northstar_quant.trading_execution.broker.ctp_contract_mapping import (
    CtpContractMapping,
    CtpContractRegistry,
)
from northstar_quant.trading_execution.broker.ctp_front import CtpFront
from northstar_quant.trading_execution.broker.ctp_front import FakeCtpFront
from northstar_quant.trading_execution.execution.models import BrokerStateSnapshot, OrderRequest



def _registry() -> CtpContractRegistry:
    return CtpContractRegistry(
        1,
        "ctp",
        (
            CtpContractMapping(
                "RB_CONT",
                "RB2610",
                "rb2610",
                "SHFE",
                "rb",
                10,
                1.0,
                True,
            ),
        ),
    )


def test_ctp_skeleton_allows_only_in_memory_fake_front() -> None:
    adapter = CtpBrokerAdapter(front=FakeCtpFront(), registry=_registry(), account="test-only")

    assert adapter.broker_status().permits_new_risk is False
    adapter.connect()
    result = adapter.submit_order(OrderRequest("test", "RB2610", "BUY", 1.0))

    assert result.accepted is True
    assert adapter.cancel_order(result.broker_order_id) is True
    assert adapter.sync_state().state_complete is True
    adapter.disconnect()
    assert adapter.broker_status().permits_new_risk is False


def test_ctp_skeleton_rejects_any_non_fake_front_before_connection() -> None:
    class _PretendFake:
        is_test_double = True

    adapter = CtpBrokerAdapter(
        front=cast(CtpFront, _PretendFake()), registry=_registry(), account="not-live"
    )

    with pytest.raises(PermissionError, match="CTP_REAL_FRONT_DISABLED"):
        adapter.connect()


def test_ctp_skeleton_requires_an_enabled_actual_contract_mapping() -> None:
    adapter = CtpBrokerAdapter(front=FakeCtpFront(), registry=_registry(), account="test-only")
    adapter.connect()

    with pytest.raises(ValueError, match="CTP_EXCHANGE_MISMATCH"):
        adapter.submit_order(
            OrderRequest("test", "RB2610", "BUY", 1.0, exchange_id="DCE")
        )
    with pytest.raises(ValueError, match="CTP_INSTRUMENT_MISMATCH"):
        adapter.submit_order(
            OrderRequest("test", "RB2610", "BUY", 1.0, instrument_id="rb2701")
        )


def test_ctp_skeleton_rejects_incomplete_or_wrong_account_front_snapshot() -> None:
    class _WrongAccountFake(FakeCtpFront):
        def sync_state(self) -> BrokerStateSnapshot:
            return BrokerStateSnapshot(account="other-account", state_complete=True)

    adapter = CtpBrokerAdapter(
        front=_WrongAccountFake(), registry=_registry(), account="test-only"
    )
    adapter.connect()

    with pytest.raises(RuntimeError, match="CTP_STATE_SNAPSHOT_UNTRUSTED"):
        adapter.sync_state()

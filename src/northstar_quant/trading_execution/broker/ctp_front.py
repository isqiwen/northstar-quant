"""CTP front protocol and a local-only fake for adapter contract tests.

This module deliberately contains no vendor SDK, socket implementation, front
address, credential, or production connection path.  A future real front must
be introduced in a separately approved work package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from northstar_quant.trading_execution.execution.models import BrokerStateSnapshot, OrderRequest, OrderResult


class CtpFront(Protocol):
    """Minimal future CTP-front boundary; only test doubles are usable today."""

    is_test_double: bool

    def connect(self, *, account: str, client_id: int) -> None: ...

    def disconnect(self) -> None: ...

    def submit_order(self, order: OrderRequest) -> OrderResult: ...

    def cancel_order(self, broker_order_id: str) -> bool: ...

    def sync_state(self) -> BrokerStateSnapshot: ...


@dataclass(slots=True)
class FakeCtpFront:
    """Deterministic in-memory protocol double; it can never reach a broker."""

    is_test_double: bool = True
    connected: bool = False
    account: str | None = None
    client_id: int | None = None

    def connect(self, *, account: str, client_id: int) -> None:
        if not account.strip() or client_id <= 0:
            raise ValueError("CTP_FAKE_FRONT_IDENTITY_REQUIRED")
        self.account = account
        self.client_id = client_id
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def submit_order(self, order: OrderRequest) -> OrderResult:
        if not self.connected:
            raise ConnectionError("CTP_FAKE_FRONT_DISCONNECTED")
        return OrderResult(
            accepted=True,
            broker_order_id="CTPFAKE-00000001",
            status="Accepted",
            client_id=self.client_id,
            perm_id=1,
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        return self.connected and bool(str(broker_order_id).strip())

    def sync_state(self) -> BrokerStateSnapshot:
        if not self.connected:
            raise ConnectionError("CTP_FAKE_FRONT_DISCONNECTED")
        return BrokerStateSnapshot(account=self.account, state_complete=True)

"""Fail-closed real-CTP adapter skeleton.

Only a ``FakeCtpFront`` may be injected during this development stage.  The
application composition root continues to reject ``NORTHSTAR_BROKER=ctp``;
there is no vendor SDK, real front address, credential handling, or production
submission path in this module.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from northstar_quant.trading_execution.broker.broker_base import BrokerAdapter
from northstar_quant.trading_execution.broker.contracts import BrokerCapabilities, BrokerConnectionState, BrokerIdentity, BrokerMode, BrokerStatus
from northstar_quant.trading_execution.broker.ctp_contract_mapping import CtpContractRegistry
from northstar_quant.trading_execution.broker.ctp_front import CtpFront, FakeCtpFront
from northstar_quant.trading_execution.execution.models import BrokerStateSnapshot, OrderRequest, OrderResult


class CtpBrokerAdapter(BrokerAdapter):
    """Protocol-only adapter whose default and production behavior is closed."""

    def __init__(
        self,
        *,
        front: CtpFront,
        registry: CtpContractRegistry,
        account: str,
        client_id: int = 1,
    ) -> None:
        normalized_account = str(account or "").strip()
        if not normalized_account:
            raise ValueError("CTP_ACCOUNT_REQUIRED")
        if client_id <= 0:
            raise ValueError("CTP_CLIENT_ID_INVALID")
        self._front = front
        if registry.broker != "ctp":
            raise ValueError("CTP_CONTRACT_BROKER_MISMATCH")
        self._registry = registry
        self._account = normalized_account
        self._client_id = client_id
        self._connected = False

    def _is_fake_front(self) -> bool:
        return isinstance(self._front, FakeCtpFront)

    def _require_fake_front(self) -> None:
        if not self._is_fake_front():
            raise PermissionError(
                "CTP_REAL_FRONT_DISABLED: real CTP front support is not implemented."
            )

    def _require_connected(self) -> None:
        if not self._connected:
            raise ConnectionError("CTP_DISCONNECTED")

    def connect(self) -> None:
        self._require_fake_front()
        self._front.connect(account=self._account, client_id=self._client_id)
        self._connected = True

    def disconnect(self) -> None:
        if self._connected:
            self._front.disconnect()
        self._connected = False

    def get_name(self) -> str:
        return "ctp"

    def get_account(self) -> str:
        return self._account

    def get_client_id(self) -> int:
        return self._client_id

    def prepare_order(self, order: OrderRequest) -> OrderRequest:
        mapping = self._registry.resolve_data_symbol(order.symbol)
        if order.instrument_id is not None and order.instrument_id.lower() != mapping.instrument_id:
            raise ValueError("CTP_INSTRUMENT_MISMATCH")
        if order.exchange_id is not None and order.exchange_id.upper() != mapping.exchange_id:
            raise ValueError("CTP_EXCHANGE_MISMATCH")
        if order.volume_multiple is not None and order.volume_multiple != mapping.volume_multiple:
            raise ValueError("CTP_MULTIPLIER_MISMATCH")
        return replace(
            order,
            symbol=mapping.data_symbol,
            account=order.account or self._account,
            instrument_id=mapping.instrument_id,
            exchange_id=mapping.exchange_id,
            volume_multiple=mapping.volume_multiple,
        )

    def broker_status(self) -> BrokerStatus:
        return BrokerStatus(
            identity=BrokerIdentity("ctp", BrokerMode.CTP, self._account, self._client_id),
            connection_state=(
                BrokerConnectionState.CONNECTED
                if self._connected and self._is_fake_front()
                else BrokerConnectionState.DISCONNECTED
            ),
            capabilities=BrokerCapabilities(
                submit_orders=self._connected and self._is_fake_front(),
                cancel_orders=self._connected and self._is_fake_front(),
                state_sync=True,
                market_quotes=False,
                persistent_idempotency=False,
            ),
            observed_at=datetime.now(UTC),
        )

    def submit_order(self, order: OrderRequest) -> OrderResult:
        self._require_fake_front()
        self._require_connected()
        return self._front.submit_order(self.prepare_order(order))

    def cancel_order(self, broker_order_id: str) -> bool:
        self._require_fake_front()
        self._require_connected()
        return self._front.cancel_order(broker_order_id)

    def sync_state(self) -> BrokerStateSnapshot:
        self._require_fake_front()
        self._require_connected()
        snapshot = self._front.sync_state()
        if not snapshot.state_complete or snapshot.account != self._account:
            raise RuntimeError("CTP_STATE_SNAPSHOT_UNTRUSTED")
        return snapshot

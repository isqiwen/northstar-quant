"""P5-WP01 typed broker and market-gateway safety contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from northstar_quant.trading_execution.execution.models import MarketQuoteSnapshot


class BrokerMode(StrEnum):
    PAPER = "paper"
    CTP_SIM = "ctp_sim"
    CTP = "ctp"


class BrokerConnectionState(StrEnum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    UNKNOWN = "UNKNOWN"


class BrokerErrorCode(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    STATE_UNKNOWN = "STATE_UNKNOWN"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    INVALID_REQUEST = "INVALID_REQUEST"


class BrokerAdapterError(RuntimeError):
    def __init__(self, code: BrokerErrorCode, message: str) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code


@dataclass(frozen=True, slots=True)
class BrokerIdentity:
    broker: str
    mode: BrokerMode
    account: str | None
    client_id: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.broker, str) or not self.broker.strip():
            raise ValueError("broker identity requires a broker name")
        if not isinstance(self.mode, BrokerMode):
            raise ValueError("broker identity requires a supported safe mode")


@dataclass(frozen=True, slots=True)
class BrokerCapabilities:
    submit_orders: bool
    cancel_orders: bool
    state_sync: bool
    market_quotes: bool
    persistent_idempotency: bool


@dataclass(frozen=True, slots=True)
class BrokerStatus:
    identity: BrokerIdentity
    connection_state: BrokerConnectionState
    capabilities: BrokerCapabilities
    observed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.connection_state, BrokerConnectionState):
            raise ValueError("connection_state must be typed")
        if not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(UTC))

    @property
    def permits_new_risk(self) -> bool:
        return self.connection_state is BrokerConnectionState.CONNECTED and self.capabilities.submit_orders


@runtime_checkable
class MarketGateway(Protocol):
    """Read-only, time-stamped broker market-data boundary."""

    def get_market_quotes(self, symbols: list[str]) -> list[MarketQuoteSnapshot]: ...


__all__ = ["BrokerAdapterError", "BrokerCapabilities", "BrokerConnectionState", "BrokerErrorCode", "BrokerIdentity", "BrokerMode", "BrokerStatus", "MarketGateway"]

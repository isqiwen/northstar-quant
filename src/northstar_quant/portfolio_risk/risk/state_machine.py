"""P3-WP05 audited, fail-closed portfolio risk state transitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
import hashlib
import json


class RiskState(str, Enum):
    NORMAL = "NORMAL"
    LIMIT_ONLY = "LIMIT_ONLY"
    REDUCE_ONLY = "REDUCE_ONLY"
    HALT = "HALT"
    MANUAL_RECOVERY = "MANUAL_RECOVERY"


class RiskStateError(ValueError):
    pass


_ALLOWED = {
    RiskState.NORMAL: {RiskState.LIMIT_ONLY, RiskState.REDUCE_ONLY, RiskState.HALT},
    RiskState.LIMIT_ONLY: {RiskState.REDUCE_ONLY, RiskState.HALT},
    RiskState.REDUCE_ONLY: {RiskState.HALT},
    RiskState.HALT: {RiskState.MANUAL_RECOVERY},
    RiskState.MANUAL_RECOVERY: {RiskState.NORMAL, RiskState.LIMIT_ONLY, RiskState.REDUCE_ONLY, RiskState.HALT},
}


@dataclass(frozen=True, slots=True)
class RiskStateSnapshot:
    state: RiskState
    occurred_at: datetime
    reason: str
    predecessor_hash: str | None = None
    recovery_approver_id: str | None = None
    state_hash: str = field(init=False)

    @classmethod
    def initial(cls, *, occurred_at: datetime) -> "RiskStateSnapshot":
        return cls(RiskState.NORMAL, occurred_at, "initial")

    def __post_init__(self) -> None:
        if not isinstance(self.state, RiskState) or self.occurred_at.tzinfo is None:
            raise RiskStateError("state and timezone-aware occurred_at are required")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise RiskStateError("reason is required")
        if self.state is RiskState.MANUAL_RECOVERY and not self.recovery_approver_id:
            raise RiskStateError("MANUAL_RECOVERY requires a named approver")
        payload = {"state": self.state.value, "occurred_at": self.occurred_at.astimezone(UTC).isoformat(), "reason": self.reason, "predecessor_hash": self.predecessor_hash, "recovery_approver_id": self.recovery_approver_id}
        object.__setattr__(self, "state_hash", hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest())

    def transition(self, *, target: RiskState, occurred_at: datetime, reason: str, recovery_approver_id: str | None = None) -> "RiskStateSnapshot":
        if target not in _ALLOWED[self.state]:
            raise RiskStateError(f"transition from {self.state.value} to {target.value} is forbidden")
        if occurred_at < self.occurred_at:
            raise RiskStateError("risk transitions cannot move backward in time")
        return RiskStateSnapshot(target, occurred_at, reason, self.state_hash, recovery_approver_id)


__all__ = ["RiskState", "RiskStateError", "RiskStateSnapshot"]

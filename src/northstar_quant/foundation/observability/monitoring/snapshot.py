"""Fail-closed operational observability snapshot contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum

from northstar_quant.foundation.db.repositories import (
    latest_reconciliation_safety_state,
    latest_runtime_risk_record,
    list_run_health_records,
)
from northstar_quant.foundation.db.session import SessionLocal


class ObservationState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class OperationalSnapshot:
    """Read-only status evidence; unknown state is never treated as healthy."""

    system: ObservationState
    job_state: ObservationState
    broker_state: ObservationState
    data_staleness: ObservationState
    risk_state: ObservationState
    reconciliation_state: ObservationState

    def as_dict(self) -> dict[str, str]:
        return {key: value.value for key, value in asdict(self).items()}


def observation_state_from_health(status: str) -> ObservationState:
    """Convert health-check status without silently mapping unknown values."""

    return {
        "pass": ObservationState.HEALTHY,
        "ok": ObservationState.HEALTHY,
        "warn": ObservationState.DEGRADED,
        "degraded": ObservationState.DEGRADED,
        "fail": ObservationState.BLOCKED,
        "blocked": ObservationState.BLOCKED,
    }.get(status, ObservationState.UNKNOWN)


def load_execution_observation_states(
    *,
    profile_id: str,
    broker: str,
    account: str | None,
    session_factory=SessionLocal,
) -> tuple[ObservationState, ObservationState, ObservationState]:
    """Read latest job/risk/reconciliation evidence; query failure remains UNKNOWN."""

    try:
        with session_factory() as session:
            run = list_run_health_records(session, limit=1, profile_id=profile_id, account=account)
            risk = latest_runtime_risk_record(
                session, profile_id=profile_id, broker=broker, account=account
            )
            reconciliation = latest_reconciliation_safety_state(
                session, profile_id=profile_id, broker=broker, account=account
            )
    except Exception:
        return (ObservationState.UNKNOWN,) * 3
    job = (
        ObservationState.UNKNOWN if not run else
        ObservationState.BLOCKED if not run[0].preflight_can_trade else
        ObservationState.DEGRADED if int(run[0].warning_count or 0) else ObservationState.HEALTHY
    )
    risk_state = (
        ObservationState.UNKNOWN if risk is None else
        ObservationState.BLOCKED if not risk.can_submit else
        ObservationState.DEGRADED if int(risk.warning_count or 0) else ObservationState.HEALTHY
    )
    reconciliation_state = (
        ObservationState.UNKNOWN if reconciliation is None else
        ObservationState.HEALTHY if reconciliation.state == "NORMAL" else ObservationState.BLOCKED
    )
    return job, risk_state, reconciliation_state


__all__ = ["ObservationState", "OperationalSnapshot", "load_execution_observation_states", "observation_state_from_health"]

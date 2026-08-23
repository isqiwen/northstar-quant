"""P6-WP03 domain-neutral scheduling contracts."""

from __future__ import annotations

import pytest

from northstar_quant.foundation.scheduling import (
    JobRegistry,
    ScheduledJob,
    ScheduledJobKind,
    SchedulingError,
)


@pytest.mark.parametrize("kind", list(ScheduledJobKind))
def test_scheduled_job_supports_each_declared_operational_family(kind: ScheduledJobKind):
    events: list[str] = []
    gate = (lambda: events.append("gate")) if kind is ScheduledJobKind.LIVE else None
    job = ScheduledJob(
        job_id=f"{kind.value}_job",
        kind=kind,
        action=lambda: events.append("action"),
        lifecycle_gate=gate,
    )

    job.run()

    assert events == (["gate", "action"] if kind is ScheduledJobKind.LIVE else ["action"])


def test_live_job_requires_a_gate_and_failed_gate_prevents_its_action():
    with pytest.raises(SchedulingError, match="SCHEDULE_LIVE_GATE_REQUIRED"):
        ScheduledJob("live_job", ScheduledJobKind.LIVE, lambda: None)

    calls: list[str] = []

    def blocked_gate() -> None:
        calls.append("gate")
        raise RuntimeError("calendar unknown")

    job = ScheduledJob(
        "live_job",
        ScheduledJobKind.LIVE,
        lambda: calls.append("action"),
        lifecycle_gate=blocked_gate,
    )
    with pytest.raises(RuntimeError, match="calendar unknown"):
        job.run()
    assert calls == ["gate"]


def test_registry_rejects_duplicate_job_ids_and_exposes_stable_order():
    registry = JobRegistry()
    registry.register(ScheduledJob("research_job", ScheduledJobKind.RESEARCH, lambda: None))
    registry.register(ScheduledJob("data_job", ScheduledJobKind.DATA, lambda: None))

    with pytest.raises(SchedulingError, match="SCHEDULE_JOB_DUPLICATE"):
        registry.register(ScheduledJob("data_job", ScheduledJobKind.DATA, lambda: None))

    assert [job.job_id for job in registry.jobs] == ["data_job", "research_job"]

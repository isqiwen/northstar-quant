"""Domain-neutral scheduled-job contracts with mandatory live safety gates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
import re
from typing import TypeAlias


_JOB_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
JobAction: TypeAlias = Callable[[], object]
LifecycleGate: TypeAlias = Callable[[], None]


class ScheduledJobKind(StrEnum):
    """All supported operational job families."""

    DATA = "data"
    INTELLIGENCE = "intelligence"
    FEATURE = "feature"
    RESEARCH = "research"
    MAINTENANCE = "maintenance"
    LIVE = "live"


class SchedulingError(ValueError):
    """A scheduler configuration is incomplete, ambiguous, or unsafe."""


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    """A scheduler registration with explicit ownership and an optional safety gate."""

    job_id: str
    kind: ScheduledJobKind
    action: JobAction
    lifecycle_gate: LifecycleGate | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, str) or not _JOB_ID_PATTERN.fullmatch(self.job_id):
            raise SchedulingError(
                "SCHEDULE_JOB_ID_INVALID: job_id must be a lowercase stable identifier."
            )
        if not isinstance(self.kind, ScheduledJobKind):
            raise SchedulingError("SCHEDULE_JOB_KIND_INVALID: job kind must be registered.")
        if not callable(self.action):
            raise SchedulingError("SCHEDULE_JOB_ACTION_INVALID: action must be callable.")
        if self.kind is ScheduledJobKind.LIVE and self.lifecycle_gate is None:
            raise SchedulingError(
                "SCHEDULE_LIVE_GATE_REQUIRED: every live job requires a lifecycle gate."
            )
        if self.lifecycle_gate is not None and not callable(self.lifecycle_gate):
            raise SchedulingError("SCHEDULE_LIFECYCLE_GATE_INVALID: lifecycle_gate must be callable.")

    def run(self) -> object:
        """Run the declared guard before its action; a failed guard prevents execution."""

        if self.lifecycle_gate is not None:
            self.lifecycle_gate()
        return self.action()


class JobRegistry:
    """Stable job definitions for a composition root to bind to a scheduler engine."""

    def __init__(self) -> None:
        self._jobs: dict[str, ScheduledJob] = {}

    def register(self, job: ScheduledJob) -> ScheduledJob:
        """Register a unique job ID; replacement requires an explicit new registry."""

        if not isinstance(job, ScheduledJob):
            raise SchedulingError("SCHEDULE_JOB_INVALID: registry accepts ScheduledJob instances only.")
        if job.job_id in self._jobs:
            raise SchedulingError(f"SCHEDULE_JOB_DUPLICATE: job_id {job.job_id} is already registered.")
        self._jobs[job.job_id] = job
        return job

    @property
    def jobs(self) -> tuple[ScheduledJob, ...]:
        """Return jobs in stable ID order for deterministic engine registration."""

        return tuple(self._jobs[job_id] for job_id in sorted(self._jobs))


__all__ = [
    "JobAction",
    "JobRegistry",
    "LifecycleGate",
    "ScheduledJob",
    "ScheduledJobKind",
    "SchedulingError",
]

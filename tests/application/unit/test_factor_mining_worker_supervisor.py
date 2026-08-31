"""Linux hard-bound contracts for the local factor-mining worker guard."""

from __future__ import annotations

import resource
import signal
import time

import pytest

from northstar_quant.application.factor_mining_worker_supervisor import (
    FactorMiningCampaignWorkerLimitExceeded,
    LinuxFactorMiningCampaignWorkerSupervisor,
)
from northstar_quant.research.factor_mining.models import (
    FactorMiningRunnerResourceBudget,
)


def _budget(*, max_wall_clock_seconds: int = 10) -> FactorMiningRunnerResourceBudget:
    return FactorMiningRunnerResourceBudget(
        max_candidates=1,
        max_concurrent_runs=1,
        max_cpu_seconds=10,
        max_memory_bytes=1 << 40,
        max_wall_clock_seconds=max_wall_clock_seconds,
        max_data_rows=1,
        max_artifact_bytes=1,
    )


def test_address_space_limit_never_loosens_an_existing_soft_limit() -> None:
    supervisor = LinuxFactorMiningCampaignWorkerSupervisor()

    assert supervisor._address_space_limit(1_024, (512, 2_048)) == (512, 2_048)


def test_address_space_limit_respects_the_existing_hard_limit() -> None:
    supervisor = LinuxFactorMiningCampaignWorkerSupervisor()

    assert supervisor._address_space_limit(4_096, (resource.RLIM_INFINITY, 2_048)) == (
        2_048,
        2_048,
    )


def test_linux_supervisor_interrupts_a_stalled_worker_and_restores_process_guards() -> None:
    supervisor = LinuxFactorMiningCampaignWorkerSupervisor()
    old_real_timer = signal.getitimer(signal.ITIMER_REAL)
    old_prof_timer = signal.getitimer(signal.ITIMER_PROF)
    old_address_limit = resource.getrlimit(resource.RLIMIT_AS)

    with pytest.raises(
        FactorMiningCampaignWorkerLimitExceeded,
        match="FACTOR_MINING_CAMPAIGN_WORKER_WALL_CLOCK_LIMIT_EXCEEDED",
    ):
        supervisor.run(
            budget=_budget(max_wall_clock_seconds=1),
            started_wall_clock_ns=time.monotonic_ns(),
            started_cpu_ns=time.process_time_ns(),
            operation=lambda: time.sleep(2),
        )

    assert signal.getitimer(signal.ITIMER_REAL) == old_real_timer
    assert signal.getitimer(signal.ITIMER_PROF) == old_prof_timer
    assert resource.getrlimit(resource.RLIMIT_AS) == old_address_limit

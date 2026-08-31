"""Linux-only hard resource guard for one DB-free factor-mining worker attempt.

The durable ledger never receives this capability.  It only surrounds the
trusted local worker's synchronous generator/research calls, using kernel
limits and per-process interval timers.  A limit signal or an unavailable
guard is deliberately an indeterminate worker outcome; the durable caller
must leave its already-reserved request unresolved.
"""

from __future__ import annotations

from collections.abc import Callable
import resource
import signal
import threading
import time
from typing import Protocol, TypeVar

from northstar_quant.foundation.platform_support import require_linux_x86_64
from northstar_quant.research.factor_mining.models import FactorMiningRunnerResourceBudget


__all__ = [
    "FactorMiningCampaignWorkerLimitExceeded",
    "FactorMiningCampaignWorkerSupervisorError",
    "FactorMiningCampaignWorkerSupervisorPort",
    "LinuxFactorMiningCampaignWorkerSupervisor",
]


_T = TypeVar("_T")
_NANOSECONDS_PER_SECOND = 1_000_000_000


class FactorMiningCampaignWorkerSupervisorError(RuntimeError):
    """The worker guard cannot prove a bounded local execution."""


class FactorMiningCampaignWorkerLimitExceeded(FactorMiningCampaignWorkerSupervisorError):
    """A hard worker deadline/resource limit fired or was already exhausted."""


class FactorMiningCampaignWorkerSupervisorPort(Protocol):
    """Execute one trusted DB-free worker operation under remaining limits."""

    def run(
        self,
        *,
        budget: FactorMiningRunnerResourceBudget,
        started_wall_clock_ns: int,
        started_cpu_ns: int,
        operation: Callable[[], _T],
    ) -> _T: ...


class LinuxFactorMiningCampaignWorkerSupervisor:
    """Apply Linux kernel CPU/address-space/timer guards to one operation.

    The adapter supplies fixed attempt-start clocks on every staged call, so
    the total CPU and wall-clock allowance cannot reset between generation,
    discovery, OOS preparation, and publication.  This supervisor refuses to
    run in a non-main thread or while another code path owns either timer;
    clobbering a foreign signal timer would be unsafe.

    ``RLIMIT_AS`` is intentionally conservative relative to an RSS budget:
    address-space exhaustion may reject a valid allocation earlier, but cannot
    permit a memory expansion beyond the declared budget.  The process is the
    DB-free local worker closure; callers must not inject sessions or other
    authority into the operation.
    """

    __slots__ = ()

    def __init__(self) -> None:
        require_linux_x86_64()

    def run(
        self,
        *,
        budget: FactorMiningRunnerResourceBudget,
        started_wall_clock_ns: int,
        started_cpu_ns: int,
        operation: Callable[[], _T],
    ) -> _T:
        if type(budget) is not FactorMiningRunnerResourceBudget:
            raise FactorMiningCampaignWorkerSupervisorError(
                "FACTOR_MINING_CAMPAIGN_WORKER_BUDGET_UNAVAILABLE"
            )
        if not callable(operation):
            raise FactorMiningCampaignWorkerSupervisorError(
                "FACTOR_MINING_CAMPAIGN_WORKER_OPERATION_UNAVAILABLE"
            )
        if (
            isinstance(started_wall_clock_ns, bool)
            or not isinstance(started_wall_clock_ns, int)
            or isinstance(started_cpu_ns, bool)
            or not isinstance(started_cpu_ns, int)
        ):
            raise FactorMiningCampaignWorkerSupervisorError(
                "FACTOR_MINING_CAMPAIGN_WORKER_CLOCK_UNAVAILABLE"
            )
        if threading.current_thread() is not threading.main_thread():
            raise FactorMiningCampaignWorkerSupervisorError(
                "FACTOR_MINING_CAMPAIGN_WORKER_SUPERVISOR_THREAD_UNAVAILABLE"
            )

        remaining_wall_seconds = self._remaining_seconds(
            started_ns=started_wall_clock_ns,
            max_seconds=budget.max_wall_clock_seconds,
            clock_ns=time.monotonic_ns,
            exhausted_code="FACTOR_MINING_CAMPAIGN_WORKER_WALL_CLOCK_LIMIT_EXCEEDED",
        )
        remaining_cpu_seconds = self._remaining_seconds(
            started_ns=started_cpu_ns,
            max_seconds=budget.max_cpu_seconds,
            clock_ns=time.process_time_ns,
            exhausted_code="FACTOR_MINING_CAMPAIGN_WORKER_CPU_LIMIT_EXCEEDED",
        )
        if self._peak_memory_bytes() > budget.max_memory_bytes:
            raise FactorMiningCampaignWorkerLimitExceeded(
                "FACTOR_MINING_CAMPAIGN_WORKER_MEMORY_LIMIT_EXCEEDED"
            )

        old_real_timer = signal.getitimer(signal.ITIMER_REAL)
        old_prof_timer = signal.getitimer(signal.ITIMER_PROF)
        if old_real_timer != (0.0, 0.0) or old_prof_timer != (0.0, 0.0):
            raise FactorMiningCampaignWorkerSupervisorError(
                "FACTOR_MINING_CAMPAIGN_WORKER_TIMER_UNAVAILABLE"
            )
        old_real_handler = signal.getsignal(signal.SIGALRM)
        old_prof_handler = signal.getsignal(signal.SIGPROF)
        old_address_limit = resource.getrlimit(resource.RLIMIT_AS)
        new_address_limit = self._address_space_limit(
            budget.max_memory_bytes,
            old_address_limit,
        )

        def wall_handler(_signal_number: int, _frame: object) -> None:
            raise FactorMiningCampaignWorkerLimitExceeded(
                "FACTOR_MINING_CAMPAIGN_WORKER_WALL_CLOCK_LIMIT_EXCEEDED"
            )

        def cpu_handler(_signal_number: int, _frame: object) -> None:
            raise FactorMiningCampaignWorkerLimitExceeded(
                "FACTOR_MINING_CAMPAIGN_WORKER_CPU_LIMIT_EXCEEDED"
            )

        try:
            resource.setrlimit(resource.RLIMIT_AS, new_address_limit)
            signal.signal(signal.SIGALRM, wall_handler)
            signal.signal(signal.SIGPROF, cpu_handler)
            signal.setitimer(signal.ITIMER_REAL, remaining_wall_seconds)
            signal.setitimer(signal.ITIMER_PROF, remaining_cpu_seconds)
            result = operation()
            self._require_within_remaining_budget(
                budget=budget,
                started_wall_clock_ns=started_wall_clock_ns,
                started_cpu_ns=started_cpu_ns,
            )
            return result
        finally:
            # Timers and the soft address-space cap are process-global.  The
            # guard either restores the exact pre-attempt values or propagates
            # an error; it never leaves an accidental permanent restriction.
            signal.setitimer(signal.ITIMER_REAL, *old_real_timer)
            signal.setitimer(signal.ITIMER_PROF, *old_prof_timer)
            signal.signal(signal.SIGALRM, old_real_handler)
            signal.signal(signal.SIGPROF, old_prof_handler)
            resource.setrlimit(resource.RLIMIT_AS, old_address_limit)

    @staticmethod
    def _remaining_seconds(
        *,
        started_ns: int,
        max_seconds: int,
        clock_ns: Callable[[], int],
        exhausted_code: str,
    ) -> float:
        elapsed_ns = clock_ns() - started_ns
        if elapsed_ns < 0:
            raise FactorMiningCampaignWorkerSupervisorError(
                "FACTOR_MINING_CAMPAIGN_WORKER_CLOCK_UNAVAILABLE"
            )
        remaining_ns = max_seconds * _NANOSECONDS_PER_SECOND - elapsed_ns
        if remaining_ns <= 0:
            raise FactorMiningCampaignWorkerLimitExceeded(exhausted_code)
        return remaining_ns / _NANOSECONDS_PER_SECOND

    @staticmethod
    def _address_space_limit(
        max_memory_bytes: int,
        previous: tuple[int, int],
    ) -> tuple[int, int]:
        previous_soft, previous_hard = previous
        # This guard may tighten the process limit for one worker stage, but
        # it must never silently relax a limit that the host or an enclosing
        # supervisor already imposed.  ``RLIM_INFINITY`` is the only value
        # that does not constrain the minimum.
        caps = [max_memory_bytes]
        if previous_soft != resource.RLIM_INFINITY:
            caps.append(previous_soft)
        if previous_hard != resource.RLIM_INFINITY:
            caps.append(previous_hard)
        return (min(caps), previous_hard)

    @staticmethod
    def _peak_memory_bytes() -> int:
        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if peak_rss < 0:
            raise FactorMiningCampaignWorkerSupervisorError(
                "FACTOR_MINING_CAMPAIGN_WORKER_MEMORY_MEASUREMENT_UNAVAILABLE"
            )
        return peak_rss * 1_024

    def _require_within_remaining_budget(
        self,
        *,
        budget: FactorMiningRunnerResourceBudget,
        started_wall_clock_ns: int,
        started_cpu_ns: int,
    ) -> None:
        self._remaining_seconds(
            started_ns=started_wall_clock_ns,
            max_seconds=budget.max_wall_clock_seconds,
            clock_ns=time.monotonic_ns,
            exhausted_code="FACTOR_MINING_CAMPAIGN_WORKER_WALL_CLOCK_LIMIT_EXCEEDED",
        )
        self._remaining_seconds(
            started_ns=started_cpu_ns,
            max_seconds=budget.max_cpu_seconds,
            clock_ns=time.process_time_ns,
            exhausted_code="FACTOR_MINING_CAMPAIGN_WORKER_CPU_LIMIT_EXCEEDED",
        )
        if self._peak_memory_bytes() > budget.max_memory_bytes:
            raise FactorMiningCampaignWorkerLimitExceeded(
                "FACTOR_MINING_CAMPAIGN_WORKER_MEMORY_LIMIT_EXCEEDED"
            )

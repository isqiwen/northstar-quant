"""One synchronous trading event boundary shared by historical and broker runners."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from threading import current_thread

from northstar_quant.messaging import DispatchFailed, Endpoint, MessageBus, Topic


class KernelState(StrEnum):
    READY = "READY"
    RUNNING = "RUNNING"
    FAULTED = "FAULTED"
    CLOSED = "CLOSED"


class FailurePolicy(StrEnum):
    # Only an adapter which rolls back its whole event may opt in to retry.
    ROLLBACK = "ROLLBACK"
    HALT = "HALT"


@dataclass(frozen=True, slots=True)
class KernelStatus:
    state: KernelState
    processed: int
    failures: int


class TradingKernel[M, R]:
    """Own ingress, routing and failure lifecycle, never account or order authority.

    The installed processor composes the owning market/strategy/risk/execution/
    accounting operations and their transaction. Notifications follow its commit.
    Broker processors halt on *any* failure: a committed fact cannot be undone
    because a later projection failed. Research processors may explicitly promise
    whole-event rollback. Neither policy retries, reconnects or authorizes sending.

    Counters describe this process's dispatch attempts, not durable event IDs.
    Runners dispose this kernel and rebuild from verified facts after a fault.
    """

    def __init__(
        self,
        endpoint: Endpoint[M, R],
        process: Callable[[M], R],
        *,
        failure_policy: FailurePolicy = FailurePolicy.HALT,
        completed: Topic[R] | None = None,
    ) -> None:
        if not isinstance(failure_policy, FailurePolicy):
            raise ValueError("kernel requires an explicit failure policy")
        self._owner, self._pid = current_thread(), os.getpid()
        self._bus = MessageBus()
        self._bus.register(endpoint, process)
        self._endpoint = endpoint
        self._completed = completed
        self._policy = failure_policy
        self._state = KernelState.READY
        self._active = False
        self._processed = self._failures = 0

    def _check_owner(self) -> None:
        if current_thread() is not self._owner or os.getpid() != self._pid:
            raise RuntimeError("trading kernel belongs to another core thread or process")

    @property
    def status(self) -> KernelStatus:
        self._check_owner()
        return KernelStatus(self._state, self._processed, self._failures)

    def start(self) -> None:
        self._check_owner()
        if self._state != KernelState.READY:
            raise RuntimeError("only a ready trading kernel can start")
        self._state = KernelState.RUNNING

    def subscribe[T](self, topic: Topic[T], handler: Callable[[T], None]) -> None:
        self._check_owner()
        if self._state not in {KernelState.READY, KernelState.RUNNING} or self._active:
            raise RuntimeError("cannot change subscriptions in an active or faulted kernel")
        self._bus.subscribe(topic, handler)

    def advance(self, message: M) -> R:
        self._check_owner()
        if self._state != KernelState.RUNNING:
            raise RuntimeError("trading kernel is not running (closed or faulted)")
        if self._active:
            raise RuntimeError("a trading event cannot reenter the kernel")
        self._active = True
        committed = False
        try:
            result = self._bus.request(self._endpoint, message)
            committed = True
            self._processed += 1
            # None is the processor's duplicate/no-op result, not a new fact.
            if self._completed is not None and result is not None:
                self._bus.publish(self._completed, result)
            return result
        except BaseException as error:
            self._failures += 1
            if (
                self._policy == FailurePolicy.HALT
                or committed
                or isinstance(error, DispatchFailed)
                or not isinstance(error, Exception)
            ):
                self._state = KernelState.FAULTED
            raise
        finally:
            self._active = False

    def close(self) -> None:
        self._check_owner()
        if self._active:
            raise RuntimeError("cannot close an active trading event")
        self._bus.close()
        self._state = KernelState.CLOSED

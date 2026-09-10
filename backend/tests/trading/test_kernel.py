"""Ingress lifecycle must not allow work after an uncertain or partially emitted event."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pytest

from northstar_quant.messaging import DispatchFailed, Endpoint, Topic
from northstar_quant.trading.environment import Environment
from northstar_quant.trading.kernel import FailurePolicy, KernelState, TradingKernel


@dataclass(frozen=True)
class Event:
    value: int


INPUT = Endpoint[Event, Event]("test.input", Event)
COMPLETED = Topic("test.completed", Event)


def test_ingress_requires_start_and_disposal_cannot_be_rearmed():
    seen = []
    kernel = TradingKernel(
        INPUT, lambda event: seen.append(event) or event, environment=Environment.BACKTEST
    )
    with pytest.raises(RuntimeError):
        kernel.advance(Event(1))
    kernel.start()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError, match="another core thread"):
            pool.submit(kernel.advance, Event(2)).result()
        with pytest.raises(RuntimeError, match="another core thread"):
            pool.submit(kernel.close).result()
    assert kernel.advance(Event(3)) == Event(3)
    kernel.close()
    with pytest.raises(RuntimeError):
        kernel.start()
    with pytest.raises(RuntimeError):
        kernel.advance(Event(4))
    assert seen == [Event(3)]


@pytest.mark.parametrize("policy", list(FailurePolicy))
def test_only_whole_event_rollback_may_retry_and_other_instances_remain_independent(policy):
    attempts = []

    def process(event):
        attempts.append(event)
        if len(attempts) == 1:
            raise ValueError("adapter failure")
        return event

    kernel = TradingKernel(INPUT, process, failure_policy=policy, environment=Environment.BACKTEST)
    other = TradingKernel(INPUT, lambda event: event, environment=Environment.BACKTEST)
    kernel.start()
    other.start()
    with pytest.raises(ValueError):
        kernel.advance(Event(1))
    assert other.advance(Event(2)) == Event(2)
    if policy == FailurePolicy.HALT:
        with pytest.raises(RuntimeError, match="faulted"):
            kernel.advance(Event(1))
        assert attempts == [Event(1)]
    else:
        assert kernel.advance(Event(1)) == Event(1)
    assert kernel.status.failures == 1
    kernel.close()
    other.close()


def test_completion_observer_fault_never_undoes_committed_work_or_permits_retry():
    facts, delivered = [], []
    kernel = TradingKernel(
        INPUT,
        lambda event: facts.append(event) or event,
        failure_policy=FailurePolicy.ROLLBACK,
        completed=COMPLETED,
        environment=Environment.BACKTEST,
    )
    kernel.subscribe(COMPLETED, delivered.append)

    def bad_observer(event):
        # Completion is part of the active event, not a place to enter another one.
        kernel.advance(Event(2))

    kernel.subscribe(COMPLETED, bad_observer)
    kernel.start()
    with pytest.raises(DispatchFailed):
        kernel.advance(Event(1))
    assert facts == delivered == [Event(1)]
    assert kernel.status.state == KernelState.FAULTED
    assert kernel.status.processed == 1
    with pytest.raises(RuntimeError):
        kernel.advance(Event(1))
    kernel.close()


def test_aborted_computation_is_faulted_even_with_rollback_policy():
    def interrupted(event):
        raise KeyboardInterrupt

    kernel = TradingKernel(
        INPUT, interrupted, failure_policy=FailurePolicy.ROLLBACK, environment=Environment.BACKTEST
    )
    kernel.start()
    with pytest.raises(KeyboardInterrupt):
        kernel.advance(Event(1))
    assert kernel.status.state == KernelState.FAULTED
    with pytest.raises(RuntimeError):
        kernel.advance(Event(1))
    kernel.close()


@pytest.mark.parametrize("environment", [Environment.SANDBOX, Environment.LIVE])
def test_external_execution_never_allows_rollback_policy(environment):
    with pytest.raises(ValueError, match="cannot roll back"):
        TradingKernel(
            INPUT,
            lambda event: event,
            environment=environment,
            failure_policy=FailurePolicy.ROLLBACK,
        )
    kernel = TradingKernel(INPUT, lambda event: event, environment=environment)
    assert kernel.status.environment is environment
    kernel.close()

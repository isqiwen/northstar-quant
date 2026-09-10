"""Observable ordering, ownership and failure boundaries of the shared bus."""

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from textwrap import dedent

import pytest

from northstar_quant.messaging import DispatchFailed, Endpoint, MessageBus, Topic


@dataclass(frozen=True)
class Value:
    value: int


@dataclass(frozen=True)
class Other:
    value: int


COMMAND: Endpoint[Value, int] = Endpoint("test.command", Value)
EVENT = Topic("test.event", Value)


def test_single_owner_exact_types_and_instance_isolation():
    first, second = MessageBus(), MessageBus()
    first.register(COMMAND, lambda message: message.value + 1)
    second.register(COMMAND, lambda message: message.value + 10)
    assert first.request(COMMAND, Value(1)) == 2
    assert second.request(COMMAND, Value(1)) == 11
    with pytest.raises(ValueError, match="already"):
        first.register(COMMAND, lambda message: 0)
    with pytest.raises(TypeError, match="exact type"):
        first.request(COMMAND, Other(1))
    with pytest.raises(ValueError, match="matching owner"):
        first.request(Endpoint("test.command", Other), Other(1))
    first.unregister(COMMAND)
    with pytest.raises(ValueError, match="owner"):
        first.request(COMMAND, Value(1))
    first.close()
    with pytest.raises(RuntimeError, match="closed"):
        first.request(COMMAND, Value(1))
    assert second.request(COMMAND, Value(1)) == 11


def test_synchronous_order_nested_dispatch_and_unsubscribe():
    bus = MessageBus()
    seen = []
    nested = Topic("test.nested", Value)
    bus.subscribe(nested, lambda event: seen.append(("nested", event.value)))

    def first(event):
        seen.append(("first", event.value))
        bus.publish(nested, event)

    def second(event):
        seen.append(("second", event.value))

    bus.subscribe(EVENT, first)
    bus.subscribe(EVENT, second)
    bus.publish(EVENT, Value(1))
    assert seen == [("first", 1), ("nested", 1), ("second", 1)]
    bus.unsubscribe(EVENT, first)
    bus.publish(EVENT, Value(2))
    assert seen[-1] == ("second", 2)
    with pytest.raises(ValueError, match="already"):
        bus.subscribe(EVENT, second)
    with pytest.raises(TypeError, match="another message type"):
        bus.publish(Topic("test.event", Other), Other(2))


def test_partial_notification_faults_bus_without_retry_or_payload_in_message():
    bus = MessageBus()
    seen = []
    bus.subscribe(EVENT, lambda event: seen.append(event))

    def fail(event):
        raise ValueError("private consumer detail")

    bus.subscribe(EVENT, fail)
    bus.subscribe(EVENT, lambda event: seen.append(event))
    with pytest.raises(DispatchFailed) as error:
        bus.publish(EVENT, Value(1))
    assert "private" not in str(error.value)
    assert len(seen) == 1
    with pytest.raises(RuntimeError, match="faulted"):
        bus.publish(EVENT, Value(1))
    bus.close()


@pytest.mark.parametrize("operation", ["recursive", "subscribe", "close"])
def test_callback_cannot_cycle_or_change_routing(operation):
    bus = MessageBus()

    def handler(event):
        if operation == "recursive":
            bus.publish(EVENT, event)
        elif operation == "subscribe":
            bus.subscribe(EVENT, lambda value: None)
        else:
            bus.close()

    bus.subscribe(EVENT, handler)
    with pytest.raises(DispatchFailed):
        bus.publish(EVENT, Value(1))


def test_endpoint_failure_propagates_and_owner_can_retry_after_rollback():
    bus = MessageBus()
    calls = []

    def fail_once(message):
        calls.append(message)
        if len(calls) == 1:
            raise ValueError("rollback by owner")
        return message.value

    bus.register(COMMAND, fail_once)
    with pytest.raises(ValueError, match="rollback"):
        bus.request(COMMAND, Value(1))
    assert len(calls) == 1
    assert bus.request(COMMAND, Value(1)) == 1
    bus.unregister(COMMAND)
    bus.register(COMMAND, lambda value: bus.request(COMMAND, value))
    with pytest.raises(RuntimeError, match="recursive"):
        bus.request(COMMAND, Value(1))


def test_bus_cannot_migrate_to_another_thread_or_fork():
    bus = MessageBus()
    bus.register(COMMAND, lambda value: value.value)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError, match="another core"):
            pool.submit(bus.request, COMMAND, Value(1)).result(timeout=3)
    # Fork inside a fresh interpreter, not pytest's already threaded process.
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            dedent("""
            import os
            from dataclasses import dataclass
            from northstar_quant.messaging import Endpoint, MessageBus
            @dataclass(frozen=True)
            class Value:
                value: int
            endpoint = Endpoint('child.read', Value)
            bus = MessageBus()
            bus.register(endpoint, lambda value: value.value)
            pid = os.fork()
            if pid == 0:
                try:
                    bus.request(endpoint, Value(1))
                except RuntimeError as error:
                    os._exit(0 if 'another core' in str(error) else 2)
                os._exit(1)
            _, status = os.waitpid(pid, 0)
            raise SystemExit(os.waitstatus_to_exitcode(status))
        """),
        ],
        timeout=5,
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stderr
    assert bus.request(COMMAND, Value(1)) == 1


def test_copied_broker_fields_remain_immutable_across_consumers():
    from northstar_quant.broker.events import BrokerEvent

    original = {"TradingDay": "20260910", "UserID": "account"}
    event = BrokerEvent(1, "TD", "OnRspUserLogin", 1, True, "2026-09-10T01:00:00Z", 0, original)
    original["UserID"] = "changed"
    bus = MessageBus()
    topic = Topic("broker.observed", BrokerEvent)
    seen = []

    def first(value):
        with pytest.raises(TypeError):
            value.data["UserID"] = "changed"
        value.to_dict()["data"]["UserID"] = "changed"

    bus.subscribe(topic, first)
    bus.subscribe(topic, lambda value: seen.append(value.to_dict()))
    bus.publish(topic, event)
    assert seen[0]["data"]["UserID"] == "account"

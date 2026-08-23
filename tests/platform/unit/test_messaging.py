"""P6-WP02 contracts for the typed process-local notification bus."""

from __future__ import annotations

from datetime import UTC, datetime
from math import inf
from uuid import uuid4

import pytest

from northstar_quant.platform.messaging import (
    DuplicateMessageError,
    InMemoryMessageBus,
    MessageBusError,
    MessageDeliveryError,
    MessageEnvelope,
    MessageTopic,
)


def _message(*, message_id=None, topic=MessageTopic.DATA_INGESTED) -> MessageEnvelope:
    return MessageEnvelope(
        message_id=message_id or uuid4(),
        topic=topic,
        occurred_at=datetime(2026, 8, 22, 9, 0, tzinfo=UTC),
        producer="data_platform.publisher",
        payload={"dataset_version_id": "dataset-1", "lineage": {"source": "fixture"}},
    )


def test_message_bus_delivers_messages_in_fifo_order_to_all_subscribers():
    bus = InMemoryMessageBus()
    received_by_first: list[str] = []
    received_by_second: list[str] = []
    bus.subscribe(MessageTopic.DATA_INGESTED, lambda event: received_by_first.append(str(event.message_id)))
    bus.subscribe(MessageTopic.DATA_INGESTED, lambda event: received_by_second.append(str(event.message_id)))
    first = _message()
    second = _message()

    bus.publish(first)
    bus.publish(second)

    assert bus.dispatch_all() == (first, second)
    assert received_by_first == [str(first.message_id), str(second.message_id)]
    assert received_by_second == received_by_first
    assert bus.pending_count == 0


def test_message_bus_rejects_duplicate_message_ids_without_requeueing():
    bus = InMemoryMessageBus()
    bus.subscribe(MessageTopic.DATA_INGESTED, lambda event: None)
    message = _message()
    bus.publish(message)

    with pytest.raises(DuplicateMessageError, match="MESSAGE_DUPLICATE"):
        bus.publish(message)

    assert bus.pending_count == 1


def test_message_bus_rejects_publish_when_no_consumer_is_registered():
    bus = InMemoryMessageBus()

    with pytest.raises(MessageBusError, match="MESSAGE_NO_SUBSCRIBER"):
        bus.publish(_message())

    assert bus.pending_count == 0


def test_message_bus_keeps_a_failed_delivery_queued_for_explicit_retry():
    bus = InMemoryMessageBus()
    calls: list[str] = []
    should_fail = True

    def flaky_consumer(event: MessageEnvelope) -> None:
        nonlocal should_fail
        calls.append(str(event.message_id))
        if should_fail:
            should_fail = False
            raise RuntimeError("temporary consumer failure")

    bus.subscribe(MessageTopic.RISK_BLOCKED, flaky_consumer)
    message = _message(topic=MessageTopic.RISK_BLOCKED)
    bus.publish(message)

    with pytest.raises(MessageDeliveryError, match="MESSAGE_DELIVERY_FAILED"):
        bus.dispatch_next()

    assert bus.pending_count == 1
    assert bus.dispatch_next() == message
    assert calls == [str(message.message_id), str(message.message_id)]
    assert bus.pending_count == 0


def test_message_envelope_freezes_json_payload_and_requires_registered_topic():
    message = _message()

    with pytest.raises(TypeError):
        message.payload["dataset_version_id"] = "mutated"  # type: ignore[index]
    with pytest.raises(TypeError):
        message.payload["lineage"]["source"] = "mutated"  # type: ignore[index]
    with pytest.raises(MessageBusError, match="MESSAGE_TOPIC_UNKNOWN"):
        MessageEnvelope.create(topic="order.submitted", producer="test")  # type: ignore[arg-type]


def test_message_bus_rejects_invalid_dispatch_limits_and_duplicate_subscriptions():
    bus = InMemoryMessageBus()

    def consumer(event: MessageEnvelope) -> None:
        del event

    bus.subscribe(MessageTopic.ORDER_UPDATED, consumer)
    with pytest.raises(MessageBusError, match="MESSAGE_SUBSCRIPTION_DUPLICATE"):
        bus.subscribe(MessageTopic.ORDER_UPDATED, consumer)
    with pytest.raises(MessageBusError, match="MESSAGE_DISPATCH_LIMIT_INVALID"):
        bus.dispatch_all(max_messages=0)
    with pytest.raises(MessageBusError, match="MESSAGE_PAYLOAD_VALUE_INVALID"):
        MessageEnvelope.create(
            topic=MessageTopic.DATA_VALIDATED,
            producer="test",
            payload={"quality_score": inf},
        )

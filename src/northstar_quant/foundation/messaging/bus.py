"""Typed in-process messaging with explicit at-least-once delivery semantics.

This module is intentionally a small process-local queue.  It is a notification
boundary, not an order-routing or risk-approval path: consumers must remain
idempotent and no message can authorize a broker action.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from threading import RLock
from types import MappingProxyType
from typing import TypeAlias
from uuid import UUID, uuid4


class MessageTopic(StrEnum):
    """The version-one cross-domain notification topics."""

    DATA_INGESTED = "data.ingested"
    DATA_VALIDATED = "data.validated"
    EVENT_EXTRACTED = "event.extracted"
    EVENT_MERGED = "event.merged"
    FEATURE_GENERATED = "feature.generated"
    RESEARCH_COMPLETED = "research.completed"
    PORTFOLIO_APPROVED = "portfolio.approved"
    RISK_BLOCKED = "risk.blocked"
    ORDER_UPDATED = "order.updated"
    RECONCILIATION_FAILED = "reconciliation.failed"


class MessageBusError(ValueError):
    """Base error for invalid or unsafe in-process messaging operations."""


class DuplicateMessageError(MessageBusError):
    """A message ID has already been accepted by this process-local bus."""


class MessageDeliveryError(RuntimeError):
    """A consumer failed; the message remains at the head of the queue."""

    def __init__(self, envelope: MessageEnvelope, handler_name: str) -> None:
        super().__init__(
            "MESSAGE_DELIVERY_FAILED: "
            f"{envelope.topic} message {envelope.message_id} was not acknowledged by {handler_name}."
        )
        self.envelope = envelope
        self.handler_name = handler_name


JsonScalar: TypeAlias = str | int | float | bool | None
FrozenPayloadValue: TypeAlias = (
    JsonScalar | tuple["FrozenPayloadValue", ...] | Mapping[str, "FrozenPayloadValue"]
)
MessageHandler: TypeAlias = Callable[["MessageEnvelope"], None]


@dataclass(frozen=True, slots=True)
class MessageEnvelope:
    """Immutable versioned notification payload accepted by the local queue."""

    message_id: UUID
    topic: MessageTopic
    occurred_at: datetime
    producer: str
    payload: Mapping[str, object] = field(default_factory=dict)
    correlation_id: UUID | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.message_id, UUID):
            raise MessageBusError("MESSAGE_ID_INVALID: message_id must be a UUID.")
        if not isinstance(self.topic, MessageTopic):
            raise MessageBusError("MESSAGE_TOPIC_UNKNOWN: topic must be a registered MessageTopic.")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise MessageBusError("MESSAGE_TIME_INVALID: occurred_at must include an offset.")
        if not isinstance(self.producer, str) or not self.producer.strip():
            raise MessageBusError("MESSAGE_PRODUCER_INVALID: producer must be a non-empty string.")
        if self.schema_version != 1:
            raise MessageBusError("MESSAGE_SCHEMA_VERSION_UNSUPPORTED: only schema version 1 is accepted.")
        if self.correlation_id is not None and not isinstance(self.correlation_id, UUID):
            raise MessageBusError("MESSAGE_CORRELATION_ID_INVALID: correlation_id must be a UUID or null.")

        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(UTC))
        object.__setattr__(self, "producer", self.producer.strip())
        object.__setattr__(self, "payload", _freeze_payload(self.payload))

    @classmethod
    def create(
        cls,
        *,
        topic: MessageTopic,
        producer: str,
        payload: Mapping[str, object] | None = None,
        correlation_id: UUID | None = None,
        occurred_at: datetime | None = None,
    ) -> MessageEnvelope:
        """Create a fresh version-one message with a UTC event timestamp."""

        return cls(
            message_id=uuid4(),
            topic=topic,
            occurred_at=occurred_at or datetime.now(UTC),
            producer=producer,
            payload={} if payload is None else payload,
            correlation_id=correlation_id,
        )


@dataclass(frozen=True, slots=True)
class Subscription:
    """A registered in-process consumer, exposed for audit and test inspection."""

    topic: MessageTopic
    handler_name: str


class InMemoryMessageBus:
    """FIFO local queue with explicit duplicate and delivery-failure behavior.

    A consumer exception intentionally leaves the message queued.  Retrying
    ``dispatch_next`` can redeliver to consumers that already completed, so each
    consumer must be idempotent by ``message_id``.  This implementation is not
    durable and must not be used as a substitute for any trading safety gate.
    """

    def __init__(self) -> None:
        self._handlers: dict[MessageTopic, list[MessageHandler]] = defaultdict(list)
        self._pending: deque[MessageEnvelope] = deque()
        self._accepted_message_ids: set[UUID] = set()
        self._lock = RLock()

    def subscribe(self, topic: MessageTopic, handler: MessageHandler) -> Subscription:
        """Register one handler for a known topic; duplicate registration is rejected."""

        if not isinstance(topic, MessageTopic):
            raise MessageBusError("MESSAGE_TOPIC_UNKNOWN: subscriptions require a registered MessageTopic.")
        if not callable(handler):
            raise MessageBusError("MESSAGE_HANDLER_INVALID: handler must be callable.")
        with self._lock:
            if handler in self._handlers[topic]:
                raise MessageBusError(
                    "MESSAGE_SUBSCRIPTION_DUPLICATE: a handler can subscribe to a topic only once."
                )
            self._handlers[topic].append(handler)
        return Subscription(topic=topic, handler_name=_handler_name(handler))

    def publish(self, envelope: MessageEnvelope) -> None:
        """Accept an immutable message once and append it to the FIFO queue."""

        if not isinstance(envelope, MessageEnvelope):
            raise MessageBusError("MESSAGE_ENVELOPE_INVALID: publish requires a MessageEnvelope.")
        with self._lock:
            if not self._handlers[envelope.topic]:
                raise MessageBusError(
                    "MESSAGE_NO_SUBSCRIBER: a topic must have a registered consumer before publishing."
                )
            if envelope.message_id in self._accepted_message_ids:
                raise DuplicateMessageError(
                    f"MESSAGE_DUPLICATE: message {envelope.message_id} was already accepted."
                )
            self._accepted_message_ids.add(envelope.message_id)
            self._pending.append(envelope)

    def dispatch_next(self) -> MessageEnvelope | None:
        """Deliver the oldest queued message, retaining it when a handler fails."""

        with self._lock:
            if not self._pending:
                return None
            envelope = self._pending[0]
            for handler in tuple(self._handlers[envelope.topic]):
                try:
                    handler(envelope)
                except Exception as exc:
                    # This is the deliberate consumer boundary: no acknowledgement
                    # is written and FIFO progress stops until an explicit retry.
                    raise MessageDeliveryError(envelope, _handler_name(handler)) from exc
            self._pending.popleft()
            return envelope

    def dispatch_all(self, *, max_messages: int | None = None) -> tuple[MessageEnvelope, ...]:
        """Deliver pending messages in order, optionally bounded for a worker tick."""

        if max_messages is not None and (
            isinstance(max_messages, bool) or not isinstance(max_messages, int) or max_messages < 1
        ):
            raise MessageBusError("MESSAGE_DISPATCH_LIMIT_INVALID: max_messages must be a positive integer.")

        delivered: list[MessageEnvelope] = []
        while max_messages is None or len(delivered) < max_messages:
            envelope = self.dispatch_next()
            if envelope is None:
                return tuple(delivered)
            delivered.append(envelope)
        return tuple(delivered)

    @property
    def pending_count(self) -> int:
        """Return the number of messages not yet acknowledged by all consumers."""

        with self._lock:
            return len(self._pending)

    @property
    def subscriptions(self) -> tuple[Subscription, ...]:
        """Return a stable snapshot of current topic-to-consumer registrations."""

        with self._lock:
            return tuple(
                Subscription(topic=topic, handler_name=_handler_name(handler))
                for topic, handlers in sorted(self._handlers.items(), key=lambda item: item[0].value)
                for handler in handlers
            )


def _freeze_payload(value: Mapping[str, object]) -> Mapping[str, FrozenPayloadValue]:
    if not isinstance(value, Mapping):
        raise MessageBusError("MESSAGE_PAYLOAD_INVALID: payload must be a mapping.")
    return MappingProxyType(
        {
            _payload_key(key): _freeze_payload_value(item, field=str(key))
            for key, item in value.items()
        }
    )


def _freeze_payload_value(value: object, *, field: str) -> FrozenPayloadValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not isfinite(value):
            raise MessageBusError(
                f"MESSAGE_PAYLOAD_VALUE_INVALID: payload.{field} must be a finite JSON number."
            )
        return value
    if isinstance(value, Mapping):
        return _freeze_payload(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_payload_value(item, field=f"{field}[]") for item in value)
    raise MessageBusError(
        f"MESSAGE_PAYLOAD_VALUE_INVALID: payload.{field} must be a JSON-compatible value."
    )


def _payload_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MessageBusError("MESSAGE_PAYLOAD_KEY_INVALID: payload keys must be non-empty strings.")
    return value


def _handler_name(handler: MessageHandler) -> str:
    return getattr(handler, "__qualname__", getattr(handler, "__name__", type(handler).__name__))


__all__ = [
    "DuplicateMessageError",
    "InMemoryMessageBus",
    "MessageBusError",
    "MessageDeliveryError",
    "MessageEnvelope",
    "MessageHandler",
    "MessageTopic",
    "Subscription",
]

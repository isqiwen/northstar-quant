"""Synchronous, instance-local routing; persistence remains with business owners."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from threading import current_thread
from typing import TypeVar, cast

M = TypeVar("M")
R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class Endpoint[M, R]:
    """A command/query delivered to exactly one owner, returning its result."""

    name: str
    message_type: type[M]


@dataclass(frozen=True, slots=True)
class Topic[M]:
    """An exact topic for immutable values, delivered in subscription order."""

    name: str
    message_type: type[M]


class DispatchFailed(RuntimeError):
    """An event consumer failed; earlier consumers may already have observed it."""


class MessageBus:
    """One core thread and process, no worker, retries, buffering or global registry.

    Request failures propagate unchanged: the endpoint owns its transaction.
    Event-consumer failure faults the bus, since a partially delivered event must
    not silently be retried. Rebuild projections from committed facts on recovery.
    Nested dispatch is synchronous/depth-first; cycles and subscription changes
    during dispatch are rejected. Messages must be deeply immutable owner values.
    """

    def __init__(self) -> None:
        self._owner = current_thread()
        self._pid = os.getpid()
        self._closed = False
        self._faulted = False
        self._endpoints: dict[str, tuple[object, Callable[[object], object]]] = {}
        self._topics: dict[str, tuple[object, list[Callable[[object], None]]]] = {}
        self._active: set[tuple[str, str]] = set()

    def _check(self, *, changing: bool = False) -> None:
        if current_thread() is not self._owner or os.getpid() != self._pid:
            raise RuntimeError("message bus belongs to another core thread or process")
        if self._closed or self._faulted:
            raise RuntimeError("message bus is closed or faulted")
        if changing and self._active:
            raise RuntimeError("cannot change routes during dispatch")

    @staticmethod
    def _route(name: str, message_type: type[object]) -> None:
        if not name or len(name) > 128 or any(c.isspace() or c in "*?" for c in name):
            raise ValueError("message route requires a bounded exact name")
        params = getattr(message_type, "__dataclass_params__", None)
        if params is None or not params.frozen:
            raise TypeError("message routes require frozen dataclass values")

    def register(self, endpoint: Endpoint[M, R], handler: Callable[[M], R]) -> None:
        self._check(changing=True)
        self._route(endpoint.name, endpoint.message_type)
        if endpoint.name in self._endpoints:
            raise ValueError("endpoint already has an owner")
        self._endpoints[endpoint.name] = (endpoint, cast(Callable[[object], object], handler))

    def unregister(self, endpoint: Endpoint[M, R]) -> None:
        self._check(changing=True)
        entry = self._endpoints.get(endpoint.name)
        if entry is None or entry[0] != endpoint:
            raise ValueError("endpoint is not registered")
        del self._endpoints[endpoint.name]

    def subscribe(self, topic: Topic[M], handler: Callable[[M], None]) -> None:
        self._check(changing=True)
        self._route(topic.name, topic.message_type)
        entry = self._topics.setdefault(topic.name, (topic, []))
        if entry[0] != topic:
            raise TypeError("topic name is already bound to another message type")
        callback = cast(Callable[[object], None], handler)
        if callback in entry[1]:
            raise ValueError("handler is already subscribed")
        entry[1].append(callback)

    def unsubscribe(self, topic: Topic[M], handler: Callable[[M], None]) -> None:
        self._check(changing=True)
        entry = self._topics.get(topic.name)
        if entry is None or entry[0] != topic:
            raise ValueError("topic is not registered")
        entry[1].remove(cast(Callable[[object], None], handler))

    def _enter(self, kind: str, name: str, expected: type[M], message: M) -> tuple[str, str]:
        self._check()
        if type(message) is not expected:
            raise TypeError("message does not match the route's exact type")
        key = (kind, name)
        if key in self._active:
            raise RuntimeError("recursive message route is forbidden")
        self._active.add(key)
        return key

    def request(self, endpoint: Endpoint[M, R], message: M) -> R:
        self._check()
        entry = self._endpoints.get(endpoint.name)
        if entry is None or entry[0] != endpoint:
            raise ValueError("endpoint has no matching owner")
        key = self._enter("request", endpoint.name, endpoint.message_type, message)
        try:
            result = cast(R, entry[1](message))
            self._check()
            return result
        finally:
            self._active.remove(key)

    def publish(self, topic: Topic[M], message: M) -> None:
        self._check()
        entry = self._topics.get(topic.name)
        if entry is not None and entry[0] != topic:
            raise TypeError("topic name is already bound to another message type")
        key = self._enter("publish", topic.name, topic.message_type, message)
        try:
            for handler in () if entry is None else entry[1]:
                handler(message)
                self._check()
        except Exception as error:
            self._faulted = True
            # Do not include payloads or consumer exception text in diagnostics.
            raise DispatchFailed(f"event consumer failed on {topic.name}") from error
        finally:
            self._active.remove(key)

    def close(self) -> None:
        if current_thread() is not self._owner or os.getpid() != self._pid:
            raise RuntimeError("message bus belongs to another core thread or process")
        if self._active:
            raise RuntimeError("cannot close during dispatch")
        self._closed = True
        self._endpoints.clear()
        self._topics.clear()

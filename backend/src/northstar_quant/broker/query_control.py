"""Repeat the bound query plan on the receiver's existing native connection."""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID


class QueryRefresh:
    """A bounded native query window; order polling continues during each wait.

    Completion means responses arrived, not account reconciliation. Query request
    IDs stay below the order namespace. Original callbacks keep their stream IDs.
    """

    def __init__(
        self, receiver: Any, trader: Any, queries: list[tuple[str, str, Any]], *, interval: float
    ) -> None:
        self.receiver, self.trader, self.queries = receiver, trader, queries
        self.interval = interval
        self.next_request = 1000
        self.seen: set[UUID] = set()
        self.active = False

    def __call__(self, identifier: UUID) -> None:
        receiver = self.receiver
        if not isinstance(identifier, UUID):
            raise ValueError("query identity must be a UUID")
        if identifier in self.seen:
            return  # A duplicate handoff cannot repeat the native queries.
        if len(self.seen) >= 10000:
            raise ValueError("query identity capacity exhausted")
        self.seen.add(identifier)
        if self.active or self.next_request + len(self.queries) > 100_000:
            receiver.event(
                "TD",
                "AccountQueryFinished",
                {
                    "query_id": str(identifier),
                    "status": "REJECTED",
                    "reason": "QUERY_BUSY_OR_LIMIT",
                },
            )
            return
        if receiver.event("TD", "AccountQueryStarted", {"query_id": str(identifier)}) is None:
            return
        self.active = True
        deadline = receiver.deadline
        receiver.deadline = min(deadline, time.monotonic() + 30)
        completed = False
        try:
            for section, suffix, native in self.queries:
                ready_at = time.monotonic() + self.interval
                if not receiver.wait(lambda: time.monotonic() >= ready_at):
                    return
                request_id = self.next_request
                self.next_request += 1
                if not receiver.request(
                    self.trader, section, "Qry" + suffix, native, request_id, "TD"
                ):
                    return
            completed = True
        finally:
            receiver.deadline = deadline
            self.active = False
            receiver.event(
                "TD",
                "AccountQueryFinished",
                {
                    "query_id": str(identifier),
                    "status": "COMPLETE" if completed else "FAILED",
                    "reason": None if completed else receiver.failure or "QUERY_INTERRUPTED",
                },
            )

"""Read-only, independently supervised health observations for one Live instance.

JSON lines on stdout are the collector interface. This process cannot authorize
orders, repair storage or restart the kernel. An off-host collector is required
to detect loss of the entire host, including this observer.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from time import monotonic
from typing import Any
from uuid import uuid4

from .auth import LiveAuth
from .client import LiveClient, RuntimeUnavailable
from .instances import Instance


def observe(client: LiveClient) -> dict[str, Any]:
    """Return bounded nonsecret conditions; never relay upstream exception text."""
    try:
        status = client.status()
        if (
            client.last_observation is None
            or client.last_observation["runtime_id"] != status["runtime_id"]
        ):
            raise RuntimeUnavailable("runtime observation differs from status")
        diagnostics = client.diagnostics()
        diagnostic_runtime = (
            None if client.last_observation is None else client.last_observation["runtime_id"]
        )
        orders = client.read("/execution/health")
        if (
            client.last_observation is None
            or client.last_observation["runtime_id"] != status["runtime_id"]
            or diagnostic_runtime != status["runtime_id"]
        ):
            raise RuntimeUnavailable("runtime changed during observation")
        conditions = []
        if status["status"] != "AVAILABLE":
            conditions.append("KERNEL_NOT_AVAILABLE")
        if diagnostics["database"]["status"] != "REACHABLE":
            conditions.append("DATABASE_UNAVAILABLE")
        if diagnostics["database"]["disk_capacity"] in {"FULL", "UNAVAILABLE"}:
            conditions.append("DATABASE_STORAGE_UNAVAILABLE")
        if diagnostics["source_filesystem"]["status"] != "OK":
            conditions.append("SOURCE_STORAGE_UNAVAILABLE")
        if diagnostics["status"] != "OK" and not conditions:
            conditions.append("DIAGNOSTICS_DEGRADED")
        for field, condition in (
            ("unknown_orders", "ORDER_OUTCOMES_UNKNOWN"),
            ("orders_with_pending_fees", "EXECUTION_FEES_UNCONFIRMED"),
            ("conflicted_orders", "ORDER_FACTS_CONFLICTED"),
        ):
            if type(orders[field]) is not int or orders[field] < 0:
                raise ValueError("invalid execution health")
            if orders[field]:
                conditions.append(condition)
        return {
            "runtime_id": status["runtime_id"],
            "conditions": sorted(conditions),
            "status": "DEGRADED" if conditions else "HEALTHY",
        }
    except (RuntimeUnavailable, ValueError, LookupError, TypeError):
        return {
            "runtime_id": None,
            "conditions": ["RUNTIME_OBSERVATION_UNAVAILABLE"],
            "status": "UNAVAILABLE",
        }


class HealthMonitor:
    """Emit changes immediately and repeat current state as a collector heartbeat."""

    def __init__(self, instance: Instance, emit: Callable[[dict[str, Any]], None]):
        self.instance = instance.identifier
        self.emit = emit
        self.monitor_id = str(uuid4())
        self.previous: dict[str, Any] | None = None
        self.emitted_at: float | None = None

    def poll(self, client: LiveClient, *, now: float | None = None) -> None:
        current = observe(client)
        at = monotonic() if now is None else now
        changed = current != self.previous
        if not changed and self.emitted_at is not None and 0 <= at - self.emitted_at < 60:
            return
        previous = self.previous
        kind = (
            "INITIAL"
            if previous is None
            else "HEARTBEAT"
            if not changed
            else "RECOVERED"
            if current["status"] == "HEALTHY" and previous["status"] != "HEALTHY"
            else "RUNTIME_CHANGED"
            if current["status"] == "HEALTHY"
            else "FAULT"
        )
        self.emit(
            {
                "schema": "northstar.live.health/1",
                "event_id": str(uuid4()),
                "monitor_id": self.monitor_id,
                "instance_id": self.instance,
                "observed_at": datetime.now(UTC).isoformat(),
                "kind": kind,
                **current,
                "scope": "KERNEL_STORAGE_AND_ORDERS_NOT_EXECUTION_READINESS",
            }
        )
        # Failed output must not acknowledge an observation that nobody received.
        self.previous, self.emitted_at = current, at


def run() -> None:
    """A separate process; no database, SDK, control commands or kernel threads."""
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    instance = Instance.from_environment()
    auth = LiveAuth.from_environment()
    client = LiveClient(
        os.environ.get("NORTHSTAR_LIVE_URL", "http://127.0.0.1:18081"),
        LiveAuth(auth.read_token),
        expected_instance_id=instance.identifier,
    )
    monitor = HealthMonitor(
        instance,
        lambda event: print(json.dumps(event, sort_keys=True), file=sys.stdout, flush=True),
    )
    try:
        while not stopped.is_set():
            monitor.poll(client)
            stopped.wait(5)
    finally:
        client.close()

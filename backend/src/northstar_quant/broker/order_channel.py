"""Bounded private order IPC; transport outcomes never establish broker execution facts."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from queue import Empty, Full
from threading import get_ident
from typing import Any

from northstar_quant.broker.order_transport import native_request


class OrderChannel:
    """One core thread, one outstanding attempt, no retry after an ambiguous exchange."""

    def __init__(self, requests: Any, responses: Any) -> None:
        self.requests, self.responses = requests, responses
        self.thread = get_ident()
        self.failed = False
        self.closed = False
        self.pending: tuple[int, datetime] | None = None

    def send(
        self, method: str, fields: dict[str, Any], request_id: int, expires_at: datetime
    ) -> int:
        if get_ident() != self.thread or self.closed or self.failed:
            raise ValueError("CTP order channel is unavailable to this core")
        now = datetime.now(UTC)
        if expires_at.utcoffset() != timedelta(0) or not now < expires_at <= now + timedelta(
            seconds=3
        ):
            raise ValueError("CTP transport requires a current bounded deadline")
        message = dict(
            method=method, fields=fields, request_id=request_id, expires_at=expires_at.isoformat()
        )
        encoded = json.dumps(message, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode()) > 4096:
            raise ValueError("CTP order message exceeds its bounded channel")
        if self.pending is not None:
            raise ValueError("CTP order channel already has an outstanding attempt")
        try:
            self.requests.put_nowait(encoded)
        except (Full, OSError, EOFError, ValueError) as error:
            self.failed = True
            raise ValueError("CTP order transport outcome is unknown") from error
        self.pending = (request_id, expires_at)
        # Only a local dispatch return. Native status is separately retained as
        # RequestSent; neither one establishes order acceptance or releases risk.
        return 0

    def poll(self) -> None:
        """Bounded nonblocking completion check on the receiving core thread."""
        if get_ident() != self.thread:
            raise ValueError("CTP order channel belongs to a different core")
        if self.closed or self.failed:
            return
        try:
            response = self.responses.get_nowait()
        except Empty:
            if self.pending is not None and datetime.now(UTC) >= self.pending[1]:
                self.failed = True
            return
        except (OSError, EOFError, ValueError):
            self.failed = True
            return
        if (
            self.pending is None
            or not isinstance(response, tuple)
            or len(response) != 2
            or response[0] != self.pending[0]
            or type(response[1]) is not int
        ):
            self.failed = True
            return
        self.pending = None

    def close(self) -> None:
        self.closed = True


def drain_order(
    requests: Any,
    responses: Any,
    *,
    trader: Any,
    structures: Any,
    broker_id: str,
    account_id: str,
    seen: set[int],
    record: Any,
) -> None:
    """Run only on the native worker's control thread after verified login/query startup."""
    try:
        encoded = requests.get_nowait()
    except Empty:
        return
    if not isinstance(encoded, str) or len(encoded.encode()) > 4096:
        raise ValueError("invalid CTP order IPC payload")
    message = json.loads(encoded)
    if not isinstance(message, dict) or set(message) != {
        "method",
        "fields",
        "request_id",
        "expires_at",
    }:
        raise ValueError("invalid CTP order IPC fields")
    request_id, method, fields = message["request_id"], message["method"], message["fields"]
    if (
        type(request_id) is not int
        or not 100_000 < request_id < 2**31
        or request_id in seen
        or len(seen) >= 100_000
    ):
        raise ValueError("CTP order attempt was already dispatched or is invalid")
    if method not in {"ReqOrderInsert", "ReqOrderAction"} or not isinstance(fields, dict):
        raise ValueError("invalid native order operation")
    if (fields.get("BrokerID"), fields.get("InvestorID"), fields.get("RequestID")) != (
        broker_id,
        account_id,
        request_id,
    ):
        raise ValueError("CTP order channel account or request identity differs")
    expires_at = datetime.fromisoformat(message["expires_at"])
    now = datetime.now(UTC)
    if expires_at.utcoffset() != timedelta(0) or not now < expires_at <= now + timedelta(seconds=3):
        raise ValueError("CTP order expired before native dispatch")
    native = native_request(structures, method, fields)
    pending = record(
        "TD",
        "RequestSent",
        {
            "section": "order_insert" if method == "ReqOrderInsert" else "order_cancel",
            "method": method,
            "return_code": None,
        },
        request_id,
    )
    if pending is None:
        raise ValueError("CTP receipt capacity prevents native dispatch")
    seen.add(request_id)  # A thrown native call is still an attempted send.
    code = getattr(trader, method)(native, request_id)
    if type(code) is not int:
        raise ValueError("CTP returned an invalid native status")
    pending["data"]["return_code"] = code
    responses.put_nowait((request_id, code))

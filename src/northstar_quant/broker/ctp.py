"""A bounded, read-only SimNow capture, isolated from native callback failure.

Only the short-lived child imports CTP. Secrets travel through spawn's private
process pipe, never command arguments or files. Previously received evidence is
returned on timeout/crash; a query ending is not account reconciliation.
"""

from __future__ import annotations

import importlib.metadata
import json
import multiprocessing
import platform
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from northstar_quant.broker.records import BrokerEvent, QueryCapture
from northstar_quant.broker.settings import (
    Credentials,
    SimnowProfile,
    get_profile,
    validate_instrument,
)

_BINDING = "ctpwrapper"
_VERSION = "6.7.13"
_MAX_EVENTS = 10_000
_MAX_BYTES = 8 * 1024 * 1024
_MAX_MESSAGE = 64 * 1024


def sdk_status() -> dict[str, object]:
    """Inspect package/platform metadata, without importing native code or secrets."""

    supported = platform.system() == "Linux" and platform.machine() in {"x86_64", "AMD64"}
    try:
        installed: str | None = importlib.metadata.version(_BINDING)
    except importlib.metadata.PackageNotFoundError:
        installed = None
    reason = None
    if not supported:
        reason = "SDK_UNSUPPORTED_PLATFORM"
    elif installed is None:
        reason = "SDK_NOT_INSTALLED"
    elif installed != _VERSION:
        reason = "SDK_VERSION_MISMATCH"
    return {
        "supported": supported,
        "available": reason is None,
        "binding_name": _BINDING,
        "binding_version": installed,
        "required_version": _VERSION,
        "target_platform": "linux/amd64",
        "current_platform": f"{platform.system()}/{platform.machine()}",
        "native_verified": False,
        "reason": reason,
    }


def sdk_self_check() -> dict[str, object]:
    """Create/release SDK handles in isolation; never Init, register or connect."""

    from northstar_quant.broker._ctp_worker import check_native

    result = sdk_status()
    if not result["available"]:
        return result
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    child = context.Process(target=check_native, args=(sender,), daemon=True)
    try:
        child.start()
        sender.close()
        if receiver.poll(10):
            message = json.loads(receiver.recv_bytes(_MAX_MESSAGE))
            if not isinstance(message, dict) or set(message) != {
                "native_verified",
                "trader_api_version",
                "market_api_version",
                "reason",
            }:
                raise ValueError("invalid SDK check response")
            result.update(message)
        else:
            result["reason"] = "SDK_SELF_CHECK_TIMEOUT"
        child.join(timeout=1)
        if child.is_alive():
            result.update(native_verified=False, reason="SDK_RELEASE_TIMEOUT")
        elif child.exitcode != 0:
            result.update(native_verified=False, reason="SDK_PROCESS_EXITED")
    except (OSError, EOFError, ValueError):
        result.update(native_verified=False, reason="SDK_SELF_CHECK_FAILED")
    finally:
        if child.pid is not None:
            if child.is_alive():
                child.terminate()
                child.join(timeout=1)
            if child.is_alive():
                child.kill()
                child.join(timeout=1)
            child.close()
        receiver.close()
        sender.close()
    return result


def query_account(
    profile: SimnowProfile,
    credentials: Credentials,
    instrument: str,
    *,
    on_event: Callable[[BrokerEvent], None] | None = None,
    timeout_seconds: float = 45,
) -> QueryCapture:
    """Capture seven account/terms queries and a bounded market observation.

    The returned callback sequence includes terminal responses and asynchronous
    order/trade reports. No order, cancellation, settlement confirmation, password
    update or transfer operation is exposed. The caller owns authorization and
    durable recording; this call never retries a login or reconnects a session.
    """

    from northstar_quant.broker._ctp_worker import capture

    if not 1 <= timeout_seconds <= 45:
        raise ValueError("read-only capture timeout must be between 1 and 45 seconds")
    if profile != get_profile(profile.name):
        raise ValueError("only the explicitly approved SimNow endpoints may be queried")
    validate_instrument(instrument)
    started = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    events: list[BrokerEvent] = []
    versions: dict[str, str | None] = {"trader": None, "market": None}
    failure: str | None = None
    installed: str | None = None

    def result() -> QueryCapture:
        return QueryCapture(
            started_at=started,
            finished_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            binding_name=_BINDING,
            binding_version=installed,
            trader_api_version=versions["trader"],
            market_api_version=versions["market"],
            events=tuple(events),
            failure_code=failure,
        )

    status = sdk_status()
    installed = cast(str | None, status["binding_version"])
    if not status["available"]:
        failure = cast(str, status["reason"])
        return result()

    # Each flow directory is private, transient and owned by this one query. A
    # native hang cannot keep it alive after its child has been terminated.
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    with tempfile.TemporaryDirectory(prefix="northstar-ctp-") as directory:
        child = context.Process(
            target=capture,
            args=(sender, profile, credentials, instrument, directory, timeout_seconds),
            name="northstar-ctp-readonly",
            daemon=True,
        )
        completed = False
        releasing = False
        total_bytes = 0
        try:
            child.start()
            sender.close()
            deadline = time.monotonic() + timeout_seconds + 2
            while time.monotonic() < deadline:
                if not receiver.poll(min(0.1, max(0, deadline - time.monotonic()))):
                    if not child.is_alive():
                        break
                    continue
                try:
                    encoded = receiver.recv_bytes(_MAX_MESSAGE)
                except EOFError:
                    break
                total_bytes += len(encoded)
                if total_bytes > _MAX_BYTES:
                    failure = "CAPTURE_LIMIT_EXCEEDED"
                    break
                message = json.loads(encoded)
                if not isinstance(message, dict):
                    raise ValueError("invalid native message")
                match message.get("kind"):
                    case "event":
                        if len(events) >= _MAX_EVENTS:
                            failure = "CAPTURE_LIMIT_EXCEEDED"
                            break
                        event = BrokerEvent.from_dict(message["event"])
                        if event.sequence != len(events) + 1:
                            raise ValueError("native sequence is not contiguous")
                        events.append(event)
                        if on_event is not None:
                            on_event(event)
                    case "versions":
                        for key in ("trader", "market"):
                            value = message.get(key)
                            if not isinstance(value, str) or not 1 <= len(value) <= 128:
                                raise ValueError("invalid SDK identity")
                            versions[key] = value
                    case "complete":
                        failure = cast(str | None, message.get("failure_code"))
                        completed = True
                        break
                    case "releasing":
                        releasing = True
                        failure = cast(str | None, message.get("failure_code"))
                        deadline = min(deadline, time.monotonic() + 2)
                    case _:
                        raise ValueError("unknown native message")
            if not completed and failure is None:
                failure = (
                    ("SDK_RELEASE_TIMEOUT" if releasing else "CAPTURE_TIMEOUT")
                    if time.monotonic() >= deadline
                    else "SDK_PROCESS_EXITED"
                )
            child.join(timeout=2)
            if child.is_alive() and completed and failure is None:
                failure = "SDK_RELEASE_TIMEOUT"
            elif not child.is_alive() and child.exitcode != 0 and failure is None:
                failure = "SDK_PROCESS_EXITED"
        except (OSError, ValueError, TypeError, KeyError):
            failure = "SDK_CAPTURE_FAILED"
        finally:
            if child.pid is not None:
                if child.is_alive():
                    child.terminate()
                    child.join(timeout=1)
                if child.is_alive():
                    child.kill()
                    child.join(timeout=1)
                child.close()
            sender.close()
            receiver.close()
    return result()

"""Recursive redaction for logs, CLI output, audit events and exports."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any


_SENSITIVE_NAME = (
    r"authorization|api[_ -]?key|credential|"
    r"(?:access|refresh|id)?[_ -]?token|"
    r"(?:client[_ -]?)?secret|password|passwd|cookie"
)
_SENSITIVE_KEY = re.compile(
    rf"(?i)(?:^|[_ .-])(?:{_SENSITIVE_NAME})(?:$|[_ .-])|{_SENSITIVE_NAME}"
)
_URL_USERINFO = re.compile(r"(?i)([a-z][a-z0-9+.-]*://[^:/\s]+:)([^@/\s]+)(@)")
_QUERY_SECRET = re.compile(rf"(?i)([?&;]\s*(?:{_SENSITIVE_NAME})\s*=\s*)([^&#;\s]+)")
_AUTH_SCHEME = re.compile(r"(?i)(\b(?:bearer|basic)\s+)([A-Za-z0-9._~+/=-]+)")
_KEY_VALUE_SECRET = re.compile(
    rf"(?i)((?:[\"']?(?:{_SENSITIVE_NAME})[\"']?\s*[:=]\s*)[\"']?)([^\s,;&}}\]\"']+)"
)
_CIRCULAR = "[CIRCULAR]"
_BINARY = "[BINARY]"
REDACTED = "[REDACTED]"


def redact_text(value: str) -> str:
    """Hide secrets embedded in a human-readable string without changing safe text."""

    redacted = _URL_USERINFO.sub(r"\1" + REDACTED + r"\3", value)
    redacted = _QUERY_SECRET.sub(r"\1" + REDACTED, redacted)
    redacted = _AUTH_SCHEME.sub(r"\1" + REDACTED, redacted)
    return _KEY_VALUE_SECRET.sub(r"\1" + REDACTED, redacted)


def redact(value: Any) -> Any:
    """Return a secret-free recursive copy suitable for an output boundary.

    Sensitive mapping keys are redacted unconditionally. Strings are also scanned so
    exception messages, DSNs, CLI text and third-party payloads cannot bypass a key
    based policy. Binary values are never rendered to an output boundary.
    """

    return _redact(value, seen=set())


def _redact(value: Any, *, seen: set[int]) -> Any:
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            return _CIRCULAR
        seen.add(identity)
        try:
            return {
                str(key): REDACTED if _SENSITIVE_KEY.search(str(key)) else _redact(item, seen=seen)
                for key, item in value.items()
            }
        finally:
            seen.remove(identity)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        identity = id(value)
        if identity in seen:
            return _CIRCULAR
        seen.add(identity)
        try:
            return [_redact(item, seen=seen) for item in value]
        finally:
            seen.remove(identity)
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (bytes, bytearray)):
        return _BINARY
    return value


__all__ = ["REDACTED", "redact", "redact_text"]

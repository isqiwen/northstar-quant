"""Deterministic, domain-neutral SHA-256 helpers.

These helpers deliberately have no filesystem, clock, database, or business
domain dependency.  Higher layers must normalize their own semantics before
hashing, so the same functions are safe for data artifacts and execution
authority evidence without reversing the domain dependency graph.
"""

from __future__ import annotations

import hashlib
import json
import re


_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class FingerprintError(ValueError):
    """A deterministic fingerprint input is invalid or non-canonical."""


def content_sha256(payload: bytes, *, field_name: str = "payload") -> str:
    """Return the SHA-256 of explicitly supplied immutable bytes."""

    if not isinstance(payload, bytes):
        raise FingerprintError(f"{field_name} 必须是 bytes")
    return hashlib.sha256(payload).hexdigest()


def require_sha256(value: str, *, field_name: str = "content_hash") -> str:
    """Validate and return a lower-case SHA-256 hexadecimal digest."""

    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        raise FingerprintError(f"{field_name} 必须是 64 位小写 SHA-256 十六进制摘要")
    return value


def canonical_json_sha256(payload: object) -> str:
    """Return the SHA-256 of canonical JSON semantics, never runtime state."""

    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FingerprintError("制品身份输入必须是有限、可 JSON 序列化的值") from exc
    return hashlib.sha256(encoded).hexdigest()

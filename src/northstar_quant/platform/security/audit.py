"""Structured, secret-free security audit event contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
from typing import Final, cast

from northstar_quant.platform.security.redaction import redact, redact_text


_OUTCOMES: Final = frozenset({"success", "failed", "denied", "planned"})


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | bool):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return all(_is_json_value(item) for item in value)
    return False


@dataclass(frozen=True, slots=True)
class SecurityAuditEvent:
    """An explicit, canonical and serializable security-relevant action record."""

    actor: str
    action: str
    outcome: str
    subject: str
    occurred_at: datetime
    details: dict[str, object]

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.actor, self.action, self.outcome, self.subject)
        ):
            raise ValueError("SECURITY_AUDIT_INVALID: actor, action, outcome and subject are required.")
        if self.outcome not in _OUTCOMES:
            raise ValueError(
                "SECURITY_AUDIT_OUTCOME_INVALID: outcome must be success, failed, denied or planned."
            )
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("SECURITY_AUDIT_TIME_INVALID: occurred_at must be timezone-aware.")

        safe_details = redact(self.details)
        if not isinstance(safe_details, dict) or not _is_json_value(safe_details):
            raise ValueError("SECURITY_AUDIT_DETAILS_INVALID: details must be JSON-compatible.")

        object.__setattr__(self, "actor", redact_text(self.actor))
        object.__setattr__(self, "action", redact_text(self.action))
        object.__setattr__(self, "subject", redact_text(self.subject))
        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(UTC))
        object.__setattr__(self, "details", cast(dict[str, object], safe_details))

    def as_dict(self) -> dict[str, object]:
        return {
            "actor": self.actor,
            "action": self.action,
            "outcome": self.outcome,
            "subject": self.subject,
            "occurred_at": self.occurred_at.isoformat(),
            "details": self.details,
        }

    def to_json(self) -> str:
        """Return a deterministic JSON representation suitable for append-only logs."""

        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True, allow_nan=False)


__all__ = ["SecurityAuditEvent"]

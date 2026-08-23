import json
from datetime import UTC, datetime

import pytest

from northstar_quant.foundation.security import REDACTED, SecurityAuditEvent


def test_security_audit_event_is_timezone_normalized_and_redacted():
    event = SecurityAuditEvent(
        "operator",
        "deploy",
        "success",
        "artifact-sha",
        datetime(2026, 8, 22, tzinfo=UTC),
        {"token": "secret"},
    )

    assert event.as_dict()["details"]["token"] == REDACTED
    payload = json.loads(event.to_json())
    assert payload["occurred_at"] == "2026-08-22T00:00:00+00:00"
    assert payload["details"]["token"] == REDACTED


def test_security_audit_rejects_unknown_outcome_and_non_json_details():
    with pytest.raises(ValueError, match="OUTCOME_INVALID"):
        SecurityAuditEvent(
            "operator",
            "deploy",
            "unknown",
            "artifact",
            datetime(2026, 8, 22, tzinfo=UTC),
            {},
        )
    with pytest.raises(ValueError, match="DETAILS_INVALID"):
        SecurityAuditEvent(
            "operator",
            "deploy",
            "failed",
            "artifact",
            datetime(2026, 8, 22, tzinfo=UTC),
            {"detail": object()},
        )
    with pytest.raises(ValueError, match="DETAILS_INVALID"):
        SecurityAuditEvent(
            "operator",
            "deploy",
            "failed",
            "artifact",
            datetime(2026, 8, 22, tzinfo=UTC),
            {"duration": float("inf")},
        )

from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from northstar_quant.intelligence.domain import Evidence
from northstar_quant.intelligence.event_merge import EventLifecycle, merge_event
from northstar_quant.intelligence.extraction import ExtractedEvent


def _candidate(identifier: str) -> ExtractedEvent:
    return ExtractedEvent(identifier, "doc", "SUPPLY", "v1", Evidence("doc", sha256(b"x").hexdigest(), 0, 1), 0.8)


def test_event_merge_preserves_newer_lifecycle_and_late_source_lineage():
    now = datetime(2026, 8, 22, 9, tzinfo=UTC)
    opened = merge_event(
        current=None,
        candidate=_candidate("a"),
        semantic_key="supply-rb",
        observed_at=now,
        lifecycle=EventLifecycle.OPEN,
    )
    confirmed = merge_event(
        current=opened,
        candidate=_candidate("b"),
        semantic_key="supply-rb",
        observed_at=now + timedelta(minutes=1),
        lifecycle=EventLifecycle.CONFIRMED,
    )

    late_source = merge_event(
        current=confirmed,
        candidate=_candidate("old"),
        semantic_key="supply-rb",
        observed_at=now,
        lifecycle=EventLifecycle.RETRACTED,
    )

    assert late_source.lifecycle is EventLifecycle.CONFIRMED
    assert late_source.observed_at == confirmed.observed_at
    assert late_source.extraction_ids == ("a", "b", "old")
    assert tuple(item.extraction_id for item in late_source.extractions) == late_source.extraction_ids


def test_event_merge_deduplicates_identical_callbacks_and_rejects_identity_collision():
    now = datetime(2026, 8, 22, 9, tzinfo=UTC)
    opened = merge_event(
        current=None,
        candidate=_candidate("a"),
        semantic_key="supply-rb",
        observed_at=now,
        lifecycle=EventLifecycle.OPEN,
    )

    assert (
        merge_event(
            current=opened,
            candidate=_candidate("a"),
            semantic_key="supply-rb",
            observed_at=now + timedelta(minutes=1),
            lifecycle=EventLifecycle.RETRACTED,
        )
        is opened
    )

    colliding = ExtractedEvent(
        "a",
        "different-document",
        "SUPPLY",
        "v1",
        Evidence("different-document", sha256(b"different").hexdigest(), 0, 1),
        0.8,
    )
    with pytest.raises(ValueError, match="ID collision"):
        merge_event(
            current=opened,
            candidate=colliding,
            semantic_key="supply-rb",
            observed_at=now + timedelta(minutes=1),
            lifecycle=EventLifecycle.CONFIRMED,
        )


def test_event_merge_rejects_equal_time_conflicts_and_reactivation_after_retraction():
    now = datetime(2026, 8, 22, 9, tzinfo=UTC)
    opened = merge_event(
        current=None,
        candidate=_candidate("a"),
        semantic_key="supply-rb",
        observed_at=now,
        lifecycle=EventLifecycle.OPEN,
    )
    with pytest.raises(ValueError, match="equal observed_at lifecycle conflict"):
        merge_event(
            current=opened,
            candidate=_candidate("b"),
            semantic_key="supply-rb",
            observed_at=now,
            lifecycle=EventLifecycle.CONFIRMED,
        )

    retracted = merge_event(
        current=opened,
        candidate=_candidate("b"),
        semantic_key="supply-rb",
        observed_at=now + timedelta(minutes=1),
        lifecycle=EventLifecycle.RETRACTED,
    )
    with pytest.raises(ValueError, match="not permitted"):
        merge_event(
            current=retracted,
            candidate=_candidate("c"),
            semantic_key="supply-rb",
            observed_at=now + timedelta(minutes=2),
            lifecycle=EventLifecycle.OPEN,
        )

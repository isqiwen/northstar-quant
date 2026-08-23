from datetime import UTC, datetime
from hashlib import sha256

import pytest

from northstar_quant.intelligence.domain import Document, Event, Evidence, Impact, IntelligenceDomainError, Mechanism


def test_document_and_event_are_distinct_and_event_requires_evidence():
    now = datetime(2026, 8, 21, 9, tzinfo=UTC)
    document = Document("doc-1", "rss", "https://example.test/a", sha256(b"a").hexdigest(), now, now, "public")
    event = Event("event-1", "v1", (Evidence(document.document_id, document.content_hash, 0, 5),), Mechanism("supply_reduction", "v1"), (Impact("impact-1", "RB", "bearish"),))
    assert event.event_hash and event.event_id != document.document_id
    with pytest.raises(IntelligenceDomainError, match="Evidence"):
        Event("bad", "v1", (), Mechanism("supply_reduction", "v1"), (Impact("impact-1", "RB", "bearish"),))

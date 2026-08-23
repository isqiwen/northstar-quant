from datetime import UTC, datetime

import pytest

from northstar_quant.intelligence.ingestion import RawDocument, ingest_adapter


class FakeAdapter:
    source_id = "rss"

    def poll(self):
        return (RawDocument("https://example.test/a", "content", datetime(2026, 8, 21, 8, tzinfo=UTC), "public"),)

    def stream(self):
        return self.poll()


def test_document_ingestion_normalizes_required_metadata_without_network():
    document = ingest_adapter(adapter=FakeAdapter(), collected_at=datetime(2026, 8, 21, 9, tzinfo=UTC))[0]
    assert document.source_id == "rss"
    assert document.license_classification == "public"
    assert len(document.content_hash) == 64


def test_document_ingestion_rejects_adapter_non_tuple_contract():
    class BadAdapter(FakeAdapter):
        def poll(self):
            return []
    with pytest.raises(ValueError, match="tuple"):
        ingest_adapter(adapter=BadAdapter(), collected_at=datetime(2026, 8, 21, 9, tzinfo=UTC))

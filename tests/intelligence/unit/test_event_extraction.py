from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from northstar_quant.intelligence.domain import Document, Evidence
from northstar_quant.intelligence.extraction import ExtractedEvent, ExtractionError, validate_extraction
from northstar_quant.intelligence.ontology import load_ontology


def test_extraction_requires_document_bound_evidence_and_ontology_validation():
    now = datetime(2026, 8, 22, 9, tzinfo=UTC)
    document = Document("doc", "rss", "https://example.test/a", sha256(b"text").hexdigest(), now, now, "public")
    candidate = ExtractedEvent("extract", "doc", "SUPPLY", "v1", Evidence("doc", document.content_hash, 0, 4), 0.8)
    assert validate_extraction(document=document, candidate=candidate, ontology=load_ontology(Path("ontology"))) is candidate
    with pytest.raises(ExtractionError, match="source Document"):
        validate_extraction(document=document, candidate=ExtractedEvent("bad", "doc", "SUPPLY", "v1", Evidence("doc", sha256(b"other").hexdigest(), 0, 4), 0.8), ontology=load_ontology(Path("ontology")))

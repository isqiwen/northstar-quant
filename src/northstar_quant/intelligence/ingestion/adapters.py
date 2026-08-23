"""P4-WP03 offline source-adapter protocol and Document normalization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from typing import Protocol

from northstar_quant.intelligence.domain import Document


@dataclass(frozen=True, slots=True)
class RawDocument:
    canonical_url: str
    content: str
    published_at: datetime
    license_classification: str


class SourceAdapter(Protocol):
    source_id: str

    def poll(self) -> tuple[RawDocument, ...]: ...

    def stream(self) -> tuple[RawDocument, ...]: ...


def normalize_document(*, source_id: str, raw: RawDocument, collected_at: datetime) -> Document:
    if not isinstance(raw, RawDocument):
        raise ValueError("raw must be RawDocument")
    if not isinstance(raw.content, str) or not raw.content.strip():
        raise ValueError("document content is required")
    if collected_at.tzinfo is None:
        raise ValueError("collected_at must be timezone-aware")
    content_hash = hashlib.sha256(raw.content.encode("utf-8")).hexdigest()
    document_id = hashlib.sha256(f"{source_id}|{raw.canonical_url}|{content_hash}|{raw.published_at.astimezone(UTC).isoformat()}".encode()).hexdigest()
    return Document(document_id, source_id, raw.canonical_url, content_hash, raw.published_at, collected_at, raw.license_classification)


def ingest_adapter(*, adapter: SourceAdapter, collected_at: datetime, streaming: bool = False) -> tuple[Document, ...]:
    source_id = adapter.source_id
    raw_documents = adapter.stream() if streaming else adapter.poll()
    if not isinstance(raw_documents, tuple):
        raise ValueError("adapter must return a tuple of RawDocument")
    return tuple(normalize_document(source_id=source_id, raw=raw, collected_at=collected_at) for raw in raw_documents)


__all__ = ["RawDocument", "SourceAdapter", "ingest_adapter", "normalize_document"]

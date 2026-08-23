"""P4-WP04 deterministic document deduplication and repost clustering."""

from __future__ import annotations

from dataclasses import dataclass
import re

from northstar_quant.intelligence.domain import Document


class DedupError(ValueError):
    pass


def _tokens(title: str) -> frozenset[str]:
    values = frozenset(re.findall(r"[a-z0-9]+", title.lower()))
    if not values:
        raise DedupError("title must contain normalized tokens")
    return values


@dataclass(frozen=True, slots=True)
class DedupDocument:
    document: Document
    title: str
    semantic_key: str
    repost_of_document_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.document, Document):
            raise DedupError("document must be a Document")
        _tokens(self.title)
        if not isinstance(self.semantic_key, str) or not self.semantic_key.strip():
            raise DedupError("semantic_key is required")


@dataclass(frozen=True, slots=True)
class DocumentCluster:
    canonical_document_id: str
    document_ids: tuple[str, ...]


def cluster_documents(*, documents: tuple[DedupDocument, ...], title_similarity: float = 0.8) -> tuple[DocumentCluster, ...]:
    if not isinstance(documents, tuple) or not documents or not all(isinstance(item, DedupDocument) for item in documents):
        raise DedupError("documents must be a non-empty DedupDocument tuple")
    if len({item.document.document_id for item in documents}) != len(documents):
        raise DedupError("documents cannot duplicate document_id")
    groups: list[list[DedupDocument]] = []
    for document in sorted(documents, key=lambda item: item.document.document_id):
        matched: list[DedupDocument] | None = None
        for group in groups:
            canonical = group[0]
            overlap = len(_tokens(document.title) & _tokens(canonical.title)) / len(_tokens(document.title) | _tokens(canonical.title))
            if document.document.canonical_url == canonical.document.canonical_url or document.document.content_hash == canonical.document.content_hash or document.semantic_key == canonical.semantic_key or document.repost_of_document_id == canonical.document.document_id or overlap >= title_similarity:
                matched = group
                break
        if matched is None:
            groups.append([document])
        else:
            matched.append(document)
    return tuple(DocumentCluster(group[0].document.document_id, tuple(item.document.document_id for item in group)) for group in groups)


__all__ = ["DedupDocument", "DedupError", "DocumentCluster", "cluster_documents"]

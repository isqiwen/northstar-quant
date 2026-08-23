from datetime import UTC, datetime
from hashlib import sha256

from northstar_quant.intelligence.domain import Document
from northstar_quant.intelligence.ingestion import DedupDocument, cluster_documents


def _document(identifier: str, url: str, content: str) -> Document:
    now = datetime(2026, 8, 21, 9, tzinfo=UTC)
    return Document(identifier, "rss", url, sha256(content.encode()).hexdigest(), now, now, "public")


def test_dedup_clusters_exact_title_semantic_and_repost_documents_without_creating_events():
    first = _document("doc-1", "https://example.test/a", "a")
    repost = _document("doc-2", "https://other.test/a", "b")
    cluster = cluster_documents(documents=(DedupDocument(first, "China steel supply disruption", "steel-supply"), DedupDocument(repost, "Steel supply disruption China", "steel-supply", "doc-1")))
    assert cluster == (type(cluster[0])("doc-1", ("doc-1", "doc-2")),)

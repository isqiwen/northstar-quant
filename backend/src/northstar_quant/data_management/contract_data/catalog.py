"""Published contract packages are the sole ordinary browsing admission boundary."""

from uuid import UUID

from sqlalchemy import Connection, text

# Publication identity pins response identities. A later download must not change
# the contract's published view, even when a request's current receipt advances.
RECEIPTS = """WITH latest AS (
    SELECT DISTINCT ON(scope) * FROM data_contract_publications
    ORDER BY scope,created_at DESC,publication_id
), admitted AS (
    SELECT p.publication_id,p.scope AS contract_scope,
        (i->>'receipt_id')::uuid AS receipt_id
    FROM latest p CROSS JOIN LATERAL jsonb_array_elements(p.manifest->'inputs') i
) """


def require_receipt(c: Connection, receipt_id: UUID) -> None:
    package = (
        c.execute(
            text("""SELECT p.path,p.manifest_hash,p.manifest_bytes
        FROM data_contract_publications p
        WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(p.manifest->'inputs') i
            WHERE i->>'receipt_id'=:id)
        ORDER BY p.created_at DESC,p.publication_id LIMIT 1"""),
            {"id": str(receipt_id)},
        )
        .mappings()
        .first()
    )
    if package is None:
        raise ValueError("该响应尚未属于完整合约发布包；请在同步记录检查处理材料")
    from ..catalog.snapshots import verify as verify_snapshot
    from ..publications import PublishedDatasets

    verify_snapshot(PublishedDatasets.from_environment().root, package)

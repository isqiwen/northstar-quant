"""Published contract packages are the sole ordinary browsing admission boundary."""

from typing import Any
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
            text("""SELECT p.path,p.package_hash,p.package_bytes
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
    from ..publications import PublishedDatasets
    from .packages import verify_package

    verify_package(PublishedDatasets.from_environment().root, package)


def list_collections(c: Connection) -> list[dict[str, Any]]:
    from ..tushare.store import serial
    from .lifecycle import describe

    return [
        {**serial(r), **describe(r)}
        for r in c.execute(
            text("""SELECT w.*,d.exchange,d.product,d.kind,d.details,
        d.details->>'name' AS display_name FROM data_contract_collections w
        JOIN data_sync_contracts d ON d.ts_code=w.scope
        ORDER BY w.end_date,w.scope LIMIT 100""")
        ).mappings()
    ]

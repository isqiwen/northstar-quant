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
    if not c.scalar(
        text("""SELECT EXISTS(SELECT 1 FROM data_contract_publications p
        CROSS JOIN LATERAL jsonb_array_elements(p.manifest->'inputs') i
        WHERE i->>'receipt_id'=:id)"""),
        {"id": str(receipt_id)},
    ):
        raise ValueError("该响应尚未属于完整合约发布包；请在同步记录检查处理材料")


def list_collections(c: Connection) -> list[dict[str, Any]]:
    from ..tushare.store import serial

    return [
        serial(r)
        for r in c.execute(
            text("""SELECT w.*,d.exchange,d.product,
        d.details->>'name' AS display_name FROM data_contract_collections w
        JOIN data_sync_contracts d ON d.ts_code=w.scope
        ORDER BY w.end_date DESC,w.scope LIMIT 100""")
        ).mappings()
    ]

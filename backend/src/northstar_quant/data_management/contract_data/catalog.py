"""Published contract packages are the sole ordinary browsing admission boundary."""

from typing import Any
from uuid import UUID

from sqlalchemy import Connection, Engine, text

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
    from ..publications import PublishedDatasets
    from .snapshots import verify_snapshot

    verify_snapshot(PublishedDatasets.from_environment().root, package)


def snapshot(engine: Engine, snapshot_id: str) -> dict[str, Any]:
    from ..publications import PublishedDatasets

    with engine.connect() as c:
        row = (
            c.execute(
                text("SELECT * FROM data_contract_publications WHERE publication_id=:id"),
                {"id": snapshot_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise LookupError("固定合约快照不存在")
    manifest = PublishedDatasets.from_environment().contract_snapshot(snapshot_id)
    return dict(
        snapshot_id=snapshot_id,
        exchange=manifest["exchange"],
        product=manifest["product"],
        contract=manifest["scope"].split(".")[0],
        files=manifest["files"],
        reference=manifest.get("reference", {}),
        time_basis=manifest["time_basis"],
        fee_basis=manifest["fee_basis"],
    )


def rows(engine: Engine, snapshot_id: str, **values: Any) -> dict[str, Any]:
    snapshot(engine, snapshot_id)
    from ..publications import PublishedDatasets

    return PublishedDatasets.from_environment().contract_rows(snapshot_id, **values)

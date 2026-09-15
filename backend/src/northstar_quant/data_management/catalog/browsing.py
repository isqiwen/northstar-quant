"""Fixed catalog browser interface; admission remains with each publication owner."""

from typing import Any

from sqlalchemy import Engine, text

from ..publications import PublishedDatasets


def snapshot(engine: Engine, snapshot_id: str) -> dict[str, Any]:
    with engine.connect() as c:
        exists = c.scalar(
            text("""SELECT EXISTS(
            SELECT 1 FROM data_contract_publications WHERE publication_id=:id
            UNION ALL SELECT 1 FROM data_series_publications WHERE publication_id=:id)"""),
            dict(id=snapshot_id),
        )
        if not exists:
            raise LookupError("固定目录快照不存在")
    manifest = PublishedDatasets.from_environment().catalog_snapshot(snapshot_id)
    real = manifest["entity_type"] == "REAL_CONTRACT"
    return dict(
        snapshot_id=snapshot_id,
        exchange=manifest.get("exchange", ""),
        product=manifest.get("product", ""),
        contract=manifest["scope"].split(".")[0] if real else "",
        series="" if real else manifest["scope"],
        entity_type=manifest["entity_type"],
        files=manifest["files"],
        reference=manifest.get("reference", {}),
        time_basis=manifest["time_basis"],
        fee_basis=manifest["fee_basis"],
    )


def rows(engine: Engine, snapshot_id: str, **values: Any) -> dict[str, Any]:
    snapshot(engine, snapshot_id)
    return PublishedDatasets.from_environment().catalog_rows(snapshot_id, **values)

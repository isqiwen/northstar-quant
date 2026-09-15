"""Publish verified supplier series intervals without certifying execution inputs."""

import io
import json
from typing import Any

from sqlalchemy import Engine, text

from northstar_quant import code_revision

from ..catalog.snapshots import write
from ..files import SourceFiles
from ..maintenance import library_write
from ..publications import PublishedDatasets
from ..tushare.series_rows import KINDS, records
from ..tushare.store import serial


def process_next(engine: Engine, source: SourceFiles) -> dict[str, Any] | None:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    with (
        library_write(engine),
        engine.connect().execution_options(isolation_level="REPEATABLE READ") as c,
        c.begin(),
    ):
        r = (
            c.execute(
                text("""SELECT r.*,j.dataset,j.scope,j.start_at,j.end_at,
            s.exchange,s.product,s.name FROM data_series_requests sr
            JOIN data_sync_jobs j USING(request_id)
            JOIN data_sync_receipts r ON r.receipt_id=j.receipt_id
            JOIN data_series_collections s ON s.dataset=sr.dataset AND s.scope=sr.scope
            WHERE j.dataset=sr.dataset AND j.status='VALIDATED'
            AND sr.processed_receipt_id IS DISTINCT FROM j.receipt_id
            ORDER BY r.created_at,j.request_id LIMIT 1 FOR UPDATE OF sr SKIP LOCKED""")
            )
            .mappings()
            .first()
        )
        if r is None:
            return None
        error = None
        published = []
        try:
            table = pq.ParquetFile(io.BytesIO(source.read(r["parquet_hash"], r["parquet_bytes"])))
            rows = table.read().to_pylist()
            if len(rows) != r["row_count"]:
                raise ValueError("序列响应行数与固定凭据不一致")
            scopes = sorted({row["ts_code"] for row in rows})
            if r["scope"] != "ALL" and scopes and scopes != [r["scope"]]:
                raise ValueError("序列响应身份不一致")
            targets = {}
            if r["dataset"] == "mapping":
                codes = list({str(row.get("mapping_ts_code")) for row in rows})
                targets = {
                    row["ts_code"]: dict(row)
                    for row in c.execute(
                        text("SELECT * FROM data_sync_contracts WHERE ts_code=ANY(:codes)"),
                        dict(codes=codes),
                    ).mappings()
                }
            for scope in scopes:
                manifest = dict(
                    rule="research-series-interval/1",
                    entity_type=KINDS[r["dataset"]],
                    dataset=r["dataset"],
                    scope=scope,
                    series=scope,
                    exchange=r["exchange"],
                    product=r["product"],
                    name=r["name"] if r["scope"] != "ALL" else scope,
                    start=r["start_at"],
                    end=r["end_at"],
                    available_at=r["created_at"].isoformat(),
                    code_revision=code_revision(),
                    fee_basis="NOT_EXECUTION_INPUT",
                    reference=dict(
                        executable=False,
                        coverage=r["quality"].get("coverage_basis", "SUPPLIER_RESPONSE_ONLY"),
                        completeness="NO_LIFECYCLE_ASSERTION",
                        definition="SUPPLIER_DERIVED_SERIES",
                        adjustment="SUPPLIER_ROLL_RATIO_CHAIN"
                        if r["dataset"] == "adjusted"
                        else None,
                    ),
                    inputs=[serial(r)],
                )
                artifact = write(
                    PublishedDatasets.from_environment().root,
                    manifest,
                    source,
                    records(r["dataset"], scope, r["exchange"], r["product"], rows, targets),
                )
                published.append((scope, artifact))
            # Register all members only after their standard values have succeeded.
            for scope, artifact in published:
                c.execute(
                    text("""INSERT INTO data_series_publications
                    (publication_id,dataset,scope,receipt_id,start_date,end_date,manifest,
                     manifest_hash,manifest_bytes,path)
                    VALUES(:id,:dataset,:scope,:receipt,:start,:end,
                        CAST(:manifest AS jsonb),:hash,:bytes,:path)
                    ON CONFLICT DO NOTHING"""),
                    dict(
                        id=artifact["publication_id"],
                        dataset=r["dataset"],
                        scope=scope,
                        receipt=r["receipt_id"],
                        start=r["start_at"],
                        end=r["end_at"],
                        manifest=json.dumps(artifact["manifest"]),
                        hash=artifact["sha256"],
                        bytes=artifact["bytes"],
                        path=artifact["path"],
                    ),
                )
        except (ValueError, OSError) as failure:
            error = str(failure)[:500]
        c.execute(
            text("""UPDATE data_series_requests SET processed_receipt_id=:receipt,
            publication_error=:error WHERE request_id=:request AND dataset=:dataset"""),
            dict(
                receipt=r["receipt_id"], error=error, request=r["request_id"], dataset=r["dataset"]
            ),
        )
        return dict(
            receipt_id=str(r["receipt_id"]), published=0 if error else len(published), error=error
        )

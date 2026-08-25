"""历史 Lake 测试的受授权 DatasetVersion fixture。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
from pathlib import Path

import polars as pl

from northstar_quant.data.artifacts.fingerprints import (
    content_sha256,
    lineage_hash,
    normalization_identity_hash,
)
from northstar_quant.data.artifacts.immutable_store import ArtifactStore
from northstar_quant.data.contracts.data_domain import (
    ArtifactMetadata,
    ArtifactProvenance,
    DataLineage,
    DataSource,
    DatasetVersion,
    LicenseMetadata,
    NormalizedArtifact,
    QualityStatus,
    RawArtifact,
)
from northstar_quant.data.lake import (
    DatasetVersionLakeMaterializer,
    LakeDatasetKind,
    LakeMaterializationRequest,
    LakeMaterializationResult,
    ParquetLakeStore,
    load_historical_lake_config,
)
from northstar_quant.data.quality import canonical_frame_payload
from tests.helpers.paths import PROJECT_ROOT


BASE_TIME = datetime(2026, 1, 5, 8, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class MaterializedLakeFixture:
    frame: pl.DataFrame
    artifact_store: ArtifactStore
    lake_store: ParquetLakeStore
    dataset_version: DatasetVersion
    normalized_snapshot_hash: str
    materialized: LakeMaterializationResult


def build_materialized_bars_lake(tmp_path: Path) -> MaterializedLakeFixture:
    frame = pl.DataFrame(
        {
            "symbol": ["RB", "RB", "CU"],
            "date": [BASE_TIME.date(), BASE_TIME.date(), BASE_TIME.date()],
            "available_at": [
                BASE_TIME,
                BASE_TIME + timedelta(hours=1),
                BASE_TIME,
            ],
            "price": [1.0, 2.0, 3.0],
        }
    ).with_columns(pl.col("available_at").cast(pl.Datetime("us", "UTC")))
    source = _source()
    provenance = ArtifactProvenance(
        source_id=source.source_id,
        source_reference="fixture://historical-lake",
        collection_method="fixture-import",
    )
    raw_payload = b"historical-lake-raw"
    raw = RawArtifact(
        metadata=ArtifactMetadata(
            artifact_id="historical-lake-raw",
            source_id=source.source_id,
            acquired_at=BASE_TIME - timedelta(minutes=5),
            available_at=BASE_TIME,
            schema_version="raw.fixture.v1",
            content_hash=content_sha256(raw_payload),
            transform_version="capture.fixture.v1",
            quality_status=QualityStatus.PASS,
            provenance=provenance,
        ),
        raw_format="application/octet-stream",
    )
    normalized_payload = canonical_frame_payload(frame)
    normalized = NormalizedArtifact(
        metadata=ArtifactMetadata(
            artifact_id="historical-lake-normalized",
            source_id=source.source_id,
            acquired_at=BASE_TIME,
            available_at=BASE_TIME + timedelta(hours=1),
            schema_version="bars.fixture.v1",
            content_hash=content_sha256(normalized_payload),
            transform_version="normalize.fixture.v1",
            quality_status=QualityStatus.PASS,
            provenance=provenance,
        ),
        raw_artifact=raw,
        normalization_identity=normalization_identity_hash(
            raw.content_hash,
            content_sha256(normalized_payload),
            "normalize.fixture.v1",
            "bars.fixture.v1",
        ),
    )
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    artifact_store.put_raw(source=source, artifact=raw, payload=raw_payload)
    stored_normalized = artifact_store.put_normalized(
        source=source,
        artifact=normalized,
        payload=normalized_payload,
        lineage=DataLineage(
            output_artifact=normalized,
            input_artifacts=(raw,),
            transform_version=normalized.transform_version,
            lineage_identity=lineage_hash(
                normalized.content_hash,
                (raw.content_hash,),
                normalized.transform_version,
            ),
            recorded_at=normalized.available_at,
        ),
    )
    dataset_version = DatasetVersion.from_artifacts(
        dataset_id="historical-lake-bars-fixture",
        artifacts=(normalized,),
        schema_version="bars.fixture.v1",
        transform_version="dataset.fixture.v1",
    )
    artifact_store.put_dataset_version(dataset_version)
    lake_store = ParquetLakeStore(tmp_path / "lake")
    materializer = DatasetVersionLakeMaterializer(
        artifact_store=artifact_store,
        lake_store=lake_store,
        lake_config=load_historical_lake_config(
            PROJECT_ROOT / "configs" / "data" / "historical_lake.yaml"
        ),
        retention_days_resolver=lambda _source: 365,
    )
    materialized = materializer.materialize(
        LakeMaterializationRequest(
            dataset_version_hash=dataset_version.version_hash,
            artifact_snapshot_hash=stored_normalized.snapshot.snapshot_hash,
            kind=LakeDatasetKind.BARS,
            event_time_column="date",
        ),
        frame,
    )
    return MaterializedLakeFixture(
        frame=frame,
        artifact_store=artifact_store,
        lake_store=lake_store,
        dataset_version=dataset_version,
        normalized_snapshot_hash=stored_normalized.snapshot.snapshot_hash,
        materialized=materialized,
    )


def _source() -> DataSource:
    def digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    return DataSource(
        source_id="fixture-licensed-source",
        adapter_id="fixture-adapter",
        name="Fixture Licensed Source",
        tier="commercial_licensed",
        status="active",
        config_sha256=digest("fixture-source-config"),
        official_references=("https://example.test/historical-lake",),
        license=LicenseMetadata(
            status="active",
            contract_reference="FIXTURE-LAKE-CONTRACT",
            effective_from="2025-01-01",
            expires_on="2027-12-31",
            terms_sha256=digest("fixture-lake-terms"),
            permitted_purposes=("internal_research", "historical_backtest"),
            allows_internal_storage=True,
            allows_derived_data_storage=True,
            allows_live_trading=False,
        ),
    )

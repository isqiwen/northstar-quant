"""受治理 Parquet Lake 的不可变性、授权与完整性测试。"""

from pathlib import Path

import polars as pl
import pytest

from northstar_quant.data.lake import (
    DatasetVersionLakeMaterializer,
    LakeContractError,
    LakeDatasetKind,
    LakeLicenseSnapshot,
    LakeMaterializationError,
    LakeMaterializationRequest,
)
from northstar_quant.data.lake.store import LakeIntegrityError, LakeStoreError
from tests.helpers.historical_lake import build_materialized_bars_lake
from tests.helpers.paths import PROJECT_ROOT
from northstar_quant.data.lake import load_historical_lake_config


def test_verified_dataset_materializes_partitioned_parquet_with_full_governance(tmp_path: Path):
    fixture = build_materialized_bars_lake(tmp_path)

    verified = fixture.lake_store.verify(fixture.materialized.verified.manifest.reference)
    manifest = verified.manifest

    assert len(verified.parquet_paths) == 2
    assert manifest.kind is LakeDatasetKind.BARS
    assert manifest.upstream_dataset_version_hash == fixture.dataset_version.version_hash
    assert manifest.upstream_artifact_snapshot_hash == fixture.normalized_snapshot_hash
    assert manifest.source_license.retention_days == 365
    assert manifest.source_license.permits_internal_storage is True
    assert manifest.source_license.permitted_purposes == (
        "historical_backtest",
        "internal_research",
    )
    assert manifest.source_license.effective_from == "2025-01-01"
    assert manifest.source_license.expires_on == "2027-12-31"
    assert manifest.available_at_column == "available_at"
    assert manifest.event_time_column == "date"
    assert manifest.minimum_available_at < manifest.maximum_available_at
    assert fixture.materialized.as_mapping()["row_count"] == 3


def test_lake_rejects_input_frame_that_is_not_bound_to_verified_artifact(tmp_path: Path):
    fixture = build_materialized_bars_lake(tmp_path)
    materializer = DatasetVersionLakeMaterializer(
        artifact_store=fixture.artifact_store,
        lake_store=fixture.lake_store,
        lake_config=load_historical_lake_config(
            PROJECT_ROOT / "configs" / "data" / "historical_lake.yaml"
        ),
        retention_days_resolver=lambda _source: 365,
    )
    changed = fixture.frame.with_columns((pl.col("price") + 1).alias("price"))

    with pytest.raises(LakeMaterializationError, match="canonical payload"):
        materializer.materialize(
            LakeMaterializationRequest(
                dataset_version_hash=fixture.dataset_version.version_hash,
                artifact_snapshot_hash=fixture.normalized_snapshot_hash,
                kind=LakeDatasetKind.BARS,
                event_time_column="date",
            ),
            changed,
        )


def test_lake_verification_fails_closed_when_partition_contents_are_tampered(tmp_path: Path):
    fixture = build_materialized_bars_lake(tmp_path)
    partition = fixture.materialized.verified.parquet_paths[0]
    pl.read_parquet(partition).with_columns((pl.col("price") + 100).alias("price")).write_parquet(
        partition
    )

    with pytest.raises(LakeIntegrityError, match="hash"):
        fixture.lake_store.verify(fixture.materialized.verified.manifest.reference)


def test_lake_rejects_non_temporal_event_time_column(tmp_path: Path):
    fixture = build_materialized_bars_lake(tmp_path)
    materializer = DatasetVersionLakeMaterializer(
        artifact_store=fixture.artifact_store,
        lake_store=fixture.lake_store,
        lake_config=load_historical_lake_config(
            PROJECT_ROOT / "configs" / "data" / "historical_lake.yaml"
        ),
        retention_days_resolver=lambda _source: 365,
    )

    with pytest.raises(LakeMaterializationError, match="event_time_column"):
        materializer.materialize(
            LakeMaterializationRequest(
                dataset_version_hash=fixture.dataset_version.version_hash,
                artifact_snapshot_hash=fixture.normalized_snapshot_hash,
                kind=LakeDatasetKind.BARS,
                event_time_column="symbol",
            ),
            fixture.frame,
        )


def test_lake_license_requires_historical_backtest_permission():
    with pytest.raises(LakeContractError, match="historical_backtest"):
        LakeLicenseSnapshot(
            source_id="licensed-source",
            source_config_sha256="a" * 64,
            status="active",
            contract_reference="LAKE-CONTRACT",
            effective_from="2025-01-01",
            expires_on="2027-12-31",
            terms_sha256="b" * 64,
            permitted_purposes=("internal_research",),
            permits_internal_storage=True,
            permits_derived_storage=True,
            retention_days=365,
        )


def test_lake_rejects_intermediate_dataset_directory_symlink(tmp_path: Path):
    fixture = build_materialized_bars_lake(tmp_path)
    reference = fixture.materialized.verified.manifest.reference
    bars_directory = fixture.lake_store.root / "datasets" / reference.kind.value
    external_directory = tmp_path / "external-bars"
    bars_directory.rename(external_directory)
    try:
        bars_directory.symlink_to(external_directory, target_is_directory=True)
    except OSError:
        pytest.skip("当前环境不允许创建目录符号链接")

    with pytest.raises(LakeStoreError, match="符号链接"):
        fixture.lake_store.verify(reference)

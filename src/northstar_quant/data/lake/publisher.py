"""将已验证 DatasetVersion 物化为不可变 Parquet Lake。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import polars as pl

from northstar_quant.data.artifacts.immutable_store import ArtifactStore, ArtifactStoreError
from northstar_quant.data.artifacts.storage import sha256_file
from northstar_quant.data.contracts.data_domain import ArtifactKind, DataSource
from northstar_quant.data.lake.config import HistoricalLakeConfig, load_historical_lake_config
from northstar_quant.data.lake.models import (
    LakeDatasetKind,
    LakeLicenseSnapshot,
    LakeManifest,
    LakePartition,
)
from northstar_quant.data.lake.store import (
    LakeStoreError,
    ParquetLakeStore,
    VerifiedLakeDataset,
    partition_value,
    schema_for,
)
from northstar_quant.data.quality import canonical_frame_payload
from northstar_quant.foundation.config.data_sources import (
    data_source_config_sha256,
    get_data_source,
)


class LakeMaterializationError(RuntimeError):
    """DatasetVersion、授权、输入帧或 Lake 物化约束不满足。"""


RetentionDaysResolver = Callable[[DataSource], int]


@dataclass(frozen=True, slots=True)
class LakeMaterializationRequest:
    """一次只可重放一个已验证 tabular artifact 的 Lake 发布请求。"""

    dataset_version_hash: str
    artifact_snapshot_hash: str
    kind: LakeDatasetKind
    event_time_column: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, LakeDatasetKind):
            raise LakeMaterializationError("kind 必须是 LakeDatasetKind")
        for field in ("dataset_version_hash", "artifact_snapshot_hash"):
            value = getattr(self, field)
            if not isinstance(value, str) or len(value) != 64:
                raise LakeMaterializationError(f"{field} 必须是 SHA-256")
        if not isinstance(self.event_time_column, str) or not self.event_time_column.strip():
            raise LakeMaterializationError("event_time_column 必须是非空列名")
        object.__setattr__(self, "event_time_column", self.event_time_column.strip())


@dataclass(frozen=True, slots=True)
class LakeMaterializationResult:
    """Lake 发布后的可展示引用；结果自身不授予交易资格。"""

    verified: VerifiedLakeDataset

    def as_mapping(self) -> dict[str, object]:
        manifest = self.verified.manifest
        return {
            "available_at": {
                "maximum": manifest.maximum_available_at.isoformat(),
                "minimum": manifest.minimum_available_at.isoformat(),
            },
            "dataset_id": manifest.dataset_id,
            "kind": manifest.kind.value,
            "manifest_path": str(self.verified.manifest_path),
            "manifest_sha256": self.verified.manifest_sha256,
            "partition_count": len(manifest.partitions),
            "row_count": sum(partition.row_count for partition in manifest.partitions),
            "upstream_dataset_version_hash": manifest.upstream_dataset_version_hash,
            "version_hash": manifest.version_hash,
        }


class DatasetVersionLakeMaterializer:
    """唯一允许把 verified DatasetVersion 写成 Lake Parquet 的组合入口。"""

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        lake_store: ParquetLakeStore,
        lake_config: HistoricalLakeConfig,
        retention_days_resolver: RetentionDaysResolver | None = None,
    ) -> None:
        if not isinstance(artifact_store, ArtifactStore):
            raise LakeMaterializationError("artifact_store 必须是 ArtifactStore")
        if not isinstance(lake_store, ParquetLakeStore):
            raise LakeMaterializationError("lake_store 必须是 ParquetLakeStore")
        if not isinstance(lake_config, HistoricalLakeConfig):
            raise LakeMaterializationError("lake_config 必须是 HistoricalLakeConfig")
        self._artifact_store = artifact_store
        self._lake_store = lake_store
        self._lake_config = lake_config
        self._retention_days_resolver = (
            retention_days_resolver
            if retention_days_resolver is not None
            else _configured_retention_days
        )

    @classmethod
    def from_settings(cls) -> "DatasetVersionLakeMaterializer":
        return cls(
            artifact_store=ArtifactStore.from_settings(),
            lake_store=ParquetLakeStore.from_settings(),
            lake_config=load_historical_lake_config(),
        )

    def materialize(
        self,
        request: LakeMaterializationRequest,
        frame: pl.DataFrame,
    ) -> LakeMaterializationResult:
        """验证上游字节与 frame 完全一致后，分区写成不可覆盖的 Parquet。"""

        if not isinstance(request, LakeMaterializationRequest):
            raise LakeMaterializationError("request 必须是 LakeMaterializationRequest")
        if not isinstance(frame, pl.DataFrame) or frame.height < 1:
            raise LakeMaterializationError("Lake 仅接受非空 Polars DataFrame")
        try:
            replay = self._artifact_store.replay_dataset_version(request.dataset_version_hash)
        except ArtifactStoreError as exc:
            raise LakeMaterializationError("上游 DatasetVersion 无法完整验证") from exc
        artifact = next(
            (
                item
                for item in replay.artifacts
                if item.stored.snapshot.snapshot_hash == request.artifact_snapshot_hash
            ),
            None,
        )
        if artifact is None:
            raise LakeMaterializationError("指定 artifact 不属于上游 DatasetVersion")
        if artifact.stored.snapshot.kind not in {ArtifactKind.NORMALIZED, ArtifactKind.DERIVED}:
            raise LakeMaterializationError("Lake 只接收已标准化或派生的 tabular artifact")
        if canonical_frame_payload(frame) != artifact.payload:
            raise LakeMaterializationError(
                "输入 Parquet 与已验证 artifact canonical payload 不一致"
            )
        policy = self._lake_config.policy_for(request.kind)
        if request.event_time_column not in frame.columns:
            raise LakeMaterializationError("event_time_column 不存在于输入 frame")
        missing = [
            column
            for column in (*policy.partition_columns, policy.available_at_column)
            if column not in frame.columns
        ]
        if missing:
            raise LakeMaterializationError(
                "输入 frame 缺少 Lake policy 字段：" + ", ".join(missing)
            )
        _validate_frame_event_time(frame, request.event_time_column)
        minimum, maximum = _validate_frame_available_at(frame, policy.available_at_column)
        source_license = _license_snapshot(
            artifact.stored.source,
            artifact.stored.snapshot.kind,
            self._retention_days_resolver,
        )
        staging_dir = self._lake_store.create_staging_dir()
        try:
            partitions = _write_partitions(
                frame,
                staging_dir=staging_dir,
                partition_columns=policy.partition_columns,
            )
            manifest = LakeManifest(
                kind=request.kind,
                dataset_id=replay.dataset_version.dataset_id,
                upstream_dataset_version_hash=replay.dataset_version.version_hash,
                upstream_artifact_snapshot_hash=artifact.stored.snapshot.snapshot_hash,
                upstream_artifact_content_hash=artifact.stored.snapshot.content_hash,
                upstream_lineage_snapshot_hash=artifact.stored.lineage_snapshot_hash,
                source_license=source_license,
                lake_config_sha256=self._lake_config.config_sha256,
                artifact_schema_version=artifact.stored.snapshot.schema_version,
                artifact_transform_version=artifact.stored.snapshot.transform_version,
                schema=schema_for(frame),
                event_time_column=request.event_time_column,
                available_at_column=policy.available_at_column,
                minimum_available_at=minimum,
                maximum_available_at=maximum,
                partitions=partitions,
            )
            return LakeMaterializationResult(
                verified=self._lake_store.publish_staged(staging_dir, manifest)
            )
        except (LakeStoreError, OSError, pl.exceptions.PolarsError) as exc:
            raise LakeMaterializationError("Lake Parquet 物化失败") from exc


def _configured_retention_days(source: DataSource) -> int:
    """绑定当前受管 source config，避免以今天的不同条款物化历史 artifact。"""

    source_config = get_data_source(source.source_id)
    if data_source_config_sha256(source_config) != source.config_sha256:
        raise LakeMaterializationError("当前 source config 与上游 artifact 冻结配置不一致")
    return source_config.license.retention_days


def _license_snapshot(
    source: DataSource,
    artifact_kind: ArtifactKind,
    retention_days_resolver: RetentionDaysResolver,
) -> LakeLicenseSnapshot:
    license_metadata = source.license
    if source.status != "active" or license_metadata.status != "active":
        raise LakeMaterializationError("上游 source 未处于 active 授权状态")
    required_purposes = {"internal_research", "historical_backtest"}
    if not required_purposes.issubset(license_metadata.permitted_purposes):
        raise LakeMaterializationError(
            "上游 source 必须同时授权 internal_research 和 historical_backtest"
        )
    if (
        license_metadata.contract_reference is None
        or license_metadata.terms_sha256 is None
        or license_metadata.effective_from is None
        or license_metadata.expires_on is None
    ):
        raise LakeMaterializationError("上游 source 缺少完整可审计合同、条款或有效期")
    if artifact_kind is ArtifactKind.DERIVED:
        if not license_metadata.allows_derived_data_storage:
            raise LakeMaterializationError("上游 source 不允许保存派生历史数据")
    elif not license_metadata.allows_internal_storage:
        raise LakeMaterializationError("上游 source 不允许内部保存历史数据")
    retention_days = retention_days_resolver(source)
    if (
        isinstance(retention_days, bool)
        or not isinstance(retention_days, int)
        or retention_days < 0
    ):
        raise LakeMaterializationError("source retention_days 必须是非负整数")
    return LakeLicenseSnapshot(
        source_id=source.source_id,
        source_config_sha256=source.config_sha256,
        status=license_metadata.status,
        contract_reference=license_metadata.contract_reference,
        effective_from=license_metadata.effective_from,
        expires_on=license_metadata.expires_on,
        terms_sha256=license_metadata.terms_sha256,
        permitted_purposes=license_metadata.permitted_purposes,
        permits_internal_storage=license_metadata.allows_internal_storage,
        permits_derived_storage=license_metadata.allows_derived_data_storage,
        retention_days=retention_days,
    )


def _validate_frame_available_at(frame: pl.DataFrame, column: str) -> tuple[datetime, datetime]:
    dtype = frame.schema[column]
    if not isinstance(dtype, pl.Datetime) or dtype.time_zone is None:
        raise LakeMaterializationError("Lake policy 的 available_at 必须是带时区的 Datetime")
    values = frame.get_column(column)
    if values.null_count() != 0:
        raise LakeMaterializationError("Lake policy 的 available_at 不得为 null")
    minimum = values.min()
    maximum = values.max()
    if not isinstance(minimum, datetime) or not isinstance(maximum, datetime):
        raise LakeMaterializationError("Lake policy 的 available_at 类型无效")
    return minimum.astimezone(UTC), maximum.astimezone(UTC)


def _validate_frame_event_time(frame: pl.DataFrame, column: str) -> None:
    """事件时间必须是非空 Date 或带时区 Datetime，不能把业务字段误标为时间。"""

    dtype = frame.schema[column]
    if isinstance(dtype, pl.Date):
        pass
    elif isinstance(dtype, pl.Datetime) and dtype.time_zone is not None:
        pass
    else:
        raise LakeMaterializationError(
            "Lake event_time_column 必须是非空 Date 或带时区的 Datetime"
        )
    if frame.get_column(column).null_count() != 0:
        raise LakeMaterializationError("Lake event_time_column 不得为 null")


def _write_partitions(
    frame: pl.DataFrame,
    *,
    staging_dir: Path,
    partition_columns: tuple[str, ...],
) -> tuple[LakePartition, ...]:
    partitions: list[LakePartition] = []
    groups = frame.partition_by(list(partition_columns), maintain_order=True, as_dict=True)
    for index, (raw_values, partition_frame) in enumerate(groups.items()):
        values = tuple(
            (column, partition_value(value))
            for column, value in zip(partition_columns, raw_values, strict=True)
        )
        relative_path = (
            Path("partitions")
            / Path(*(_partition_segment(column, value) for column, value in values))
            / f"part-{index:06d}.parquet"
        )
        path = staging_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        partition_frame.write_parquet(path)
        partitions.append(
            LakePartition(
                relative_path=relative_path.as_posix(),
                content_sha256=sha256_file(path),
                row_count=partition_frame.height,
                values=values,
            )
        )
    return tuple(partitions)


def _partition_segment(column: str, value: str) -> str:
    return f"{column}={quote(value, safe='-_.')}"

"""受治理 Parquet Lake 的不可变值对象。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum
import re
from typing import Mapping

from northstar_quant.data.artifacts.fingerprints import canonical_json_sha256, require_sha256


class LakeContractError(ValueError):
    """Parquet Lake 的身份、许可或 PIT 契约不满足。"""


class LakeDatasetKind(str, Enum):
    """历史数据湖允许保存的制品类别。"""

    TICK = "tick"
    BARS = "bars"
    FACTORS = "factors"
    FEATURES = "features"
    RESEARCH = "research"
    BACKTEST = "backtest"


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_COLUMN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _text(value: object, field: str, *, column: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LakeContractError(f"{field} 必须是非空文本")
    normalized = value.strip()
    matcher = _COLUMN_RE if column else _IDENTIFIER_RE
    if matcher.fullmatch(normalized) is None:
        raise LakeContractError(f"{field} 格式不合法")
    return normalized


def _hash(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise LakeContractError(f"{field} 必须是 SHA-256")
    try:
        return require_sha256(value, field_name=field)
    except ValueError as exc:
        raise LakeContractError(str(exc)) from exc


def _utc_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise LakeContractError(f"{field} 必须是带时区的 datetime")
    return value.astimezone(UTC)


def _text_tuple(value: object, field: str, *, column: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or not value:
        raise LakeContractError(f"{field} 必须是非空文本列表")
    values = tuple(_text(item, field, column=column) for item in value)
    if len(values) != len(set(values)):
        raise LakeContractError(f"{field} 不得重复")
    return values


@dataclass(frozen=True, slots=True)
class LakeDatasetPolicy:
    """一种可发布历史数据的固定分区与 PIT 字段规则。"""

    kind: LakeDatasetKind
    partition_columns: tuple[str, ...]
    available_at_column: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, LakeDatasetKind):
            raise LakeContractError("policy.kind 必须是 LakeDatasetKind")
        partitions = _text_tuple(self.partition_columns, "policy.partition_columns", column=True)
        available_at = _text(self.available_at_column, "policy.available_at_column", column=True)
        if available_at in partitions:
            raise LakeContractError("policy.available_at_column 不能作为分区字段")
        object.__setattr__(self, "partition_columns", partitions)
        object.__setattr__(self, "available_at_column", available_at)


@dataclass(frozen=True, slots=True)
class LakeDatasetReference:
    """定位一份不可变 Lake 数据集，而不是可变的 latest 指针。"""

    kind: LakeDatasetKind
    dataset_id: str
    version_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, LakeDatasetKind):
            raise LakeContractError("reference.kind 必须是 LakeDatasetKind")
        object.__setattr__(self, "dataset_id", _text(self.dataset_id, "reference.dataset_id"))
        object.__setattr__(self, "version_hash", _hash(self.version_hash, "reference.version_hash"))

    def as_mapping(self) -> dict[str, str]:
        return {
            "dataset_id": self.dataset_id,
            "kind": self.kind.value,
            "version_hash": self.version_hash,
        }


@dataclass(frozen=True, slots=True)
class LakeLicenseSnapshot:
    """物化时冻结的无密钥许可与保留期事实。"""

    source_id: str
    source_config_sha256: str
    status: str
    contract_reference: str | None
    effective_from: str
    expires_on: str
    terms_sha256: str | None
    permitted_purposes: tuple[str, ...]
    permits_internal_storage: bool
    permits_derived_storage: bool
    retention_days: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_id", _text(self.source_id, "license.source_id"))
        object.__setattr__(
            self,
            "source_config_sha256",
            _hash(self.source_config_sha256, "license.source_config_sha256"),
        )
        object.__setattr__(self, "status", _text(self.status, "license.status"))
        if self.contract_reference is not None:
            object.__setattr__(
                self,
                "contract_reference",
                _text(self.contract_reference, "license.contract_reference"),
            )
        effective_from = _date_text(self.effective_from, "license.effective_from")
        expires_on = _date_text(self.expires_on, "license.expires_on")
        if effective_from > expires_on:
            raise LakeContractError("license.effective_from 不能晚于 license.expires_on")
        object.__setattr__(self, "effective_from", effective_from.isoformat())
        object.__setattr__(self, "expires_on", expires_on.isoformat())
        if self.terms_sha256 is not None:
            object.__setattr__(
                self, "terms_sha256", _hash(self.terms_sha256, "license.terms_sha256")
            )
        purposes = _text_tuple(self.permitted_purposes, "license.permitted_purposes")
        if not {"internal_research", "historical_backtest"}.issubset(purposes):
            raise LakeContractError(
                "license.permitted_purposes 必须包含 internal_research 和 historical_backtest"
            )
        object.__setattr__(self, "permitted_purposes", purposes)
        for field in ("permits_internal_storage", "permits_derived_storage"):
            if type(getattr(self, field)) is not bool:
                raise LakeContractError(f"license.{field} 必须是 bool")
        if isinstance(self.retention_days, bool) or not isinstance(self.retention_days, int):
            raise LakeContractError("license.retention_days 必须是非负整数")
        if self.retention_days < 0:
            raise LakeContractError("license.retention_days 必须是非负整数")

    def as_mapping(self) -> dict[str, object]:
        return {
            "contract_reference": self.contract_reference,
            "effective_from": self.effective_from,
            "expires_on": self.expires_on,
            "permits_derived_storage": self.permits_derived_storage,
            "permits_internal_storage": self.permits_internal_storage,
            "permitted_purposes": list(self.permitted_purposes),
            "retention_days": self.retention_days,
            "source_config_sha256": self.source_config_sha256,
            "source_id": self.source_id,
            "status": self.status,
            "terms_sha256": self.terms_sha256,
        }


@dataclass(frozen=True, slots=True)
class LakePartition:
    """一份已哈希的 Parquet 分区。"""

    relative_path: str
    content_sha256: str
    row_count: int
    values: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.relative_path, str) or not self.relative_path:
            raise LakeContractError("partition.relative_path 必须是非空相对路径")
        normalized_path = self.relative_path.replace("\\", "/")
        if normalized_path.startswith("/") or ".." in normalized_path.split("/"):
            raise LakeContractError("partition.relative_path 必须位于数据集目录内")
        if not normalized_path.endswith(".parquet"):
            raise LakeContractError("partition.relative_path 必须指向 Parquet 文件")
        if (
            isinstance(self.row_count, bool)
            or not isinstance(self.row_count, int)
            or self.row_count < 1
        ):
            raise LakeContractError("partition.row_count 必须是正整数")
        pairs: list[tuple[str, str]] = []
        for pair in self.values:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise LakeContractError("partition.values 必须是 (column, value) 元组")
            column, value = pair
            if not isinstance(value, str) or not value:
                raise LakeContractError("partition.values 的值必须是非空文本")
            pairs.append((_text(column, "partition.values.column", column=True), value))
        if len({column for column, _ in pairs}) != len(pairs):
            raise LakeContractError("partition.values 不能包含重复字段")
        object.__setattr__(self, "relative_path", normalized_path)
        object.__setattr__(
            self, "content_sha256", _hash(self.content_sha256, "partition.content_sha256")
        )
        object.__setattr__(self, "values", tuple(sorted(pairs)))

    def as_mapping(self) -> dict[str, object]:
        return {
            "content_sha256": self.content_sha256,
            "relative_path": self.relative_path,
            "row_count": self.row_count,
            "values": [{"column": column, "value": value} for column, value in self.values],
        }


@dataclass(frozen=True, slots=True)
class LakeManifest:
    """Parquet Lake 的完整可回放 manifest。"""

    kind: LakeDatasetKind
    dataset_id: str
    upstream_dataset_version_hash: str
    upstream_artifact_snapshot_hash: str
    upstream_artifact_content_hash: str
    upstream_lineage_snapshot_hash: str | None
    source_license: LakeLicenseSnapshot
    lake_config_sha256: str
    artifact_schema_version: str
    artifact_transform_version: str
    schema: tuple[tuple[str, str], ...]
    event_time_column: str
    available_at_column: str
    minimum_available_at: datetime
    maximum_available_at: datetime
    partitions: tuple[LakePartition, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, LakeDatasetKind):
            raise LakeContractError("manifest.kind 必须是 LakeDatasetKind")
        object.__setattr__(self, "dataset_id", _text(self.dataset_id, "manifest.dataset_id"))
        for field in (
            "upstream_dataset_version_hash",
            "upstream_artifact_snapshot_hash",
            "upstream_artifact_content_hash",
            "lake_config_sha256",
        ):
            object.__setattr__(self, field, _hash(getattr(self, field), f"manifest.{field}"))
        if self.upstream_lineage_snapshot_hash is not None:
            object.__setattr__(
                self,
                "upstream_lineage_snapshot_hash",
                _hash(
                    self.upstream_lineage_snapshot_hash, "manifest.upstream_lineage_snapshot_hash"
                ),
            )
        if not isinstance(self.source_license, LakeLicenseSnapshot):
            raise LakeContractError("manifest.source_license 必须是 LakeLicenseSnapshot")
        object.__setattr__(
            self,
            "artifact_schema_version",
            _text(self.artifact_schema_version, "manifest.artifact_schema_version"),
        )
        object.__setattr__(
            self,
            "artifact_transform_version",
            _text(self.artifact_transform_version, "manifest.artifact_transform_version"),
        )
        schema: list[tuple[str, str]] = []
        for item in self.schema:
            if not isinstance(item, tuple) or len(item) != 2:
                raise LakeContractError("manifest.schema 必须是 (column, dtype) 元组")
            column, dtype = item
            if not isinstance(dtype, str) or not dtype.strip():
                raise LakeContractError("manifest.schema dtype 必须是非空文本")
            schema.append((_text(column, "manifest.schema.column", column=True), dtype.strip()))
        if not schema or len({column for column, _ in schema}) != len(schema):
            raise LakeContractError("manifest.schema 必须有唯一列")
        event_time = _text(self.event_time_column, "manifest.event_time_column", column=True)
        available_at = _text(self.available_at_column, "manifest.available_at_column", column=True)
        columns = {column for column, _ in schema}
        if event_time not in columns or available_at not in columns:
            raise LakeContractError("manifest 时间字段必须属于 schema")
        minimum = _utc_datetime(self.minimum_available_at, "manifest.minimum_available_at")
        maximum = _utc_datetime(self.maximum_available_at, "manifest.maximum_available_at")
        if minimum > maximum:
            raise LakeContractError("manifest minimum_available_at 不能晚于 maximum_available_at")
        partitions = tuple(self.partitions)
        if not partitions or not all(isinstance(item, LakePartition) for item in partitions):
            raise LakeContractError("manifest.partitions 必须是非空 LakePartition 列表")
        if len({item.relative_path for item in partitions}) != len(partitions):
            raise LakeContractError("manifest.partitions 路径不能重复")
        object.__setattr__(self, "schema", tuple(schema))
        object.__setattr__(self, "event_time_column", event_time)
        object.__setattr__(self, "available_at_column", available_at)
        object.__setattr__(self, "minimum_available_at", minimum)
        object.__setattr__(self, "maximum_available_at", maximum)
        object.__setattr__(
            self, "partitions", tuple(sorted(partitions, key=lambda item: item.relative_path))
        )

    @property
    def version_hash(self) -> str:
        return canonical_json_sha256(self._identity_mapping())

    @property
    def reference(self) -> LakeDatasetReference:
        return LakeDatasetReference(
            kind=self.kind,
            dataset_id=self.dataset_id,
            version_hash=self.version_hash,
        )

    def _identity_mapping(self) -> dict[str, object]:
        return {
            "artifact_schema_version": self.artifact_schema_version,
            "artifact_transform_version": self.artifact_transform_version,
            "available_at_column": self.available_at_column,
            "event_time_column": self.event_time_column,
            "format": "northstar.parquet-lake-manifest.v1",
            "lake_config_sha256": self.lake_config_sha256,
            "maximum_available_at": self.maximum_available_at.isoformat(),
            "minimum_available_at": self.minimum_available_at.isoformat(),
            "partitions": [partition.as_mapping() for partition in self.partitions],
            "reference": {
                "dataset_id": self.dataset_id,
                "kind": self.kind.value,
            },
            "schema": [{"column": column, "dtype": dtype} for column, dtype in self.schema],
            "source_license": self.source_license.as_mapping(),
            "upstream_artifact_content_hash": self.upstream_artifact_content_hash,
            "upstream_artifact_snapshot_hash": self.upstream_artifact_snapshot_hash,
            "upstream_dataset_version_hash": self.upstream_dataset_version_hash,
            "upstream_lineage_snapshot_hash": self.upstream_lineage_snapshot_hash,
        }

    def as_mapping(self) -> dict[str, object]:
        payload = self._identity_mapping()
        payload["reference"] = self.reference.as_mapping()
        payload["version_hash"] = self.version_hash
        return payload

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "LakeManifest":
        expected = {
            "artifact_schema_version",
            "artifact_transform_version",
            "available_at_column",
            "event_time_column",
            "format",
            "lake_config_sha256",
            "maximum_available_at",
            "minimum_available_at",
            "partitions",
            "reference",
            "schema",
            "source_license",
            "upstream_artifact_content_hash",
            "upstream_artifact_snapshot_hash",
            "upstream_dataset_version_hash",
            "upstream_lineage_snapshot_hash",
            "version_hash",
        }
        if (
            set(payload) != expected
            or payload.get("format") != "northstar.parquet-lake-manifest.v1"
        ):
            raise LakeContractError("Lake manifest 字段或格式不受支持")
        reference_payload = _mapping(payload["reference"], "manifest.reference")
        if set(reference_payload) != {"dataset_id", "kind", "version_hash"}:
            raise LakeContractError("manifest.reference 字段不完整")
        try:
            kind = LakeDatasetKind(_string(reference_payload["kind"], "manifest.reference.kind"))
        except (TypeError, ValueError) as exc:
            raise LakeContractError("manifest.reference.kind 不受支持") from exc
        source_payload = _mapping(payload["source_license"], "manifest.source_license")
        expected_license = {
            "contract_reference",
            "effective_from",
            "expires_on",
            "permits_derived_storage",
            "permits_internal_storage",
            "permitted_purposes",
            "retention_days",
            "source_config_sha256",
            "source_id",
            "status",
            "terms_sha256",
        }
        if set(source_payload) != expected_license:
            raise LakeContractError("manifest.source_license 字段不完整")
        source_license = LakeLicenseSnapshot(
            source_id=_string(source_payload["source_id"], "manifest.source_license.source_id"),
            source_config_sha256=_string(
                source_payload["source_config_sha256"],
                "manifest.source_license.source_config_sha256",
            ),
            status=_string(source_payload["status"], "manifest.source_license.status"),
            contract_reference=_optional_string(
                source_payload["contract_reference"],
                "manifest.source_license.contract_reference",
            ),
            effective_from=_string(
                source_payload["effective_from"], "manifest.source_license.effective_from"
            ),
            expires_on=_string(source_payload["expires_on"], "manifest.source_license.expires_on"),
            terms_sha256=_optional_string(
                source_payload["terms_sha256"],
                "manifest.source_license.terms_sha256",
            ),
            permitted_purposes=tuple(
                _string(item, "manifest.source_license.permitted_purposes[]")
                for item in _list(
                    source_payload["permitted_purposes"],
                    "manifest.source_license.permitted_purposes",
                )
            ),
            permits_internal_storage=_bool(
                source_payload["permits_internal_storage"],
                "manifest.source_license.permits_internal_storage",
            ),
            permits_derived_storage=_bool(
                source_payload["permits_derived_storage"],
                "manifest.source_license.permits_derived_storage",
            ),
            retention_days=_int(
                source_payload["retention_days"], "manifest.source_license.retention_days"
            ),
        )
        schema_items = _list(payload["schema"], "manifest.schema")
        schema: list[tuple[str, str]] = []
        for item in schema_items:
            schema_payload = _mapping(item, "manifest.schema[]")
            if set(schema_payload) != {"column", "dtype"}:
                raise LakeContractError("manifest.schema[] 字段不完整")
            schema.append(
                (
                    _string(schema_payload["column"], "manifest.schema[].column"),
                    _string(schema_payload["dtype"], "manifest.schema[].dtype"),
                )
            )
        partitions: list[LakePartition] = []
        for item in _list(payload["partitions"], "manifest.partitions"):
            partition_payload = _mapping(item, "manifest.partitions[]")
            if set(partition_payload) != {"content_sha256", "relative_path", "row_count", "values"}:
                raise LakeContractError("manifest.partitions[] 字段不完整")
            values: list[tuple[str, str]] = []
            for value in _list(partition_payload["values"], "manifest.partitions[].values"):
                value_payload = _mapping(value, "manifest.partitions[].values[]")
                if set(value_payload) != {"column", "value"}:
                    raise LakeContractError("manifest partition value 字段不完整")
                column = value_payload["column"]
                raw_value = value_payload["value"]
                if not isinstance(column, str) or not isinstance(raw_value, str):
                    raise LakeContractError("manifest partition value 必须是文本")
                values.append((column, raw_value))
            partitions.append(
                LakePartition(
                    relative_path=_string(
                        partition_payload["relative_path"], "manifest.partitions[].relative_path"
                    ),
                    content_sha256=_string(
                        partition_payload["content_sha256"], "manifest.partitions[].content_sha256"
                    ),
                    row_count=_int(
                        partition_payload["row_count"], "manifest.partitions[].row_count"
                    ),
                    values=tuple(values),
                )
            )
        result = cls(
            kind=kind,
            dataset_id=_string(reference_payload["dataset_id"], "manifest.reference.dataset_id"),
            upstream_dataset_version_hash=_string(
                payload["upstream_dataset_version_hash"], "manifest.upstream_dataset_version_hash"
            ),
            upstream_artifact_snapshot_hash=_string(
                payload["upstream_artifact_snapshot_hash"],
                "manifest.upstream_artifact_snapshot_hash",
            ),
            upstream_artifact_content_hash=_string(
                payload["upstream_artifact_content_hash"],
                "manifest.upstream_artifact_content_hash",
            ),
            upstream_lineage_snapshot_hash=_optional_string(
                payload["upstream_lineage_snapshot_hash"],
                "manifest.upstream_lineage_snapshot_hash",
            ),
            source_license=source_license,
            lake_config_sha256=_string(
                payload["lake_config_sha256"], "manifest.lake_config_sha256"
            ),
            artifact_schema_version=_string(
                payload["artifact_schema_version"], "manifest.artifact_schema_version"
            ),
            artifact_transform_version=_string(
                payload["artifact_transform_version"], "manifest.artifact_transform_version"
            ),
            schema=tuple(schema),
            event_time_column=_string(payload["event_time_column"], "manifest.event_time_column"),
            available_at_column=_string(
                payload["available_at_column"], "manifest.available_at_column"
            ),
            minimum_available_at=_parse_datetime(
                payload["minimum_available_at"], "manifest.minimum_available_at"
            ),
            maximum_available_at=_parse_datetime(
                payload["maximum_available_at"], "manifest.maximum_available_at"
            ),
            partitions=tuple(partitions),
        )
        declared_version = _hash(payload["version_hash"], "manifest.version_hash")
        if (
            result.version_hash != declared_version
            or _string(reference_payload["version_hash"], "manifest.reference.version_hash")
            != declared_version
        ):
            raise LakeContractError("Lake manifest version_hash 与内容不一致")
        return result


def _mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise LakeContractError(f"{field} 必须是对象")
    return value


def _list(value: object, field: str) -> list[object]:
    if not isinstance(value, list):
        raise LakeContractError(f"{field} 必须是列表")
    return value


def _string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise LakeContractError(f"{field} 必须是文本")
    return value


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _string(value, field)


def _bool(value: object, field: str) -> bool:
    if type(value) is not bool:
        raise LakeContractError(f"{field} 必须是 bool")
    return value


def _int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LakeContractError(f"{field} 必须是整数")
    return value


def _parse_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise LakeContractError(f"{field} 必须是 ISO-8601 时间")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise LakeContractError(f"{field} 必须是 ISO-8601 时间") from exc
    return _utc_datetime(parsed, field)


def _date_text(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise LakeContractError(f"{field} 必须是 ISO-8601 日期")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise LakeContractError(f"{field} 必须是 ISO-8601 日期") from exc

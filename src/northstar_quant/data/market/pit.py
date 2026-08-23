"""市场数据的行级 point-in-time 选择。

本模块只消费 ``ArtifactStore`` 已校验的不可变 ``DatasetVersion``。它不读取 legacy
market 文件、不维护 ``latest`` 指针，也不使用系统时钟。制品级 ``available_at`` 说明整份
制品何时可读；这里额外要求每个 bar/tick/snapshot 行都有自己的 ``available_at``，并在
调用者给出的 ``as_of`` 时点选择当时唯一可见的修订。

``MarketDataSnapshot`` 是单一、显式 as-of 视图，适合冻结研究输入和重放旧结果；它不声称
替代逐决策时点的完整策略模拟。后者必须对每一个 simulation time 重新调用
``select``，不能把一次回测结束时的静态快照误当成历史每一步都已知的数据。
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import Enum
import json
import math
import re
from typing import Any, ClassVar, Literal, cast

import polars as pl

from northstar_quant.data.artifacts.fingerprints import (
    FingerprintError,
    canonical_json_sha256,
    content_sha256,
    require_sha256,
)
from northstar_quant.data.artifacts.immutable_store import (
    ArtifactStore,
    ArtifactStoreError,
    DatasetReplay,
)
from northstar_quant.data.contracts.data_domain import ArtifactKind, QualityStatus
from northstar_quant.data.quality import canonical_frame_payload
from northstar_quant.data.sources.protocol import (
    DataSourceProtocolError,
    PublicationPurpose,
    PublicationScope,
)


_CANONICAL_FRAME_FORMAT = "northstar.data_quality.canonical_frame.v1"
_PIT_SPEC_FORMAT = "northstar.market-data-pit-spec.v1"
_PIT_REVISION_FORMAT = "northstar.market-data-revision.v1"
_PIT_SNAPSHOT_FORMAT = "northstar.market-data-snapshot.v1"
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DATETIME_DTYPE_RE = re.compile(
    r"^Datetime\(time_unit='(?P<unit>ns|us|ms)', time_zone=(?P<timezone>None|'[^']+')\)$"
)


class MarketDataPITError(ValueError):
    """市场行情的时间、修订或不可变来源约束不满足。"""


class MarketDataKind(str, Enum):
    """PIT 行的事实类别。三类均必须拥有行级可用时间。"""

    BAR = "bar"
    TICK = "tick"
    SNAPSHOT = "snapshot"


def _required_field(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _FIELD_RE.fullmatch(value) is None:
        raise MarketDataPITError(f"{field_name} 必须是合法、非空的字段名")
    return value


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MarketDataPITError(f"{field_name} 必须是非空文本")
    text = value.strip()
    if text.startswith(("/", "\\\\", "~/", "~\\")) or re.match(r"^[A-Za-z]:[\\\\/]", text):
        raise MarketDataPITError(f"{field_name} 不得包含本机绝对路径")
    return text


def _utc_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MarketDataPITError(f"{field_name} 必须是带时区的 datetime")
    return value.astimezone(UTC)


def _hash(value: object, field_name: str) -> str:
    try:
        return require_sha256(value, field_name=field_name)  # type: ignore[arg-type]
    except FingerprintError as exc:
        raise MarketDataPITError(str(exc)) from exc


def _canonical_json(value: object, field_name: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise MarketDataPITError(f"{field_name} 必须是可安全序列化的有限 JSON") from exc


def _canonical_cell(value: object, field_name: str) -> dict[str, object]:
    """把一格市场数据转换为带类型的稳定 JSON，避免 ``1`` 与 ``\"1\"`` 碰撞。"""

    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "bool", "value": value}
    if isinstance(value, int) and not isinstance(value, bool):
        return {"type": "int", "value": str(value)}
    if isinstance(value, float):
        if not math.isfinite(value):
            raise MarketDataPITError(f"{field_name} 不得包含 NaN 或无穷值")
        return {"type": "float", "value": value.hex()}
    if isinstance(value, str):
        return {"type": "str", "value": value}
    if isinstance(value, bytes):
        return {"type": "bytes", "value": value.hex()}
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise MarketDataPITError(f"{field_name} 的 datetime 必须带时区")
        return {"type": "datetime", "value": value.astimezone(UTC).isoformat()}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, time):
        if value.tzinfo is not None and value.utcoffset() is None:
            raise MarketDataPITError(f"{field_name} 的 time 时区无效")
        return {"type": "time", "value": value.isoformat()}
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise MarketDataPITError(f"{field_name} 不得包含非有限 Decimal")
        return {"type": "decimal", "value": str(value)}
    raise MarketDataPITError(f"{field_name} 包含不支持的运行时值类型")


def _canonical_row(row: dict[str, object], columns: tuple[str, ...], field_name: str) -> str:
    return _canonical_json(
        {column: _canonical_cell(row[column], f"{field_name}.{column}") for column in columns},
        field_name,
    )


@dataclass(frozen=True, slots=True)
class MarketDataPITSpec:
    """一类标准化市场表的行级时间契约。

    ``key_columns`` 必须包含 ``event_time_column``，确保同一标的的不同 bar/tick 是不同的
    逻辑事实。``value_columns`` 是修订身份的一部分；调用者不能依赖未声明的列来判断修订。
    """

    kind: MarketDataKind
    key_columns: tuple[str, ...]
    event_time_column: str
    available_at_column: str
    value_columns: tuple[str, ...]
    schema_version: str
    spec_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MarketDataKind):
            raise MarketDataPITError("kind 必须是 MarketDataKind")
        keys = tuple(_required_field(value, "key_columns") for value in self.key_columns)
        values = tuple(_required_field(value, "value_columns") for value in self.value_columns)
        if not keys:
            raise MarketDataPITError("key_columns 不能为空")
        if not values:
            raise MarketDataPITError("value_columns 不能为空")
        if len(keys) != len(set(keys)):
            raise MarketDataPITError("key_columns 不能包含重复字段")
        if len(values) != len(set(values)):
            raise MarketDataPITError("value_columns 不能包含重复字段")
        event_time_column = _required_field(self.event_time_column, "event_time_column")
        available_at_column = _required_field(self.available_at_column, "available_at_column")
        if event_time_column not in keys:
            raise MarketDataPITError("event_time_column 必须包含在 key_columns 中")
        if available_at_column in keys or available_at_column in values:
            raise MarketDataPITError("available_at_column 不能同时是主键或数值字段")
        if set(keys).intersection(values):
            raise MarketDataPITError("key_columns 与 value_columns 不能重叠")
        schema_version = _required_text(self.schema_version, "schema_version")
        spec_hash = canonical_json_sha256(
            {
                "available_at_column": available_at_column,
                "event_time_column": event_time_column,
                "format": _PIT_SPEC_FORMAT,
                "key_columns": list(keys),
                "kind": self.kind.value,
                "schema_version": schema_version,
                "value_columns": list(values),
            }
        )
        object.__setattr__(self, "key_columns", keys)
        object.__setattr__(self, "value_columns", values)
        object.__setattr__(self, "event_time_column", event_time_column)
        object.__setattr__(self, "available_at_column", available_at_column)
        object.__setattr__(self, "schema_version", schema_version)
        object.__setattr__(self, "spec_hash", spec_hash)

    def as_mapping(self) -> dict[str, object]:
        return {
            "available_at_column": self.available_at_column,
            "event_time_column": self.event_time_column,
            "format": _PIT_SPEC_FORMAT,
            "key_columns": list(self.key_columns),
            "kind": self.kind.value,
            "schema_version": self.schema_version,
            "spec_hash": self.spec_hash,
            "value_columns": list(self.value_columns),
        }


@dataclass(frozen=True, slots=True)
class MarketDataRevision:
    """一条可比较的行级市场修订，不可由排序顺序替代。"""

    source_artifact_snapshot_hash: str
    spec_hash: str
    logical_key_json: str
    event_time: str
    available_at: datetime
    value_hash: str
    revision_id: str = field(init=False)

    def __post_init__(self) -> None:
        source_snapshot = _hash(self.source_artifact_snapshot_hash, "source_artifact_snapshot_hash")
        spec_hash = _hash(self.spec_hash, "spec_hash")
        if not isinstance(self.logical_key_json, str) or not self.logical_key_json:
            raise MarketDataPITError("logical_key_json 必须是非空 canonical JSON")
        try:
            key = json.loads(self.logical_key_json)
        except json.JSONDecodeError as exc:
            raise MarketDataPITError("logical_key_json 必须是 JSON") from exc
        canonical_key = _canonical_json(key, "logical_key_json")
        if canonical_key != self.logical_key_json or not isinstance(key, dict):
            raise MarketDataPITError("logical_key_json 必须是 canonical JSON 映射")
        event_time = _required_text(self.event_time, "event_time")
        available_at = _utc_datetime(self.available_at, "available_at")
        value_hash = _hash(self.value_hash, "value_hash")
        revision_id = canonical_json_sha256(
            {
                "available_at": available_at.isoformat(),
                "event_time": event_time,
                "format": _PIT_REVISION_FORMAT,
                "logical_key": key,
                "source_artifact_snapshot_hash": source_snapshot,
                "spec_hash": spec_hash,
                "value_hash": value_hash,
            }
        )
        object.__setattr__(self, "source_artifact_snapshot_hash", source_snapshot)
        object.__setattr__(self, "spec_hash", spec_hash)
        object.__setattr__(self, "logical_key_json", canonical_key)
        object.__setattr__(self, "event_time", event_time)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "value_hash", value_hash)
        object.__setattr__(self, "revision_id", revision_id)


@dataclass(frozen=True, slots=True)
class MarketDataSnapshot:
    """由某一 immutable DatasetVersion 在明确 ``as_of`` 生成的研究输入快照。"""

    SELECTION_MODE: ClassVar[str] = "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY"

    dataset_id: str
    dataset_version_hash: str
    source_artifact_snapshot_hash: str
    source_id: str
    source_config_sha256: str
    publication_authorization_hash: str
    publication_scope: PublicationScope
    spec: MarketDataPITSpec
    source_artifact_available_at: datetime
    as_of: datetime
    revisions: tuple[MarketDataRevision, ...]
    selected_frame_hash: str
    _frame: InitVar[pl.DataFrame]
    _frame_payload: bytes = field(init=False, repr=False, compare=False)
    snapshot_id: str = field(init=False)

    def __post_init__(self, _frame: pl.DataFrame) -> None:
        dataset_id = _required_text(self.dataset_id, "dataset_id")
        dataset_version_hash = _hash(self.dataset_version_hash, "dataset_version_hash")
        source_snapshot = _hash(self.source_artifact_snapshot_hash, "source_artifact_snapshot_hash")
        source_id = _required_text(self.source_id, "source_id")
        source_config_sha256 = _hash(self.source_config_sha256, "source_config_sha256")
        publication_authorization_hash = _hash(
            self.publication_authorization_hash,
            "publication_authorization_hash",
        )
        if not isinstance(self.publication_scope, PublicationScope):
            raise MarketDataPITError("publication_scope 必须是 PublicationScope")
        if self.publication_scope.dataset_id != dataset_id:
            raise MarketDataPITError(
                "publication_scope.dataset_id 必须与 PIT snapshot.dataset_id 一致"
            )
        if not isinstance(self.spec, MarketDataPITSpec):
            raise MarketDataPITError("spec 必须是 MarketDataPITSpec")
        source_artifact_available_at = _utc_datetime(
            self.source_artifact_available_at,
            "source_artifact_available_at",
        )
        as_of = _utc_datetime(self.as_of, "as_of")
        if source_artifact_available_at > as_of:
            raise MarketDataPITError("source artifact 在 as_of 时尚不可用")
        revisions = tuple(self.revisions)
        if not revisions or not all(isinstance(item, MarketDataRevision) for item in revisions):
            raise MarketDataPITError("revisions 必须是非空 MarketDataRevision 元组")
        if len({item.revision_id for item in revisions}) != len(revisions):
            raise MarketDataPITError("snapshot 不能包含重复 revision_id")
        if any(item.source_artifact_snapshot_hash != source_snapshot for item in revisions):
            raise MarketDataPITError("snapshot revisions 必须来自同一 source artifact snapshot")
        if any(item.spec_hash != self.spec.spec_hash for item in revisions):
            raise MarketDataPITError("snapshot revisions 必须与 PIT spec 一致")
        if any(item.available_at > as_of for item in revisions):
            raise MarketDataPITError("snapshot 不能包含 as_of 之后才可见的 revision")
        if any(item.available_at > source_artifact_available_at for item in revisions):
            raise MarketDataPITError("snapshot revision 不得晚于 source artifact 的 available_at")
        if not isinstance(_frame, pl.DataFrame):
            raise MarketDataPITError("snapshot frame 必须是 Polars DataFrame")
        frame = _frame.clone()
        _validate_frame_against_spec(frame, self.spec)
        frame_payload = canonical_frame_payload(frame)
        frame_hash = content_sha256(frame_payload, field_name="snapshot.frame")
        if frame_hash != _hash(self.selected_frame_hash, "selected_frame_hash"):
            raise MarketDataPITError("selected_frame_hash 必须精确匹配 snapshot frame")
        if frame.height != len(revisions):
            raise MarketDataPITError("snapshot frame 行数必须与 selected revisions 一致")
        frame_revisions = tuple(
            _revision_from_row(
                row,
                spec=self.spec,
                source_artifact_snapshot_hash=source_snapshot,
            )
            for row in frame.iter_rows(named=True)
        )
        if len({item.logical_key_json for item in frame_revisions}) != len(frame_revisions):
            raise MarketDataPITError("snapshot frame 不能包含重复 logical key")
        if tuple(sorted(item.revision_id for item in frame_revisions)) != tuple(
            sorted(item.revision_id for item in revisions)
        ):
            raise MarketDataPITError("snapshot revisions 必须精确匹配 snapshot frame")
        revision_ids = tuple(sorted(item.revision_id for item in revisions))
        snapshot_id = canonical_json_sha256(
            {
                "as_of": as_of.isoformat(),
                "dataset_id": dataset_id,
                "dataset_version_hash": dataset_version_hash,
                "format": _PIT_SNAPSHOT_FORMAT,
                "publication_authorization_hash": publication_authorization_hash,
                "publication_scope_hash": self.publication_scope.identity_hash,
                "revision_ids": list(revision_ids),
                "selected_frame_hash": frame_hash,
                "source_artifact_snapshot_hash": source_snapshot,
                "source_artifact_available_at": source_artifact_available_at.isoformat(),
                "source_config_sha256": source_config_sha256,
                "source_id": source_id,
                "spec_hash": self.spec.spec_hash,
            }
        )
        object.__setattr__(self, "dataset_id", dataset_id)
        object.__setattr__(self, "dataset_version_hash", dataset_version_hash)
        object.__setattr__(self, "source_artifact_snapshot_hash", source_snapshot)
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "source_config_sha256", source_config_sha256)
        object.__setattr__(self, "publication_authorization_hash", publication_authorization_hash)
        object.__setattr__(self, "source_artifact_available_at", source_artifact_available_at)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "revisions", tuple(sorted(revisions, key=lambda item: item.revision_id)))
        object.__setattr__(self, "selected_frame_hash", frame_hash)
        object.__setattr__(self, "_frame_payload", frame_payload)
        object.__setattr__(self, "snapshot_id", snapshot_id)

    @classmethod
    def from_selected_frame(
        cls,
        *,
        dataset_id: str,
        dataset_version_hash: str,
        source_artifact_snapshot_hash: str,
        source_id: str,
        source_config_sha256: str,
        publication_authorization_hash: str,
        publication_scope: PublicationScope,
        spec: MarketDataPITSpec,
        source_artifact_available_at: datetime,
        as_of: datetime,
        frame: pl.DataFrame,
    ) -> MarketDataSnapshot:
        """由已选择的 frame 构建快照，并把行与 revision 身份逐一绑定。

        该工厂仅用于已经完成 as-of 选择的受控调用方；研究入口仍会通过
        :class:`MarketDataPITSelector` 对 immutable DatasetVersion 重新计算，不能把它当成
        绕过数据集回放的授权能力。
        """

        if not isinstance(spec, MarketDataPITSpec):
            raise MarketDataPITError("spec 必须是 MarketDataPITSpec")
        if not isinstance(frame, pl.DataFrame):
            raise MarketDataPITError("frame 必须是 Polars DataFrame")
        _validate_frame_against_spec(frame, spec)
        source_snapshot = _hash(source_artifact_snapshot_hash, "source_artifact_snapshot_hash")
        revisions = tuple(
            _revision_from_row(
                row,
                spec=spec,
                source_artifact_snapshot_hash=source_snapshot,
            )
            for row in frame.iter_rows(named=True)
        )
        return cls(
            dataset_id=dataset_id,
            dataset_version_hash=dataset_version_hash,
            source_artifact_snapshot_hash=source_snapshot,
            source_id=source_id,
            source_config_sha256=source_config_sha256,
            publication_authorization_hash=publication_authorization_hash,
            publication_scope=publication_scope,
            spec=spec,
            source_artifact_available_at=source_artifact_available_at,
            as_of=as_of,
            revisions=revisions,
            selected_frame_hash=content_sha256(
                canonical_frame_payload(frame),
                field_name="selected market frame",
            ),
            _frame=frame,
        )

    @property
    def revision_ids(self) -> tuple[str, ...]:
        return tuple(item.revision_id for item in self.revisions)

    def selected_frame(self) -> pl.DataFrame:
        """从不可变 canonical payload 重建 frame，不暴露内部可变对象。"""

        frame = _decode_canonical_frame(self._frame_payload)
        if content_sha256(canonical_frame_payload(frame), field_name="snapshot.frame") != self.selected_frame_hash:
            raise MarketDataPITError("snapshot canonical frame 完整性校验失败")
        return frame

    def as_manifest_mapping(self) -> dict[str, object]:
        """返回可进入研究/回测 manifest 的无路径、无密钥快照证据。"""

        return {
            "as_of": self.as_of.isoformat(),
            "dataset_id": self.dataset_id,
            "dataset_version_hash": self.dataset_version_hash,
            "format": _PIT_SNAPSHOT_FORMAT,
            "publication_authorization_hash": self.publication_authorization_hash,
            "publication_scope": _publication_scope_mapping(self.publication_scope),
            "publication_scope_hash": self.publication_scope.identity_hash,
            "revision_ids": list(self.revision_ids),
            "row_count": len(self.revisions),
            "decision_time_safe": False,
            "selection_mode": self.SELECTION_MODE,
            "selected_frame_hash": self.selected_frame_hash,
            "snapshot_id": self.snapshot_id,
            "source_artifact_snapshot_hash": self.source_artifact_snapshot_hash,
            "source_artifact_available_at": self.source_artifact_available_at.isoformat(),
            "source_config_sha256": self.source_config_sha256,
            "source_id": self.source_id,
            "spec": self.spec.as_mapping(),
        }


def _publication_scope_mapping(scope: PublicationScope) -> dict[str, object]:
    """把已冻结授权用途写入 PIT manifest，避免下游扩大数据许可范围。"""

    return {
        "actual_contract_data": scope.actual_contract_data,
        "asset_type": scope.asset_type,
        "dataset_id": scope.dataset_id,
        "environment": scope.environment,
        "exchanges": list(scope.exchanges),
        "frequency": scope.frequency,
        "market": scope.market,
        "products": list(scope.products),
        "purpose": scope.purpose.value,
        "requires_authoritative_calendar": scope.requires_authoritative_calendar,
        "requires_authoritative_dynamic_rules": scope.requires_authoritative_dynamic_rules,
    }


def _publication_scope_from_authorization(scope: dict[str, object]) -> PublicationScope:
    """从已校验授权收据严格重建 scope，拒绝缺失或扩大的字段。"""

    expected = {
        "actual_contract_data",
        "asset_type",
        "dataset_id",
        "environment",
        "exchanges",
        "frequency",
        "market",
        "products",
        "purpose",
        "requires_authoritative_calendar",
        "requires_authoritative_dynamic_rules",
    }
    if set(scope) != expected:
        raise MarketDataPITError("PIT publication authorization.scope 字段不完整或包含未知项")

    def text(name: str) -> str:
        return _required_text(scope[name], f"publication authorization.scope.{name}")

    def text_tuple(name: str) -> tuple[str, ...]:
        value = scope[name]
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise MarketDataPITError(f"publication authorization.scope.{name} 必须是字符串列表")
        return tuple(value)

    def bool_value(name: str) -> bool:
        value = scope[name]
        if type(value) is not bool:
            raise MarketDataPITError(f"publication authorization.scope.{name} 必须是布尔值")
        return value

    try:
        return PublicationScope(
            dataset_id=text("dataset_id"),
            market=text("market"),
            asset_type=text("asset_type"),
            frequency=text("frequency"),
            purpose=PublicationPurpose(text("purpose")),
            environment=text("environment"),
            exchanges=text_tuple("exchanges"),
            products=text_tuple("products"),
            actual_contract_data=bool_value("actual_contract_data"),
            requires_authoritative_calendar=bool_value("requires_authoritative_calendar"),
            requires_authoritative_dynamic_rules=bool_value(
                "requires_authoritative_dynamic_rules"
            ),
        )
    except (DataSourceProtocolError, ValueError) as exc:
        raise MarketDataPITError("PIT publication authorization.scope 无效") from exc


class MarketDataPITSelector:
    """从一份 immutable DatasetVersion 构造单一、显式 as-of 的市场数据快照。"""

    def __init__(self, store: ArtifactStore) -> None:
        if not isinstance(store, ArtifactStore):
            raise MarketDataPITError("store 必须是 ArtifactStore")
        self._store = store

    def select(
        self,
        *,
        dataset_version_hash: str,
        spec: MarketDataPITSpec,
        as_of: datetime,
    ) -> MarketDataSnapshot:
        """在 ``as_of`` 选择每个逻辑主键当时唯一可见的最新修订。

        这里的“最新”仅指同一逻辑事实中 ``available_at`` 最大且不晚于 ``as_of`` 的修订；
        它不是全局数据集的 mutable latest 指针。并列修订或缺少时间语义会失败关闭。
        """

        version_hash = _hash(dataset_version_hash, "dataset_version_hash")
        if not isinstance(spec, MarketDataPITSpec):
            raise MarketDataPITError("spec 必须是 MarketDataPITSpec")
        selection_time = _utc_datetime(as_of, "as_of")
        try:
            replay = self._store.replay_dataset_version(version_hash)
        except (ArtifactStoreError, FingerprintError) as exc:
            raise MarketDataPITError("immutable DatasetVersion 无法安全回放") from exc
        return self._select_replay(replay=replay, spec=spec, as_of=selection_time)

    def _select_replay(
        self,
        *,
        replay: DatasetReplay,
        spec: MarketDataPITSpec,
        as_of: datetime,
    ) -> MarketDataSnapshot:
        if len(replay.artifacts) != 1:
            raise MarketDataPITError("PIT 市场数据集必须精确包含一份 normalized 制品")
        replayed = replay.artifacts[0]
        stored = replayed.stored
        snapshot = stored.snapshot
        if snapshot.kind is not ArtifactKind.NORMALIZED:
            raise MarketDataPITError("PIT 市场数据集只能消费 normalized 制品")
        if snapshot.quality_status is not QualityStatus.PASS:
            raise MarketDataPITError("PIT 市场数据集的 normalized 制品质量必须为 PASS")
        if stored.quality_assessment_hash is None:
            raise MarketDataPITError(
                "PIT 市场数据集缺少不可变 quality assessment，拒绝手工制品回放"
            )
        if stored.publication_authorization_hash is None:
            raise MarketDataPITError(
                "PIT 市场数据集缺少不可变 publication authorization，拒绝未授权制品回放"
            )
        try:
            assessment = self._store.load_quality_assessment(snapshot.snapshot_hash)
            authorization = self._store.load_publication_authorization(
                stored.publication_authorization_hash
            )
        except ArtifactStoreError as exc:
            raise MarketDataPITError("PIT 市场数据集的质量或授权证据无法安全读取") from exc
        if assessment.assessment.aggregate_status is not QualityStatus.PASS:
            raise MarketDataPITError("PIT 市场数据集的不可变 quality assessment 必须为 PASS")
        scope = authorization.authorization.get("scope")
        if not isinstance(scope, dict):
            raise MarketDataPITError("PIT publication authorization 缺少 scope")
        if scope.get("dataset_id") != replay.dataset_version.dataset_id:
            raise MarketDataPITError("PIT publication authorization.dataset_id 与 DatasetVersion 不一致")
        publication_scope = _publication_scope_from_authorization(scope)
        if publication_scope.dataset_id != replay.dataset_version.dataset_id:
            raise MarketDataPITError(
                "PIT publication authorization.scope.dataset_id 与 DatasetVersion 不一致"
            )
        if publication_scope.purpose not in {
            PublicationPurpose.HISTORICAL_BACKTEST,
            PublicationPurpose.INTERNAL_RESEARCH,
        }:
            raise MarketDataPITError("PIT publication authorization 不覆盖历史回测或内部研究用途")
        if snapshot.schema_version != spec.schema_version:
            raise MarketDataPITError("PIT spec.schema_version 必须与 normalized 制品一致")
        if replay.dataset_version.schema_version != spec.schema_version:
            raise MarketDataPITError("PIT spec.schema_version 必须与 immutable DatasetVersion 一致")
        if snapshot.available_at > as_of:
            raise MarketDataPITError("normalized 制品在 as_of 时尚不可用")
        frame = _decode_canonical_frame(replayed.payload)
        if canonical_frame_payload(frame) != replayed.payload:
            raise MarketDataPITError("normalized 制品 payload 不能无损重建 canonical frame")
        _validate_frame_against_spec(frame, spec)

        working = frame.with_row_index("__northstar_pit_row_index")
        revisions_by_key: dict[str, list[tuple[int, MarketDataRevision]]] = {}
        for record in working.iter_rows(named=True):
            row_index = record.pop("__northstar_pit_row_index")
            if not isinstance(row_index, int):  # Polars row index 的防御性收窄。
                raise MarketDataPITError("PIT 行索引无效")
            revision = _revision_from_row(
                record,
                spec=spec,
                source_artifact_snapshot_hash=snapshot.snapshot_hash,
            )
            if revision.available_at > snapshot.available_at:
                raise MarketDataPITError(
                    "PIT row.available_at 不得晚于其 immutable normalized 制品的 available_at"
                )
            revisions_by_key.setdefault(revision.logical_key_json, []).append((row_index, revision))

        selected: list[tuple[int, MarketDataRevision]] = []
        for logical_key, candidates in sorted(revisions_by_key.items()):
            visible = [item for item in candidates if item[1].available_at <= as_of]
            if not visible:
                continue
            selected_at = max(item[1].available_at for item in visible)
            at_same_time = [item for item in visible if item[1].available_at == selected_at]
            if len(at_same_time) != 1:
                hashes = {item[1].value_hash for item in at_same_time}
                if len(hashes) > 1:
                    raise MarketDataPITError(
                        f"逻辑主键 {logical_key} 在相同 available_at 存在冲突修订"
                    )
                raise MarketDataPITError(
                    f"逻辑主键 {logical_key} 在相同 available_at 存在重复修订"
                )
            selected.append(at_same_time[0])
        if not selected:
            raise MarketDataPITError("as_of 时没有任何可见市场数据 revision")

        selected_indices = {row_index for row_index, _ in selected}
        selected_frame = (
            working.filter(pl.col("__northstar_pit_row_index").is_in(sorted(selected_indices)))
            .drop("__northstar_pit_row_index")
        )
        selected_revisions = tuple(revision for _, revision in selected)
        return MarketDataSnapshot(
            dataset_id=replay.dataset_version.dataset_id,
            dataset_version_hash=replay.dataset_version.version_hash,
            source_artifact_snapshot_hash=snapshot.snapshot_hash,
            source_id=stored.source.source_id,
            source_config_sha256=stored.source.config_sha256,
            publication_authorization_hash=authorization.authorization_hash,
            publication_scope=publication_scope,
            spec=spec,
            source_artifact_available_at=snapshot.available_at,
            as_of=as_of,
            revisions=selected_revisions,
            selected_frame_hash=content_sha256(
                canonical_frame_payload(selected_frame),
                field_name="selected market frame",
            ),
            _frame=selected_frame,
        )


def _validate_frame_against_spec(frame: pl.DataFrame, spec: MarketDataPITSpec) -> None:
    required = [*spec.key_columns, spec.available_at_column, *spec.value_columns]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise MarketDataPITError("PIT normalized frame 缺少字段: " + ", ".join(missing))
    unexpected = [column for column in frame.columns if column not in required]
    if unexpected:
        raise MarketDataPITError("PIT normalized frame 包含未在 spec 声明的字段: " + ", ".join(unexpected))
    if frame.is_empty():
        raise MarketDataPITError("PIT normalized frame 不能为空")
    for column in (*spec.key_columns, spec.available_at_column):
        if frame.get_column(column).null_count() > 0:
            raise MarketDataPITError(f"PIT 字段 {column} 不能包含空值")
    available_dtype = frame.schema[spec.available_at_column]
    if not isinstance(available_dtype, pl.Datetime):
        raise MarketDataPITError("PIT available_at_column 必须是带时区的 Datetime")
    if available_dtype.time_zone is None:
        raise MarketDataPITError("PIT available_at_column 必须明确时区")
    event_dtype = frame.schema[spec.event_time_column]
    if event_dtype != pl.Date and not isinstance(event_dtype, pl.Datetime):
        raise MarketDataPITError("PIT event_time_column 必须是 Date 或带时区 Datetime")
    if isinstance(event_dtype, pl.Datetime) and event_dtype.time_zone is None:
        raise MarketDataPITError("PIT Datetime event_time_column 必须明确时区")


def _revision_from_row(
    row: dict[str, object],
    *,
    spec: MarketDataPITSpec,
    source_artifact_snapshot_hash: str,
) -> MarketDataRevision:
    available_at = _utc_datetime(row[spec.available_at_column], "row.available_at")
    event_value = row[spec.event_time_column]
    if isinstance(event_value, datetime):
        event_datetime = _utc_datetime(event_value, "event_time")
        if event_datetime > available_at:
            raise MarketDataPITError("PIT event_time 不能晚于该行 available_at")
        event_time = event_datetime.isoformat()
    elif isinstance(event_value, date):
        if event_value > available_at.date():
            raise MarketDataPITError("PIT event_date 不能晚于该行 available_at 的日期")
        event_time = event_value.isoformat()
    else:  # schema 验证已检查；这里处理 Arrow/Polars 转换异常。
        raise MarketDataPITError("event_time 必须是 Date 或带时区 Datetime")
    logical_key_json = _canonical_row(row, spec.key_columns, "logical_key")
    value_hash = canonical_json_sha256(
        {
            "values": {
                column: _canonical_cell(row[column], f"value.{column}")
                for column in spec.value_columns
            }
        }
    )
    return MarketDataRevision(
        source_artifact_snapshot_hash=source_artifact_snapshot_hash,
        spec_hash=spec.spec_hash,
        logical_key_json=logical_key_json,
        event_time=event_time,
        available_at=available_at,
        value_hash=value_hash,
    )


def _json_load_no_duplicates(payload: bytes) -> object:
    if not isinstance(payload, bytes):
        raise MarketDataPITError("canonical payload 必须是 bytes")

    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise MarketDataPITError("canonical payload 不能包含重复 JSON 键")
            result[key] = value
        return result

    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketDataPITError("canonical payload 必须是 UTF-8 JSON") from exc


def _decode_canonical_frame(payload: bytes) -> pl.DataFrame:
    """严格解码质量核心的 canonical frame 格式，拒绝未知字段与 dtype。"""

    loaded = _json_load_no_duplicates(payload)
    if not isinstance(loaded, dict) or set(loaded) != {"format", "rows", "schema"}:
        raise MarketDataPITError("canonical frame 顶层字段不受支持")
    if loaded["format"] != _CANONICAL_FRAME_FORMAT:
        raise MarketDataPITError("PIT 只接受 quality canonical frame payload")
    schema_payload = loaded["schema"]
    rows_payload = loaded["rows"]
    if not isinstance(schema_payload, list) or not isinstance(rows_payload, list):
        raise MarketDataPITError("canonical frame.schema 与 rows 必须是列表")
    columns: list[str] = []
    schema: dict[str, pl.DataType] = {}
    for item in schema_payload:
        if not isinstance(item, dict) or set(item) != {"dtype", "name"}:
            raise MarketDataPITError("canonical frame.schema 条目不受支持")
        column = _required_field(item["name"], "canonical frame column")
        if column in schema:
            raise MarketDataPITError("canonical frame.schema 不能包含重复列")
        dtype = _decode_dtype(item["dtype"])
        columns.append(column)
        schema[column] = dtype
    if not columns:
        raise MarketDataPITError("canonical frame.schema 不能为空")
    rows: list[dict[str, object]] = []
    for item in rows_payload:
        if not isinstance(item, dict) or set(item) != set(columns):
            raise MarketDataPITError("canonical frame 行字段必须与 schema 精确一致")
        row: dict[str, object] = {}
        for column in columns:
            row[column] = _decode_cell(item[column], f"canonical frame.{column}")
        rows.append(row)
    try:
        return pl.DataFrame(rows, schema=schema, strict=True)
    except (TypeError, ValueError, pl.exceptions.PolarsError) as exc:
        raise MarketDataPITError("canonical frame 无法按声明 schema 重建") from exc


def _decode_dtype(value: object) -> Any:
    if not isinstance(value, str):
        raise MarketDataPITError("canonical frame dtype 必须是文本")
    # Polars 的 stubs 将 ``pl.Int64`` 一类 dtype class 与 ``DataType`` 实例区分；这里的
    # schema API 合法地接受两者，故仅在这个解码边界保留 Any，不把它泄漏到公共模型。
    simple: dict[str, Any] = {
        "Boolean": pl.Boolean,
        "Date": pl.Date,
        "Float32": pl.Float32,
        "Float64": pl.Float64,
        "Int8": pl.Int8,
        "Int16": pl.Int16,
        "Int32": pl.Int32,
        "Int64": pl.Int64,
        "String": pl.String,
        "Time": pl.Time,
        "UInt8": pl.UInt8,
        "UInt16": pl.UInt16,
        "UInt32": pl.UInt32,
        "UInt64": pl.UInt64,
        "Binary": pl.Binary,
    }
    if value in simple:
        return simple[value]
    match = _DATETIME_DTYPE_RE.fullmatch(value)
    if match is not None:
        timezone_value = match.group("timezone")
        timezone_name = None if timezone_value == "None" else timezone_value[1:-1]
        unit = match.group("unit")
        if unit not in {"ns", "us", "ms"}:  # 正则已限制；保留类型与运行时双重收窄。
            raise MarketDataPITError("PIT Datetime time_unit 不受支持")
        return pl.Datetime(cast(Literal["ns", "us", "ms"], unit), timezone_name)
    raise MarketDataPITError(f"PIT 不支持的 canonical frame dtype: {value}")


def _decode_cell(value: object, field_name: str) -> object:
    if not isinstance(value, dict) or set(value).difference({"type", "value"}) or "type" not in value:
        raise MarketDataPITError(f"{field_name} 的 canonical cell 不受支持")
    kind = value["type"]
    encoded = value.get("value")
    if kind == "null":
        if "value" in value:
            raise MarketDataPITError(f"{field_name} 的 null cell 不得携带 value")
        return None
    if kind == "bool" and isinstance(encoded, bool):
        return encoded
    if kind == "int" and isinstance(encoded, str):
        try:
            return int(encoded)
        except ValueError as exc:
            raise MarketDataPITError(f"{field_name} 的 int cell 无效") from exc
    if kind == "float" and isinstance(encoded, str):
        try:
            decoded = float.fromhex(encoded)
        except ValueError as exc:
            raise MarketDataPITError(f"{field_name} 的 float cell 无效") from exc
        if not math.isfinite(decoded):
            raise MarketDataPITError(f"{field_name} 不得包含非有限 float")
        return decoded
    if kind == "str" and isinstance(encoded, str):
        return encoded
    if kind == "bytes" and isinstance(encoded, str):
        try:
            return bytes.fromhex(encoded)
        except ValueError as exc:
            raise MarketDataPITError(f"{field_name} 的 bytes cell 无效") from exc
    if kind == "date" and isinstance(encoded, str):
        try:
            return date.fromisoformat(encoded)
        except ValueError as exc:
            raise MarketDataPITError(f"{field_name} 的 date cell 无效") from exc
    if kind == "datetime" and isinstance(encoded, str):
        try:
            datetime_value = datetime.fromisoformat(encoded)
        except ValueError as exc:
            raise MarketDataPITError(f"{field_name} 的 datetime cell 无效") from exc
        return _utc_datetime(datetime_value, field_name)
    if kind == "time" and isinstance(encoded, str):
        try:
            return time.fromisoformat(encoded)
        except ValueError as exc:
            raise MarketDataPITError(f"{field_name} 的 time cell 无效") from exc
    if kind == "decimal" and isinstance(encoded, str):
        try:
            decimal_value = Decimal(encoded)
        except Exception as exc:  # Decimal 的异常类型依赖实现；统一转换为领域错误。
            raise MarketDataPITError(f"{field_name} 的 decimal cell 无效") from exc
        if not decimal_value.is_finite():
            raise MarketDataPITError(f"{field_name} 不得包含非有限 Decimal")
        return decimal_value
    raise MarketDataPITError(f"{field_name} 的 canonical cell 类型或值不匹配")

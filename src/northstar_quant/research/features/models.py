"""Feature Registry 的不可变领域对象。

本模块只描述可复现研究特征的定义、版本、输入证据、血缘和数值；它不读取
``latest`` 数据、不访问当前时钟，也不执行策略或交易。特征计算器、制品持久化
和逐决策 PIT 回放会在后续工作包接入这些稳定契约。

所有时间均使用带时区的 ``datetime`` 并规范化为 UTC。一个 FeatureLineage 只能
引用在其 ``decision_at`` 时已经可用的不可变输入；FeatureValue 又必须不早于该
lineage 的输出可用时间。因此调用方不能把未来数据伪装成历史特征值。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
import json
import math
import re
from typing import TYPE_CHECKING

from northstar_quant.data_platform.artifacts.fingerprints import (
    FingerprintError,
    canonical_json_sha256,
    require_sha256,
)

if TYPE_CHECKING:
    from northstar_quant.data_platform.market.pit import MarketDataPITSpec, MarketDataSnapshot


_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_FEATURE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_SAFE_TEXT_RE = re.compile(r"^[^\x00\r\n]+$")


class FeatureRegistryError(ValueError):
    """特征定义、血缘或回填证据不满足可复现/PIT 约束。"""


class FeatureDeterminismError(FeatureRegistryError):
    """同一特征血缘的重复回填产生了不同结果。"""


class FeatureDependencyKind(str, Enum):
    """特征可消费的不可变输入类别。"""

    DATASET = "dataset"
    FEATURE = "feature"


_STATIC_SELECTION_MODE = "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY"
_PER_DECISION_SELECTION_MODE = "PER_DECISION_POINT_IN_TIME_REPLAY"
_PIT_SPEC_FORMAT = "northstar.market-data-pit-spec.v1"


def _required_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise FeatureRegistryError(f"{field_name} 必须是小写 snake_case 标识符")
    return value


def _required_feature_id(value: object, field_name: str = "feature_id") -> str:
    if not isinstance(value, str) or _FEATURE_ID_RE.fullmatch(value) is None:
        raise FeatureRegistryError(f"{field_name} 必须是至少两段的小写点分标识，例如 technical.sma")
    return value


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or _SAFE_TEXT_RE.fullmatch(value) is None:
        raise FeatureRegistryError(f"{field_name} 必须是非空单行文本")
    text = value.strip()
    if text.startswith(("/", "\\\\", "~/", "~\\")) or re.match(r"^[A-Za-z]:[\\/]", text):
        raise FeatureRegistryError(f"{field_name} 不得包含本机绝对路径")
    return text


def _required_version(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _VERSION_RE.fullmatch(value) is None:
        raise FeatureRegistryError(f"{field_name} 必须是稳定版本文本")
    return value


def _hash(value: object, field_name: str) -> str:
    try:
        return require_sha256(value, field_name=field_name)  # type: ignore[arg-type]
    except FingerprintError as exc:
        raise FeatureRegistryError(str(exc)) from exc


def _utc_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise FeatureRegistryError(f"{field_name} 必须是带时区的 datetime")
    return value.astimezone(UTC)


def _event_time(value: object, field_name: str) -> date | datetime:
    """规范化 feature 观测时间，同时保留日线 ``date`` 的真实语义。"""

    if isinstance(value, datetime):
        return _utc_datetime(value, field_name)
    if isinstance(value, date):
        return value
    raise FeatureRegistryError(f"{field_name} 必须是 date 或带时区的 datetime")


def _event_time_identity(value: date | datetime) -> dict[str, str]:
    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.isoformat()}
    return {"type": "date", "value": value.isoformat()}


def _canonical_json(value: object, field_name: str) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise FeatureRegistryError(f"{field_name} 必须是有限、可 JSON 序列化的值") from exc


def _canonical_mapping(value: Mapping[str, object], field_name: str) -> str:
    if not isinstance(value, Mapping):
        raise FeatureRegistryError(f"{field_name} 必须是映射")
    normalized: dict[str, object] = {}
    for key, item in value.items():
        normalized[_required_identifier(key, f"{field_name}.key")] = item
    return _canonical_json(normalized, field_name)


def _load_canonical_mapping(value: str, field_name: str) -> Mapping[str, object]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:  # pragma: no cover - protected by constructors
        raise FeatureRegistryError(f"{field_name} 必须是 canonical JSON") from exc
    if not isinstance(decoded, dict) or _canonical_json(decoded, field_name) != value:
        raise FeatureRegistryError(f"{field_name} 必须是 canonical JSON 映射")
    return decoded


_PARAMETER_SCHEMA_FIELDS = frozenset({"type", "required", "minimum", "maximum", "allowed_values"})
_PARAMETER_TYPES = frozenset({"integer", "number", "string", "boolean"})


def _finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FeatureRegistryError(f"{field_name} 必须是有限数值")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise FeatureRegistryError(f"{field_name} 必须是有限数值")
    return numeric


def _canonical_parameter_schema(value: Mapping[str, object]) -> str:
    """校验并规范化 FeatureVersion 的显式参数合同。"""

    if not isinstance(value, Mapping):
        raise FeatureRegistryError("parameter_schema 必须是映射")
    normalized: dict[str, dict[str, object]] = {}
    for raw_name, raw_rule in value.items():
        name = _required_identifier(raw_name, "parameter_schema.key")
        if not isinstance(raw_rule, Mapping):
            raise FeatureRegistryError(f"parameter_schema.{name} 必须是映射")
        if not all(isinstance(key, str) for key in raw_rule):
            raise FeatureRegistryError(f"parameter_schema.{name} 的字段名必须是文本")
        unknown = set(raw_rule).difference(_PARAMETER_SCHEMA_FIELDS)
        if unknown:
            raise FeatureRegistryError(
                f"parameter_schema.{name} 包含未知字段: {', '.join(sorted(unknown))}"
            )
        raw_type = raw_rule.get("type")
        if not isinstance(raw_type, str) or raw_type not in _PARAMETER_TYPES:
            raise FeatureRegistryError(
                f"parameter_schema.{name}.type 必须是 {', '.join(sorted(_PARAMETER_TYPES))}"
            )
        required = raw_rule.get("required")
        if type(required) is not bool:
            raise FeatureRegistryError(f"parameter_schema.{name}.required 必须是 bool")
        numeric_type = raw_type in {"integer", "number"}
        minimum = raw_rule.get("minimum")
        maximum = raw_rule.get("maximum")
        if (minimum is not None or maximum is not None) and not numeric_type:
            raise FeatureRegistryError(f"parameter_schema.{name} 的范围只适用于数值类型")
        if minimum is not None:
            minimum = _finite_number(minimum, f"parameter_schema.{name}.minimum")
        if maximum is not None:
            maximum = _finite_number(maximum, f"parameter_schema.{name}.maximum")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise FeatureRegistryError(f"parameter_schema.{name}.minimum 不能大于 maximum")
        allowed_values = raw_rule.get("allowed_values")
        if allowed_values is not None:
            if not isinstance(allowed_values, (list, tuple)) or not allowed_values:
                raise FeatureRegistryError(f"parameter_schema.{name}.allowed_values 必须是非空列表")
            canonical_allowed = tuple(
                _canonical_json(item, f"parameter_schema.{name}") for item in allowed_values
            )
            if len(canonical_allowed) != len(set(canonical_allowed)):
                raise FeatureRegistryError(f"parameter_schema.{name}.allowed_values 不能包含重复值")
            allowed_values = [json.loads(item) for item in canonical_allowed]
        rule: dict[str, object] = {"type": raw_type, "required": required}
        if minimum is not None:
            rule["minimum"] = minimum
        if maximum is not None:
            rule["maximum"] = maximum
        if allowed_values is not None:
            rule["allowed_values"] = allowed_values
        normalized[name] = rule
    return _canonical_json(normalized, "parameter_schema")


def _validate_parameters(parameters: Mapping[str, object], parameter_schema_json: str) -> str:
    """按 FeatureVersion 参数合同验证一次实际回填参数，并返回 canonical JSON。"""

    if not isinstance(parameters, Mapping):
        raise FeatureRegistryError("parameters 必须是映射")
    normalized = {
        _required_identifier(key, "parameters.key"): value for key, value in parameters.items()
    }
    schema = _load_canonical_mapping(parameter_schema_json, "parameter_schema_json")
    unknown = set(normalized).difference(schema)
    if unknown:
        raise FeatureRegistryError(f"parameters 包含未声明字段: {', '.join(sorted(unknown))}")
    missing = sorted(
        name
        for name, raw_rule in schema.items()
        if isinstance(raw_rule, Mapping)
        and raw_rule.get("required") is True
        and name not in normalized
    )
    if missing:
        raise FeatureRegistryError(f"parameters 缺少必需字段: {', '.join(missing)}")
    for name, value in normalized.items():
        raw_rule = schema[name]
        if not isinstance(raw_rule, Mapping):  # pragma: no cover - schema already validated.
            raise FeatureRegistryError("parameter_schema_json 无效")
        kind = raw_rule["type"]
        if kind == "integer":
            valid = isinstance(value, int) and not isinstance(value, bool)
        elif kind == "number":
            valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif kind == "string":
            valid = isinstance(value, str)
        else:
            valid = type(value) is bool
        if not valid:
            raise FeatureRegistryError(f"parameters.{name} 不符合声明类型 {kind}")
        if kind in {"integer", "number"}:
            numeric = _finite_number(value, f"parameters.{name}")
            minimum = raw_rule.get("minimum")
            maximum = raw_rule.get("maximum")
            if isinstance(minimum, (int, float)) and numeric < float(minimum):
                raise FeatureRegistryError(f"parameters.{name} 不能小于 minimum")
            if isinstance(maximum, (int, float)) and numeric > float(maximum):
                raise FeatureRegistryError(f"parameters.{name} 不能大于 maximum")
        allowed_values = raw_rule.get("allowed_values")
        if isinstance(allowed_values, list):
            if _canonical_json(value, f"parameters.{name}") not in {
                _canonical_json(item, f"parameters.{name}") for item in allowed_values
            }:
                raise FeatureRegistryError(f"parameters.{name} 不在 allowed_values 中")
    return _canonical_mapping(normalized, "parameters")


def _pit_spec_from_mapping(
    value: Mapping[str, object],
    *,
    expected_hash: str,
) -> "MarketDataPITSpec":
    """严格重建 P1 PIT spec；血缘不能只保存无法重放的 spec hash。"""

    from northstar_quant.data_platform.market.pit import (
        MarketDataKind,
        MarketDataPITError,
        MarketDataPITSpec,
    )

    expected_fields = {
        "available_at_column",
        "event_time_column",
        "format",
        "key_columns",
        "kind",
        "schema_version",
        "spec_hash",
        "value_columns",
    }
    if set(value) != expected_fields:
        raise FeatureRegistryError("pit_spec_json 必须包含完整且精确的 P1 PIT spec 字段")
    if value.get("format") != _PIT_SPEC_FORMAT:
        raise FeatureRegistryError("pit_spec_json.format 不受支持")
    if value.get("spec_hash") != expected_hash:
        raise FeatureRegistryError("pit_spec_json.spec_hash 必须与 pit_spec_hash 一致")

    def string_tuple(field_name: str) -> tuple[str, ...]:
        raw = value.get(field_name)
        if not isinstance(raw, list) or not raw or not all(isinstance(item, str) for item in raw):
            raise FeatureRegistryError(f"pit_spec_json.{field_name} 必须是非空字符串列表")
        return tuple(raw)

    kind = value.get("kind")
    event_time_column = value.get("event_time_column")
    available_at_column = value.get("available_at_column")
    schema_version = value.get("schema_version")
    if not isinstance(kind, str):
        raise FeatureRegistryError("pit_spec_json.kind 必须是文本")
    if not isinstance(event_time_column, str):
        raise FeatureRegistryError("pit_spec_json.event_time_column 必须是文本")
    if not isinstance(available_at_column, str):
        raise FeatureRegistryError("pit_spec_json.available_at_column 必须是文本")
    if not isinstance(schema_version, str):
        raise FeatureRegistryError("pit_spec_json.schema_version 必须是文本")
    try:
        spec = MarketDataPITSpec(
            kind=MarketDataKind(kind),
            key_columns=string_tuple("key_columns"),
            event_time_column=event_time_column,
            available_at_column=available_at_column,
            value_columns=string_tuple("value_columns"),
            schema_version=schema_version,
        )
    except (MarketDataPITError, ValueError) as exc:
        raise FeatureRegistryError("pit_spec_json 不能重建有效的 P1 PIT spec") from exc
    if spec.spec_hash != expected_hash:
        raise FeatureRegistryError("pit_spec_json 重建后的 spec_hash 与冻结证据不一致")
    return spec


@dataclass(frozen=True, slots=True)
class FeatureSpec:
    """一个稳定特征定义，不随实现或参数变更而覆盖。

    ``feature_id`` 表示研究语义，例如 ``technical.sma``；实现代码和参数约束属于
    :class:`FeatureVersion`。显式的输入、lookback、缺失值和时间语义是未来每个特征
    家族都必须声明的研究合同。
    """

    feature_id: str
    family: str
    description: str
    input_columns: tuple[str, ...]
    input_schema_version: str
    entity_key_columns: tuple[str, ...]
    output_column: str
    event_time_column: str
    available_at_column: str
    lookback_semantics: str
    missing_value_semantics: str
    spec_hash: str = field(init=False)

    def __post_init__(self) -> None:
        feature_id = _required_feature_id(self.feature_id)
        family = _required_identifier(self.family, "family")
        if feature_id.split(".", 1)[0] != family:
            raise FeatureRegistryError("feature_id 的首段必须与 family 一致")
        description = _required_text(self.description, "description")
        input_columns = tuple(
            _required_identifier(column, "input_columns") for column in self.input_columns
        )
        if not input_columns:
            raise FeatureRegistryError("input_columns 不能为空")
        if len(input_columns) != len(set(input_columns)):
            raise FeatureRegistryError("input_columns 不能包含重复字段")
        input_columns = tuple(sorted(input_columns))
        input_schema_version = _required_version(self.input_schema_version, "input_schema_version")
        entity_key_columns = tuple(
            _required_identifier(column, "entity_key_columns") for column in self.entity_key_columns
        )
        if not entity_key_columns:
            raise FeatureRegistryError("entity_key_columns 不能为空")
        if len(entity_key_columns) != len(set(entity_key_columns)):
            raise FeatureRegistryError("entity_key_columns 不能包含重复字段")
        entity_key_columns = tuple(sorted(entity_key_columns))
        if not set(entity_key_columns).issubset(input_columns):
            raise FeatureRegistryError("entity_key_columns 必须包含在 input_columns 中")
        output_column = _required_identifier(self.output_column, "output_column")
        if output_column in input_columns:
            raise FeatureRegistryError("output_column 不能覆盖输入字段")
        event_time_column = _required_identifier(self.event_time_column, "event_time_column")
        available_at_column = _required_identifier(self.available_at_column, "available_at_column")
        if event_time_column not in input_columns or available_at_column not in input_columns:
            raise FeatureRegistryError(
                "event_time_column 和 available_at_column 必须在 input_columns 中"
            )
        if event_time_column == available_at_column:
            raise FeatureRegistryError("event_time_column 与 available_at_column 不能相同")
        lookback_semantics = _required_text(self.lookback_semantics, "lookback_semantics")
        missing_value_semantics = _required_text(
            self.missing_value_semantics, "missing_value_semantics"
        )
        spec_hash = canonical_json_sha256(
            {
                "available_at_column": available_at_column,
                "description": description,
                "entity_key_columns": list(entity_key_columns),
                "event_time_column": event_time_column,
                "family": family,
                "feature_id": feature_id,
                "format": "northstar.feature-spec.v1",
                "input_columns": list(input_columns),
                "input_schema_version": input_schema_version,
                "lookback_semantics": lookback_semantics,
                "missing_value_semantics": missing_value_semantics,
                "output_column": output_column,
            }
        )
        object.__setattr__(self, "feature_id", feature_id)
        object.__setattr__(self, "family", family)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "input_columns", input_columns)
        object.__setattr__(self, "input_schema_version", input_schema_version)
        object.__setattr__(self, "entity_key_columns", entity_key_columns)
        object.__setattr__(self, "output_column", output_column)
        object.__setattr__(self, "event_time_column", event_time_column)
        object.__setattr__(self, "available_at_column", available_at_column)
        object.__setattr__(self, "lookback_semantics", lookback_semantics)
        object.__setattr__(self, "missing_value_semantics", missing_value_semantics)
        object.__setattr__(self, "spec_hash", spec_hash)


@dataclass(frozen=True, slots=True)
class FeatureVersion:
    """一个不可变的特征实现版本及其参数合同。"""

    feature_id: str
    spec_hash: str
    version: str
    implementation_hash: str
    code_revision: str
    parameter_schema_json: str
    version_hash: str = field(init=False)

    @classmethod
    def from_spec(
        cls,
        spec: FeatureSpec,
        *,
        version: str,
        implementation_hash: str,
        code_revision: str,
        parameter_schema: Mapping[str, object],
    ) -> "FeatureVersion":
        """由已验证定义创建版本，避免调用方手工复制其身份字段。"""

        if not isinstance(spec, FeatureSpec):
            raise FeatureRegistryError("spec 必须是 FeatureSpec")
        return cls(
            feature_id=spec.feature_id,
            spec_hash=spec.spec_hash,
            version=version,
            implementation_hash=implementation_hash,
            code_revision=code_revision,
            parameter_schema_json=_canonical_parameter_schema(parameter_schema),
        )

    @property
    def parameter_schema(self) -> Mapping[str, object]:
        """返回新建的不可变语义副本；调用方修改它不会影响版本身份。"""

        return dict(_load_canonical_mapping(self.parameter_schema_json, "parameter_schema_json"))

    def __post_init__(self) -> None:
        feature_id = _required_feature_id(self.feature_id)
        spec_hash = _hash(self.spec_hash, "spec_hash")
        version = _required_version(self.version, "version")
        implementation_hash = _hash(self.implementation_hash, "implementation_hash")
        code_revision = _required_text(self.code_revision, "code_revision")
        parameter_schema_json = _canonical_parameter_schema(
            _load_canonical_mapping(
                _required_text(self.parameter_schema_json, "parameter_schema_json"),
                "parameter_schema_json",
            )
        )
        parameter_schema = _load_canonical_mapping(parameter_schema_json, "parameter_schema_json")
        version_hash = canonical_json_sha256(
            {
                "code_revision": code_revision,
                "feature_id": feature_id,
                "format": "northstar.feature-version.v1",
                "implementation_hash": implementation_hash,
                "parameter_schema": parameter_schema,
                "spec_hash": spec_hash,
                "version": version,
            }
        )
        object.__setattr__(self, "feature_id", feature_id)
        object.__setattr__(self, "spec_hash", spec_hash)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "implementation_hash", implementation_hash)
        object.__setattr__(self, "code_revision", code_revision)
        object.__setattr__(self, "parameter_schema_json", parameter_schema_json)
        object.__setattr__(self, "version_hash", version_hash)


@dataclass(frozen=True, slots=True)
class FeatureDatasetEvidence:
    """Feature 输入所绑定的完整、不可变 P1 MarketDataSnapshot 证据。

    不能只保存 ``DatasetVersion`` 或 snapshot ID：同一数据集在不同 ``as_of`` 的 revision
    选择不同。该对象把构成快照身份的 frame、PIT spec、revision、来源与授权证据一并冻结，
    以便研究 manifest 在不读取 ``latest`` 的前提下审计输入。
    """

    dataset_id: str
    dataset_version_hash: str
    snapshot_id: str
    selected_frame_hash: str
    pit_spec_hash: str
    pit_spec_json: str
    revision_ids: tuple[str, ...]
    source_artifact_snapshot_hash: str
    source_artifact_available_at: datetime
    source_id: str
    source_config_sha256: str
    publication_authorization_hash: str
    publication_scope_json: str
    as_of: datetime
    evidence_hash: str = field(init=False)

    @classmethod
    def from_market_data_snapshot(cls, snapshot: MarketDataSnapshot) -> "FeatureDatasetEvidence":
        """从 P1 snapshot 完整复制无密钥、可进入研究 manifest 的证据。"""

        from northstar_quant.data_platform.market.pit import MarketDataSnapshot

        if not isinstance(snapshot, MarketDataSnapshot):
            raise FeatureRegistryError("snapshot 必须是已验证的 MarketDataSnapshot")
        manifest = snapshot.as_manifest_mapping()
        decision_time_safe = manifest.get("decision_time_safe")
        selection_mode = manifest.get("selection_mode")
        publication_scope = manifest.get("publication_scope")
        if decision_time_safe is not False or selection_mode != _STATIC_SELECTION_MODE:
            raise FeatureRegistryError(
                "P2-WP01 只接受 P1 当前 STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY 输入"
            )
        if not isinstance(publication_scope, Mapping):
            raise FeatureRegistryError("MarketDataSnapshot 缺少 publication_scope")
        return cls(
            dataset_id=snapshot.dataset_id,
            dataset_version_hash=snapshot.dataset_version_hash,
            snapshot_id=snapshot.snapshot_id,
            selected_frame_hash=snapshot.selected_frame_hash,
            pit_spec_hash=snapshot.spec.spec_hash,
            pit_spec_json=_canonical_mapping(snapshot.spec.as_mapping(), "pit_spec"),
            revision_ids=snapshot.revision_ids,
            source_artifact_snapshot_hash=snapshot.source_artifact_snapshot_hash,
            source_artifact_available_at=snapshot.source_artifact_available_at,
            source_id=snapshot.source_id,
            source_config_sha256=snapshot.source_config_sha256,
            publication_authorization_hash=snapshot.publication_authorization_hash,
            publication_scope_json=_canonical_mapping(publication_scope, "publication_scope"),
            as_of=snapshot.as_of,
        )

    @property
    def publication_scope(self) -> Mapping[str, object]:
        """返回冻结授权范围的独立副本，避免研究消费者扩大许可。"""

        return dict(_load_canonical_mapping(self.publication_scope_json, "publication_scope_json"))

    @property
    def pit_spec(self) -> MarketDataPITSpec:
        """从冻结的完整 spec 重建 selector 所需的 P1 对象。"""

        return _pit_spec_from_mapping(
            _load_canonical_mapping(self.pit_spec_json, "pit_spec_json"),
            expected_hash=self.pit_spec_hash,
        )

    def __post_init__(self) -> None:
        dataset_id = _required_identifier(self.dataset_id, "dataset_id")
        dataset_version_hash = _hash(self.dataset_version_hash, "dataset_version_hash")
        snapshot_id = _hash(self.snapshot_id, "snapshot_id")
        selected_frame_hash = _hash(self.selected_frame_hash, "selected_frame_hash")
        pit_spec_hash = _hash(self.pit_spec_hash, "pit_spec_hash")
        pit_spec_json = _canonical_mapping(
            _load_canonical_mapping(
                _required_text(self.pit_spec_json, "pit_spec_json"), "pit_spec_json"
            ),
            "pit_spec_json",
        )
        _pit_spec_from_mapping(
            _load_canonical_mapping(pit_spec_json, "pit_spec_json"),
            expected_hash=pit_spec_hash,
        )
        revision_ids = tuple(_hash(item, "revision_ids") for item in self.revision_ids)
        if not revision_ids or len(revision_ids) != len(set(revision_ids)):
            raise FeatureRegistryError("revision_ids 必须是非空且不重复的 SHA-256 元组")
        revision_ids = tuple(sorted(revision_ids))
        source_artifact_snapshot_hash = _hash(
            self.source_artifact_snapshot_hash, "source_artifact_snapshot_hash"
        )
        source_artifact_available_at = _utc_datetime(
            self.source_artifact_available_at, "source_artifact_available_at"
        )
        source_id = _required_identifier(self.source_id, "source_id")
        source_config_sha256 = _hash(self.source_config_sha256, "source_config_sha256")
        publication_authorization_hash = _hash(
            self.publication_authorization_hash, "publication_authorization_hash"
        )
        publication_scope = _load_canonical_mapping(
            _required_text(self.publication_scope_json, "publication_scope_json"),
            "publication_scope_json",
        )
        as_of = _utc_datetime(self.as_of, "as_of")
        if source_artifact_available_at > as_of:
            raise FeatureRegistryError("source_artifact_available_at 不能晚于 as_of")
        evidence_hash = canonical_json_sha256(
            {
                "as_of": as_of.isoformat(),
                "dataset_id": dataset_id,
                "dataset_version_hash": dataset_version_hash,
                "format": "northstar.feature-dataset-evidence.v2",
                "pit_spec": _load_canonical_mapping(pit_spec_json, "pit_spec_json"),
                "pit_spec_hash": pit_spec_hash,
                "publication_authorization_hash": publication_authorization_hash,
                "publication_scope": publication_scope,
                "revision_ids": list(revision_ids),
                "selected_frame_hash": selected_frame_hash,
                "snapshot_id": snapshot_id,
                "source_artifact_snapshot_hash": source_artifact_snapshot_hash,
                "source_artifact_available_at": source_artifact_available_at.isoformat(),
                "source_config_sha256": source_config_sha256,
                "source_id": source_id,
            }
        )
        object.__setattr__(self, "dataset_id", dataset_id)
        object.__setattr__(self, "dataset_version_hash", dataset_version_hash)
        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "selected_frame_hash", selected_frame_hash)
        object.__setattr__(self, "pit_spec_hash", pit_spec_hash)
        object.__setattr__(self, "pit_spec_json", pit_spec_json)
        object.__setattr__(self, "revision_ids", revision_ids)
        object.__setattr__(self, "source_artifact_snapshot_hash", source_artifact_snapshot_hash)
        object.__setattr__(self, "source_artifact_available_at", source_artifact_available_at)
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "source_config_sha256", source_config_sha256)
        object.__setattr__(self, "publication_authorization_hash", publication_authorization_hash)
        object.__setattr__(
            self,
            "publication_scope_json",
            _canonical_mapping(publication_scope, "publication_scope"),
        )
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "evidence_hash", evidence_hash)


@dataclass(frozen=True, slots=True)
class FeatureDependency:
    """FeatureLineage 中的一条具名、不可变输入边。

    P2-WP01 仅允许静态 P1 market snapshot 进入受控 Registry。Feature 类型保留为领域
    表达，但尚没有可验证的上游 FeatureBackfill 存储，因此 Registry 不会接受它来物化回填。
    """

    role: str
    kind: FeatureDependencyKind
    reference_hash: str
    available_at: datetime
    dataset_evidence: FeatureDatasetEvidence | None = None
    feature_version_hash: str | None = None

    @classmethod
    def dataset(
        cls,
        *,
        role: str,
        evidence: FeatureDatasetEvidence,
    ) -> "FeatureDependency":
        """创建对完整 MarketDataSnapshotEvidence 的输入引用。"""

        if not isinstance(evidence, FeatureDatasetEvidence):
            raise FeatureRegistryError("evidence 必须是 FeatureDatasetEvidence")
        return cls(
            role=role,
            kind=FeatureDependencyKind.DATASET,
            reference_hash=evidence.snapshot_id,
            available_at=evidence.as_of,
            dataset_evidence=evidence,
        )

    @classmethod
    def from_market_data_snapshot(
        cls,
        *,
        role: str,
        snapshot: MarketDataSnapshot,
    ) -> "FeatureDependency":
        """由 P1 snapshot 构建依赖；Registry 仍会在消费前重算 snapshot。"""

        return cls.dataset(
            role=role,
            evidence=FeatureDatasetEvidence.from_market_data_snapshot(snapshot),
        )

    @classmethod
    def feature(
        cls,
        *,
        role: str,
        feature_version_hash: str,
        lineage_hash: str,
        available_at: datetime,
    ) -> "FeatureDependency":
        """表达上游特征边；P2-WP01 Registry 暂不把它作为可物化输入。"""

        return cls(
            role=role,
            kind=FeatureDependencyKind.FEATURE,
            reference_hash=lineage_hash,
            feature_version_hash=feature_version_hash,
            available_at=available_at,
        )

    @property
    def dataset_version_hash(self) -> str | None:
        return self.dataset_evidence.dataset_version_hash if self.dataset_evidence else None

    @property
    def selection_mode(self) -> str:
        return _STATIC_SELECTION_MODE

    @property
    def decision_time_safe(self) -> bool:
        return False

    def __post_init__(self) -> None:
        role = _required_identifier(self.role, "dependency.role")
        if not isinstance(self.kind, FeatureDependencyKind):
            raise FeatureRegistryError("dependency.kind 必须是 FeatureDependencyKind")
        reference_hash = _hash(self.reference_hash, "dependency.reference_hash")
        available_at = _utc_datetime(self.available_at, "dependency.available_at")
        if self.kind is FeatureDependencyKind.DATASET:
            evidence = self.dataset_evidence
            if not isinstance(evidence, FeatureDatasetEvidence):
                raise FeatureRegistryError("dataset dependency 必须绑定完整 FeatureDatasetEvidence")
            if evidence.snapshot_id != reference_hash or evidence.as_of != available_at:
                raise FeatureRegistryError(
                    "dataset dependency 必须与 FeatureDatasetEvidence 的 snapshot/as_of 一致"
                )
            if self.feature_version_hash is not None:
                raise FeatureRegistryError("dataset dependency 不能设置 feature_version_hash")
        else:
            evidence = None
            feature_version_hash = _hash(
                self.feature_version_hash, "dependency.feature_version_hash"
            )
            if self.dataset_evidence is not None:
                raise FeatureRegistryError("feature dependency 不能设置 dataset_evidence")
            object.__setattr__(self, "feature_version_hash", feature_version_hash)
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "reference_hash", reference_hash)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "dataset_evidence", evidence)

    @property
    def identity_hash(self) -> str:
        """返回该有向输入边的稳定身份。"""

        return canonical_json_sha256(
            {
                "available_at": self.available_at.isoformat(),
                "dataset_evidence_hash": (
                    self.dataset_evidence.evidence_hash if self.dataset_evidence else None
                ),
                "feature_version_hash": self.feature_version_hash,
                "format": "northstar.feature-dependency.v2",
                "kind": self.kind.value,
                "reference_hash": self.reference_hash,
                "role": self.role,
            }
        )


@dataclass(frozen=True, slots=True)
class FeatureLineage:
    """一次特征计算使用的版本、参数、输入快照及其 PIT 时间。"""

    feature_version_hash: str
    implementation_hash: str
    dependencies: tuple[FeatureDependency, ...]
    parameters_json: str
    decision_at: datetime
    available_at: datetime
    selection_mode: str = field(init=False)
    decision_time_safe: bool = field(init=False)
    lineage_hash: str = field(init=False)

    @classmethod
    def create(
        cls,
        *,
        feature_version: FeatureVersion,
        dependencies: Iterable[FeatureDependency],
        parameters: Mapping[str, object],
        decision_at: datetime,
        available_at: datetime,
    ) -> "FeatureLineage":
        """从受控 FeatureVersion 构建特征血缘。"""

        if not isinstance(feature_version, FeatureVersion):
            raise FeatureRegistryError("feature_version 必须是 FeatureVersion")
        normalized_dependencies = tuple(dependencies)
        if any(
            item.kind is FeatureDependencyKind.FEATURE
            and item.feature_version_hash == feature_version.version_hash
            for item in normalized_dependencies
        ):
            raise FeatureRegistryError("FeatureLineage 不能直接依赖自身的 FeatureVersion")
        if any(item.kind is FeatureDependencyKind.FEATURE for item in normalized_dependencies):
            raise FeatureRegistryError(
                "P2-WP01 尚未接受 Feature 类型输入；必须等待已验证的上游特征制品契约"
            )
        return cls(
            feature_version_hash=feature_version.version_hash,
            implementation_hash=feature_version.implementation_hash,
            dependencies=normalized_dependencies,
            parameters_json=_validate_parameters(parameters, feature_version.parameter_schema_json),
            decision_at=decision_at,
            available_at=available_at,
        )

    @property
    def parameters(self) -> Mapping[str, object]:
        """返回参数的独立副本，便于写入 run manifest。"""

        return dict(_load_canonical_mapping(self.parameters_json, "parameters_json"))

    @property
    def input_dataset_version_hashes(self) -> tuple[str, ...]:
        """返回 lineage 显式绑定的输入 DatasetVersion 集合。"""

        return tuple(
            dependency.dataset_version_hash
            for dependency in self.dependencies
            if dependency.kind is FeatureDependencyKind.DATASET
            and dependency.dataset_version_hash is not None
        )

    def __post_init__(self) -> None:
        feature_version_hash = _hash(self.feature_version_hash, "feature_version_hash")
        implementation_hash = _hash(self.implementation_hash, "implementation_hash")
        dependencies = tuple(self.dependencies)
        if not dependencies or not all(
            isinstance(item, FeatureDependency) for item in dependencies
        ):
            raise FeatureRegistryError("dependencies 必须是非空 FeatureDependency 元组")
        if len({item.role for item in dependencies}) != len(dependencies):
            raise FeatureRegistryError("dependencies.role 不能重复")
        if any(
            item.kind is FeatureDependencyKind.FEATURE
            and item.feature_version_hash == feature_version_hash
            for item in dependencies
        ):
            raise FeatureRegistryError("FeatureLineage 不能直接依赖自身的 FeatureVersion")
        if not any(item.kind is FeatureDependencyKind.DATASET for item in dependencies):
            raise FeatureRegistryError("FeatureLineage 至少必须绑定一个 input DatasetVersion")
        if any(item.kind is FeatureDependencyKind.FEATURE for item in dependencies):
            raise FeatureRegistryError(
                "P2-WP01 尚未接受 Feature 类型输入；必须等待已验证的上游特征制品契约"
            )
        decision_at = _utc_datetime(self.decision_at, "decision_at")
        available_at = _utc_datetime(self.available_at, "available_at")
        if any(item.available_at > decision_at for item in dependencies):
            raise FeatureRegistryError("FeatureLineage 不能引用 decision_at 后才可见的输入")
        if available_at < decision_at:
            raise FeatureRegistryError("FeatureLineage.available_at 不能早于 decision_at")
        parameters = _load_canonical_mapping(
            _required_text(self.parameters_json, "parameters_json"), "parameters_json"
        )
        canonical_dependencies = tuple(sorted(dependencies, key=lambda item: item.role))
        # P1 当前只产生单一静态 as-of snapshot。P2-WP01 不能仅因调用方提供了一段
        # 字符串就把它升级为逐决策 PIT 安全；该能力留给 P2-WP05 的专用 replay 证据。
        decision_time_safe = False
        selection_mode = _STATIC_SELECTION_MODE
        lineage_hash = canonical_json_sha256(
            {
                "available_at": available_at.isoformat(),
                "decision_at": decision_at.isoformat(),
                "dependencies": [
                    {
                        "available_at": item.available_at.isoformat(),
                        "dataset_version_hash": item.dataset_version_hash,
                        "feature_version_hash": item.feature_version_hash,
                        "dataset_evidence_hash": (
                            item.dataset_evidence.evidence_hash
                            if item.dataset_evidence is not None
                            else None
                        ),
                        "identity_hash": item.identity_hash,
                        "kind": item.kind.value,
                        "reference_hash": item.reference_hash,
                        "role": item.role,
                    }
                    for item in canonical_dependencies
                ],
                "feature_version_hash": feature_version_hash,
                "format": "northstar.feature-lineage.v1",
                "implementation_hash": implementation_hash,
                "parameters": parameters,
                "selection_mode": selection_mode,
                "decision_time_safe": decision_time_safe,
            }
        )
        object.__setattr__(self, "feature_version_hash", feature_version_hash)
        object.__setattr__(self, "implementation_hash", implementation_hash)
        object.__setattr__(self, "dependencies", canonical_dependencies)
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "selection_mode", selection_mode)
        object.__setattr__(self, "decision_time_safe", decision_time_safe)
        object.__setattr__(self, "lineage_hash", lineage_hash)


@dataclass(frozen=True, slots=True)
class FeatureValue:
    """一个可回放的数值或显式缺失的特征值。"""

    feature_version_hash: str
    lineage_hash: str
    key_json: str
    event_time: date | datetime
    available_at: datetime
    value: float | None
    missing_reason: str | None = None
    value_id: str = field(init=False)

    @classmethod
    def from_lineage(
        cls,
        *,
        lineage: FeatureLineage,
        key: Mapping[str, object],
        event_time: date | datetime,
        value: float | None,
        missing_reason: str | None = None,
    ) -> "FeatureValue":
        """按 lineage 的输出时间创建值，避免手工伪造可用时间。"""

        if not isinstance(lineage, FeatureLineage):
            raise FeatureRegistryError("lineage 必须是 FeatureLineage")
        return cls(
            feature_version_hash=lineage.feature_version_hash,
            lineage_hash=lineage.lineage_hash,
            key_json=_canonical_mapping(key, "key"),
            event_time=event_time,
            available_at=lineage.available_at,
            value=value,
            missing_reason=missing_reason,
        )

    @property
    def key(self) -> Mapping[str, object]:
        """返回逻辑主键的独立副本。"""

        return dict(_load_canonical_mapping(self.key_json, "key_json"))

    def __post_init__(self) -> None:
        feature_version_hash = _hash(self.feature_version_hash, "feature_version_hash")
        lineage_hash = _hash(self.lineage_hash, "lineage_hash")
        key = _load_canonical_mapping(_required_text(self.key_json, "key_json"), "key_json")
        if not key:
            raise FeatureRegistryError("FeatureValue.key 不能为空")
        event_time = _event_time(self.event_time, "event_time")
        available_at = _utc_datetime(self.available_at, "available_at")
        event_after_available = (
            event_time > available_at
            if isinstance(event_time, datetime)
            else event_time > available_at.date()
        )
        if event_after_available:
            raise FeatureRegistryError("FeatureValue.event_time 不能晚于 available_at")
        if self.value is None:
            missing_reason = _required_text(self.missing_reason, "missing_reason")
            canonical_value: object = None
        else:
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
                raise FeatureRegistryError("FeatureValue.value 必须是有限数值或 None")
            numeric_value = float(self.value)
            if not math.isfinite(numeric_value):
                raise FeatureRegistryError("FeatureValue.value 不得为 NaN 或无穷")
            if self.missing_reason is not None:
                raise FeatureRegistryError("非缺失 FeatureValue 不能设置 missing_reason")
            missing_reason = None
            canonical_value = numeric_value.hex()
            object.__setattr__(self, "value", numeric_value)
        value_id = canonical_json_sha256(
            {
                "available_at": available_at.isoformat(),
                "event_time": _event_time_identity(event_time),
                "feature_version_hash": feature_version_hash,
                "format": "northstar.feature-value.v1",
                "key": key,
                "lineage_hash": lineage_hash,
                "missing_reason": missing_reason,
                "value": canonical_value,
            }
        )
        object.__setattr__(self, "feature_version_hash", feature_version_hash)
        object.__setattr__(self, "lineage_hash", lineage_hash)
        object.__setattr__(self, "event_time", event_time)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "missing_reason", missing_reason)
        object.__setattr__(self, "value_id", value_id)


@dataclass(frozen=True, slots=True)
class FeatureBackfill:
    """同一 FeatureLineage 的一次完整、不可变回填结果。"""

    lineage_hash: str
    feature_version_hash: str
    implementation_hash: str
    available_at: datetime
    selection_mode: str
    decision_time_safe: bool
    values: tuple[FeatureValue, ...]
    backfill_hash: str = field(init=False)

    @classmethod
    def from_values(
        cls,
        *,
        lineage: FeatureLineage,
        values: Iterable[FeatureValue],
    ) -> "FeatureBackfill":
        """把特征值绑定到单一 lineage，并拒绝重复逻辑键。"""

        if not isinstance(lineage, FeatureLineage):
            raise FeatureRegistryError("lineage 必须是 FeatureLineage")
        return cls(
            lineage_hash=lineage.lineage_hash,
            feature_version_hash=lineage.feature_version_hash,
            implementation_hash=lineage.implementation_hash,
            available_at=lineage.available_at,
            selection_mode=lineage.selection_mode,
            decision_time_safe=lineage.decision_time_safe,
            values=tuple(values),
        )

    def __post_init__(self) -> None:
        lineage_hash = _hash(self.lineage_hash, "lineage_hash")
        feature_version_hash = _hash(self.feature_version_hash, "feature_version_hash")
        implementation_hash = _hash(self.implementation_hash, "implementation_hash")
        available_at = _utc_datetime(self.available_at, "available_at")
        selection_mode = _required_text(self.selection_mode, "selection_mode")
        if type(self.decision_time_safe) is not bool:
            raise FeatureRegistryError("decision_time_safe 必须是 bool")
        if self.decision_time_safe is not False or selection_mode != _STATIC_SELECTION_MODE:
            raise FeatureRegistryError(
                "P2-WP01 FeatureBackfill 只能是 STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY"
            )
        values = tuple(self.values)
        if not values or not all(isinstance(value, FeatureValue) for value in values):
            raise FeatureRegistryError("FeatureBackfill.values 必须是非空 FeatureValue 元组")
        if any(value.lineage_hash != lineage_hash for value in values):
            raise FeatureRegistryError("FeatureBackfill 的 value 必须来自同一 lineage")
        if any(value.feature_version_hash != feature_version_hash for value in values):
            raise FeatureRegistryError("FeatureBackfill 的 value 必须来自同一 feature version")
        if any(value.available_at != available_at for value in values):
            raise FeatureRegistryError(
                "FeatureBackfill 的 value.available_at 必须与 lineage 输出时间一致"
            )
        logical_keys = [
            (value.key_json, _canonical_json(_event_time_identity(value.event_time), "event_time"))
            for value in values
        ]
        if len(logical_keys) != len(set(logical_keys)):
            raise FeatureRegistryError("FeatureBackfill 不能包含重复的 key/event_time")
        canonical_values = tuple(
            sorted(
                values,
                key=lambda item: (
                    item.key_json,
                    _canonical_json(_event_time_identity(item.event_time), "event_time"),
                    item.value_id,
                ),
            )
        )
        backfill_hash = canonical_json_sha256(
            {
                "feature_version_hash": feature_version_hash,
                "format": "northstar.feature-backfill.v1",
                "implementation_hash": implementation_hash,
                "lineage_hash": lineage_hash,
                "available_at": available_at.isoformat(),
                "selection_mode": selection_mode,
                "decision_time_safe": self.decision_time_safe,
                "value_ids": [item.value_id for item in canonical_values],
            }
        )
        object.__setattr__(self, "lineage_hash", lineage_hash)
        object.__setattr__(self, "feature_version_hash", feature_version_hash)
        object.__setattr__(self, "implementation_hash", implementation_hash)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "selection_mode", selection_mode)
        object.__setattr__(self, "values", canonical_values)
        object.__setattr__(self, "backfill_hash", backfill_hash)


class _FeatureBackfillRunner:
    """Registry 内部以双执行比较证明某次回填在同一输入证据下是确定的。

    不应由研究消费者直接调用；受控入口必须先重放 DatasetVersion/PIT evidence，再把
    已登记 FeatureComputer 收到的同一输入传入这里。该类不会注入当前时钟、随机数、网络或
    文件路径；若两次返回的不可变 ``FeatureBackfill`` 身份不同，立即失败关闭。
    """

    @staticmethod
    def run_deterministic(
        lineage: FeatureLineage,
        compute: Callable[[], Iterable[FeatureValue]],
    ) -> FeatureBackfill:
        if not isinstance(lineage, FeatureLineage):
            raise FeatureRegistryError("lineage 必须是 FeatureLineage")
        if not callable(compute):
            raise FeatureRegistryError("compute 必须是无参可调用对象")
        first = FeatureBackfill.from_values(lineage=lineage, values=compute())
        second = FeatureBackfill.from_values(lineage=lineage, values=compute())
        if first.backfill_hash != second.backfill_hash:
            raise FeatureDeterminismError(
                "同一 FeatureLineage 的两次回填结果不同，拒绝发布不可复现特征"
            )
        return first

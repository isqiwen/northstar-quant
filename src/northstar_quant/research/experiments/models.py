"""P2 可复现实验的不可变领域对象。

本模块只冻结已经存在的特征血缘、数据集证据和实验声明；它不执行回测、不读取
当前时钟、不持久化到数据库，也不会产生候选策略或交易审批。P1/P2 当前只有单一
静态 as-of 数据视图，因此所有 Experiment 都被明确标记为
``STATIC_REPRODUCIBILITY_ONLY``，不能升级为逐决策 PIT、回测准入或实盘证据。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
import json
import math
import re

from northstar_quant.data.artifacts.fingerprints import canonical_json_sha256
from northstar_quant.research.features.models import FeatureDatasetEvidence


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_DOTTED_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_SAFE_TEXT_RE = re.compile(r"^[^\x00\r\n]+$")
_SECRET_TEXT_RE = re.compile(
    r"(?i)(?:authorization|bearer|api[ _-]?key|credential|token|secret|password|passwd|cookie)"
)
_SENSITIVE_KEY_PARTS = (
    "access_key",
    "api_key",
    "authorization",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
    "webhook",
)
_RAW_CONTENT_EXACT_KEYS = {
    "bars",
    "data",
    "dataframe",
    "feature_value",
    "feature_values",
    "frame",
    "market_data",
    "observations",
    "payload",
    "raw",
    "records",
    "rows",
    "series",
    "ticks",
    "values",
}
_RAW_CONTENT_KEY_PARTS = ("payload", "raw")
_STATIC_FEATURE_SELECTION_MODE = "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY"
STATIC_REPRODUCIBILITY_SELECTION_MODE = "STATIC_REPRODUCIBILITY_ONLY"


class ExperimentError(ValueError):
    """实验声明、输入证据或静态运行记录不满足可复现边界。"""


class ExperimentRunStatus(str, Enum):
    """仅描述静态可复现实验记录，不表示研究或交易审批。"""

    RECORDED = "recorded"
    VERIFICATION_FAILED = "verification_failed"


def _require_hash(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ExperimentError(f"{field_name} 必须是小写 SHA-256")
    return value


def _require_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ExperimentError(f"{field_name} 必须是小写 snake_case 标识符")
    return value


def _require_dotted_identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _DOTTED_IDENTIFIER_RE.fullmatch(value) is None:
        raise ExperimentError(f"{field_name} 必须是小写点分标识符")
    return value


def _require_version(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _VERSION_RE.fullmatch(value) is None:
        raise ExperimentError(f"{field_name} 必须是非空版本文本")
    return value


def _require_safe_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or _SAFE_TEXT_RE.fullmatch(value) is None:
        raise ExperimentError(f"{field_name} 必须是非空单行文本")
    text = value.strip()
    # ExperimentSpec/Run 没有任何路径型字段。即使路径被嵌在说明文本中，也会把机器布局
    # 带入可复现账本并诱导未来消费者读取本地文件，因此统一拒绝绝对和相对路径表达。
    if "/" in text or "\\" in text:
        raise ExperimentError(f"{field_name} 不得包含本机或相对路径")
    if _SECRET_TEXT_RE.search(text):
        raise ExperimentError(f"{field_name} 不得包含授权凭据")
    return text


def _utc_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ExperimentError(f"{field_name} 必须是带时区 datetime")
    return value.astimezone(UTC)


def _calendar_date(value: object, field_name: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise ExperimentError(f"{field_name} 必须是 date，不能使用 datetime")
    return value


def _reject_sensitive_key(key: str, field_name: str) -> None:
    normalized = key.lower().replace("-", "_")
    if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
        raise ExperimentError(f"{field_name} 不得包含凭据或密钥字段: {key}")


def _canonical_json_value(
    value: object,
    field_name: str,
    *,
    depth: int = 0,
    allow_sequences: bool = True,
) -> object:
    """递归规范化声明性 JSON，拒绝路径、凭据、NaN 与运行时对象。"""

    if depth > 8:
        raise ExperimentError(f"{field_name} 的嵌套层级过深")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ExperimentError(f"{field_name} 不得包含 NaN 或无穷数值")
        return value
    if isinstance(value, str):
        text = _require_safe_text(value, field_name)
        if len(text) > 1_024:
            raise ExperimentError(f"{field_name} 的文本过长，实验账本不得保存原始数据")
        return text
    if isinstance(value, Mapping):
        if len(value) > 64:
            raise ExperimentError(f"{field_name} 的映射条目过多，实验账本不得保存原始数据")
        normalized: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str) or not raw_key:
                raise ExperimentError(f"{field_name} 的映射键必须是非空文本")
            key = _require_safe_text(raw_key, f"{field_name}.key")
            _reject_sensitive_key(key, field_name)
            normalized_key = key.lower().replace("-", "_")
            key_parts = tuple(part for part in re.split(r"[_.]+", normalized_key) if part)
            if normalized_key in _RAW_CONTENT_EXACT_KEYS or any(
                part in key_parts for part in _RAW_CONTENT_KEY_PARTS
            ):
                raise ExperimentError(f"{field_name} 不得保存原始数据字段: {key}")
            if key in normalized:
                raise ExperimentError(f"{field_name} 不能包含重复映射键: {key}")
            normalized[key] = _canonical_json_value(
                raw_value,
                f"{field_name}.{key}",
                depth=depth + 1,
                allow_sequences=allow_sequences,
            )
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        if not allow_sequences:
            raise ExperimentError(f"{field_name} 不得保存序列型原始结果")
        if len(value) > 64:
            raise ExperimentError(f"{field_name} 的序列过长，实验账本不得保存原始数据")
        return [
            _canonical_json_value(
                item,
                f"{field_name}[{index}]",
                depth=depth + 1,
                allow_sequences=allow_sequences,
            )
            for index, item in enumerate(value)
        ]
    raise ExperimentError(f"{field_name} 只能包含有限 JSON 标量、映射或序列")


def _canonical_mapping(
    value: Mapping[str, object],
    field_name: str,
    *,
    allow_sequences: bool = True,
    max_bytes: int = 16_384,
) -> str:
    if not isinstance(value, Mapping):
        raise ExperimentError(f"{field_name} 必须是映射")
    normalized = _canonical_json_value(value, field_name, allow_sequences=allow_sequences)
    if not isinstance(normalized, dict):  # 防御类型收窄与未来 helper 修改。
        raise ExperimentError(f"{field_name} 必须是映射")
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(canonical.encode("utf-8")) > max_bytes:
        raise ExperimentError(f"{field_name} 过大，实验账本只允许小型声明性摘要")
    return canonical


def _load_canonical_mapping(
    value: object,
    field_name: str,
    *,
    allow_sequences: bool = True,
    max_bytes: int = 16_384,
) -> Mapping[str, object]:
    text = _require_safe_text(value, field_name)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExperimentError(f"{field_name} 必须是合法 JSON") from exc
    if not isinstance(parsed, dict):
        raise ExperimentError(f"{field_name} 必须编码 JSON 映射")
    canonical = _canonical_mapping(
        parsed,
        field_name,
        allow_sequences=allow_sequences,
        max_bytes=max_bytes,
    )
    if canonical != text:
        raise ExperimentError(f"{field_name} 必须是规范化 JSON")
    return parsed


def _canonical_scalar_mapping(value: Mapping[str, object], field_name: str) -> str:
    """冻结参数/模型的扁平标量合同，避免把表格或原始特征藏进声明字段。"""

    canonical = _canonical_mapping(value, field_name, allow_sequences=False, max_bytes=4_096)
    parsed = _load_canonical_mapping(
        canonical,
        field_name,
        allow_sequences=False,
        max_bytes=4_096,
    )
    if any(isinstance(item, (dict, list)) for item in parsed.values()):
        raise ExperimentError(f"{field_name} 只能包含扁平有限标量，不能保存原始数据结构")
    return canonical


def _load_canonical_scalar_mapping(value: object, field_name: str) -> Mapping[str, object]:
    parsed = _load_canonical_mapping(
        value,
        field_name,
        allow_sequences=False,
        max_bytes=4_096,
    )
    if any(isinstance(item, (dict, list)) for item in parsed.values()):
        raise ExperimentError(f"{field_name} 只能包含扁平有限标量，不能保存原始数据结构")
    return parsed


@dataclass(frozen=True, slots=True)
class ExperimentPeriod:
    """一个明确的、闭区间日期样本段。"""

    start: date
    end: date

    def __post_init__(self) -> None:
        start = _calendar_date(self.start, "period.start")
        end = _calendar_date(self.end, "period.end")
        if start > end:
            raise ExperimentError("ExperimentPeriod.start 不能晚于 end")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def as_manifest_mapping(self) -> dict[str, str]:
        return {"end": self.end.isoformat(), "start": self.start.isoformat()}


@dataclass(frozen=True, slots=True)
class StrategyVersionReference:
    """策略代码的冻结声明性身份。

    当前策略注册表尚无不可变 StrategyVersion，所以该对象不会调用运行时 factory，也不把
    字符串声明误认成策略授权；它只将经人工构建流程提供的身份写入实验账本。
    """

    strategy_id: str
    version: str
    spec_hash: str
    implementation_hash: str
    code_revision: str
    reference_hash: str = field(init=False)

    def __post_init__(self) -> None:
        strategy_id = _require_dotted_identifier(self.strategy_id, "strategy_id")
        version = _require_version(self.version, "strategy.version")
        spec_hash = _require_hash(self.spec_hash, "strategy.spec_hash")
        implementation_hash = _require_hash(
            self.implementation_hash, "strategy.implementation_hash"
        )
        code_revision = _require_safe_text(self.code_revision, "strategy.code_revision")
        reference_hash = canonical_json_sha256(
            {
                "code_revision": code_revision,
                "format": "northstar.strategy-version-reference.v1",
                "implementation_hash": implementation_hash,
                "spec_hash": spec_hash,
                "strategy_id": strategy_id,
                "version": version,
            }
        )
        object.__setattr__(self, "strategy_id", strategy_id)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "spec_hash", spec_hash)
        object.__setattr__(self, "implementation_hash", implementation_hash)
        object.__setattr__(self, "code_revision", code_revision)
        object.__setattr__(self, "reference_hash", reference_hash)

    def as_manifest_mapping(self) -> dict[str, str]:
        return {
            "code_revision": self.code_revision,
            "implementation_hash": self.implementation_hash,
            "reference_hash": self.reference_hash,
            "spec_hash": self.spec_hash,
            "strategy_id": self.strategy_id,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class ExperimentDatasetInput:
    """单个特征输入中保留角色关系的完整 DatasetVersion/PIT 证据。"""

    role: str
    evidence: FeatureDatasetEvidence
    binding_hash: str = field(init=False)

    def __post_init__(self) -> None:
        role = _require_identifier(self.role, "dataset_input.role")
        if not isinstance(self.evidence, FeatureDatasetEvidence):
            raise ExperimentError("dataset_input.evidence 必须是完整 FeatureDatasetEvidence")
        binding_hash = canonical_json_sha256(
            {
                "dataset_evidence_hash": self.evidence.evidence_hash,
                "format": "northstar.experiment-dataset-input.v1",
                "role": role,
            }
        )
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "binding_hash", binding_hash)

    def as_manifest_mapping(self) -> dict[str, object]:
        return {
            "as_of": self.evidence.as_of.isoformat(),
            "dataset_evidence_hash": self.evidence.evidence_hash,
            "dataset_id": self.evidence.dataset_id,
            "dataset_version_hash": self.evidence.dataset_version_hash,
            "pit_spec_hash": self.evidence.pit_spec_hash,
            "publication_authorization_hash": self.evidence.publication_authorization_hash,
            "publication_scope": self.evidence.publication_scope,
            "revision_ids": list(self.evidence.revision_ids),
            "role": self.role,
            "selected_frame_hash": self.evidence.selected_frame_hash,
            "snapshot_id": self.evidence.snapshot_id,
            "source_artifact_available_at": self.evidence.source_artifact_available_at.isoformat(),
            "source_artifact_snapshot_hash": self.evidence.source_artifact_snapshot_hash,
            "source_config_sha256": self.evidence.source_config_sha256,
            "source_id": self.evidence.source_id,
        }


@dataclass(frozen=True, slots=True)
class ExperimentFeatureInput:
    """Experiment 对一个已受控物化 FeatureBackfill 的完整冻结绑定。"""

    feature_id: str
    feature_version_hash: str
    feature_spec_hash: str
    implementation_hash: str
    code_revision: str
    lineage_hash: str
    backfill_hash: str
    as_of: datetime
    available_at: datetime
    dataset_inputs: tuple[ExperimentDatasetInput, ...]
    source_selection_mode: str
    source_decision_time_safe: bool
    input_hash: str = field(init=False)

    def __post_init__(self) -> None:
        feature_id = _require_dotted_identifier(self.feature_id, "feature_input.feature_id")
        feature_version_hash = _require_hash(
            self.feature_version_hash, "feature_input.feature_version_hash"
        )
        feature_spec_hash = _require_hash(self.feature_spec_hash, "feature_input.feature_spec_hash")
        implementation_hash = _require_hash(
            self.implementation_hash, "feature_input.implementation_hash"
        )
        code_revision = _require_safe_text(self.code_revision, "feature_input.code_revision")
        lineage_hash = _require_hash(self.lineage_hash, "feature_input.lineage_hash")
        backfill_hash = _require_hash(self.backfill_hash, "feature_input.backfill_hash")
        as_of = _utc_datetime(self.as_of, "feature_input.as_of")
        available_at = _utc_datetime(self.available_at, "feature_input.available_at")
        if available_at != as_of:
            raise ExperimentError("静态 FeatureBackfill.available_at 必须精确等于其输入 as_of")
        if self.source_selection_mode != _STATIC_FEATURE_SELECTION_MODE:
            raise ExperimentError(
                "Experiment 目前只接受 STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY 特征输入"
            )
        if self.source_decision_time_safe is not False:
            raise ExperimentError("Experiment 不得把静态 FeatureBackfill 升级为逐决策 PIT 安全")
        if not isinstance(self.dataset_inputs, tuple):
            raise ExperimentError(
                "feature_input.dataset_inputs 必须是非空 DatasetVersion/PIT 证据元组"
            )
        dataset_inputs = self.dataset_inputs
        if not dataset_inputs or not all(
            isinstance(item, ExperimentDatasetInput) for item in dataset_inputs
        ):
            raise ExperimentError("feature_input.dataset_inputs 必须是非空 DatasetVersion/PIT 证据")
        if len({item.role for item in dataset_inputs}) != len(dataset_inputs):
            raise ExperimentError("feature_input.dataset_inputs.role 不能重复")
        if any(item.evidence.as_of != as_of for item in dataset_inputs):
            raise ExperimentError("每个 Feature 输入的 DatasetVersion/PIT evidence.as_of 必须一致")
        canonical_inputs = tuple(sorted(dataset_inputs, key=lambda item: item.role))
        input_hash = canonical_json_sha256(
            {
                "as_of": as_of.isoformat(),
                "available_at": available_at.isoformat(),
                "backfill_hash": backfill_hash,
                "code_revision": code_revision,
                "dataset_inputs": [
                    {
                        "binding_hash": item.binding_hash,
                        "dataset_evidence_hash": item.evidence.evidence_hash,
                        "role": item.role,
                    }
                    for item in canonical_inputs
                ],
                "feature_id": feature_id,
                "feature_spec_hash": feature_spec_hash,
                "feature_version_hash": feature_version_hash,
                "format": "northstar.experiment-feature-input.v1",
                "implementation_hash": implementation_hash,
                "lineage_hash": lineage_hash,
                "source_decision_time_safe": False,
                "source_selection_mode": _STATIC_FEATURE_SELECTION_MODE,
            }
        )
        object.__setattr__(self, "feature_id", feature_id)
        object.__setattr__(self, "feature_version_hash", feature_version_hash)
        object.__setattr__(self, "feature_spec_hash", feature_spec_hash)
        object.__setattr__(self, "implementation_hash", implementation_hash)
        object.__setattr__(self, "code_revision", code_revision)
        object.__setattr__(self, "lineage_hash", lineage_hash)
        object.__setattr__(self, "backfill_hash", backfill_hash)
        object.__setattr__(self, "as_of", as_of)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "dataset_inputs", canonical_inputs)
        object.__setattr__(self, "input_hash", input_hash)

    @property
    def dataset_version_hashes(self) -> tuple[str, ...]:
        return tuple(sorted({item.evidence.dataset_version_hash for item in self.dataset_inputs}))

    def as_manifest_mapping(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "available_at": self.available_at.isoformat(),
            "backfill_hash": self.backfill_hash,
            "code_revision": self.code_revision,
            "dataset_inputs": [item.as_manifest_mapping() for item in self.dataset_inputs],
            "feature_id": self.feature_id,
            "feature_spec_hash": self.feature_spec_hash,
            "feature_version_hash": self.feature_version_hash,
            "implementation_hash": self.implementation_hash,
            "input_hash": self.input_hash,
            "lineage_hash": self.lineage_hash,
            "source_decision_time_safe": self.source_decision_time_safe,
            "source_selection_mode": self.source_selection_mode,
        }


@dataclass(frozen=True, slots=True)
class ExperimentModelAssumption:
    """成本或滑点模型的声明性、无凭据引用。"""

    model_id: str
    parameters_json: str
    model_hash: str = field(init=False)

    @classmethod
    def from_mapping(
        cls, *, model_id: str, parameters: Mapping[str, object]
    ) -> "ExperimentModelAssumption":
        return cls(
            model_id=model_id,
            parameters_json=_canonical_scalar_mapping(parameters, "model.parameters"),
        )

    @property
    def parameters(self) -> Mapping[str, object]:
        return dict(_load_canonical_scalar_mapping(self.parameters_json, "model.parameters_json"))

    def __post_init__(self) -> None:
        model_id = _require_dotted_identifier(self.model_id, "model_id")
        parameters = _load_canonical_scalar_mapping(self.parameters_json, "model.parameters_json")
        parameters_json = _canonical_scalar_mapping(parameters, "model.parameters")
        model_hash = canonical_json_sha256(
            {
                "format": "northstar.experiment-model-assumption.v1",
                "model_id": model_id,
                "parameters": parameters,
            }
        )
        object.__setattr__(self, "model_id", model_id)
        object.__setattr__(self, "parameters_json", parameters_json)
        object.__setattr__(self, "model_hash", model_hash)

    def as_manifest_mapping(self) -> dict[str, object]:
        return {
            "model_hash": self.model_hash,
            "model_id": self.model_id,
            "parameters": self.parameters,
        }


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    """一份只能作静态重现检查的完整实验声明。"""

    experiment_id: str
    strategy: StrategyVersionReference
    feature_inputs: tuple[ExperimentFeatureInput, ...]
    parameters_json: str
    train_period: ExperimentPeriod
    validation_period: ExperimentPeriod
    oos_period: ExperimentPeriod
    cost_model: ExperimentModelAssumption
    slippage_model: ExperimentModelAssumption
    random_seed: int
    code_revision: str
    input_as_of: datetime
    selection_mode: str = field(init=False)
    decision_time_safe: bool = field(init=False)
    eligible_for_backtest: bool = field(init=False)
    eligible_for_admission: bool = field(init=False)
    spec_hash: str = field(init=False)

    @classmethod
    def create(
        cls,
        *,
        experiment_id: str,
        strategy: StrategyVersionReference,
        feature_inputs: Sequence[ExperimentFeatureInput],
        parameters: Mapping[str, object],
        train_period: ExperimentPeriod,
        validation_period: ExperimentPeriod,
        oos_period: ExperimentPeriod,
        cost_model: ExperimentModelAssumption,
        slippage_model: ExperimentModelAssumption,
        random_seed: int,
        code_revision: str,
        input_as_of: datetime,
    ) -> "ExperimentSpec":
        if not isinstance(feature_inputs, (list, tuple)):
            raise ExperimentError("feature_inputs 必须是非空 ExperimentFeatureInput list 或 tuple")
        return cls(
            experiment_id=experiment_id,
            strategy=strategy,
            feature_inputs=tuple(feature_inputs),
            parameters_json=_canonical_scalar_mapping(parameters, "experiment.parameters"),
            train_period=train_period,
            validation_period=validation_period,
            oos_period=oos_period,
            cost_model=cost_model,
            slippage_model=slippage_model,
            random_seed=random_seed,
            code_revision=code_revision,
            input_as_of=input_as_of,
        )

    @property
    def parameters(self) -> Mapping[str, object]:
        return dict(
            _load_canonical_scalar_mapping(self.parameters_json, "experiment.parameters_json")
        )

    @property
    def dataset_version_hashes(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    dataset_hash
                    for feature_input in self.feature_inputs
                    for dataset_hash in feature_input.dataset_version_hashes
                }
            )
        )

    def __post_init__(self) -> None:
        experiment_id = _require_identifier(self.experiment_id, "experiment_id")
        if not isinstance(self.strategy, StrategyVersionReference):
            raise ExperimentError("strategy 必须是 StrategyVersionReference")
        if not isinstance(self.feature_inputs, tuple):
            raise ExperimentError("feature_inputs 必须是非空 ExperimentFeatureInput 元组")
        feature_inputs = self.feature_inputs
        if not feature_inputs or not all(
            isinstance(item, ExperimentFeatureInput) for item in feature_inputs
        ):
            raise ExperimentError("feature_inputs 必须是非空 ExperimentFeatureInput 元组")
        if len({item.lineage_hash for item in feature_inputs}) != len(feature_inputs):
            raise ExperimentError("同一 Experiment 不能重复绑定 FeatureLineage")
        if len({item.feature_id for item in feature_inputs}) != len(feature_inputs):
            raise ExperimentError(
                "同一 Experiment 不能重复绑定同一 feature_id；多参数输入须先建立显式角色合同"
            )
        canonical_inputs = tuple(
            sorted(feature_inputs, key=lambda item: (item.feature_id, item.lineage_hash))
        )
        parameters = _load_canonical_scalar_mapping(
            self.parameters_json, "experiment.parameters_json"
        )
        parameters_json = _canonical_scalar_mapping(parameters, "experiment.parameters")
        if not all(
            isinstance(item, ExperimentPeriod)
            for item in (self.train_period, self.validation_period, self.oos_period)
        ):
            raise ExperimentError("train/validation/oos period 必须是 ExperimentPeriod")
        if self.train_period.end >= self.validation_period.start:
            raise ExperimentError("train_period 必须在 validation_period 之前且不可重叠")
        if self.validation_period.end >= self.oos_period.start:
            raise ExperimentError("validation_period 必须在 oos_period 之前且不可重叠")
        if not isinstance(self.cost_model, ExperimentModelAssumption):
            raise ExperimentError("cost_model 必须是 ExperimentModelAssumption")
        if not isinstance(self.slippage_model, ExperimentModelAssumption):
            raise ExperimentError("slippage_model 必须是 ExperimentModelAssumption")
        if type(self.random_seed) is not int or self.random_seed < 0:
            raise ExperimentError("random_seed 必须是非负整数，bool 不可用")
        code_revision = _require_safe_text(self.code_revision, "experiment.code_revision")
        if self.strategy.code_revision != code_revision:
            raise ExperimentError("experiment.code_revision 必须与 strategy.code_revision 精确一致")
        if any(item.code_revision != code_revision for item in canonical_inputs):
            raise ExperimentError(
                "所有 FeatureVersion.code_revision 必须与 experiment.code_revision 一致"
            )
        input_as_of = _utc_datetime(self.input_as_of, "experiment.input_as_of")
        if any(
            item.as_of != input_as_of or item.available_at != input_as_of
            for item in canonical_inputs
        ):
            raise ExperimentError("Experiment 的所有 Feature 输入必须绑定同一个静态 input_as_of")
        spec_hash = canonical_json_sha256(
            {
                "code_revision": code_revision,
                "cost_model": self.cost_model.as_manifest_mapping(),
                "decision_time_safe": False,
                "feature_inputs": [item.as_manifest_mapping() for item in canonical_inputs],
                "format": "northstar.experiment-spec.v1",
                "input_as_of": input_as_of.isoformat(),
                "oos_period": self.oos_period.as_manifest_mapping(),
                "parameters": parameters,
                "random_seed": self.random_seed,
                "selection_mode": STATIC_REPRODUCIBILITY_SELECTION_MODE,
                "slippage_model": self.slippage_model.as_manifest_mapping(),
                "strategy": self.strategy.as_manifest_mapping(),
                "train_period": self.train_period.as_manifest_mapping(),
                "validation_period": self.validation_period.as_manifest_mapping(),
            }
        )
        object.__setattr__(self, "experiment_id", experiment_id)
        object.__setattr__(self, "feature_inputs", canonical_inputs)
        object.__setattr__(self, "parameters_json", parameters_json)
        object.__setattr__(self, "code_revision", code_revision)
        object.__setattr__(self, "input_as_of", input_as_of)
        object.__setattr__(self, "selection_mode", STATIC_REPRODUCIBILITY_SELECTION_MODE)
        object.__setattr__(self, "decision_time_safe", False)
        object.__setattr__(self, "eligible_for_backtest", False)
        object.__setattr__(self, "eligible_for_admission", False)
        object.__setattr__(self, "spec_hash", spec_hash)

    def as_manifest_mapping(self) -> dict[str, object]:
        return {
            "code_revision": self.code_revision,
            "cost_model": self.cost_model.as_manifest_mapping(),
            "dataset_version_hashes": list(self.dataset_version_hashes),
            "decision_time_safe": self.decision_time_safe,
            "eligible_for_admission": self.eligible_for_admission,
            "eligible_for_backtest": self.eligible_for_backtest,
            "experiment_id": self.experiment_id,
            "feature_inputs": [item.as_manifest_mapping() for item in self.feature_inputs],
            "input_as_of": self.input_as_of.isoformat(),
            "oos_period": self.oos_period.as_manifest_mapping(),
            "parameters": self.parameters,
            "random_seed": self.random_seed,
            "selection_mode": self.selection_mode,
            "slippage_model": self.slippage_model.as_manifest_mapping(),
            "spec_hash": self.spec_hash,
            "strategy": self.strategy.as_manifest_mapping(),
            "train_period": self.train_period.as_manifest_mapping(),
            "validation_period": self.validation_period.as_manifest_mapping(),
        }


@dataclass(frozen=True, slots=True)
class ExperimentRun:
    """对单一静态 ExperimentSpec 的确定性结果记录。

    ``run_id`` 是调用方显式提供的账本键；它不进入 ``run_hash``，从而不会把 UUID、机器
    时间或运行耗时误当作研究结果身份。运行只保存经上游生成的摘要 hash：P2-WP03 不允许
    用 outcome/evidence 字段暗中携带 FeatureValue、行情帧、回测曲线或原始制品内容。
    """

    run_id: str
    spec_hash: str
    feature_input_hashes: tuple[str, ...]
    status: ExperimentRunStatus
    runner_id: str
    run_configuration_hash: str
    outcome_hash: str
    evidence_hashes: tuple[str, ...]
    selection_mode: str = field(init=False)
    decision_time_safe: bool = field(init=False)
    eligible_for_backtest: bool = field(init=False)
    eligible_for_admission: bool = field(init=False)
    run_hash: str = field(init=False)

    @classmethod
    def from_spec(
        cls,
        *,
        run_id: str,
        spec: ExperimentSpec,
        status: ExperimentRunStatus,
        runner_id: str,
        run_configuration_hash: str,
        outcome_hash: str,
        evidence_hashes: Sequence[str],
    ) -> "ExperimentRun":
        if not isinstance(spec, ExperimentSpec):
            raise ExperimentError("spec 必须是 ExperimentSpec")
        if not isinstance(evidence_hashes, (list, tuple)):
            raise ExperimentError("run.evidence_hashes 必须是 SHA-256 list 或 tuple")
        return cls(
            run_id=run_id,
            spec_hash=spec.spec_hash,
            feature_input_hashes=tuple(item.input_hash for item in spec.feature_inputs),
            status=status,
            runner_id=runner_id,
            run_configuration_hash=run_configuration_hash,
            outcome_hash=outcome_hash,
            evidence_hashes=tuple(evidence_hashes),
        )

    def __post_init__(self) -> None:
        run_id = _require_identifier(self.run_id, "run_id")
        spec_hash = _require_hash(self.spec_hash, "run.spec_hash")
        if not isinstance(self.feature_input_hashes, tuple):
            raise ExperimentError("run.feature_input_hashes 必须是非空 SHA-256 元组")
        feature_input_hashes = tuple(
            _require_hash(item, "run.feature_input_hashes") for item in self.feature_input_hashes
        )
        if not feature_input_hashes or len(feature_input_hashes) != len(set(feature_input_hashes)):
            raise ExperimentError("run.feature_input_hashes 必须是非空且不重复的哈希元组")
        feature_input_hashes = tuple(sorted(feature_input_hashes))
        if not isinstance(self.status, ExperimentRunStatus):
            raise ExperimentError("run.status 必须是 ExperimentRunStatus")
        runner_id = _require_dotted_identifier(self.runner_id, "run.runner_id")
        run_configuration_hash = _require_hash(
            self.run_configuration_hash, "run.run_configuration_hash"
        )
        outcome_hash = _require_hash(self.outcome_hash, "run.outcome_hash")
        if not isinstance(self.evidence_hashes, tuple):
            raise ExperimentError("run.evidence_hashes 必须是非空 SHA-256 元组")
        evidence_hashes = tuple(
            _require_hash(item, "run.evidence_hashes") for item in self.evidence_hashes
        )
        if not evidence_hashes or len(evidence_hashes) != len(set(evidence_hashes)):
            raise ExperimentError("run.evidence_hashes 必须是非空且不重复的哈希元组")
        evidence_hashes = tuple(sorted(evidence_hashes))
        run_hash = canonical_json_sha256(
            {
                "decision_time_safe": False,
                "evidence_hashes": list(evidence_hashes),
                "feature_input_hashes": list(feature_input_hashes),
                "format": "northstar.experiment-run.v1",
                "outcome_hash": outcome_hash,
                "run_configuration_hash": run_configuration_hash,
                "runner_id": runner_id,
                "selection_mode": STATIC_REPRODUCIBILITY_SELECTION_MODE,
                "spec_hash": spec_hash,
                "status": self.status.value,
            }
        )
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "spec_hash", spec_hash)
        object.__setattr__(self, "feature_input_hashes", feature_input_hashes)
        object.__setattr__(self, "runner_id", runner_id)
        object.__setattr__(self, "run_configuration_hash", run_configuration_hash)
        object.__setattr__(self, "outcome_hash", outcome_hash)
        object.__setattr__(self, "evidence_hashes", evidence_hashes)
        object.__setattr__(self, "selection_mode", STATIC_REPRODUCIBILITY_SELECTION_MODE)
        object.__setattr__(self, "decision_time_safe", False)
        object.__setattr__(self, "eligible_for_backtest", False)
        object.__setattr__(self, "eligible_for_admission", False)
        object.__setattr__(self, "run_hash", run_hash)

    def as_manifest_mapping(self) -> dict[str, object]:
        return {
            "decision_time_safe": self.decision_time_safe,
            "eligible_for_admission": self.eligible_for_admission,
            "eligible_for_backtest": self.eligible_for_backtest,
            "evidence_hashes": list(self.evidence_hashes),
            "feature_input_hashes": list(self.feature_input_hashes),
            "outcome_hash": self.outcome_hash,
            "run_configuration_hash": self.run_configuration_hash,
            "run_hash": self.run_hash,
            "run_id": self.run_id,
            "runner_id": self.runner_id,
            "selection_mode": self.selection_mode,
            "spec_hash": self.spec_hash,
            "status": self.status.value,
        }

"""回测请求、结果和运行清单的统一合同。

统一合同只统一可审计边界，不抹平三种引擎的市场与成交语义：

* ``weight_return`` 是连续研究序列的收益近似；
* ``futures_daily`` 是实际合约的逐日状态机；
* ``futures_intraday_replay`` 是实际合约的分钟级订单回放。

所有对象都用于离线研究。它们不构成候选策略准入、订单或任何交易授权。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
from typing import cast

import polars as pl


_SHA256_LENGTH = 64
_MANIFEST_SCHEMA_VERSION = "northstar_backtest_manifest_v4"


class BacktestContractError(ValueError):
    """回测统一合同不能被安全构造或验证时抛出。"""


class BacktestEngine(str, Enum):
    """唯一允许进入统一编排的历史回测引擎。"""

    WEIGHT_RETURN = "weight_return"
    FUTURES_DAILY = "futures_daily"
    FUTURES_INTRADAY_REPLAY = "futures_intraday_replay"

    @classmethod
    def parse(cls, value: str | "BacktestEngine") -> "BacktestEngine":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip())
        except ValueError as exc:
            supported = ", ".join(item.value for item in cls)
            raise BacktestContractError(
                f"不支持的回测引擎：{value!r}；仅允许 {supported}"
            ) from exc


class BacktestFidelity(str, Enum):
    """结果能够诚实主张的撮合/账户真实性层级。"""

    CONTINUOUS_RETURN_APPROXIMATION = "continuous_return_approximation"
    ACTUAL_CONTRACT_DAILY_STATE_MACHINE = "actual_contract_daily_state_machine"
    ACTUAL_CONTRACT_INTRADAY_ORDER_REPLAY = "actual_contract_intraday_order_replay"


class BacktestDataSemantics(str, Enum):
    """进入引擎的行情与合约语义。"""

    CONTINUOUS_RESEARCH_SERIES = "continuous_research_series"
    ACTUAL_CONTRACT_DAILY_BARS = "actual_contract_daily_bars"
    ACTUAL_CONTRACT_INTRADAY_BARS = "actual_contract_intraday_bars"


class ExecutionAuditLevel(str, Enum):
    """统一结果可提供的执行审计深度。"""

    NOT_MODELED = "not_modeled"
    TARGET_EVENTS_AND_FILL_EVENTS = "target_events_and_fill_events"
    ORDERS_AND_FILL_EVENTS = "orders_and_fill_events"


class BacktestInputKind(str, Enum):
    """当前正式回测编排唯一接受的策略输出形式。"""

    TARGET_WEIGHT = "target_weight"


class BacktestDataInputKind(str, Enum):
    """数据证据来源；legacy 投影与 immutable PIT 绝不等价。"""

    LEGACY_MARKET_PROJECTION = "legacy_market_projection"
    IMMUTABLE_PIT_SNAPSHOT = "immutable_pit_snapshot"
    DECISION_REPLAY_RECEIPT = "decision_replay_receipt"


@dataclass(frozen=True, slots=True)
class BacktestEngineSemantics:
    """某个引擎固定、不可由调用方覆盖的能力与限制。"""

    engine: BacktestEngine
    fidelity: BacktestFidelity
    data_semantics: BacktestDataSemantics
    execution_audit_level: ExecutionAuditLevel
    models_orders: bool
    models_fills: bool
    models_margin: bool
    models_rollover: bool
    models_partial_fills: bool
    limitations: tuple[str, ...]

    def as_mapping(self) -> dict[str, object]:
        return {
            "engine": self.engine.value,
            "fidelity": self.fidelity.value,
            "data_semantics": self.data_semantics.value,
            "execution_audit_level": self.execution_audit_level.value,
            "capabilities": {
                "models_orders": self.models_orders,
                "models_fills": self.models_fills,
                "models_margin": self.models_margin,
                "models_rollover": self.models_rollover,
                "models_partial_fills": self.models_partial_fills,
            },
            "limitations": list(self.limitations),
        }


_ENGINE_SEMANTICS: dict[BacktestEngine, BacktestEngineSemantics] = {
    BacktestEngine.WEIGHT_RETURN: BacktestEngineSemantics(
        engine=BacktestEngine.WEIGHT_RETURN,
        fidelity=BacktestFidelity.CONTINUOUS_RETURN_APPROXIMATION,
        data_semantics=BacktestDataSemantics.CONTINUOUS_RESEARCH_SERIES,
        execution_audit_level=ExecutionAuditLevel.NOT_MODELED,
        models_orders=False,
        models_fills=False,
        models_margin=False,
        models_rollover=False,
        models_partial_fills=False,
        limitations=(
            "连续研究序列仅按延迟权重与收盘收益近似，不模拟实际可交易合约。",
            "不模拟逐笔订单、成交、保证金、换月、涨跌停或流动性约束。",
        ),
    ),
    BacktestEngine.FUTURES_DAILY: BacktestEngineSemantics(
        engine=BacktestEngine.FUTURES_DAILY,
        fidelity=BacktestFidelity.ACTUAL_CONTRACT_DAILY_STATE_MACHINE,
        data_semantics=BacktestDataSemantics.ACTUAL_CONTRACT_DAILY_BARS,
        execution_audit_level=ExecutionAuditLevel.TARGET_EVENTS_AND_FILL_EVENTS,
        models_orders=False,
        models_fills=True,
        models_margin=True,
        models_rollover=True,
        models_partial_fills=False,
        limitations=(
            "实际合约按逐日开盘、结算和保守 OHLC 顺序模拟，不能还原日内逐笔路径。",
            "输出的是目标约束事件与模拟成交事件，不含订单生命周期。",
        ),
    ),
    BacktestEngine.FUTURES_INTRADAY_REPLAY: BacktestEngineSemantics(
        engine=BacktestEngine.FUTURES_INTRADAY_REPLAY,
        fidelity=BacktestFidelity.ACTUAL_CONTRACT_INTRADAY_ORDER_REPLAY,
        data_semantics=BacktestDataSemantics.ACTUAL_CONTRACT_INTRADAY_BARS,
        execution_audit_level=ExecutionAuditLevel.ORDERS_AND_FILL_EVENTS,
        models_orders=True,
        models_fills=True,
        models_margin=True,
        models_rollover=True,
        models_partial_fills=True,
        limitations=(
            "使用分钟线、盘口快照、成交量参与率和队列假设回放，不是逐笔或 L2 队列重建。",
            "历史回放订单不是券商订单，也不能作为实时执行授权。",
        ),
    ),
}


def engine_semantics(engine: BacktestEngine | str) -> BacktestEngineSemantics:
    """返回由代码固定的引擎能力声明。"""

    return _ENGINE_SEMANTICS[BacktestEngine.parse(engine)]


def _require_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BacktestContractError(f"{field_name} 必须是非空字符串")
    return value.strip()


def _require_sha256(value: object, *, field_name: str, allow_none: bool = False) -> str | None:
    if value is None and allow_none:
        return None
    normalized = _require_text(value, field_name=field_name)
    if len(normalized) != _SHA256_LENGTH or any(
        character not in "0123456789abcdef" for character in normalized.lower()
    ):
        raise BacktestContractError(f"{field_name} 必须是小写 SHA-256 十六进制摘要")
    return normalized.lower()


def _optional_text(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name=field_name)


def _optional_nonnegative_int(value: object, *, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BacktestContractError(f"{field_name} 必须是非负整数或 null")
    return value


def _json_value(value: object) -> object:
    """把合同允许的值转换为稳定、无路径的 JSON 值。"""

    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if not math.isfinite(number):
            raise BacktestContractError("回测合同不得包含 NaN 或无穷数")
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path) or isinstance(value, (bytes, bytearray)):
        raise BacktestContractError("回测合同不得包含路径或二进制载荷")
    if isinstance(value, Mapping):
        encoded: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            key = _require_text(raw_key, field_name="回测合同映射键")
            if key in encoded:
                raise BacktestContractError(f"回测合同映射包含重复键：{key}")
            encoded[key] = _json_value(raw_value)
        return dict(sorted(encoded.items()))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]
    raise BacktestContractError(
        f"回测合同包含不支持的值类型：{type(value).__name__}"
    )


def _canonical_json(payload: object) -> str:
    return json.dumps(
        _json_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


_PIT_EVIDENCE_FIELDS = frozenset(
    {
        "as_of",
        "dataset_id",
        "dataset_version_hash",
        "format",
        "publication_authorization_hash",
        "publication_scope",
        "publication_scope_hash",
        "revision_ids",
        "row_count",
        "decision_time_safe",
        "selection_mode",
        "selected_frame_hash",
        "snapshot_id",
        "source_artifact_snapshot_hash",
        "source_artifact_available_at",
        "source_config_sha256",
        "source_id",
        "spec",
    }
)


def _canonical_pit_evidence(value: object) -> tuple[str, dict[str, object]]:
    """冻结可独立重放 P1 snapshot 所需的完整、无路径证据。"""

    if not isinstance(value, Mapping):
        raise BacktestContractError("PIT evidence 必须是对象")
    payload = json.loads(_canonical_json(value))
    if not isinstance(payload, dict) or set(payload) != _PIT_EVIDENCE_FIELDS:
        raise BacktestContractError("PIT evidence 字段不完整或包含未知字段")
    for field_name in ("as_of", "format", "selection_mode", "source_artifact_available_at"):
        _require_text(payload[field_name], field_name=f"pit_evidence.{field_name}")
    for field_name in (
        "dataset_version_hash",
        "publication_authorization_hash",
        "publication_scope_hash",
        "selected_frame_hash",
        "snapshot_id",
        "source_artifact_snapshot_hash",
        "source_config_sha256",
    ):
        _require_sha256(payload[field_name], field_name=f"pit_evidence.{field_name}")
    for field_name in ("dataset_id", "source_id"):
        _require_text(payload[field_name], field_name=f"pit_evidence.{field_name}")
    if not isinstance(payload["decision_time_safe"], bool):
        raise BacktestContractError("pit_evidence.decision_time_safe 必须是 bool")
    if isinstance(payload["row_count"], bool) or not isinstance(payload["row_count"], int):
        raise BacktestContractError("pit_evidence.row_count 必须是整数")
    revision_ids = payload["revision_ids"]
    if not isinstance(revision_ids, list) or len(revision_ids) != payload["row_count"]:
        raise BacktestContractError("pit_evidence.revision_ids 与 row_count 不一致")
    for revision_id in revision_ids:
        _require_sha256(revision_id, field_name="pit_evidence.revision_id")
    if not isinstance(payload["publication_scope"], dict) or not payload["publication_scope"]:
        raise BacktestContractError("pit_evidence.publication_scope 必须是非空对象")
    if not isinstance(payload["spec"], dict) or not payload["spec"]:
        raise BacktestContractError("pit_evidence.spec 必须是非空对象")
    return _canonical_json(payload), payload


@dataclass(frozen=True, slots=True)
class BacktestRecord(Mapping[str, object]):
    """不可变的输出行；读取时返回新的普通 JSON 值，避免事后篡改结果。"""

    payload_json: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "BacktestRecord":
        if not isinstance(value, Mapping):
            raise BacktestContractError("回测输出行必须是映射")
        payload = _json_value(value)
        if not isinstance(payload, dict) or not payload:
            raise BacktestContractError("回测输出行必须是非空对象")
        return cls(payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))

    def as_mapping(self) -> dict[str, object]:
        parsed = json.loads(self.payload_json)
        if not isinstance(parsed, dict):  # 防御未来错误修改 payload_json。
            raise BacktestContractError("回测输出行不是对象")
        return parsed

    def __getitem__(self, key: str) -> object:
        return self.as_mapping()[key]

    def __iter__(self):
        return iter(self.as_mapping())

    def __len__(self) -> int:
        return len(self.as_mapping())


def _freeze_records(
    values: Sequence[Mapping[str, object]],
    *,
    field_name: str,
) -> tuple[BacktestRecord, ...]:
    if isinstance(values, (str, bytes, bytearray, Mapping)):
        raise BacktestContractError(f"{field_name} 必须是输出行序列")
    return tuple(BacktestRecord.from_mapping(item) for item in values)


@dataclass(frozen=True, slots=True)
class TargetFrameReference:
    """目标权重表的 schema、行数与规范内容指纹。"""

    input_kind: BacktestInputKind
    time_column: str
    columns: tuple[str, ...]
    row_count: int
    target_frame_sha256: str

    def __post_init__(self) -> None:
        if self.input_kind is not BacktestInputKind.TARGET_WEIGHT:
            raise BacktestContractError("当前仅允许 target_weight 回测输入")
        object.__setattr__(self, "time_column", _require_text(self.time_column, field_name="time_column"))
        if not self.columns or any(not isinstance(column, str) or not column for column in self.columns):
            raise BacktestContractError("target columns 必须是非空字段名元组")
        if len(set(self.columns)) != len(self.columns):
            raise BacktestContractError("target columns 不能重复")
        required = {self.time_column, "symbol", "target_weight"}
        if not required.issubset(self.columns):
            raise BacktestContractError("target 引用缺少时间、symbol 或 target_weight 字段")
        if isinstance(self.row_count, bool) or self.row_count <= 0:
            raise BacktestContractError("target row_count 必须是正整数")
        object.__setattr__(
            self,
            "target_frame_sha256",
            _require_sha256(self.target_frame_sha256, field_name="target_frame_sha256"),
        )

    @classmethod
    def from_frame(
        cls,
        frame: pl.DataFrame,
        *,
        time_column: str,
    ) -> "TargetFrameReference":
        if not isinstance(frame, pl.DataFrame) or frame.is_empty():
            raise BacktestContractError("target_weight 回测输入必须是非空 Polars DataFrame")
        normalized_time_column = _require_text(time_column, field_name="time_column")
        required = {normalized_time_column, "symbol", "target_weight"}
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise BacktestContractError("target_weight 输入缺少字段：" + ", ".join(missing))
        rows: list[dict[str, object]] = []
        seen_keys: set[tuple[str, str]] = set()
        for row in frame.to_dicts():
            time_value = row.get(normalized_time_column)
            symbol = str(row.get("symbol") or "").strip().upper()
            if time_value is None or not symbol:
                raise BacktestContractError("target_weight 输入含空时间或 symbol")
            raw_weight = row.get("target_weight")
            if isinstance(raw_weight, bool) or not isinstance(raw_weight, (int, float, str)):
                raise BacktestContractError("target_weight 必须是有限数")
            try:
                weight = float(raw_weight)
            except ValueError as exc:
                raise BacktestContractError("target_weight 必须是有限数") from exc
            if not math.isfinite(weight):
                raise BacktestContractError("target_weight 必须是有限数")
            key = (str(_json_value(time_value)), symbol)
            if key in seen_keys:
                raise BacktestContractError(
                    f"target_weight 输入在 {normalized_time_column}/symbol 上重复：{key[0]}/{symbol}"
                )
            seen_keys.add(key)
            rows.append({column: row.get(column) for column in frame.columns})
        rows.sort(key=lambda row: (str(_json_value(row[normalized_time_column])), str(row["symbol"]).upper()))
        schema = tuple(f"{column}:{frame.schema[column]}" for column in frame.columns)
        digest = _sha256({"schema": schema, "rows": rows})
        return cls(
            input_kind=BacktestInputKind.TARGET_WEIGHT,
            time_column=normalized_time_column,
            columns=tuple(frame.columns),
            row_count=frame.height,
            target_frame_sha256=digest,
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "input_kind": self.input_kind.value,
            "time_column": self.time_column,
            "columns": list(self.columns),
            "row_count": self.row_count,
            "target_frame_sha256": self.target_frame_sha256,
        }


@dataclass(frozen=True, slots=True)
class BacktestDecisionReplayBinding:
    """Hash-only binding for a controlled per-decision replay receipt.

    This records every immutable PIT snapshot used by the replay without representing the
    receipt as decision-time safe or candidate-admissible.  A future runner must explicitly
    support this input kind; it cannot be silently downgraded to a static PIT view.
    """

    receipt_hash: str
    certificate_hash: str
    trace_hash: str
    schedule_hash: str
    market_replay_hash: str
    strategy_identity_hash: str
    target_frame_sha256: str
    profile_id: str
    profile_config_sha256: str
    profile_dimension_key: str
    selected_strategy_ids: tuple[str, ...]
    pit_evidence_json: tuple[str, ...]
    binding_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for field_name in (
            "receipt_hash",
            "certificate_hash",
            "trace_hash",
            "schedule_hash",
            "market_replay_hash",
            "strategy_identity_hash",
            "target_frame_sha256",
            "profile_config_sha256",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_sha256(getattr(self, field_name), field_name=field_name),
            )
        for field_name in ("profile_id", "profile_dimension_key"):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name=field_name),
            )
        if not isinstance(self.selected_strategy_ids, tuple) or not self.selected_strategy_ids:
            raise BacktestContractError("decision replay selected_strategy_ids must be non-empty")
        strategy_ids = tuple(
            _require_text(item, field_name="decision replay selected_strategy_id")
            for item in self.selected_strategy_ids
        )
        if len(set(strategy_ids)) != len(strategy_ids):
            raise BacktestContractError("decision replay selected_strategy_ids cannot repeat")
        if not isinstance(self.pit_evidence_json, tuple) or not self.pit_evidence_json:
            raise BacktestContractError("decision replay pit_evidence_json must be non-empty")
        canonical_evidence: list[str] = []
        evidence_mappings: list[dict[str, object]] = []
        as_of_values: list[str] = []
        for item in self.pit_evidence_json:
            try:
                value = json.loads(item)
            except (TypeError, json.JSONDecodeError) as exc:
                raise BacktestContractError("decision replay PIT evidence must be canonical JSON") from exc
            canonical, evidence = _canonical_pit_evidence(value)
            if evidence["selection_mode"] != "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY":
                raise BacktestContractError("decision replay snapshots must retain static PIT semantics")
            if evidence["decision_time_safe"] is not False:
                raise BacktestContractError("decision replay snapshots cannot self-declare decision safety")
            canonical_evidence.append(canonical)
            evidence_mappings.append(evidence)
            as_of_values.append(str(evidence["as_of"]))
        if as_of_values != sorted(as_of_values) or len(set(as_of_values)) != len(as_of_values):
            raise BacktestContractError("decision replay snapshot as_of values must be strictly ordered")
        first_evidence = evidence_mappings[0]
        for evidence in evidence_mappings[1:]:
            if (
                evidence["dataset_id"] != first_evidence["dataset_id"]
                or evidence["source_id"] != first_evidence["source_id"]
                or evidence["source_config_sha256"] != first_evidence["source_config_sha256"]
                or evidence["spec"] != first_evidence["spec"]
            ):
                raise BacktestContractError(
                    "decision replay checkpoints must share dataset, source, and PIT specification"
                )
        binding_hash = _sha256(
            {
                "certificate_hash": self.certificate_hash,
                "format": "northstar.backtest-decision-replay-binding.v1",
                "market_replay_hash": self.market_replay_hash,
                "pit_snapshot_ids": [item["snapshot_id"] for item in evidence_mappings],
                "profile_config_sha256": self.profile_config_sha256,
                "profile_dimension_key": self.profile_dimension_key,
                "profile_id": self.profile_id,
                "receipt_hash": self.receipt_hash,
                "schedule_hash": self.schedule_hash,
                "selected_strategy_ids": list(strategy_ids),
                "strategy_identity_hash": self.strategy_identity_hash,
                "target_frame_sha256": self.target_frame_sha256,
                "trace_hash": self.trace_hash,
            }
        )
        object.__setattr__(self, "selected_strategy_ids", strategy_ids)
        object.__setattr__(self, "pit_evidence_json", tuple(canonical_evidence))
        object.__setattr__(self, "binding_hash", binding_hash)

    def as_mapping(self) -> dict[str, object]:
        return {
            "binding_hash": self.binding_hash,
            "certificate_hash": self.certificate_hash,
            "decision_time_safe": False,
            "format": "northstar.backtest-decision-replay-binding.v1",
            "market_replay_hash": self.market_replay_hash,
            "pit_evidence": [json.loads(item) for item in self.pit_evidence_json],
            "profile": {
                "dimension_key": self.profile_dimension_key,
                "profile_config_sha256": self.profile_config_sha256,
                "profile_id": self.profile_id,
            },
            "receipt_hash": self.receipt_hash,
            "schedule_hash": self.schedule_hash,
            "selected_strategy_ids": list(self.selected_strategy_ids),
            "selection_mode": "PER_DECISION_POINT_IN_TIME_REPLAY",
            "strategy_identity_hash": self.strategy_identity_hash,
            "target_frame_sha256": self.target_frame_sha256,
            "trace_hash": self.trace_hash,
        }


@dataclass(frozen=True, slots=True)
class BacktestDataReference:
    """回测输入数据的最小、可审计且无路径的证据。"""

    input_kind: BacktestDataInputKind
    dataset_id: str
    source_id: str
    adapter_id: str | None
    content_sha256: str
    schema_version: str
    source_config_sha256: str | None = None
    selection_mode: str | None = None
    decision_time_safe: bool = False
    dataset_version_hash: str | None = None
    snapshot_id: str | None = None
    selected_frame_hash: str | None = None
    publication_authorization_hash: str | None = None
    publication_scope_json: str | None = None
    pit_evidence_json: str | None = None
    decision_replay: BacktestDecisionReplayBinding | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.input_kind, BacktestDataInputKind):
            raise BacktestContractError("data input_kind 必须是 BacktestDataInputKind")
        for field_name in ("dataset_id", "source_id", "schema_version"):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name=field_name),
            )
        if self.adapter_id is not None:
            object.__setattr__(
                self,
                "adapter_id",
                _require_text(self.adapter_id, field_name="adapter_id"),
            )
        object.__setattr__(
            self,
            "content_sha256",
            _require_sha256(self.content_sha256, field_name="content_sha256"),
        )
        for field_name in (
            "source_config_sha256",
            "dataset_version_hash",
            "snapshot_id",
            "selected_frame_hash",
            "publication_authorization_hash",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_sha256(getattr(self, field_name), field_name=field_name, allow_none=True),
            )
        if not isinstance(self.decision_time_safe, bool):
            raise BacktestContractError("decision_time_safe 必须是明确布尔值")
        if self.selection_mode is not None:
            object.__setattr__(
                self,
                "selection_mode",
                _require_text(self.selection_mode, field_name="selection_mode"),
            )
        if self.publication_scope_json is not None:
            try:
                scope = json.loads(self.publication_scope_json)
            except (TypeError, json.JSONDecodeError) as exc:
                raise BacktestContractError("publication_scope_json 必须是 canonical JSON 对象") from exc
            if not isinstance(scope, dict):
                raise BacktestContractError("publication_scope_json 必须是对象")
            object.__setattr__(self, "publication_scope_json", _canonical_json(scope))
        pit_evidence: dict[str, object] | None = None
        if self.pit_evidence_json is not None:
            try:
                pit_evidence_value = json.loads(self.pit_evidence_json)
            except (TypeError, json.JSONDecodeError) as exc:
                raise BacktestContractError("pit_evidence_json 必须是 canonical JSON 对象") from exc
            canonical_evidence, pit_evidence = _canonical_pit_evidence(pit_evidence_value)
            object.__setattr__(self, "pit_evidence_json", canonical_evidence)
        if self.input_kind is BacktestDataInputKind.LEGACY_MARKET_PROJECTION:
            if self.dataset_version_hash is not None or self.snapshot_id is not None:
                raise BacktestContractError("legacy 市场投影不得伪装为 immutable PIT snapshot")
            if self.decision_time_safe:
                raise BacktestContractError("legacy 市场投影不能声明逐决策 PIT 安全")
            if pit_evidence is not None:
                raise BacktestContractError("legacy 市场投影不得携带 immutable PIT evidence")
            if self.decision_replay is not None:
                raise BacktestContractError("legacy market projection cannot carry decision replay binding")
        elif self.input_kind is BacktestDataInputKind.IMMUTABLE_PIT_SNAPSHOT:
            required = (
                self.dataset_version_hash,
                self.snapshot_id,
                self.selected_frame_hash,
                self.publication_authorization_hash,
                self.publication_scope_json,
                self.selection_mode,
                pit_evidence,
            )
            if any(value is None for value in required):
                raise BacktestContractError("immutable PIT 输入缺少版本、snapshot、授权或选择模式")
            assert pit_evidence is not None
            if self.decision_time_safe:
                raise BacktestContractError(
                    "P2-WP04 只接受静态 as-of PIT 视图，不能手工声明逐决策安全"
                )
            if self.selection_mode != "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY":
                raise BacktestContractError(
                    "P2-WP04 只接受 STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY 选择模式"
                )
            if (
                pit_evidence["dataset_id"] != self.dataset_id
                or pit_evidence["source_id"] != self.source_id
                or pit_evidence["source_config_sha256"] != self.source_config_sha256
                or pit_evidence["dataset_version_hash"] != self.dataset_version_hash
                or pit_evidence["snapshot_id"] != self.snapshot_id
                or pit_evidence["selected_frame_hash"] != self.selected_frame_hash
                or pit_evidence["publication_authorization_hash"]
                != self.publication_authorization_hash
                or pit_evidence["selection_mode"] != self.selection_mode
                or pit_evidence["decision_time_safe"] != self.decision_time_safe
            ):
                raise BacktestContractError("PIT evidence 与回测数据引用不一致")
            if _canonical_json(pit_evidence["publication_scope"]) != self.publication_scope_json:
                raise BacktestContractError("PIT evidence publication_scope 与回测数据引用不一致")
            if pit_evidence["selected_frame_hash"] != self.content_sha256:
                raise BacktestContractError(
                    "PIT evidence selected_frame_hash 与回测数据 content_sha256 不一致"
                )
            pit_spec = pit_evidence["spec"]
            assert isinstance(pit_spec, dict)  # 已由 _canonical_pit_evidence 严格验证。
            if self.schema_version != _require_text(
                pit_spec.get("schema_version"),
                field_name="pit_evidence.spec.schema_version",
            ):
                raise BacktestContractError(
                    "PIT evidence spec.schema_version 与回测数据 schema_version 不一致"
                )
            if self.decision_replay is not None:
                raise BacktestContractError("static PIT snapshot cannot carry decision replay binding")
        else:
            if not isinstance(self.decision_replay, BacktestDecisionReplayBinding):
                raise BacktestContractError("decision replay input requires BacktestDecisionReplayBinding")
            if self.decision_time_safe:
                raise BacktestContractError("decision replay receipt cannot self-declare decision safety")
            if self.selection_mode != "PER_DECISION_POINT_IN_TIME_REPLAY":
                raise BacktestContractError("decision replay input requires PER_DECISION_POINT_IN_TIME_REPLAY")
            if any(
                value is not None
                for value in (
                    self.dataset_version_hash,
                    self.snapshot_id,
                    self.selected_frame_hash,
                    self.publication_authorization_hash,
                    self.publication_scope_json,
                    pit_evidence,
                )
            ):
                raise BacktestContractError("decision replay input cannot masquerade as one static PIT snapshot")
            binding = self.decision_replay
            first_evidence = json.loads(binding.pit_evidence_json[0])
            if not isinstance(first_evidence, dict):  # pragma: no cover - binding validates this.
                raise BacktestContractError("decision replay binding evidence is invalid")
            if (
                self.dataset_id != first_evidence["dataset_id"]
                or self.source_id != first_evidence["source_id"]
                or self.source_config_sha256 != first_evidence["source_config_sha256"]
                or self.schema_version != first_evidence["spec"]["schema_version"]
                or self.content_sha256 != binding.market_replay_hash
            ):
                raise BacktestContractError("decision replay binding does not match data reference")

    @classmethod
    def from_source_manifest(cls, source_manifest: Mapping[str, object]) -> "BacktestDataReference":
        if not isinstance(source_manifest, Mapping):
            raise BacktestContractError("source_manifest 必须是映射")
        point_in_time = source_manifest.get("point_in_time")
        data_source = source_manifest.get("data_source")
        governance = source_manifest.get("governance")
        source_id = (
            governance.get("source_id")
            if isinstance(governance, Mapping) and governance.get("source_id") is not None
            else data_source
        )
        source_config_sha256 = (
            governance.get("source_config_sha256")
            if isinstance(governance, Mapping)
            else None
        )
        dataset_id = _require_text(source_manifest.get("dataset_id"), field_name="dataset_id")
        normalized_source_id = _require_text(source_id, field_name="source_id")
        adapter_id = (
            _require_text(data_source, field_name="adapter_id")
            if data_source is not None
            else None
        )
        content_sha256 = _require_sha256(
            source_manifest.get("content_sha256"), field_name="content_sha256"
        )
        if content_sha256 is None:  # 仅为类型收窄；allow_none 默认为 false。
            raise BacktestContractError("content_sha256 不能为空")
        schema_version = _require_text(
            source_manifest.get("schema_version"), field_name="schema_version"
        )
        normalized_source_config_sha256 = _require_sha256(
            source_config_sha256,
            field_name="source_config_sha256",
            allow_none=True,
        )
        if not isinstance(point_in_time, Mapping) or point_in_time.get("status") == "LEGACY_NOT_PIT":
            return cls(
                input_kind=BacktestDataInputKind.LEGACY_MARKET_PROJECTION,
                dataset_id=dataset_id,
                source_id=normalized_source_id,
                adapter_id=adapter_id,
                content_sha256=content_sha256,
                schema_version=schema_version,
                source_config_sha256=normalized_source_config_sha256,
                decision_time_safe=False,
            )
        return cls(
            input_kind=BacktestDataInputKind.IMMUTABLE_PIT_SNAPSHOT,
            dataset_id=dataset_id,
            source_id=normalized_source_id,
            adapter_id=adapter_id,
            content_sha256=content_sha256,
            schema_version=schema_version,
            source_config_sha256=normalized_source_config_sha256,
            selection_mode=point_in_time.get("selection_mode"),
            decision_time_safe=point_in_time.get("decision_time_safe", False),
            dataset_version_hash=point_in_time.get("dataset_version_hash"),
            snapshot_id=point_in_time.get("snapshot_id"),
            selected_frame_hash=point_in_time.get("selected_frame_hash"),
            publication_authorization_hash=point_in_time.get(
                "publication_authorization_hash"
            ),
            publication_scope_json=_canonical_json(point_in_time.get("publication_scope")),
            pit_evidence_json=_canonical_json(point_in_time),
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "input_kind": self.input_kind.value,
            "dataset_id": self.dataset_id,
            "data_source": self.adapter_id or self.source_id,
            "content_sha256": self.content_sha256,
            "schema_version": self.schema_version,
            "governance": {
                "source_id": self.source_id,
                "source_config_sha256": self.source_config_sha256,
            },
            "point_in_time": (
                self.decision_replay.as_mapping()
                if self.decision_replay is not None
                else json.loads(self.pit_evidence_json)
                if self.pit_evidence_json is not None
                else {
                    "status": "LEGACY_NOT_PIT",
                    "selection_mode": self.selection_mode,
                    "decision_time_safe": self.decision_time_safe,
                }
            ),
        }


@dataclass(frozen=True, slots=True)
class BacktestAssumptions:
    """所有引擎可见的显式成本、延迟与撮合参数快照。"""

    initial_cash: float
    commission_bps: float
    min_commission: float
    slippage_bps: float
    slippage_ticks: float
    max_volume_participation: float
    lot_size: int
    execution_delay_sessions: int
    sellable_after_sessions: int
    order_ttl_bars: int
    queue_ahead_ratio: float

    def __post_init__(self) -> None:
        for field_name in (
            "initial_cash",
            "commission_bps",
            "min_commission",
            "slippage_bps",
            "slippage_ticks",
            "max_volume_participation",
            "queue_ahead_ratio",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not math.isfinite(float(value)):
                raise BacktestContractError(f"{field_name} 必须是有限数")
        if self.initial_cash <= 0:
            raise BacktestContractError("initial_cash 必须大于 0")
        if any(
            getattr(self, field_name) < 0
            for field_name in ("commission_bps", "min_commission", "slippage_bps", "slippage_ticks")
        ):
            raise BacktestContractError("佣金与滑点假设不能为负数")
        if not 0 < self.max_volume_participation <= 1:
            raise BacktestContractError("max_volume_participation 必须位于 (0, 1]")
        if not 0 <= self.queue_ahead_ratio <= 1:
            raise BacktestContractError("queue_ahead_ratio 必须位于 [0, 1]")
        for field_name, minimum in (
            ("lot_size", 1),
            ("execution_delay_sessions", 1),
            ("sellable_after_sessions", 0),
            ("order_ttl_bars", 1),
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise BacktestContractError(f"{field_name} 不满足最小值 {minimum}")

    def as_mapping(self) -> dict[str, object]:
        return {
            "initial_cash": self.initial_cash,
            "commission_bps": self.commission_bps,
            "min_commission": self.min_commission,
            "slippage_bps": self.slippage_bps,
            "slippage_ticks": self.slippage_ticks,
            "max_volume_participation": self.max_volume_participation,
            "lot_size": self.lot_size,
            "execution_delay_sessions": self.execution_delay_sessions,
            "sellable_after_sessions": self.sellable_after_sessions,
            "order_ttl_bars": self.order_ttl_bars,
            "queue_ahead_ratio": self.queue_ahead_ratio,
        }


@dataclass(frozen=True, slots=True)
class BacktestCodeReference:
    """回测构建的代码身份；摘要是完整性标识而非签名。"""

    package_version: str
    git_commit: str | None
    git_dirty: bool | None
    worktree_sha256: str | None
    strategy_identity_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "package_version", _require_text(self.package_version, field_name="package_version"))
        if self.git_commit is not None:
            object.__setattr__(self, "git_commit", _require_text(self.git_commit, field_name="git_commit"))
        if self.git_dirty is not None and not isinstance(self.git_dirty, bool):
            raise BacktestContractError("git_dirty 必须是 bool 或 null")
        object.__setattr__(
            self,
            "worktree_sha256",
            _require_sha256(self.worktree_sha256, field_name="worktree_sha256", allow_none=True),
        )
        object.__setattr__(
            self,
            "strategy_identity_hash",
            _require_sha256(
                self.strategy_identity_hash,
                field_name="strategy_identity_hash",
                allow_none=True,
            ),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "BacktestCodeReference":
        package_version = _require_text(value.get("package_version"), field_name="package_version")
        raw_git_commit = value.get("git_commit")
        git_commit = (
            _require_text(raw_git_commit, field_name="git_commit")
            if raw_git_commit is not None
            else None
        )
        raw_git_dirty = value.get("git_dirty")
        if raw_git_dirty is not None and not isinstance(raw_git_dirty, bool):
            raise BacktestContractError("git_dirty 必须是 bool 或 null")
        worktree_sha256 = _require_sha256(
            value.get("worktree_sha256"),
            field_name="worktree_sha256",
            allow_none=True,
        )
        strategy_identity_hash = _require_sha256(
            value.get("strategy_identity_hash"),
            field_name="strategy_identity_hash",
            allow_none=True,
        )
        return cls(
            package_version=package_version,
            git_commit=git_commit,
            git_dirty=raw_git_dirty,
            worktree_sha256=worktree_sha256,
            strategy_identity_hash=strategy_identity_hash,
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "package_version": self.package_version,
            "git_commit": self.git_commit,
            "git_dirty": self.git_dirty,
            "worktree_sha256": self.worktree_sha256,
            "strategy_identity_hash": self.strategy_identity_hash,
        }


@dataclass(frozen=True, slots=True)
class BacktestRequest:
    """统一回测的不可变声明，不携带裸 DataFrame、订单或券商对象。"""

    engine: BacktestEngine
    profile_id: str
    profile_config_sha256: str
    profile_dimension_key: str
    source_frequency: str
    signal_frequency: str
    execution_frequency: str
    settlement_frequency: str
    result_frequency: str
    selected_strategy_ids: tuple[str, ...]
    target: TargetFrameReference
    data: BacktestDataReference
    assumptions: BacktestAssumptions
    code: BacktestCodeReference

    def __post_init__(self) -> None:
        if not isinstance(self.engine, BacktestEngine):
            raise BacktestContractError("engine 必须是 BacktestEngine")
        object.__setattr__(self, "profile_id", _require_text(self.profile_id, field_name="profile_id"))
        object.__setattr__(
            self,
            "profile_config_sha256",
            _require_sha256(self.profile_config_sha256, field_name="profile_config_sha256"),
        )
        object.__setattr__(
            self,
            "profile_dimension_key",
            _require_text(self.profile_dimension_key, field_name="profile_dimension_key"),
        )
        for field_name in (
            "source_frequency",
            "signal_frequency",
            "execution_frequency",
            "settlement_frequency",
            "result_frequency",
        ):
            object.__setattr__(
                self,
                field_name,
                _require_text(getattr(self, field_name), field_name=field_name),
            )
        if not isinstance(self.selected_strategy_ids, tuple) or not self.selected_strategy_ids:
            raise BacktestContractError("selected_strategy_ids 必须是非空元组")
        normalized_strategy_ids = tuple(
            _require_text(item, field_name="selected_strategy_id")
            for item in self.selected_strategy_ids
        )
        if len(set(normalized_strategy_ids)) != len(normalized_strategy_ids):
            raise BacktestContractError("selected_strategy_ids 不能重复")
        object.__setattr__(self, "selected_strategy_ids", normalized_strategy_ids)
        if not isinstance(self.target, TargetFrameReference):
            raise BacktestContractError("target 必须是 TargetFrameReference")
        if not isinstance(self.data, BacktestDataReference):
            raise BacktestContractError("data 必须是 BacktestDataReference")
        if not isinstance(self.assumptions, BacktestAssumptions):
            raise BacktestContractError("assumptions 必须是 BacktestAssumptions")
        if not isinstance(self.code, BacktestCodeReference):
            raise BacktestContractError("code 必须是 BacktestCodeReference")
        if self.data.input_kind is BacktestDataInputKind.DECISION_REPLAY_RECEIPT:
            binding = self.data.decision_replay
            if binding is None:  # pragma: no cover - BacktestDataReference validates this.
                raise BacktestContractError("decision replay request requires a binding")
            if self.engine is not BacktestEngine.WEIGHT_RETURN:
                raise BacktestContractError("decision replay receipt currently supports weight_return only")
            if (
                self.profile_id != binding.profile_id
                or self.profile_config_sha256 != binding.profile_config_sha256
                or self.profile_dimension_key != binding.profile_dimension_key
                or self.selected_strategy_ids != binding.selected_strategy_ids
                or self.target.target_frame_sha256 != binding.target_frame_sha256
                or self.code.strategy_identity_hash != binding.strategy_identity_hash
            ):
                raise BacktestContractError("decision replay binding does not match BacktestRequest")

    @property
    def semantics(self) -> BacktestEngineSemantics:
        return engine_semantics(self.engine)

    @property
    def request_hash(self) -> str:
        return _sha256(self.as_mapping())

    def as_mapping(self) -> dict[str, object]:
        return {
            "engine": self.engine.value,
            "profile": {
                "profile_id": self.profile_id,
                "profile_config_sha256": self.profile_config_sha256,
                "dimension_key": self.profile_dimension_key,
            },
            "frequencies": {
                "source_frequency": self.source_frequency,
                "signal_frequency": self.signal_frequency,
                "execution_frequency": self.execution_frequency,
                "settlement_frequency": self.settlement_frequency,
                "result_frequency": self.result_frequency,
            },
            "strategy": {
                "selected_strategy_ids": list(self.selected_strategy_ids),
                **self.target.as_mapping(),
            },
            "data": self.data.as_mapping(),
            "assumptions": self.assumptions.as_mapping(),
            "code": self.code.as_mapping(),
            "engine_semantics": self.semantics.as_mapping(),
        }


@dataclass(frozen=True, slots=True)
class BacktestExecutionAudit:
    """结果的执行审计摘要；它不会把模拟成交称作已闭合交易。"""

    level: ExecutionAuditLevel
    fill_event_count: int | None
    order_event_count: int | None
    rejected_event_count: int
    filled_quantity: float | None
    commission_total: float | None
    traded_notional_total: float | None
    order_status_counts: tuple[tuple[str, int], ...] = ()
    limitations: tuple[str, ...] = ()

    def as_mapping(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "detail_level": self.level.value,
            "fill_event_count": self.fill_event_count,
            "order_event_count": self.order_event_count,
            "rejected_event_count": self.rejected_event_count,
            "filled_quantity": self.filled_quantity,
            "commission_total": self.commission_total,
            "traded_notional_total": self.traded_notional_total,
            "limitations": list(self.limitations),
        }
        if self.order_status_counts:
            payload["order_status_counts"] = dict(self.order_status_counts)
        return payload


def _sum_records(records: Sequence[BacktestRecord], field_name: str) -> float | None:
    values: list[float] = []
    for record in records:
        value = record.get(field_name)
        if isinstance(value, bool):
            continue
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            values.append(number)
    return sum(values) if values else None


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """三种引擎共享的不可变结果 envelope。

    ``trades`` 是历史模拟的成交事件，不等于真实成交，也不应被报告为 closed trades。
    引擎真实性只能从 ``engine``、``semantics`` 和 ``execution_audit`` 读取。
    """

    engine: BacktestEngine
    total_return: float
    annualized_return: float
    max_drawdown: float
    turnover_estimate: float
    equity_curve: Sequence[Mapping[str, object]] = field(default_factory=tuple)
    drawdown_curve: Sequence[Mapping[str, object]] = field(default_factory=tuple)
    monthly_returns: Sequence[Mapping[str, object]] = field(default_factory=tuple)
    turnover_curve: Sequence[Mapping[str, object]] = field(default_factory=tuple)
    trades: Sequence[Mapping[str, object]] = field(default_factory=tuple)
    orders: Sequence[Mapping[str, object]] = field(default_factory=tuple)
    rejected_orders: Sequence[str] = field(default_factory=tuple)
    request_hash: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.engine, BacktestEngine):
            raise BacktestContractError("result.engine 必须是 BacktestEngine")
        for field_name in (
            "total_return",
            "annualized_return",
            "max_drawdown",
            "turnover_estimate",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not math.isfinite(float(value)):
                raise BacktestContractError(f"result.{field_name} 必须是有限数")
        for field_name in (
            "equity_curve",
            "drawdown_curve",
            "monthly_returns",
            "turnover_curve",
            "trades",
            "orders",
        ):
            object.__setattr__(
                self,
                field_name,
                _freeze_records(getattr(self, field_name), field_name=field_name),
            )
        if isinstance(self.rejected_orders, (str, bytes, bytearray, Mapping)):
            raise BacktestContractError("rejected_orders 必须是字符串序列")
        rejected = tuple(
            _require_text(value, field_name="rejected_order")
            for value in self.rejected_orders
        )
        object.__setattr__(self, "rejected_orders", rejected)
        object.__setattr__(
            self,
            "request_hash",
            _require_sha256(self.request_hash, field_name="result.request_hash", allow_none=True),
        )
        if not self.equity_curve:
            raise BacktestContractError("回测结果必须包含净值曲线")
        trades = cast(tuple[BacktestRecord, ...], self.trades)
        orders = cast(tuple[BacktestRecord, ...], self.orders)
        if self.engine is BacktestEngine.WEIGHT_RETURN and (
            trades or orders or self.rejected_orders
        ):
            raise BacktestContractError(
                "weight_return 不模拟订单或成交事件，不能携带 trades/orders/rejections"
            )
        if self.engine is BacktestEngine.FUTURES_DAILY and orders:
            raise BacktestContractError(
                "futures_daily 只记录目标与模拟成交事件，不能携带订单生命周期"
            )

    @property
    def semantics(self) -> BacktestEngineSemantics:
        return engine_semantics(self.engine)

    @property
    def fidelity(self) -> BacktestFidelity:
        return self.semantics.fidelity

    @property
    def data_semantics(self) -> BacktestDataSemantics:
        return self.semantics.data_semantics

    @property
    def limitations(self) -> tuple[str, ...]:
        return self.semantics.limitations

    @property
    def eligible_for_admission(self) -> bool:
        """P2-WP04 不升级任何回测结果的准入资格。"""

        return False

    @property
    def execution_audit(self) -> BacktestExecutionAudit:
        semantics = self.semantics
        if semantics.execution_audit_level is ExecutionAuditLevel.NOT_MODELED:
            return BacktestExecutionAudit(
                level=ExecutionAuditLevel.NOT_MODELED,
                fill_event_count=None,
                order_event_count=None,
                rejected_event_count=0,
                filled_quantity=None,
                commission_total=None,
                traded_notional_total=None,
                limitations=semantics.limitations,
            )
        trades = cast(tuple[BacktestRecord, ...], self.trades)
        orders = cast(tuple[BacktestRecord, ...], self.orders)
        status_counts = Counter(str(record.get("status") or "unknown") for record in orders)
        return BacktestExecutionAudit(
            level=semantics.execution_audit_level,
            fill_event_count=len(trades),
            order_event_count=(len(orders) if orders else None),
            rejected_event_count=len(self.rejected_orders)
            + sum(count for status, count in status_counts.items() if status == "REJECTED"),
            filled_quantity=_sum_records(trades, "qty"),
            commission_total=_sum_records(trades, "commission"),
            traded_notional_total=_sum_records(trades, "notional"),
            order_status_counts=tuple(sorted(status_counts.items())),
            limitations=semantics.limitations,
        )

    @property
    def result_hash(self) -> str:
        return _sha256(self.as_mapping(include_request_hash=True))

    def bind_request(self, request: BacktestRequest) -> "BacktestResult":
        if not isinstance(request, BacktestRequest):
            raise BacktestContractError("request 必须是 BacktestRequest")
        if request.data.input_kind is BacktestDataInputKind.DECISION_REPLAY_RECEIPT:
            raise BacktestContractError(
                "decision replay receipt requests are construction-only and cannot bind a result"
            )
        if request.engine is not self.engine:
            raise BacktestContractError(
                f"回测结果引擎 {self.engine.value} 与请求 {request.engine.value} 不一致"
            )
        if self.request_hash is not None and self.request_hash != request.request_hash:
            raise BacktestContractError("回测结果已绑定到另一份请求，拒绝重用")
        equity_curve = cast(tuple[BacktestRecord, ...], self.equity_curve)
        drawdown_curve = cast(tuple[BacktestRecord, ...], self.drawdown_curve)
        monthly_returns = cast(tuple[BacktestRecord, ...], self.monthly_returns)
        turnover_curve = cast(tuple[BacktestRecord, ...], self.turnover_curve)
        trades = cast(tuple[BacktestRecord, ...], self.trades)
        orders = cast(tuple[BacktestRecord, ...], self.orders)
        return BacktestResult(
            engine=self.engine,
            total_return=self.total_return,
            annualized_return=self.annualized_return,
            max_drawdown=self.max_drawdown,
            turnover_estimate=self.turnover_estimate,
            equity_curve=tuple(record.as_mapping() for record in equity_curve),
            drawdown_curve=tuple(record.as_mapping() for record in drawdown_curve),
            monthly_returns=tuple(record.as_mapping() for record in monthly_returns),
            turnover_curve=tuple(record.as_mapping() for record in turnover_curve),
            trades=tuple(record.as_mapping() for record in trades),
            orders=tuple(record.as_mapping() for record in orders),
            rejected_orders=self.rejected_orders,
            request_hash=request.request_hash,
        )

    def as_mapping(self, *, include_request_hash: bool = True) -> dict[str, object]:
        equity_curve = cast(tuple[BacktestRecord, ...], self.equity_curve)
        drawdown_curve = cast(tuple[BacktestRecord, ...], self.drawdown_curve)
        monthly_returns = cast(tuple[BacktestRecord, ...], self.monthly_returns)
        turnover_curve = cast(tuple[BacktestRecord, ...], self.turnover_curve)
        trades = cast(tuple[BacktestRecord, ...], self.trades)
        orders = cast(tuple[BacktestRecord, ...], self.orders)
        payload: dict[str, object] = {
            "engine": self.engine.value,
            "fidelity": self.fidelity.value,
            "data_semantics": self.data_semantics.value,
            "eligible_for_admission": False,
            "limitations": list(self.limitations),
            "total_return": self.total_return,
            "annualized_return": self.annualized_return,
            "max_drawdown": self.max_drawdown,
            "turnover_estimate": self.turnover_estimate,
            "equity_curve": [record.as_mapping() for record in equity_curve],
            "drawdown_curve": [record.as_mapping() for record in drawdown_curve],
            "monthly_returns": [record.as_mapping() for record in monthly_returns],
            "turnover_curve": [record.as_mapping() for record in turnover_curve],
            "trades": [record.as_mapping() for record in trades],
            "orders": [record.as_mapping() for record in orders],
            "rejected_orders": list(self.rejected_orders),
            "execution_audit": self.execution_audit.as_mapping(),
        }
        if include_request_hash:
            payload["request_hash"] = self.request_hash
        return payload


@dataclass(frozen=True, slots=True)
class RunManifest:
    """一份可复验的回测运行清单；不保存路径、裸行情或运行时钟。"""

    request: BacktestRequest
    result: BacktestResult
    analytics_sha256: str
    metrics_sha256: str
    admission_status: str | None
    admission_policy_id: str | None
    admission_policy_config_sha256: str | None
    admission_blocking_check_count: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.request, BacktestRequest):
            raise BacktestContractError("manifest.request 必须是 BacktestRequest")
        if not isinstance(self.result, BacktestResult):
            raise BacktestContractError("manifest.result 必须是 BacktestResult")
        if self.result.request_hash != self.request.request_hash:
            raise BacktestContractError("manifest result 未绑定当前 request")
        object.__setattr__(
            self,
            "analytics_sha256",
            _require_sha256(self.analytics_sha256, field_name="analytics_sha256"),
        )
        object.__setattr__(
            self,
            "metrics_sha256",
            _require_sha256(self.metrics_sha256, field_name="metrics_sha256"),
        )
        if self.admission_status is not None:
            object.__setattr__(
                self,
                "admission_status",
                _require_text(self.admission_status, field_name="admission_status"),
            )
        if self.admission_policy_id is not None:
            object.__setattr__(
                self,
                "admission_policy_id",
                _require_text(self.admission_policy_id, field_name="admission_policy_id"),
            )
        object.__setattr__(
            self,
            "admission_policy_config_sha256",
            _require_sha256(
                self.admission_policy_config_sha256,
                field_name="admission_policy_config_sha256",
                allow_none=True,
            ),
        )
        if self.admission_blocking_check_count is not None and (
            isinstance(self.admission_blocking_check_count, bool)
            or not isinstance(self.admission_blocking_check_count, int)
            or self.admission_blocking_check_count < 0
        ):
            raise BacktestContractError("admission_blocking_check_count 必须是非负整数或 null")

    @classmethod
    def create(
        cls,
        *,
        request: BacktestRequest,
        result: BacktestResult,
        analytics: Mapping[str, object],
        metrics: Mapping[str, object],
        admission: Mapping[str, object],
    ) -> "RunManifest":
        bound_result = result.bind_request(request)
        return cls(
            request=request,
            result=bound_result,
            analytics_sha256=_sha256(analytics),
            metrics_sha256=_sha256(metrics),
            admission_status=_optional_text(admission.get("status"), field_name="admission.status"),
            admission_policy_id=_optional_text(
                admission.get("policy_id"), field_name="admission.policy_id"
            ),
            admission_policy_config_sha256=(
                _require_sha256(
                    admission.get("policy_config_sha256"),
                    field_name="admission.policy_config_sha256",
                    allow_none=True,
                )
            ),
            admission_blocking_check_count=_optional_nonnegative_int(
                admission.get("blocking_check_count"),
                field_name="admission.blocking_check_count",
            ),
        )

    @property
    def run_id(self) -> str:
        """同一请求使用同一稳定 ID；不同输出会被不可变制品检查拒绝覆盖。"""

        return f"bt-{self.request.request_hash[:16]}"

    @property
    def run_fingerprint(self) -> str:
        return _sha256(
            {
                "schema_version": _MANIFEST_SCHEMA_VERSION,
                "request_hash": self.request.request_hash,
                "result_hash": self.result.result_hash,
                "analytics_sha256": self.analytics_sha256,
                "metrics_sha256": self.metrics_sha256,
                "research_admission": self.research_admission_mapping(),
            }
        )

    def research_admission_mapping(self) -> dict[str, object]:
        return {
            "status": "NOT_ELIGIBLE",
            "observed_policy_status": self.admission_status,
            "policy_id": self.admission_policy_id,
            "policy_config_sha256": self.admission_policy_config_sha256,
            "blocking_check_count": self.admission_blocking_check_count,
            "eligible_for_human_review": False,
            "reason": (
                "P2-WP04 仅冻结回测审计合同；无论上游政策观察值为何，"
                "它都不会把静态、legacy 或本阶段结果升级为候选策略准入。"
            ),
        }

    def verify_outputs(
        self,
        *,
        result: BacktestResult,
        analytics: Mapping[str, object],
        metrics: Mapping[str, object],
    ) -> None:
        """报告写入前复验结果和分析没有在建清单后被篡改。"""

        if result.result_hash != self.result.result_hash:
            raise BacktestContractError("回测结果与运行清单 checksum 不一致，已拒绝归档")
        if _sha256(analytics) != self.analytics_sha256:
            raise BacktestContractError("回测 analytics 与运行清单 checksum 不一致，已拒绝归档")
        if _sha256(metrics) != self.metrics_sha256:
            raise BacktestContractError("回测 metrics 与运行清单 checksum 不一致，已拒绝归档")

    def as_mapping(self) -> dict[str, object]:
        request = self.request.as_mapping()
        return {
            "schema_version": _MANIFEST_SCHEMA_VERSION,
            "run_id": self.run_id,
            "run_fingerprint": f"sha256:{self.run_fingerprint}",
            "request_hash": self.request.request_hash,
            "result_hash": self.result.result_hash,
            "code": request["code"],
            "profile": request["profile"],
            "data": request["data"],
            "strategy": request["strategy"],
            "engine": request["engine_semantics"],
            "effective_configuration": {"backtest": request["assumptions"]},
            "request": request,
            "result": {
                "engine": self.result.engine.value,
                "fidelity": self.result.fidelity.value,
                "data_semantics": self.result.data_semantics.value,
                "execution_audit": self.result.execution_audit.as_mapping(),
                "limitations": list(self.result.limitations),
                "eligible_for_admission": False,
            },
            "candidate_admission_eligible": False,
            "research_admission": self.research_admission_mapping(),
            "output_checksums": {
                "result_sha256": self.result.result_hash,
                "analytics_sha256": self.analytics_sha256,
                "metrics_sha256": self.metrics_sha256,
            },
            "reproducibility_note": _reproducibility_note(self.request.code),
        }

    def to_json(self) -> str:
        return json.dumps(self.as_mapping(), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)


def _reproducibility_note(code: BacktestCodeReference) -> str:
    if code.git_dirty is True:
        return "运行时工作树含未提交变更；已记录差异哈希，正式比较前应固定到提交版本。"
    if code.git_commit:
        return "数据、有效配置、策略目标和代码提交均已写入本清单。"
    return "无法读取 Git 提交信息；请在受版本控制的环境中复跑以获得完整代码溯源。"


__all__ = [
    "BacktestAssumptions",
    "BacktestCodeReference",
    "BacktestContractError",
    "BacktestDataInputKind",
    "BacktestDataReference",
    "BacktestDecisionReplayBinding",
    "BacktestDataSemantics",
    "BacktestEngine",
    "BacktestEngineSemantics",
    "BacktestExecutionAudit",
    "BacktestFidelity",
    "BacktestInputKind",
    "BacktestRecord",
    "BacktestRequest",
    "BacktestResult",
    "ExecutionAuditLevel",
    "RunManifest",
    "TargetFrameReference",
    "engine_semantics",
]

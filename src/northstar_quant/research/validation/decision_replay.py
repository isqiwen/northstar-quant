"""受控逐决策策略目标轨迹的不可变合同。

本模块只描述 ``DecisionReplayPlan`` 每个 checkpoint 已经实际产生的目标切片。它不运行
策略、不读取制品库，也不把轨迹升级为回测、候选准入或交易资格；这些组合职责只能留在
Application 层。P1 的 ``MarketDataSnapshot`` 仍是一份单点 static as-of 视图，严格性来自
每个 checkpoint 的独立重放与本合同对 target slice 的逐项绑定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
import json
import math
import re
from typing import Any

import polars as pl

from northstar_quant.data_platform.artifacts.fingerprints import (
    FingerprintError,
    canonical_json_sha256,
    require_sha256,
)
from northstar_quant.research.backtest.models import (
    BacktestContractError,
    TargetFrameReference,
)
from northstar_quant.research.validation.lookahead import (
    DecisionMarketDataEvidence,
    DecisionReplayEvidence,
    DecisionReplayPlan,
    LookaheadInputKind,
    LookaheadInputUsage,
    LookaheadInputUsageDeclaration,
    TargetDecisionEvidence,
)


_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SECRET_TEXT_RE = re.compile(
    r"(?i)(api[_ -]?key|authorization|bearer|cookie|credential|password|passwd|secret|token)"
)


class DecisionReplayTargetError(ValueError):
    """逐决策 target 轨迹无法安全构造或验证时抛出。"""


class DecisionTargetStatus(str, Enum):
    """每个 checkpoint 的 target 产出状态。"""

    TARGETS = "targets"
    NO_TARGET_WARMUP = "no_target_warmup"


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _TEXT_RE.fullmatch(value.strip()) is None:
        raise DecisionReplayTargetError(f"{field_name} 必须是无路径的规范标识")
    return value.strip()


def _field(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _FIELD_RE.fullmatch(value.strip()) is None:
        raise DecisionReplayTargetError(f"{field_name} 必须是合法字段名")
    return value.strip()


def _hash(value: object, field_name: str) -> str:
    try:
        return require_sha256(value, field_name=field_name)  # type: ignore[arg-type]
    except FingerprintError as exc:
        raise DecisionReplayTargetError(str(exc)) from exc


def _utc_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DecisionReplayTargetError(f"{field_name} 必须是带时区的 datetime")
    return value.astimezone(UTC)


def _event_time(value: object, field_name: str) -> date | datetime:
    if isinstance(value, datetime):
        return _utc_datetime(value, field_name)
    if isinstance(value, date):
        return value
    raise DecisionReplayTargetError(f"{field_name} 必须是 date 或带时区的 datetime")


def _event_mapping(value: date | datetime) -> dict[str, str]:
    if isinstance(value, datetime):
        return {"kind": "datetime", "value": value.astimezone(UTC).isoformat()}
    return {"kind": "date", "value": value.isoformat()}


def _finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecisionReplayTargetError(f"{field_name} 必须是有限数值")
    result = float(value)
    if not math.isfinite(result):
        raise DecisionReplayTargetError(f"{field_name} 必须是有限数值")
    return result


def _canonical_parameter_mapping(payload_json: object) -> tuple[str, str]:
    """解析有限、无路径的有效参数 JSON，并返回 canonical 文本及其摘要。"""

    if not isinstance(payload_json, str) or not payload_json:
        raise DecisionReplayTargetError("effective_parameters_json 必须是非空 canonical JSON")

    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise DecisionReplayTargetError("effective_parameters_json 不能包含重复键")
            result[key] = value
        return result

    try:
        decoded = json.loads(payload_json, object_pairs_hook=no_duplicates)
    except json.JSONDecodeError as exc:
        raise DecisionReplayTargetError("effective_parameters_json 必须是有效 JSON") from exc
    if not isinstance(decoded, dict) or not decoded:
        raise DecisionReplayTargetError("effective_parameters_json 必须是非空对象")
    if len(decoded) > 32:
        raise DecisionReplayTargetError("effective_parameters_json 的字段数过多")

    normalized: dict[str, object] = {}
    for raw_key, raw_value in decoded.items():
        key = _field(raw_key, "effective_parameters_json.key")
        if key in normalized:
            raise DecisionReplayTargetError("effective_parameters_json 不能包含重复键")
        if _SECRET_TEXT_RE.search(key):
            raise DecisionReplayTargetError("effective_parameters_json 不得包含凭据字段")
        if isinstance(raw_value, bool) or not isinstance(raw_value, (str, int, float, type(None))):
            raise DecisionReplayTargetError("effective_parameters_json 只能包含有限标量")
        if isinstance(raw_value, float) and not math.isfinite(raw_value):
            raise DecisionReplayTargetError("effective_parameters_json 不得包含 NaN 或无穷数")
        if isinstance(raw_value, str):
            if (
                not raw_value
                or raw_value.startswith("~")
                or "/" in raw_value
                or "\\" in raw_value
            ):
                raise DecisionReplayTargetError("effective_parameters_json 不得包含路径或空文本")
            if _SECRET_TEXT_RE.search(raw_value):
                raise DecisionReplayTargetError("effective_parameters_json 不得包含凭据文本")
            if len(raw_value) > 256:
                raise DecisionReplayTargetError("effective_parameters_json 文本值过长")
        normalized[key] = raw_value
    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if canonical != payload_json:
        raise DecisionReplayTargetError("effective_parameters_json 必须是 canonical JSON")
    return canonical, canonical_json_sha256({"parameters": normalized})


@dataclass(frozen=True, slots=True)
class DecisionReplayStrategyIdentity:
    """由受控 Application resolver 得到的单一策略构建身份。

    摘要是完整性/审计标识，不是 Python 运行时的签名或沙箱证明。当前 target replay 根只
    允许一个内建 target-weight 策略；不得用 Experiment 的声明性 StrategyVersionReference
    替代本对象。
    """

    strategy_id: str
    output_type: str
    time_column: str
    effective_parameters_json: str
    profile_strategy_config_hash: str
    implementation_hash: str
    code_reference_hash: str
    effective_parameters_hash: str = field(init=False)
    identity_hash: str = field(init=False)

    def __post_init__(self) -> None:
        strategy_id = _text(self.strategy_id, "strategy_id")
        output_type = _text(self.output_type, "output_type")
        if output_type != "target_weight":
            raise DecisionReplayTargetError("逐决策 target 轨迹当前只允许 target_weight 策略")
        time_column = _field(self.time_column, "time_column")
        parameters_json, parameters_hash = _canonical_parameter_mapping(
            self.effective_parameters_json
        )
        profile_strategy_config_hash = _hash(
            self.profile_strategy_config_hash,
            "profile_strategy_config_hash",
        )
        implementation_hash = _hash(self.implementation_hash, "implementation_hash")
        code_reference_hash = _hash(self.code_reference_hash, "code_reference_hash")
        identity_hash = canonical_json_sha256(
            {
                "code_reference_hash": code_reference_hash,
                "effective_parameters_hash": parameters_hash,
                "format": "northstar.decision-replay-strategy-identity.v1",
                "implementation_hash": implementation_hash,
                "output_type": output_type,
                "profile_strategy_config_hash": profile_strategy_config_hash,
                "strategy_id": strategy_id,
                "time_column": time_column,
            }
        )
        object.__setattr__(self, "strategy_id", strategy_id)
        object.__setattr__(self, "output_type", output_type)
        object.__setattr__(self, "time_column", time_column)
        object.__setattr__(self, "effective_parameters_json", parameters_json)
        object.__setattr__(self, "profile_strategy_config_hash", profile_strategy_config_hash)
        object.__setattr__(self, "implementation_hash", implementation_hash)
        object.__setattr__(self, "code_reference_hash", code_reference_hash)
        object.__setattr__(self, "effective_parameters_hash", parameters_hash)
        object.__setattr__(self, "identity_hash", identity_hash)

    def as_mapping(self) -> dict[str, object]:
        return {
            "code_reference_hash": self.code_reference_hash,
            "effective_parameters_hash": self.effective_parameters_hash,
            "identity_hash": self.identity_hash,
            "implementation_hash": self.implementation_hash,
            "output_type": self.output_type,
            "profile_strategy_config_hash": self.profile_strategy_config_hash,
            "strategy_id": self.strategy_id,
            "time_column": self.time_column,
        }


@dataclass(frozen=True, slots=True)
class DecisionTarget:
    """一个 checkpoint 当前时点的单标的 target-weight 输出。"""

    symbol: str
    signal_value: float
    target_weight: float

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or _SYMBOL_RE.fullmatch(self.symbol.strip().upper()) is None:
            raise DecisionReplayTargetError("target.symbol 必须是规范大写研究标的")
        symbol = self.symbol.strip().upper()
        signal_value = _finite_number(self.signal_value, "target.signal_value")
        target_weight = _finite_number(self.target_weight, "target.target_weight")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "signal_value", signal_value)
        object.__setattr__(self, "target_weight", target_weight)

    def as_mapping(self) -> dict[str, object]:
        return {
            "signal_value": self.signal_value,
            "symbol": self.symbol,
            "target_weight": self.target_weight,
        }


def _empty_target_hash(*, time_column: str, decision_event_time: date | datetime) -> str:
    return canonical_json_sha256(
        {
            "columns": [time_column, "symbol", "signal_value", "target_weight"],
            "decision_event_time": _event_mapping(decision_event_time),
            "format": "northstar.decision-replay-empty-target-frame.v1",
            "rows": [],
        }
    )


def _target_frame(
    *,
    time_column: str,
    decision_event_time: date | datetime,
    targets: tuple[DecisionTarget, ...],
) -> pl.DataFrame:
    if isinstance(decision_event_time, datetime):
        # Polars stubs 将 Date / Datetime 视为不同的 dtype class；这里只在重建边界
        # 使用 Any，不把该实现细节泄漏到公开的 frozen trace 合同。
        time_dtype: Any = pl.Datetime("us", "UTC")
    else:
        time_dtype = pl.Date
    return pl.DataFrame(
        {
            time_column: [decision_event_time for _ in targets],
            "symbol": [item.symbol for item in targets],
            "signal_value": [item.signal_value for item in targets],
            "target_weight": [item.target_weight for item in targets],
        },
        schema={
            time_column: time_dtype,
            "symbol": pl.String,
            "signal_value": pl.Float64,
            "target_weight": pl.Float64,
        },
        strict=True,
    )


@dataclass(frozen=True, slots=True)
class DecisionTargetSlice:
    """一个 checkpoint 的市场快照与当前 target slice 的精确绑定。"""

    checkpoint_hash: str
    decision_at: datetime
    decision_event_time: date | datetime
    market_snapshot_id: str
    market_selected_frame_hash: str
    market_revision_ids_hash: str
    source_artifact_snapshot_hash: str
    strategy_identity_hash: str
    time_column: str
    target_status: DecisionTargetStatus
    targets: tuple[DecisionTarget, ...]
    target_row_count: int = field(init=False)
    target_frame_sha256: str = field(init=False)
    slice_hash: str = field(init=False)

    def __post_init__(self) -> None:
        checkpoint_hash = _hash(self.checkpoint_hash, "checkpoint_hash")
        decision_at = _utc_datetime(self.decision_at, "decision_at")
        decision_event_time = _event_time(self.decision_event_time, "decision_event_time")
        market_snapshot_id = _hash(self.market_snapshot_id, "market_snapshot_id")
        market_selected_frame_hash = _hash(
            self.market_selected_frame_hash,
            "market_selected_frame_hash",
        )
        market_revision_ids_hash = _hash(
            self.market_revision_ids_hash,
            "market_revision_ids_hash",
        )
        source_artifact_snapshot_hash = _hash(
            self.source_artifact_snapshot_hash,
            "source_artifact_snapshot_hash",
        )
        strategy_identity_hash = _hash(self.strategy_identity_hash, "strategy_identity_hash")
        time_column = _field(self.time_column, "time_column")
        if not isinstance(self.target_status, DecisionTargetStatus):
            raise DecisionReplayTargetError("target_status 必须是 DecisionTargetStatus")
        if not isinstance(self.targets, tuple) or not all(
            isinstance(item, DecisionTarget) for item in self.targets
        ):
            raise DecisionReplayTargetError("targets 必须是 DecisionTarget 元组")
        targets = tuple(sorted(self.targets, key=lambda item: item.symbol))
        if targets != self.targets:
            raise DecisionReplayTargetError("targets 必须按 symbol 升序排列")
        if len({item.symbol for item in targets}) != len(targets):
            raise DecisionReplayTargetError("targets 不能包含重复 symbol")
        if self.target_status is DecisionTargetStatus.TARGETS and not targets:
            raise DecisionReplayTargetError("TARGETS 状态必须至少包含一条 target")
        if self.target_status is DecisionTargetStatus.NO_TARGET_WARMUP and targets:
            raise DecisionReplayTargetError("NO_TARGET_WARMUP 状态不得包含 target")
        if targets:
            try:
                target_frame_sha256 = TargetFrameReference.from_frame(
                    _target_frame(
                        time_column=time_column,
                        decision_event_time=decision_event_time,
                        targets=targets,
                    ),
                    time_column=time_column,
                ).target_frame_sha256
            except BacktestContractError as exc:
                raise DecisionReplayTargetError("target slice 无法构造规范 target frame") from exc
        else:
            target_frame_sha256 = _empty_target_hash(
                time_column=time_column,
                decision_event_time=decision_event_time,
            )
        slice_hash = canonical_json_sha256(
            {
                "checkpoint_hash": checkpoint_hash,
                "decision_at": decision_at.isoformat(),
                "decision_event_time": _event_mapping(decision_event_time),
                "format": "northstar.decision-target-slice.v1",
                "market_revision_ids_hash": market_revision_ids_hash,
                "market_selected_frame_hash": market_selected_frame_hash,
                "market_snapshot_id": market_snapshot_id,
                "source_artifact_snapshot_hash": source_artifact_snapshot_hash,
                "strategy_identity_hash": strategy_identity_hash,
                "target_frame_sha256": target_frame_sha256,
                "target_status": self.target_status.value,
                "time_column": time_column,
            }
        )
        object.__setattr__(self, "checkpoint_hash", checkpoint_hash)
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "decision_event_time", decision_event_time)
        object.__setattr__(self, "market_snapshot_id", market_snapshot_id)
        object.__setattr__(self, "market_selected_frame_hash", market_selected_frame_hash)
        object.__setattr__(self, "market_revision_ids_hash", market_revision_ids_hash)
        object.__setattr__(self, "source_artifact_snapshot_hash", source_artifact_snapshot_hash)
        object.__setattr__(self, "strategy_identity_hash", strategy_identity_hash)
        object.__setattr__(self, "time_column", time_column)
        object.__setattr__(self, "targets", targets)
        object.__setattr__(self, "target_row_count", len(targets))
        object.__setattr__(self, "target_frame_sha256", target_frame_sha256)
        object.__setattr__(self, "slice_hash", slice_hash)

    def targets_frame(self) -> pl.DataFrame:
        """返回新的 target DataFrame，并重新核对其冻结内容摘要。"""

        frame = _target_frame(
            time_column=self.time_column,
            decision_event_time=self.decision_event_time,
            targets=self.targets,
        )
        if self.targets:
            try:
                reference = TargetFrameReference.from_frame(frame, time_column=self.time_column)
            except BacktestContractError as exc:  # pragma: no cover - 构造期已覆盖的防御分支。
                raise DecisionReplayTargetError("target slice 完整性校验失败") from exc
            if reference.target_frame_sha256 != self.target_frame_sha256:
                raise DecisionReplayTargetError("target slice 完整性校验失败")
        elif self.target_frame_sha256 != _empty_target_hash(
            time_column=self.time_column,
            decision_event_time=self.decision_event_time,
        ):
            raise DecisionReplayTargetError("空 target slice 完整性校验失败")
        return frame

    def as_mapping(self) -> dict[str, object]:
        """返回可归档的 hash-only slice 证据，不写出逐标的 target 数值。"""

        return {
            "checkpoint_hash": self.checkpoint_hash,
            "decision_at": self.decision_at.isoformat(),
            "decision_event_time": _event_mapping(self.decision_event_time),
            "market_revision_ids_hash": self.market_revision_ids_hash,
            "market_selected_frame_hash": self.market_selected_frame_hash,
            "market_snapshot_id": self.market_snapshot_id,
            "slice_hash": self.slice_hash,
            "source_artifact_snapshot_hash": self.source_artifact_snapshot_hash,
            "strategy_identity_hash": self.strategy_identity_hash,
            "target_frame_sha256": self.target_frame_sha256,
            "target_row_count": self.target_row_count,
            "target_status": self.target_status.value,
            "time_column": self.time_column,
        }


@dataclass(frozen=True, slots=True)
class DecisionReplayTargetTrace:
    """单策略逐 checkpoint target replay 的不可变、不可准入轨迹。"""

    plan: DecisionReplayPlan
    profile_id: str
    profile_config_sha256: str
    profile_dimension_key: str
    selected_strategy_ids: tuple[str, ...]
    strategy_identity: DecisionReplayStrategyIdentity
    target_slices: tuple[DecisionTargetSlice, ...]
    aggregate_target: TargetFrameReference
    market_replay_hash: str = field(init=False)
    trace_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.plan, DecisionReplayPlan):
            raise DecisionReplayTargetError("plan 必须是 DecisionReplayPlan")
        profile_id = _text(self.profile_id, "profile_id")
        profile_config_sha256 = _hash(self.profile_config_sha256, "profile_config_sha256")
        profile_dimension_key = _text(self.profile_dimension_key, "profile_dimension_key")
        if not isinstance(self.selected_strategy_ids, tuple) or self.selected_strategy_ids != (
            "futures_trend",
        ):
            raise DecisionReplayTargetError(
                "当前逐决策 target replay 只允许唯一内建策略 futures_trend"
            )
        if not isinstance(self.strategy_identity, DecisionReplayStrategyIdentity):
            raise DecisionReplayTargetError("strategy_identity 必须是 DecisionReplayStrategyIdentity")
        if self.strategy_identity.strategy_id != "futures_trend":
            raise DecisionReplayTargetError("strategy_identity 必须绑定 futures_trend")
        if not isinstance(self.target_slices, tuple) or not all(
            isinstance(item, DecisionTargetSlice) for item in self.target_slices
        ):
            raise DecisionReplayTargetError("target_slices 必须是 DecisionTargetSlice 元组")
        if len(self.target_slices) != len(self.plan.checkpoints):
            raise DecisionReplayTargetError("target_slices 必须与 plan.checkpoints 一一对应")
        checkpoint_hashes = tuple(item.checkpoint_hash for item in self.plan.checkpoints)
        slice_checkpoint_hashes = tuple(item.checkpoint_hash for item in self.target_slices)
        if checkpoint_hashes != slice_checkpoint_hashes:
            raise DecisionReplayTargetError("target_slices 的 checkpoint 顺序必须与 plan 精确一致")
        if any(
            item.strategy_identity_hash != self.strategy_identity.identity_hash
            for item in self.target_slices
        ):
            raise DecisionReplayTargetError("target_slices 必须绑定同一个受控策略身份")
        for checkpoint, target_slice in zip(self.plan.checkpoints, self.target_slices, strict=True):
            if target_slice.decision_at != checkpoint.decision_at:
                raise DecisionReplayTargetError("target slice.decision_at 与 checkpoint 不一致")
            if target_slice.decision_event_time != checkpoint.decision_event_time:
                raise DecisionReplayTargetError("target slice.decision_event_time 与 checkpoint 不一致")
        time_column = self.strategy_identity.time_column
        if any(item.time_column != time_column for item in self.target_slices):
            raise DecisionReplayTargetError("target_slices 的 time_column 必须与策略身份一致")
        event_types = {type(item.decision_event_time) for item in self.target_slices}
        if len(event_types) != 1:
            raise DecisionReplayTargetError("当前 target trace 不支持混合 date/datetime event time")
        event_times = tuple(item.decision_event_time for item in self.target_slices)
        if tuple(sorted(event_times)) != event_times or len(set(event_times)) != len(event_times):
            raise DecisionReplayTargetError("target trace 的 decision_event_time 必须严格升序且无重复")
        target_frames = [item.targets_frame() for item in self.target_slices if item.targets]
        if not target_frames:
            raise DecisionReplayTargetError("target trace 全部处于 warmup，不能形成可审计 target 轨迹")
        aggregate_frame = pl.concat(target_frames, how="vertical").sort([time_column, "symbol"])
        try:
            computed_aggregate = TargetFrameReference.from_frame(
                aggregate_frame,
                time_column=time_column,
            )
        except BacktestContractError as exc:
            raise DecisionReplayTargetError("target trace aggregate target 无法安全构造") from exc
        if not isinstance(self.aggregate_target, TargetFrameReference):
            raise DecisionReplayTargetError("aggregate_target 必须是 TargetFrameReference")
        if computed_aggregate != self.aggregate_target:
            raise DecisionReplayTargetError("aggregate_target 必须精确匹配每个 target slice")
        market_replay_hash = canonical_json_sha256(
            {
                "format": "northstar.decision-target-market-replay.v1",
                "slices": [
                    {
                        "checkpoint_hash": item.checkpoint_hash,
                        "market_revision_ids_hash": item.market_revision_ids_hash,
                        "market_selected_frame_hash": item.market_selected_frame_hash,
                        "market_snapshot_id": item.market_snapshot_id,
                        "source_artifact_snapshot_hash": item.source_artifact_snapshot_hash,
                    }
                    for item in self.target_slices
                ],
            }
        )
        trace_hash = canonical_json_sha256(
            {
                "aggregate_target_sha256": computed_aggregate.target_frame_sha256,
                "format": "northstar.decision-replay-target-trace.v1",
                "market_replay_hash": market_replay_hash,
                "plan_schedule_hash": self.plan.schedule_hash,
                "profile_config_sha256": profile_config_sha256,
                "profile_dimension_key": profile_dimension_key,
                "profile_id": profile_id,
                "selected_strategy_ids": list(self.selected_strategy_ids),
                "strategy_identity_hash": self.strategy_identity.identity_hash,
                "target_slice_hashes": [item.slice_hash for item in self.target_slices],
            }
        )
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "profile_config_sha256", profile_config_sha256)
        object.__setattr__(self, "profile_dimension_key", profile_dimension_key)
        object.__setattr__(self, "market_replay_hash", market_replay_hash)
        object.__setattr__(self, "trace_hash", trace_hash)

    def targets_frame(self) -> pl.DataFrame:
        """从不可变 target values 重建并校验聚合 target frame。"""

        target_frames = [item.targets_frame() for item in self.target_slices if item.targets]
        if not target_frames:  # pragma: no cover - 构造期已拒绝。
            raise DecisionReplayTargetError("target trace 缺少 target rows")
        frame = pl.concat(target_frames, how="vertical").sort(
            [self.aggregate_target.time_column, "symbol"]
        )
        try:
            reference = TargetFrameReference.from_frame(
                frame,
                time_column=self.aggregate_target.time_column,
            )
        except BacktestContractError as exc:  # pragma: no cover - 构造期已覆盖。
            raise DecisionReplayTargetError("target trace aggregate 完整性校验失败") from exc
        if reference != self.aggregate_target:
            raise DecisionReplayTargetError("target trace aggregate 完整性校验失败")
        return frame

    def lookahead_evidence(
        self,
        market_data: tuple[DecisionMarketDataEvidence, ...],
    ) -> tuple[DecisionReplayEvidence, ...]:
        """Bind this immutable target trace to the exact replayed market snapshots.

        This helper deliberately supplies no feature, event, contract, or execution-rule
        evidence: the currently supported continuous ``weight_return`` target producer does
        not consume those inputs.  It is therefore only suitable for the current
        evidence-consistency receipt, which remains non-admissible.  A future actual-contract
        composition root must use controlled producers for each of those categories instead of
        extending this method.
        """

        if not isinstance(market_data, tuple) or not all(
            isinstance(item, DecisionMarketDataEvidence) for item in market_data
        ):
            raise DecisionReplayTargetError(
                "market_data 必须是 DecisionMarketDataEvidence 元组"
            )
        if len(market_data) != len(self.target_slices):
            raise DecisionReplayTargetError("market_data 必须与 target_slices 一一对应")

        evidence_items: list[DecisionReplayEvidence] = []
        for target_slice, market_evidence in zip(
            self.target_slices,
            market_data,
            strict=True,
        ):
            snapshot = market_evidence.market_snapshot
            if market_evidence.checkpoint.checkpoint_hash != target_slice.checkpoint_hash:
                raise DecisionReplayTargetError(
                    "market_data checkpoint 必须与 target slice 精确一致"
                )
            revision_ids_hash = canonical_json_sha256(
                {"revision_ids": list(snapshot.revision_ids)}
            )
            if (
                target_slice.decision_at != market_evidence.decision_at
                or target_slice.market_snapshot_id != snapshot.snapshot_id
                or target_slice.market_selected_frame_hash != snapshot.selected_frame_hash
                or target_slice.market_revision_ids_hash != revision_ids_hash
                or target_slice.source_artifact_snapshot_hash
                != snapshot.source_artifact_snapshot_hash
            ):
                raise DecisionReplayTargetError(
                    "target slice 必须绑定同一 checkpoint 的完整 replay 市场快照"
                )
            evidence_items.append(
                DecisionReplayEvidence(
                    market_data=market_evidence,
                    target=TargetDecisionEvidence(
                        decision_at=target_slice.decision_at,
                        available_at=target_slice.decision_at,
                        source_snapshot_hash=snapshot.snapshot_id,
                        target_hash=target_slice.target_frame_sha256,
                    ),
                    input_usage=tuple(
                        LookaheadInputUsageDeclaration(
                            input_kind=input_kind,
                            usage=LookaheadInputUsage.NOT_USED,
                            producer_identity_hash=self.strategy_identity.identity_hash,
                        )
                        for input_kind in LookaheadInputKind
                    ),
                )
            )
        return tuple(evidence_items)

    def as_mapping(self) -> dict[str, object]:
        """返回无 target 数值、不可作为准入结论的轨迹清单。"""

        return {
            "aggregate_target": self.aggregate_target.as_mapping(),
            "candidate_admission_eligible": False,
            "decision_time_safe": False,
            "format": "northstar.decision-replay-target-trace.v1",
            "market_replay_hash": self.market_replay_hash,
            "plan": self.plan.as_mapping(),
            "profile_config_sha256": self.profile_config_sha256,
            "profile_dimension_key": self.profile_dimension_key,
            "profile_id": self.profile_id,
            "selected_strategy_ids": list(self.selected_strategy_ids),
            "strategy_identity": self.strategy_identity.as_mapping(),
            "target_slices": [item.as_mapping() for item in self.target_slices],
            "trace_hash": self.trace_hash,
        }


__all__ = [
    "DecisionReplayStrategyIdentity",
    "DecisionReplayTargetError",
    "DecisionReplayTargetTrace",
    "DecisionTarget",
    "DecisionTargetSlice",
    "DecisionTargetStatus",
]

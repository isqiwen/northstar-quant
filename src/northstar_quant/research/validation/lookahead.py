"""逐决策时点的前视偏差防线。

P1 的 :class:`~northstar_quant.data.market.pit.MarketDataSnapshot` 是一份
可复现的静态 as-of 视图。它本身并不表示历史模拟的每一个决策时点都经过了重放。
本模块在 Research 边界建立另一层严格合同：每一个决策时点都必须显式绑定当时重放的
市场快照、特征、合约/费率规则、事件和目标证据；任何一项在该时点之后才可用，都会
产生确定性的违规并拒绝签发证书。

这里不读取当前时钟、网络、文件或数据库，也不会把既有 static PIT、FeatureBackfill
或普通回测升级为安全路径。未来的 Application 编排必须逐 checkpoint 调用
``DecisionReplayPlan.replay_market_data``，再把同一时点的证据交给 Guard。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import InitVar, dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
import re

from northstar_quant.data.artifacts.fingerprints import (
    FingerprintError,
    canonical_json_sha256,
    require_sha256,
)
from northstar_quant.data.artifacts.immutable_store import (
    ArtifactStore,
    ArtifactStoreError,
)
from northstar_quant.data.contracts.contract_master import (
    Contract,
    ContractRuleSnapshot,
    DeliveryRestriction,
    ListingState,
    RuleQualityStatus,
)
from northstar_quant.data.contracts.artifact_rulebook import (
    ArtifactBackedContractRuleReplay,
    ArtifactRuleBookError,
    ContractRuleBookPITSelector,
)
from northstar_quant.data.market.pit import (
    MarketDataKind,
    MarketDataPITError,
    MarketDataPITSelector,
    MarketDataPITSpec,
    MarketDataSnapshot,
)
from northstar_quant.research.features.models import (
    DecisionReplayFeatureMaterialization,
    FeatureBackfill,
    FeatureLineage,
    FeatureValue,
)


_PER_DECISION_SELECTION_MODE = "PER_DECISION_POINT_IN_TIME_REPLAY"
_EVENT_REPLAY_DATASET_ID = "research_event_fact"
_EVENT_REPLAY_SCHEMA_VERSION = "research_event_fact_v1"
_EVENT_REPLAY_SPEC = MarketDataPITSpec(
    kind=MarketDataKind.SNAPSHOT,
    key_columns=("event_id", "event_at"),
    event_time_column="event_at",
    available_at_column="available_at",
    value_columns=("source_evidence_hash",),
    schema_version=_EVENT_REPLAY_SCHEMA_VERSION,
)
_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_CERTIFICATE_ISSUER = object()


class LookaheadGuardError(ValueError):
    """逐决策证据不完整、不可验证或包含前视信息。"""


class LookaheadViolationKind(str, Enum):
    """P2-WP05 必须逐项拒绝的前视偏差类别。"""

    FUTURE_FEATURE = "future_feature"
    REVISED_HISTORICAL_DATA = "revised_historical_data"
    FUTURE_CONTRACT_KNOWLEDGE = "future_contract_knowledge"
    FUTURE_FEE_MARGIN_RULE = "future_fee_margin_rule"
    FUTURE_EVENT = "future_event"
    FUTURE_TARGET = "future_target"


class LookaheadInputKind(str, Enum):
    """Non-market inputs whose absence must be explicit in a signed receipt."""

    FEATURE = "feature"
    EVENT = "event"
    CONTRACT = "contract"
    FEE_MARGIN_RULE = "fee_margin_rule"


class LookaheadInputUsage(str, Enum):
    """Whether the controlled producer consumed one input category at a checkpoint."""

    PROVIDED = "provided"
    NOT_USED = "not_used"


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _TEXT_RE.fullmatch(value.strip()) is None:
        raise LookaheadGuardError(f"{field_name} 必须是无路径的规范标识")
    return value.strip()


def _hash(value: object, field_name: str) -> str:
    try:
        return require_sha256(value, field_name=field_name)  # type: ignore[arg-type]
    except FingerprintError as exc:
        raise LookaheadGuardError(str(exc)) from exc


def _utc_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise LookaheadGuardError(f"{field_name} 必须是带时区的 datetime")
    return value.astimezone(UTC)


def _event_not_after(event_time: date | datetime, decision_at: datetime) -> bool:
    if isinstance(event_time, datetime):
        if event_time.tzinfo is None or event_time.utcoffset() is None:
            return False
        return event_time.astimezone(UTC) <= decision_at
    if isinstance(event_time, date):
        return event_time <= decision_at.date()
    return False


def _decision_event_time(value: object, field_name: str) -> date | datetime:
    """校验计划显式指定的行情事件时点。

    ``date`` 与带时区 ``datetime`` 的语义不同，不能仅以 ISO 文本比较；日期型日线
    checkpoint 也不能被隐式提升为午夜 UTC 的 datetime。
    """

    if isinstance(value, datetime):
        return _utc_datetime(value, field_name)
    if isinstance(value, date):
        return value
    raise LookaheadGuardError(f"{field_name} 必须是 date 或带时区的 datetime")


def _decision_event_time_mapping(value: date | datetime) -> dict[str, str]:
    """保留 date/datetime 区别的稳定 checkpoint 身份片段。"""

    if isinstance(value, datetime):
        return {"kind": "datetime", "value": value.astimezone(UTC).isoformat()}
    return {"kind": "date", "value": value.isoformat()}


@dataclass(frozen=True, slots=True)
class DecisionReplayCheckpoint:
    """一个必须独立重放的决策时点和其 immutable 数据版本。"""

    decision_at: datetime
    decision_event_time: date | datetime
    dataset_version_hash: str
    pit_spec: MarketDataPITSpec
    checkpoint_hash: str = field(init=False)

    def __post_init__(self) -> None:
        decision_at = _utc_datetime(self.decision_at, "checkpoint.decision_at")
        decision_event_time = _decision_event_time(
            self.decision_event_time,
            "checkpoint.decision_event_time",
        )
        dataset_version_hash = _hash(self.dataset_version_hash, "checkpoint.dataset_version_hash")
        if not isinstance(self.pit_spec, MarketDataPITSpec):
            raise LookaheadGuardError("checkpoint.pit_spec 必须是 MarketDataPITSpec")
        checkpoint_hash = canonical_json_sha256(
            {
                "dataset_version_hash": dataset_version_hash,
                "decision_at": decision_at.isoformat(),
                "decision_event_time": _decision_event_time_mapping(decision_event_time),
                "format": "northstar.decision-replay-checkpoint.v1",
                "pit_spec_hash": self.pit_spec.spec_hash,
            }
        )
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "decision_event_time", decision_event_time)
        object.__setattr__(self, "dataset_version_hash", dataset_version_hash)
        object.__setattr__(self, "checkpoint_hash", checkpoint_hash)

    def as_mapping(self) -> dict[str, object]:
        return {
            "checkpoint_hash": self.checkpoint_hash,
            "dataset_version_hash": self.dataset_version_hash,
            "decision_at": self.decision_at.isoformat(),
            "decision_event_time": _decision_event_time_mapping(self.decision_event_time),
            "pit_spec_hash": self.pit_spec.spec_hash,
        }


@dataclass(frozen=True, slots=True)
class DecisionReplayPlan:
    """显式、排序且无重复的逐决策重放计划。

    计划不得从最终 DatasetVersion 或当前时钟推断。每个 checkpoint 可以引用不同版本，
    因而历史修订必须明确地进入后续计划，而不能倒灌先前决策。
    """

    checkpoints: tuple[DecisionReplayCheckpoint, ...]
    schedule_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoints, tuple) or not self.checkpoints:
            raise LookaheadGuardError("checkpoints 必须是非空 DecisionReplayCheckpoint 元组")
        if not all(isinstance(item, DecisionReplayCheckpoint) for item in self.checkpoints):
            raise LookaheadGuardError("checkpoints 包含非 DecisionReplayCheckpoint 项")
        if tuple(sorted(self.checkpoints, key=lambda item: item.decision_at)) != self.checkpoints:
            raise LookaheadGuardError("checkpoints 必须按 decision_at 严格升序排列")
        if len({item.decision_at for item in self.checkpoints}) != len(self.checkpoints):
            raise LookaheadGuardError("checkpoints 不能包含重复 decision_at")
        schedule_hash = canonical_json_sha256(
            {
                "checkpoint_hashes": [item.checkpoint_hash for item in self.checkpoints],
                "format": "northstar.decision-replay-plan.v1",
            }
        )
        object.__setattr__(self, "schedule_hash", schedule_hash)

    @classmethod
    def create(cls, checkpoints: Iterable[DecisionReplayCheckpoint]) -> "DecisionReplayPlan":
        """从显式 iterable 构造计划；调用方必须自行提供排序后的完整时间表。"""

        return cls(checkpoints=tuple(checkpoints))

    def replay_market_data(
        self,
        artifact_store: ArtifactStore,
    ) -> tuple["DecisionMarketDataEvidence", ...]:
        """逐个 checkpoint 重新选择 immutable 数据，不接受调用方注入的 selector。"""

        if type(artifact_store) is not ArtifactStore:
            raise LookaheadGuardError("artifact_store 必须是精确的 ArtifactStore，不能使用子类")
        selector = MarketDataPITSelector(artifact_store)
        return tuple(
            DecisionMarketDataEvidence(
                checkpoint=checkpoint,
                market_snapshot=MarketDataPITSelector.select(
                    selector,
                    dataset_version_hash=checkpoint.dataset_version_hash,
                    spec=checkpoint.pit_spec,
                    as_of=checkpoint.decision_at,
                ),
            )
            for checkpoint in self.checkpoints
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "checkpoints": [item.as_mapping() for item in self.checkpoints],
            "schedule_hash": self.schedule_hash,
        }


@dataclass(frozen=True, slots=True)
class DecisionMarketDataEvidence:
    """一个 checkpoint 的重新选择结果；不把 static snapshot 伪称为全程安全。"""

    checkpoint: DecisionReplayCheckpoint
    market_snapshot: MarketDataSnapshot
    evidence_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.checkpoint, DecisionReplayCheckpoint):
            raise LookaheadGuardError("market.checkpoint 必须是 DecisionReplayCheckpoint")
        if not isinstance(self.market_snapshot, MarketDataSnapshot):
            raise LookaheadGuardError("market.market_snapshot 必须是 MarketDataSnapshot")
        snapshot = self.market_snapshot
        checkpoint = self.checkpoint
        if snapshot.dataset_version_hash != checkpoint.dataset_version_hash:
            raise LookaheadGuardError("market snapshot 与 checkpoint.dataset_version_hash 不一致")
        if snapshot.spec.spec_hash != checkpoint.pit_spec.spec_hash:
            raise LookaheadGuardError("market snapshot 与 checkpoint.pit_spec 不一致")
        # 时间关系刻意由 ``LookaheadGuard.evaluate`` 形成可审计的
        # ``REVISED_HISTORICAL_DATA`` 违规，而不是在构造值对象时悄悄丢失诊断。
        # ``DecisionReplayPlan.replay_market_data`` 正常路径始终传入相同的 as_of。
        evidence_hash = canonical_json_sha256(
            {
                "checkpoint_hash": checkpoint.checkpoint_hash,
                "format": "northstar.decision-market-evidence.v1",
                "revision_ids": list(snapshot.revision_ids),
                "selected_frame_hash": snapshot.selected_frame_hash,
                "snapshot_id": snapshot.snapshot_id,
                "source_artifact_snapshot_hash": snapshot.source_artifact_snapshot_hash,
            }
        )
        object.__setattr__(self, "evidence_hash", evidence_hash)

    @property
    def decision_at(self) -> datetime:
        return self.checkpoint.decision_at

    def as_mapping(self) -> dict[str, object]:
        snapshot = self.market_snapshot
        return {
            "checkpoint_hash": self.checkpoint.checkpoint_hash,
            "dataset_version_hash": snapshot.dataset_version_hash,
            "evidence_hash": self.evidence_hash,
            "revision_ids": list(snapshot.revision_ids),
            "selected_frame_hash": snapshot.selected_frame_hash,
            "snapshot_id": snapshot.snapshot_id,
            "source_artifact_available_at": snapshot.source_artifact_available_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class FeatureAvailabilityEvidence:
    """严格特征路径的一次逐决策输出摘要。

    现有 ``FeatureLineage`` / ``FeatureBackfill`` 固定为 static 模式，不能填入本对象。
    后续 strict Feature Registry 必须用同一 checkpoint 产生该证据；本对象只保存值身份，
    不把原始 feature 数值写入证书或清单。
    """

    feature_version_hash: str
    lineage_hash: str
    input_snapshot_hash: str
    decision_at: datetime
    available_at: datetime
    values: tuple[FeatureValue, ...]
    replay_materialization: DecisionReplayFeatureMaterialization | None = None
    evidence_hash: str = field(init=False)

    def __post_init__(self) -> None:
        feature_version_hash = _hash(self.feature_version_hash, "feature.feature_version_hash")
        lineage_hash = _hash(self.lineage_hash, "feature.lineage_hash")
        input_snapshot_hash = _hash(self.input_snapshot_hash, "feature.input_snapshot_hash")
        decision_at = _utc_datetime(self.decision_at, "feature.decision_at")
        available_at = _utc_datetime(self.available_at, "feature.available_at")
        if not isinstance(self.values, tuple) or not self.values:
            raise LookaheadGuardError("feature.values 必须是非空 FeatureValue 元组")
        if not all(isinstance(item, FeatureValue) for item in self.values):
            raise LookaheadGuardError("feature.values 包含非 FeatureValue 项")
        if any(item.feature_version_hash != feature_version_hash for item in self.values):
            raise LookaheadGuardError("feature values 必须来自同一 FeatureVersion")
        if any(item.lineage_hash != lineage_hash for item in self.values):
            raise LookaheadGuardError("feature values 必须来自同一严格 lineage")
        if any(item.available_at > available_at for item in self.values):
            raise LookaheadGuardError("feature.available_at 不能早于其中 FeatureValue")
        if self.replay_materialization is not None:
            materialization = self.replay_materialization
            if not isinstance(materialization, DecisionReplayFeatureMaterialization):
                raise LookaheadGuardError(
                    "feature.replay_materialization 必须是 DecisionReplayFeatureMaterialization"
                )
            if materialization.lineage.feature_version_hash != feature_version_hash:
                raise LookaheadGuardError("feature replay materialization FeatureVersion 不一致")
            if materialization.lineage.lineage_hash != lineage_hash:
                raise LookaheadGuardError("feature replay materialization lineage 不一致")
            if materialization.input_snapshot_hash != input_snapshot_hash:
                raise LookaheadGuardError("feature replay materialization input snapshot 不一致")
            if materialization.lineage.decision_at != decision_at:
                raise LookaheadGuardError("feature replay materialization decision_at 不一致")
            if materialization.lineage.available_at != available_at:
                raise LookaheadGuardError("feature replay materialization available_at 不一致")
            if materialization.values != tuple(sorted(self.values, key=lambda item: item.value_id)):
                raise LookaheadGuardError("feature replay materialization values 不一致")
        evidence_hash = canonical_json_sha256(
            {
                "available_at": available_at.isoformat(),
                "decision_at": decision_at.isoformat(),
                "feature_version_hash": feature_version_hash,
                "format": "northstar.feature-availability-evidence.v1",
                "input_snapshot_hash": input_snapshot_hash,
                "lineage_hash": lineage_hash,
                "replay_materialization_hash": (
                    self.replay_materialization.materialization_hash
                    if self.replay_materialization is not None
                    else None
                ),
                "value_ids": sorted(item.value_id for item in self.values),
            }
        )
        object.__setattr__(self, "feature_version_hash", feature_version_hash)
        object.__setattr__(self, "lineage_hash", lineage_hash)
        object.__setattr__(self, "input_snapshot_hash", input_snapshot_hash)
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "values", tuple(sorted(self.values, key=lambda item: item.value_id)))
        object.__setattr__(self, "evidence_hash", evidence_hash)

    @classmethod
    def from_replay_materialization(
        cls,
        materialization: DecisionReplayFeatureMaterialization,
    ) -> "FeatureAvailabilityEvidence":
        """将 Registry 签发的逐决策结果转换为 Guard 证据，拒绝裸 FeatureValue。"""

        if not isinstance(materialization, DecisionReplayFeatureMaterialization):
            raise LookaheadGuardError(
                "materialization 必须是 DecisionReplayFeatureMaterialization"
            )
        lineage = materialization.lineage
        return cls(
            feature_version_hash=lineage.feature_version_hash,
            lineage_hash=lineage.lineage_hash,
            input_snapshot_hash=materialization.input_snapshot_hash,
            decision_at=lineage.decision_at,
            available_at=lineage.available_at,
            values=materialization.values,
            replay_materialization=materialization,
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "available_at": self.available_at.isoformat(),
            "decision_at": self.decision_at.isoformat(),
            "evidence_hash": self.evidence_hash,
            "feature_version_hash": self.feature_version_hash,
            "input_snapshot_hash": self.input_snapshot_hash,
            "lineage_hash": self.lineage_hash,
            "value_ids": [item.value_id for item in self.values],
        }


@dataclass(frozen=True, slots=True)
class EventAvailabilityEvidence:
    """一个已经发生且已经可获取的事件事实，不表示未来日程或结果。"""

    event_id: str
    event_at: datetime
    available_at: datetime
    source_artifact_snapshot_hash: str
    evidence_hash: str = field(init=False)

    def __post_init__(self) -> None:
        event_id = _text(self.event_id, "event.event_id")
        event_at = _utc_datetime(self.event_at, "event.event_at")
        available_at = _utc_datetime(self.available_at, "event.available_at")
        if available_at < event_at:
            raise LookaheadGuardError("event.available_at 不能早于 event_at")
        source_hash = _hash(
            self.source_artifact_snapshot_hash,
            "event.source_artifact_snapshot_hash",
        )
        evidence_hash = canonical_json_sha256(
            {
                "available_at": available_at.isoformat(),
                "event_at": event_at.isoformat(),
                "event_id": event_id,
                "format": "northstar.event-availability-evidence.v1",
                "source_artifact_snapshot_hash": source_hash,
            }
        )
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "event_at", event_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "source_artifact_snapshot_hash", source_hash)
        object.__setattr__(self, "evidence_hash", evidence_hash)


@dataclass(frozen=True, slots=True)
class ArtifactEventReplayEvidence:
    """由 immutable DatasetVersion 在一个 checkpoint 重放的事件事实。"""

    dataset_version_hash: str
    decision_at: datetime
    snapshot: MarketDataSnapshot
    events: tuple[EventAvailabilityEvidence, ...]
    replay_hash: str = field(init=False)

    def __post_init__(self) -> None:
        dataset_version_hash = _hash(self.dataset_version_hash, "event.dataset_version_hash")
        decision_at = _utc_datetime(self.decision_at, "event.decision_at")
        if not isinstance(self.snapshot, MarketDataSnapshot):
            raise LookaheadGuardError("event.snapshot 必须是 MarketDataSnapshot")
        if self.snapshot.dataset_id != _EVENT_REPLAY_DATASET_ID:
            raise LookaheadGuardError("event replay 必须使用固定 DatasetVersion 身份")
        if self.snapshot.dataset_version_hash != dataset_version_hash:
            raise LookaheadGuardError("event DatasetVersion 与 snapshot 不一致")
        if self.snapshot.spec != _EVENT_REPLAY_SPEC or self.snapshot.as_of != decision_at:
            raise LookaheadGuardError("event replay PIT spec 或 decision_at 不一致")
        if not isinstance(self.events, tuple) or not self.events or not all(
            isinstance(item, EventAvailabilityEvidence) for item in self.events
        ):
            raise LookaheadGuardError("event replay events 必须是非空 EventAvailabilityEvidence 元组")
        canonical_events = tuple(sorted(self.events, key=lambda item: item.event_id))
        if len({item.event_id for item in canonical_events}) != len(canonical_events):
            raise LookaheadGuardError("event replay 不能包含重复 event_id")
        if any(item.available_at > decision_at for item in canonical_events):
            raise LookaheadGuardError("event replay 不能包含 decision_at 后可用的事件")
        replay_hash = canonical_json_sha256(
            {
                "dataset_version_hash": dataset_version_hash,
                "decision_at": decision_at.isoformat(),
                "event_evidence_hashes": [item.evidence_hash for item in canonical_events],
                "format": "northstar.artifact-event-replay.v1",
                "snapshot_id": self.snapshot.snapshot_id,
            }
        )
        object.__setattr__(self, "dataset_version_hash", dataset_version_hash)
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "events", canonical_events)
        object.__setattr__(self, "replay_hash", replay_hash)


def replay_artifact_event_evidence(
    *,
    artifact_store: ArtifactStore,
    dataset_version_hash: str,
    decision_at: datetime,
) -> ArtifactEventReplayEvidence:
    """从固定 Event fact DatasetVersion 重放可见事件，不接受手工 Event 对象。"""

    if type(artifact_store) is not ArtifactStore:
        raise LookaheadGuardError("artifact_store 必须是精确的 ArtifactStore，不能使用子类")
    normalized_decision_at = _utc_datetime(decision_at, "decision_at")
    selector = MarketDataPITSelector(artifact_store)
    try:
        snapshot = MarketDataPITSelector.select(
            selector,
            dataset_version_hash=dataset_version_hash,
            spec=_EVENT_REPLAY_SPEC,
            as_of=normalized_decision_at,
        )
    except MarketDataPITError as exc:
        raise LookaheadGuardError("immutable Event DatasetVersion 无法重放") from exc
    if snapshot.dataset_id != _EVENT_REPLAY_DATASET_ID:
        raise LookaheadGuardError("Event replay DatasetVersion 必须使用固定 dataset_id")
    frame = snapshot.selected_frame()
    events: list[EventAvailabilityEvidence] = []
    for row in frame.iter_rows(named=True):
        event_id = _text(row["event_id"], "event.event_id")
        event_at = _utc_datetime(row["event_at"], "event.event_at")
        available_at = _utc_datetime(row["available_at"], "event.available_at")
        source_hash = _hash(row["source_evidence_hash"], "event.source_evidence_hash")
        try:
            source = artifact_store.load_artifact(source_hash)
        except ArtifactStoreError as exc:
            raise LookaheadGuardError("event source evidence artifact 无法重放") from exc
        if source.snapshot.available_at > available_at:
            raise LookaheadGuardError("event source evidence artifact 晚于事件可用时间")
        events.append(
            EventAvailabilityEvidence(
                event_id=event_id,
                event_at=event_at,
                available_at=available_at,
                source_artifact_snapshot_hash=source_hash,
            )
        )
    return ArtifactEventReplayEvidence(
        dataset_version_hash=dataset_version_hash,
        decision_at=normalized_decision_at,
        snapshot=snapshot,
        events=tuple(events),
    )


@dataclass(frozen=True, slots=True)
class TargetDecisionEvidence:
    """目标在何时由哪个逐决策输入生成的证据。

    ``execution_at`` 可晚于 ``decision_at``：这是正常的延迟执行，不等于目标来自未来。
    Guard 只要求目标生成/可用时间和源快照均不晚于当前决策。
    """

    decision_at: datetime
    available_at: datetime
    source_snapshot_hash: str
    target_hash: str
    execution_at: datetime | None = None
    evidence_hash: str = field(init=False)

    def __post_init__(self) -> None:
        decision_at = _utc_datetime(self.decision_at, "target.decision_at")
        available_at = _utc_datetime(self.available_at, "target.available_at")
        if available_at < decision_at:
            raise LookaheadGuardError("target.available_at 不能早于 target.decision_at")
        source_snapshot_hash = _hash(self.source_snapshot_hash, "target.source_snapshot_hash")
        target_hash = _hash(self.target_hash, "target.target_hash")
        execution_at = (
            _utc_datetime(self.execution_at, "target.execution_at")
            if self.execution_at is not None
            else None
        )
        if execution_at is not None and execution_at < available_at:
            raise LookaheadGuardError("target.execution_at 不能早于 target.available_at")
        evidence_hash = canonical_json_sha256(
            {
                "available_at": available_at.isoformat(),
                "decision_at": decision_at.isoformat(),
                "execution_at": execution_at.isoformat() if execution_at else None,
                "format": "northstar.target-decision-evidence.v1",
                "source_snapshot_hash": source_snapshot_hash,
                "target_hash": target_hash,
            }
        )
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "source_snapshot_hash", source_snapshot_hash)
        object.__setattr__(self, "target_hash", target_hash)
        object.__setattr__(self, "execution_at", execution_at)
        object.__setattr__(self, "evidence_hash", evidence_hash)


@dataclass(frozen=True, slots=True)
class ContractKnowledgeEvidence:
    """实际合约身份在某个时间前已知的不可变来源摘要。"""

    contract: Contract
    master_fingerprint: str
    available_at: datetime
    source_artifact_snapshot_hash: str
    evidence_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.contract, Contract):
            raise LookaheadGuardError("contract.contract 必须是 Contract")
        master_fingerprint = _hash(self.master_fingerprint, "contract.master_fingerprint")
        available_at = _utc_datetime(self.available_at, "contract.available_at")
        source_hash = _hash(
            self.source_artifact_snapshot_hash,
            "contract.source_artifact_snapshot_hash",
        )
        evidence_hash = canonical_json_sha256(
            {
                "available_at": available_at.isoformat(),
                "contract_id": self.contract.contract_id,
                "format": "northstar.contract-knowledge-evidence.v1",
                "master_fingerprint": master_fingerprint,
                "source_artifact_snapshot_hash": source_hash,
            }
        )
        object.__setattr__(self, "master_fingerprint", master_fingerprint)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "source_artifact_snapshot_hash", source_hash)
        object.__setattr__(self, "evidence_hash", evidence_hash)


@dataclass(frozen=True, slots=True)
class FeeMarginRuleEvidence:
    """费率、保证金及限制规则在执行/决策时点可见的证据。"""

    master_fingerprint: str
    rule_snapshot: ContractRuleSnapshot
    evidence_hash: str = field(init=False)

    def __post_init__(self) -> None:
        master_fingerprint = _hash(self.master_fingerprint, "rule.master_fingerprint")
        if not isinstance(self.rule_snapshot, ContractRuleSnapshot):
            raise LookaheadGuardError("rule.rule_snapshot 必须是 ContractRuleSnapshot")
        evidence_hash = canonical_json_sha256(
            {
                "contract_id": self.rule_snapshot.contract_id,
                "format": "northstar.fee-margin-rule-evidence.v1",
                "master_fingerprint": master_fingerprint,
                "rule_snapshot_hash": self.rule_snapshot.snapshot_hash,
                "source_artifact_hash": self.rule_snapshot.source_artifact_hash,
            }
        )
        object.__setattr__(self, "master_fingerprint", master_fingerprint)
        object.__setattr__(self, "evidence_hash", evidence_hash)


@dataclass(frozen=True, slots=True)
class ArtifactRuleBookEvidence:
    """由 immutable Contract RuleBook 逐决策重放出的合约与规则证据。

    这是研究历史事实，不是执行资格：底层 RuleBook selector 固定重建
    ``execution_eligible=False``。调用方不能传入静态 YAML、手工 ``ContractMaster`` 或
    ``ContractRuleSnapshot`` 来伪装历史知识；必须从给定 DatasetVersion 在给定
    ``decision_at`` 重新选择。
    """

    replay: ArtifactBackedContractRuleReplay
    contracts: tuple[ContractKnowledgeEvidence, ...]
    fee_margin_rules: tuple[FeeMarginRuleEvidence, ...]
    evidence_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.replay, ArtifactBackedContractRuleReplay):
            raise LookaheadGuardError("rulebook replay 必须是 ArtifactBackedContractRuleReplay")
        if not isinstance(self.contracts, tuple) or not all(
            isinstance(item, ContractKnowledgeEvidence) for item in self.contracts
        ):
            raise LookaheadGuardError("rulebook contracts 必须是 ContractKnowledgeEvidence 元组")
        if not isinstance(self.fee_margin_rules, tuple) or not all(
            isinstance(item, FeeMarginRuleEvidence) for item in self.fee_margin_rules
        ):
            raise LookaheadGuardError("rulebook fee_margin_rules 必须是 FeeMarginRuleEvidence 元组")
        master = self.replay.master
        contracts_by_id = {item.contract.contract_id: item for item in self.contracts}
        rules_by_id = {item.rule_snapshot.contract_id: item for item in self.fee_margin_rules}
        expected_ids = set(self.replay.selected_contract_ids)
        if set(contracts_by_id) != expected_ids or set(rules_by_id) != expected_ids:
            raise LookaheadGuardError("rulebook evidence 必须精确覆盖 replay 的 selected contracts")
        if len(contracts_by_id) != len(self.contracts) or len(rules_by_id) != len(self.fee_margin_rules):
            raise LookaheadGuardError("rulebook evidence 不能包含重复合约")
        if any(item.master_fingerprint != master.fingerprint for item in self.contracts):
            raise LookaheadGuardError("rulebook contract evidence master fingerprint 不一致")
        if any(item.master_fingerprint != master.fingerprint for item in self.fee_margin_rules):
            raise LookaheadGuardError("rulebook rule evidence master fingerprint 不一致")
        rules = {item.contract_id: item for item in master.rule_snapshots}
        if any(rules[contract_id].snapshot_hash != rules_by_id[contract_id].rule_snapshot.snapshot_hash for contract_id in expected_ids):
            raise LookaheadGuardError("rulebook evidence 与 replay 的 rule snapshot 不一致")
        evidence_hash = canonical_json_sha256(
            {
                "contracts": sorted(item.evidence_hash for item in self.contracts),
                "format": "northstar.artifact-rulebook-lookahead-evidence.v1",
                "replay_hash": self.replay.replay_hash,
                "rules": sorted(item.evidence_hash for item in self.fee_margin_rules),
            }
        )
        object.__setattr__(
            self,
            "contracts",
            tuple(sorted(self.contracts, key=lambda item: item.contract.contract_id)),
        )
        object.__setattr__(
            self,
            "fee_margin_rules",
            tuple(sorted(self.fee_margin_rules, key=lambda item: item.rule_snapshot.contract_id)),
        )
        object.__setattr__(self, "evidence_hash", evidence_hash)


def replay_artifact_rulebook_evidence(
    *,
    artifact_store: ArtifactStore,
    dataset_version_hash: str,
    decision_at: datetime,
    contract_refs: tuple[str, ...],
) -> ArtifactRuleBookEvidence:
    """重放唯一的 immutable RuleBook，并转换为 Guard 可验证的严格证据。

    合约可用时间取关联规则的 ``available_at``，这是保守上界；若合约实际更早已知也不会
    被提前使用。研究 RuleBook 不具执行资格，因此调用方必须保持
    ``DecisionReplayEvidence.require_execution_rules=False``。
    """

    if type(artifact_store) is not ArtifactStore:
        raise LookaheadGuardError("artifact_store 必须是精确的 ArtifactStore，不能使用子类")
    normalized_decision_at = _utc_datetime(decision_at, "decision_at")
    if not isinstance(contract_refs, tuple):
        raise LookaheadGuardError("contract_refs 必须是非空字符串元组")
    try:
        replay = ContractRuleBookPITSelector(artifact_store).select(
            dataset_version_hash=dataset_version_hash,
            decision_at=normalized_decision_at,
            contract_refs=contract_refs,
        )
    except ArtifactRuleBookError as exc:
        raise LookaheadGuardError("immutable Contract RuleBook 无法重放") from exc
    rules_by_contract = {item.contract_id: item for item in replay.master.rule_snapshots}
    contracts_by_id = {item.contract_id: item for item in replay.master.contracts}
    try:
        contracts = tuple(
            ContractKnowledgeEvidence(
                contract=contracts_by_id[contract_id],
                master_fingerprint=replay.master.fingerprint,
                available_at=rules_by_contract[contract_id].available_at,
                source_artifact_snapshot_hash=replay.normalized_snapshot_hash,
            )
            for contract_id in replay.selected_contract_ids
        )
        rules = tuple(
            FeeMarginRuleEvidence(
                master_fingerprint=replay.master.fingerprint,
                rule_snapshot=rules_by_contract[contract_id],
            )
            for contract_id in replay.selected_contract_ids
        )
        return ArtifactRuleBookEvidence(
            replay=replay,
            contracts=contracts,
            fee_margin_rules=rules,
        )
    except (KeyError, LookaheadGuardError) as exc:  # pragma: no cover - selector returns closed data.
        raise LookaheadGuardError("immutable Contract RuleBook 重放结果不完整") from exc


@dataclass(frozen=True, slots=True)
class LookaheadInputUsageDeclaration:
    """Hash-only declaration that a controlled producer used or did not use an input."""

    input_kind: LookaheadInputKind
    usage: LookaheadInputUsage
    producer_identity_hash: str
    declaration_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.input_kind, LookaheadInputKind):
            raise LookaheadGuardError("input_usage.input_kind must be LookaheadInputKind")
        if not isinstance(self.usage, LookaheadInputUsage):
            raise LookaheadGuardError("input_usage.usage must be LookaheadInputUsage")
        producer_identity_hash = _hash(
            self.producer_identity_hash,
            "input_usage.producer_identity_hash",
        )
        declaration_hash = canonical_json_sha256(
            {
                "format": "northstar.lookahead-input-usage.v1",
                "input_kind": self.input_kind.value,
                "producer_identity_hash": producer_identity_hash,
                "usage": self.usage.value,
            }
        )
        object.__setattr__(self, "producer_identity_hash", producer_identity_hash)
        object.__setattr__(self, "declaration_hash", declaration_hash)

    def as_mapping(self) -> dict[str, str]:
        return {
            "declaration_hash": self.declaration_hash,
            "input_kind": self.input_kind.value,
            "usage": self.usage.value,
        }


@dataclass(frozen=True, slots=True)
class DecisionReplayEvidence:
    """一个决策时点的所有输入证据；不包含行情、特征值或订单明细。"""

    market_data: DecisionMarketDataEvidence
    target: TargetDecisionEvidence
    features: tuple[FeatureAvailabilityEvidence, ...] = ()
    events: tuple[EventAvailabilityEvidence, ...] = ()
    artifact_events: ArtifactEventReplayEvidence | None = None
    contracts: tuple[ContractKnowledgeEvidence, ...] = ()
    fee_margin_rules: tuple[FeeMarginRuleEvidence, ...] = ()
    artifact_rulebook: ArtifactRuleBookEvidence | None = None
    input_usage: tuple[LookaheadInputUsageDeclaration, ...] = ()
    require_execution_rules: bool = False
    evidence_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.market_data, DecisionMarketDataEvidence):
            raise LookaheadGuardError("market_data 必须是 DecisionMarketDataEvidence")
        if not isinstance(self.target, TargetDecisionEvidence):
            raise LookaheadGuardError("target 必须是 TargetDecisionEvidence")
        groups: tuple[tuple[str, tuple[object, ...], type[object]], ...] = (
            ("features", self.features, FeatureAvailabilityEvidence),
            ("events", self.events, EventAvailabilityEvidence),
            ("contracts", self.contracts, ContractKnowledgeEvidence),
            ("fee_margin_rules", self.fee_margin_rules, FeeMarginRuleEvidence),
        )
        for field_name, values, expected_type in groups:
            if not isinstance(values, tuple) or not all(isinstance(item, expected_type) for item in values):
                raise LookaheadGuardError(f"{field_name} 必须是 {expected_type.__name__} 元组")
        if self.artifact_rulebook is not None and not isinstance(
            self.artifact_rulebook,
            ArtifactRuleBookEvidence,
        ):
            raise LookaheadGuardError("artifact_rulebook 必须是 ArtifactRuleBookEvidence 或 None")
        if self.artifact_rulebook is not None and (
            self.contracts != self.artifact_rulebook.contracts
            or self.fee_margin_rules != self.artifact_rulebook.fee_margin_rules
        ):
            raise LookaheadGuardError(
                "artifact_rulebook 必须精确绑定 contracts 与 fee_margin_rules"
            )
        if self.artifact_events is not None and not isinstance(
            self.artifact_events,
            ArtifactEventReplayEvidence,
        ):
            raise LookaheadGuardError("artifact_events 必须是 ArtifactEventReplayEvidence 或 None")
        if self.artifact_events is not None and self.events != self.artifact_events.events:
            raise LookaheadGuardError("artifact_events 必须精确绑定 events")
        if not isinstance(self.input_usage, tuple) or not all(
            isinstance(item, LookaheadInputUsageDeclaration) for item in self.input_usage
        ):
            raise LookaheadGuardError(
                "input_usage must be a LookaheadInputUsageDeclaration tuple"
            )
        if type(self.require_execution_rules) is not bool:
            raise LookaheadGuardError("require_execution_rules 必须是 bool")
        if self.target.decision_at != self.market_data.decision_at:
            raise LookaheadGuardError(
                "target.decision_at 必须与 market_data checkpoint.decision_at 完全一致"
            )
        if self.require_execution_rules and not self.fee_margin_rules:
            raise LookaheadGuardError("实际合约逐决策重放必须提供 fee_margin_rules")
        canonical_features = tuple(sorted(self.features, key=lambda item: item.evidence_hash))
        canonical_events = tuple(sorted(self.events, key=lambda item: item.evidence_hash))
        canonical_contracts = tuple(sorted(self.contracts, key=lambda item: item.evidence_hash))
        canonical_rules = tuple(sorted(self.fee_margin_rules, key=lambda item: item.evidence_hash))
        canonical_input_usage = tuple(
            sorted(self.input_usage, key=lambda item: item.input_kind.value)
        )
        if len({item.evidence_hash for item in canonical_features}) != len(canonical_features):
            raise LookaheadGuardError("features 不能包含重复证据")
        if len({item.evidence_hash for item in canonical_events}) != len(canonical_events):
            raise LookaheadGuardError("events 不能包含重复证据")
        if len({item.evidence_hash for item in canonical_contracts}) != len(canonical_contracts):
            raise LookaheadGuardError("contracts 不能包含重复证据")
        if len({item.evidence_hash for item in canonical_rules}) != len(canonical_rules):
            raise LookaheadGuardError("fee_margin_rules 不能包含重复证据")
        if len({item.input_kind for item in canonical_input_usage}) != len(
            canonical_input_usage
        ):
            raise LookaheadGuardError("input_usage cannot declare an input kind twice")
        evidence_by_kind: dict[LookaheadInputKind, tuple[object, ...]] = {
            LookaheadInputKind.FEATURE: canonical_features,
            LookaheadInputKind.EVENT: canonical_events,
            LookaheadInputKind.CONTRACT: canonical_contracts,
            LookaheadInputKind.FEE_MARGIN_RULE: canonical_rules,
        }
        for declaration in canonical_input_usage:
            evidence_items = evidence_by_kind[declaration.input_kind]
            if declaration.usage is LookaheadInputUsage.PROVIDED and not evidence_items:
                raise LookaheadGuardError(
                    "a provided input usage declaration requires matching evidence"
                )
            if declaration.usage is LookaheadInputUsage.NOT_USED and evidence_items:
                raise LookaheadGuardError(
                    "a not_used input usage declaration cannot carry matching evidence"
                )
        evidence_hash = canonical_json_sha256(
            {
                "contracts": [item.evidence_hash for item in canonical_contracts],
                "artifact_rulebook": (
                    self.artifact_rulebook.evidence_hash
                    if self.artifact_rulebook is not None
                    else None
                ),
                "artifact_events": (
                    self.artifact_events.replay_hash
                    if self.artifact_events is not None
                    else None
                ),
                "events": [item.evidence_hash for item in canonical_events],
                "features": [item.evidence_hash for item in canonical_features],
                "fee_margin_rules": [item.evidence_hash for item in canonical_rules],
                "format": "northstar.decision-replay-evidence.v1",
                "market_data": self.market_data.evidence_hash,
                "input_usage": [item.declaration_hash for item in canonical_input_usage],
                "require_execution_rules": self.require_execution_rules,
                "target": self.target.evidence_hash,
            }
        )
        object.__setattr__(self, "features", canonical_features)
        object.__setattr__(self, "events", canonical_events)
        object.__setattr__(self, "contracts", canonical_contracts)
        object.__setattr__(self, "fee_margin_rules", canonical_rules)
        object.__setattr__(self, "input_usage", canonical_input_usage)
        object.__setattr__(self, "evidence_hash", evidence_hash)

    @property
    def decision_at(self) -> datetime:
        return self.market_data.decision_at

    def as_mapping(self) -> dict[str, object]:
        return {
            "contracts": [item.evidence_hash for item in self.contracts],
            "artifact_rulebook": (
                self.artifact_rulebook.evidence_hash
                if self.artifact_rulebook is not None
                else None
            ),
            "artifact_events": (
                self.artifact_events.replay_hash
                if self.artifact_events is not None
                else None
            ),
            "decision_at": self.decision_at.isoformat(),
            "events": [item.evidence_hash for item in self.events],
            "evidence_hash": self.evidence_hash,
            "features": [item.evidence_hash for item in self.features],
            "fee_margin_rules": [item.evidence_hash for item in self.fee_margin_rules],
            "market_data": self.market_data.as_mapping(),
            "input_usage": [item.as_mapping() for item in self.input_usage],
            "require_execution_rules": self.require_execution_rules,
            "target": self.target.evidence_hash,
        }


@dataclass(frozen=True, slots=True)
class LookaheadViolation:
    """一条确定性、无载荷泄露的前视偏差诊断。"""

    kind: LookaheadViolationKind
    decision_at: datetime
    evidence_id: str
    reason_code: str
    evidence_available_at: datetime | None = None
    violation_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, LookaheadViolationKind):
            raise LookaheadGuardError("violation.kind 必须是 LookaheadViolationKind")
        decision_at = _utc_datetime(self.decision_at, "violation.decision_at")
        evidence_id = _text(self.evidence_id, "violation.evidence_id")
        reason_code = _text(self.reason_code, "violation.reason_code")
        available_at = (
            _utc_datetime(self.evidence_available_at, "violation.evidence_available_at")
            if self.evidence_available_at is not None
            else None
        )
        violation_hash = canonical_json_sha256(
            {
                "decision_at": decision_at.isoformat(),
                "evidence_available_at": available_at.isoformat() if available_at else None,
                "evidence_id": evidence_id,
                "format": "northstar.lookahead-violation.v1",
                "kind": self.kind.value,
                "reason_code": reason_code,
            }
        )
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "evidence_id", evidence_id)
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "evidence_available_at", available_at)
        object.__setattr__(self, "violation_hash", violation_hash)

    def as_mapping(self) -> dict[str, object]:
        return {
            "decision_at": self.decision_at.isoformat(),
            "evidence_available_at": (
                self.evidence_available_at.isoformat() if self.evidence_available_at else None
            ),
            "evidence_id": self.evidence_id,
            "kind": self.kind.value,
            "reason_code": self.reason_code,
            "violation_hash": self.violation_hash,
        }


@dataclass(frozen=True, slots=True)
class LookaheadReport:
    """一个 checkpoint 的审计结果；存在任何 violation 即不可签发证书。"""

    evidence: DecisionReplayEvidence
    violations: tuple[LookaheadViolation, ...]
    report_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, DecisionReplayEvidence):
            raise LookaheadGuardError("report.evidence 必须是 DecisionReplayEvidence")
        if not isinstance(self.violations, tuple) or not all(
            isinstance(item, LookaheadViolation) for item in self.violations
        ):
            raise LookaheadGuardError("report.violations 必须是 LookaheadViolation 元组")
        if any(item.decision_at != self.evidence.decision_at for item in self.violations):
            raise LookaheadGuardError("report violation 的 decision_at 必须与 evidence 一致")
        canonical_violations = tuple(sorted(self.violations, key=lambda item: item.violation_hash))
        report_hash = canonical_json_sha256(
            {
                "evidence_hash": self.evidence.evidence_hash,
                "format": "northstar.lookahead-report.v1",
                "violation_hashes": [item.violation_hash for item in canonical_violations],
            }
        )
        object.__setattr__(self, "violations", canonical_violations)
        object.__setattr__(self, "report_hash", report_hash)

    @property
    def is_safe(self) -> bool:
        return not self.violations

    def as_mapping(self) -> dict[str, object]:
        return {
            "decision_at": self.evidence.decision_at.isoformat(),
            "evidence_hash": self.evidence.evidence_hash,
            "report_hash": self.report_hash,
            "violations": [item.as_mapping() for item in self.violations],
        }


@dataclass(frozen=True, slots=True)
class LookaheadCertificate:
    """由 Guard 重算的逐决策证据一致性回执，当前不可作为候选资格。"""

    plan: DecisionReplayPlan
    reports: tuple[LookaheadReport, ...]
    _issuer: InitVar[object | None] = None
    selection_mode: str = field(init=False)
    decision_time_safe: bool = field(init=False)
    candidate_admission_eligible: bool = field(init=False)
    assurance_level: str = field(init=False)
    certificate_hash: str = field(init=False)

    def __post_init__(self, _issuer: object | None) -> None:
        if _issuer is not _CERTIFICATE_ISSUER:
            raise LookaheadGuardError(
                "LookaheadCertificate 只能由 LookaheadGuard.certify 签发"
            )
        if not isinstance(self.plan, DecisionReplayPlan):
            raise LookaheadGuardError("certificate.plan 必须是 DecisionReplayPlan")
        if not isinstance(self.reports, tuple) or not self.reports:
            raise LookaheadGuardError("certificate.reports 必须是非空 LookaheadReport 元组")
        if not all(isinstance(item, LookaheadReport) for item in self.reports):
            raise LookaheadGuardError("certificate.reports 包含非 LookaheadReport 项")
        if any(not item.is_safe for item in self.reports):
            raise LookaheadGuardError("存在前视偏差，不能签发 LookaheadCertificate")
        report_by_checkpoint = {
            item.evidence.market_data.checkpoint.checkpoint_hash: item for item in self.reports
        }
        expected = tuple(item.checkpoint_hash for item in self.plan.checkpoints)
        if len(report_by_checkpoint) != len(self.reports) or set(report_by_checkpoint) != set(expected):
            raise LookaheadGuardError("certificate reports 必须与 replay plan checkpoints 一一对应")
        canonical_reports = tuple(report_by_checkpoint[checkpoint_hash] for checkpoint_hash in expected)
        certificate_hash = canonical_json_sha256(
            {
                "format": "northstar.lookahead-certificate.v1",
                "assurance_level": "EVIDENCE_CONSISTENCY_ONLY",
                "candidate_admission_eligible": False,
                "decision_time_safe": False,
                "report_hashes": [item.report_hash for item in canonical_reports],
                "schedule_hash": self.plan.schedule_hash,
                "selection_mode": _PER_DECISION_SELECTION_MODE,
            }
        )
        object.__setattr__(self, "reports", canonical_reports)
        object.__setattr__(self, "selection_mode", _PER_DECISION_SELECTION_MODE)
        # 当前仓库没有受控的逐 checkpoint 特征、事件、目标和 Contract Master 发布器。
        # 因此本对象只证明 Guard 已用 immutable 市场制品重放并复算提交的证据；它不能
        # 证明策略/目标计算本身无前视，也绝不能提升候选策略资格。
        object.__setattr__(self, "decision_time_safe", False)
        object.__setattr__(self, "candidate_admission_eligible", False)
        object.__setattr__(self, "assurance_level", "EVIDENCE_CONSISTENCY_ONLY")
        object.__setattr__(self, "certificate_hash", certificate_hash)

    def as_manifest_mapping(self) -> dict[str, object]:
        """返回可进入未来回测 manifest 的 hash-only 安全投影。"""

        return {
            "assurance_level": self.assurance_level,
            "certificate_hash": self.certificate_hash,
            "candidate_admission_eligible": self.candidate_admission_eligible,
            "decision_time_safe": self.decision_time_safe,
            "format": "northstar.lookahead-certificate.v1",
            "replay_plan": self.plan.as_mapping(),
            "reports": [item.as_mapping() for item in self.reports],
            "selection_mode": self.selection_mode,
        }


class LookaheadGuard:
    """检查全部六类证据并仅对零违规的逐决策计划签发证书。"""

    def evaluate(self, evidence: DecisionReplayEvidence) -> LookaheadReport:
        """返回完整诊断；调用方必须使用 :meth:`certify` 才能获得安全声明。"""

        if not isinstance(evidence, DecisionReplayEvidence):
            raise LookaheadGuardError("evidence 必须是 DecisionReplayEvidence")
        decision_at = evidence.decision_at
        violations: list[LookaheadViolation] = []
        market = evidence.market_data.market_snapshot

        if market.as_of != decision_at:
            violations.append(
                self._violation(
                    LookaheadViolationKind.REVISED_HISTORICAL_DATA,
                    decision_at,
                    evidence.market_data.evidence_hash,
                    "MARKET_SNAPSHOT_AS_OF_MISMATCH",
                    market.as_of,
                )
            )
        if market.source_artifact_available_at > decision_at:
            violations.append(
                self._violation(
                    LookaheadViolationKind.REVISED_HISTORICAL_DATA,
                    decision_at,
                    evidence.market_data.evidence_hash,
                    "MARKET_ARTIFACT_AVAILABLE_AFTER_DECISION",
                    market.source_artifact_available_at,
                )
            )
        for revision in market.revisions:
            if revision.available_at > decision_at:
                violations.append(
                    self._violation(
                        LookaheadViolationKind.REVISED_HISTORICAL_DATA,
                        decision_at,
                        revision.revision_id,
                        "MARKET_REVISION_AVAILABLE_AFTER_DECISION",
                        revision.available_at,
                    )
                )

        target = evidence.target
        if target.decision_at != decision_at:
            violations.append(
                self._violation(
                    LookaheadViolationKind.FUTURE_TARGET,
                    decision_at,
                    target.evidence_hash,
                    "TARGET_DECISION_CHECKPOINT_MISMATCH",
                    target.decision_at,
                )
            )
        if target.available_at < target.decision_at:
            violations.append(
                self._violation(
                    LookaheadViolationKind.FUTURE_TARGET,
                    decision_at,
                    target.evidence_hash,
                    "TARGET_AVAILABLE_BEFORE_GENERATION",
                    target.available_at,
                )
            )
        if target.available_at > decision_at:
            violations.append(
                self._violation(
                    LookaheadViolationKind.FUTURE_TARGET,
                    decision_at,
                    target.evidence_hash,
                    "TARGET_AVAILABLE_AFTER_DECISION",
                    target.available_at,
                )
            )
        if target.execution_at is not None and target.execution_at < target.available_at:
            violations.append(
                self._violation(
                    LookaheadViolationKind.FUTURE_TARGET,
                    decision_at,
                    target.evidence_hash,
                    "TARGET_EXECUTION_BEFORE_AVAILABLE",
                    target.execution_at,
                )
            )
        if target.source_snapshot_hash != market.snapshot_id:
            violations.append(
                self._violation(
                    LookaheadViolationKind.FUTURE_TARGET,
                    decision_at,
                    target.evidence_hash,
                    "TARGET_SOURCE_SNAPSHOT_MISMATCH",
                )
            )

        for feature in evidence.features:
            # P2-WP01 的 FeatureRegistry 只能签发 static as-of lineage/backfill。
            # 在真正的逐 checkpoint Feature replay producer 落地前，任何裸
            # FeatureValue/lineage 哈希都不能成为 strict certificate 的依据。
            materialization = feature.replay_materialization
            if materialization is None:
                violations.append(
                    self._violation(
                        LookaheadViolationKind.FUTURE_FEATURE,
                        decision_at,
                        feature.evidence_hash,
                        "STRICT_FEATURE_REPLAY_PRODUCER_UNAVAILABLE",
                    )
                )
            elif (
                materialization.replay_checkpoint_hash
                != evidence.market_data.checkpoint.checkpoint_hash
            ):
                violations.append(
                    self._violation(
                        LookaheadViolationKind.FUTURE_FEATURE,
                        decision_at,
                        feature.evidence_hash,
                        "FEATURE_REPLAY_CHECKPOINT_MISMATCH",
                    )
                )
            if feature.input_snapshot_hash != market.snapshot_id:
                violations.append(
                    self._violation(
                        LookaheadViolationKind.FUTURE_FEATURE,
                        decision_at,
                        feature.evidence_hash,
                        "FEATURE_INPUT_SNAPSHOT_MISMATCH",
                    )
                )
            if feature.decision_at > decision_at:
                violations.append(
                    self._violation(
                        LookaheadViolationKind.FUTURE_FEATURE,
                        decision_at,
                        feature.evidence_hash,
                        "FEATURE_GENERATED_AFTER_DECISION",
                        feature.decision_at,
                    )
                )
            if feature.available_at > decision_at:
                violations.append(
                    self._violation(
                        LookaheadViolationKind.FUTURE_FEATURE,
                        decision_at,
                        feature.evidence_hash,
                        "FEATURE_AVAILABLE_AFTER_DECISION",
                        feature.available_at,
                    )
                )
            for value in feature.values:
                if value.available_at > decision_at or not _event_not_after(value.event_time, decision_at):
                    violations.append(
                        self._violation(
                            LookaheadViolationKind.FUTURE_FEATURE,
                            decision_at,
                            value.value_id,
                            "FEATURE_VALUE_NOT_VISIBLE_AT_DECISION",
                            value.available_at,
                        )
                    )

        for event in evidence.events:
            if event.event_at > decision_at:
                violations.append(
                    self._violation(
                        LookaheadViolationKind.FUTURE_EVENT,
                        decision_at,
                        event.evidence_hash,
                        "EVENT_OCCURRED_AFTER_DECISION",
                        event.event_at,
                    )
                )
            if event.available_at > decision_at:
                violations.append(
                    self._violation(
                        LookaheadViolationKind.FUTURE_EVENT,
                        decision_at,
                        event.evidence_hash,
                        "EVENT_AVAILABLE_AFTER_DECISION",
                        event.available_at,
                    )
                )

        contracts: dict[str, ContractKnowledgeEvidence] = {}
        for contract in evidence.contracts:
            previous = contracts.get(contract.contract.contract_id)
            if previous is not None:
                violations.append(
                    self._violation(
                        LookaheadViolationKind.FUTURE_CONTRACT_KNOWLEDGE,
                        decision_at,
                        contract.evidence_hash,
                        "DUPLICATE_CONTRACT_KNOWLEDGE_FOR_CONTRACT",
                    )
                )
            else:
                contracts[contract.contract.contract_id] = contract
            if contract.available_at > decision_at:
                violations.append(
                    self._violation(
                        LookaheadViolationKind.FUTURE_CONTRACT_KNOWLEDGE,
                        decision_at,
                        contract.evidence_hash,
                        "CONTRACT_KNOWLEDGE_AVAILABLE_AFTER_DECISION",
                        contract.available_at,
                    )
                )
            if decision_at.date() < contract.contract.listed_on or decision_at.date() > contract.contract.expires_on:
                violations.append(
                    self._violation(
                        LookaheadViolationKind.FUTURE_CONTRACT_KNOWLEDGE,
                        decision_at,
                        contract.evidence_hash,
                        "CONTRACT_NOT_ACTIVE_AT_DECISION",
                    )
                )

        rule_contract_ids: set[str] = set()
        for rule_evidence in evidence.fee_margin_rules:
            rule = rule_evidence.rule_snapshot
            if rule.contract_id in rule_contract_ids:
                violations.append(
                    self._violation(
                        LookaheadViolationKind.FUTURE_FEE_MARGIN_RULE,
                        decision_at,
                        rule_evidence.evidence_hash,
                        "DUPLICATE_FEE_MARGIN_RULE_FOR_CONTRACT",
                    )
                )
            rule_contract_ids.add(rule.contract_id)
            if rule.contract_id not in contracts:
                violations.append(
                    self._violation(
                        LookaheadViolationKind.FUTURE_CONTRACT_KNOWLEDGE,
                        decision_at,
                        rule_evidence.evidence_hash,
                        "RULE_WITHOUT_CONTRACT_KNOWLEDGE",
                    )
                )
            elif rule_evidence.master_fingerprint != contracts[rule.contract_id].master_fingerprint:
                violations.append(
                    self._violation(
                        LookaheadViolationKind.FUTURE_CONTRACT_KNOWLEDGE,
                        decision_at,
                        rule_evidence.evidence_hash,
                        "CONTRACT_RULE_MASTER_FINGERPRINT_MISMATCH",
                    )
                )
            if (
                rule.available_at > decision_at
                or rule.observed_at > decision_at
                or rule.effective_from > decision_at
            ):
                available_at = max(rule.available_at, rule.observed_at, rule.effective_from)
                violations.append(
                    self._violation(
                        LookaheadViolationKind.FUTURE_FEE_MARGIN_RULE,
                        decision_at,
                        rule_evidence.evidence_hash,
                        "FEE_MARGIN_RULE_NOT_VISIBLE_AT_DECISION",
                        available_at,
                    )
                )
            if rule.effective_until is not None and decision_at >= rule.effective_until:
                violations.append(
                    self._violation(
                        LookaheadViolationKind.FUTURE_FEE_MARGIN_RULE,
                        decision_at,
                        rule_evidence.evidence_hash,
                        "FEE_MARGIN_RULE_NOT_EFFECTIVE_AT_DECISION",
                    )
                )
            if rule.quality_status is not RuleQualityStatus.PASS:
                violations.append(
                    self._violation(
                        LookaheadViolationKind.FUTURE_FEE_MARGIN_RULE,
                        decision_at,
                        rule_evidence.evidence_hash,
                        "FEE_MARGIN_RULE_QUALITY_NOT_PASS",
                    )
                )
            if evidence.require_execution_rules:
                if rule.listing_state is not ListingState.LISTED:
                    violations.append(
                        self._violation(
                            LookaheadViolationKind.FUTURE_FEE_MARGIN_RULE,
                            decision_at,
                            rule_evidence.evidence_hash,
                            "FEE_MARGIN_RULE_LISTING_NOT_LISTED",
                        )
                    )
                if decision_at.date() > rule.expires_on:
                    violations.append(
                        self._violation(
                            LookaheadViolationKind.FUTURE_FEE_MARGIN_RULE,
                            decision_at,
                            rule_evidence.evidence_hash,
                            "FEE_MARGIN_RULE_EXPIRED_AT_DECISION",
                        )
                    )
                if rule.delivery_restriction is not DeliveryRestriction.NONE:
                    violations.append(
                        self._violation(
                            LookaheadViolationKind.FUTURE_FEE_MARGIN_RULE,
                            decision_at,
                            rule_evidence.evidence_hash,
                            "FEE_MARGIN_RULE_DELIVERY_RESTRICTED",
                        )
                    )
                contract_evidence = contracts.get(rule.contract_id)
                if (
                    contract_evidence is not None
                    and rule.expires_on != contract_evidence.contract.expires_on
                ):
                    violations.append(
                        self._violation(
                            LookaheadViolationKind.FUTURE_FEE_MARGIN_RULE,
                            decision_at,
                            rule_evidence.evidence_hash,
                            "FEE_MARGIN_RULE_CONTRACT_EXPIRY_MISMATCH",
                        )
                    )
                if not rule.execution_eligible:
                    violations.append(
                        self._violation(
                            LookaheadViolationKind.FUTURE_FEE_MARGIN_RULE,
                            decision_at,
                            rule_evidence.evidence_hash,
                            "FEE_MARGIN_RULE_NOT_EXECUTION_ELIGIBLE",
                        )
                    )

        if evidence.require_execution_rules:
            missing_rules = sorted(set(contracts).difference(rule_contract_ids))
            for contract_id in missing_rules:
                violations.append(
                    self._violation(
                        LookaheadViolationKind.FUTURE_FEE_MARGIN_RULE,
                        decision_at,
                        contracts[contract_id].evidence_hash,
                        "FEE_MARGIN_RULE_MISSING_FOR_CONTRACT",
                    )
                )
        return LookaheadReport(evidence=evidence, violations=tuple(violations))

    def certify(
        self,
        plan: DecisionReplayPlan,
        evidence: Iterable[DecisionReplayEvidence],
        *,
        artifact_store: ArtifactStore,
    ) -> LookaheadCertificate:
        """重放市场快照并检查提交证据；返回不可准入的一致性回执。

        这里不能信任调用方传入的 ``MarketDataSnapshot``：签发前必须从同一个
        immutable ``ArtifactStore`` 新建 selector 重放 DatasetVersion，并逐一比较 snapshot
        identity。这样最终 as-of snapshot 或手工 ``from_selected_frame`` 无法伪装成较早决策。

        这不是完整回测的无前视认证：当前没有受控逐 checkpoint Feature、事件、目标、
        Contract Master/规则发布器，故返回对象固定 ``decision_time_safe=false``，不能被
        Research Admission 或交易路径用作资格证据。
        """

        if type(plan) is not DecisionReplayPlan:
            raise LookaheadGuardError("plan 必须是精确的 DecisionReplayPlan，不能使用子类")
        if type(artifact_store) is not ArtifactStore:
            raise LookaheadGuardError("artifact_store 必须是精确的 ArtifactStore，不能使用子类")
        selector = MarketDataPITSelector(artifact_store)
        evidence_items = tuple(evidence)
        if not evidence_items or not all(isinstance(item, DecisionReplayEvidence) for item in evidence_items):
            raise LookaheadGuardError("evidence 必须是非空 DecisionReplayEvidence 序列")
        by_checkpoint = {
            item.market_data.checkpoint.checkpoint_hash: item for item in evidence_items
        }
        expected = tuple(item.checkpoint_hash for item in plan.checkpoints)
        if len(by_checkpoint) != len(evidence_items) or set(by_checkpoint) != set(expected):
            raise LookaheadGuardError("evidence 必须与 DecisionReplayPlan checkpoints 一一对应")
        for checkpoint in plan.checkpoints:
            supplied = by_checkpoint[checkpoint.checkpoint_hash].market_data.market_snapshot
            replayed = MarketDataPITSelector.select(
                selector,
                dataset_version_hash=checkpoint.dataset_version_hash,
                spec=checkpoint.pit_spec,
                as_of=checkpoint.decision_at,
            )
            if replayed.snapshot_id != supplied.snapshot_id:
                raise LookaheadGuardError(
                    "MARKET_SNAPSHOT_REPLAY_MISMATCH：必须使用 selector 在原 decision_at 重放的快照"
                )
        for checkpoint in plan.checkpoints:
            declared_kinds = {
                item.input_kind
                for item in by_checkpoint[checkpoint.checkpoint_hash].input_usage
            }
            if declared_kinds != set(LookaheadInputKind):
                missing = sorted(
                    item.value for item in set(LookaheadInputKind) - declared_kinds
                )
                raise LookaheadGuardError(
                    "INPUT_USAGE_DECLARATIONS_INCOMPLETE: " + ", ".join(missing)
                )
        for item in evidence_items:
            event_replay = item.artifact_events
            if event_replay is None:
                if item.events:
                    raise LookaheadGuardError("ARTIFACT_EVENT_EVIDENCE_REQUIRED_FOR_EVENTS")
            else:
                if event_replay.decision_at != item.decision_at:
                    raise LookaheadGuardError("ARTIFACT_EVENT_DECISION_TIME_MISMATCH")
                recomputed_events = replay_artifact_event_evidence(
                    artifact_store=artifact_store,
                    dataset_version_hash=event_replay.dataset_version_hash,
                    decision_at=item.decision_at,
                )
                if recomputed_events.replay_hash != event_replay.replay_hash:
                    raise LookaheadGuardError("ARTIFACT_EVENT_REPLAY_MISMATCH")
        for item in evidence_items:
            rulebook = item.artifact_rulebook
            if rulebook is None:
                if item.contracts or item.fee_margin_rules:
                    raise LookaheadGuardError(
                        "ARTIFACT_RULEBOOK_EVIDENCE_REQUIRED_FOR_CONTRACT_RULES"
                    )
                continue
            if rulebook.replay.decision_at != item.decision_at:
                raise LookaheadGuardError("ARTIFACT_RULEBOOK_DECISION_TIME_MISMATCH")
            recomputed_rulebook = replay_artifact_rulebook_evidence(
                artifact_store=artifact_store,
                dataset_version_hash=rulebook.replay.dataset_version_hash,
                decision_at=item.decision_at,
                contract_refs=rulebook.replay.selected_contract_ids,
            )
            if recomputed_rulebook.evidence_hash != rulebook.evidence_hash:
                raise LookaheadGuardError("ARTIFACT_RULEBOOK_REPLAY_MISMATCH")
        reports = tuple(self.evaluate(by_checkpoint[item]) for item in expected)
        violations = tuple(
            violation
            for report in reports
            for violation in report.violations
        )
        if violations:
            codes = ", ".join(
                sorted({f"{item.kind.value}:{item.reason_code}" for item in violations})
            )
            raise LookaheadGuardError(f"检测到前视偏差，拒绝签发证书：{codes}")
        return LookaheadCertificate(plan=plan, reports=reports, _issuer=_CERTIFICATE_ISSUER)

    def verify_certificate(
        self,
        certificate: LookaheadCertificate,
        *,
        artifact_store: ArtifactStore,
    ) -> LookaheadCertificate:
        """从计划和证据完全重算一份证书，拒绝手工构造或被改写的 report。"""

        if not isinstance(certificate, LookaheadCertificate):
            raise LookaheadGuardError("certificate 必须是 LookaheadCertificate")
        if type(artifact_store) is not ArtifactStore:
            raise LookaheadGuardError("artifact_store 必须是精确的 ArtifactStore，不能使用子类")
        recomputed = self.certify(
            certificate.plan,
            tuple(report.evidence for report in certificate.reports),
            artifact_store=artifact_store,
        )
        if recomputed.certificate_hash != certificate.certificate_hash:
            raise LookaheadGuardError("LOOKAHEAD_CERTIFICATE_RECOMPUTE_MISMATCH")
        if tuple(item.report_hash for item in recomputed.reports) != tuple(
            item.report_hash for item in certificate.reports
        ):
            raise LookaheadGuardError("LOOKAHEAD_CERTIFICATE_REPORT_MISMATCH")
        return recomputed

    def assert_static_feature_rejected(
        self,
        *,
        lineage: FeatureLineage | None = None,
        backfill: FeatureBackfill | None = None,
        decision_at: datetime,
    ) -> None:
        """显式拒绝 P2-WP01 现有 static feature 证据，避免未来调用方误接入。"""

        checked_at = _utc_datetime(decision_at, "decision_at")
        if lineage is not None:
            if not isinstance(lineage, FeatureLineage):
                raise LookaheadGuardError("lineage 必须是 FeatureLineage")
            if lineage.selection_mode != _PER_DECISION_SELECTION_MODE or not lineage.decision_time_safe:
                raise LookaheadGuardError(
                    "STATIC_FEATURE_LINEAGE_NOT_ALLOWED_FOR_PER_DECISION_REPLAY"
                )
        if backfill is not None:
            if not isinstance(backfill, FeatureBackfill):
                raise LookaheadGuardError("backfill 必须是 FeatureBackfill")
            if backfill.selection_mode != _PER_DECISION_SELECTION_MODE or not backfill.decision_time_safe:
                raise LookaheadGuardError(
                    "STATIC_FEATURE_BACKFILL_NOT_ALLOWED_FOR_PER_DECISION_REPLAY"
                )
        if lineage is None and backfill is None:
            raise LookaheadGuardError("必须提供 lineage 或 backfill 以验证 static feature 边界")
        # ``checked_at`` 保持显式，防止这个 API 以后偷偷引入当前时钟。
        del checked_at

    @staticmethod
    def _violation(
        kind: LookaheadViolationKind,
        decision_at: datetime,
        evidence_id: str,
        reason_code: str,
        evidence_available_at: datetime | None = None,
    ) -> LookaheadViolation:
        return LookaheadViolation(
            kind=kind,
            decision_at=decision_at,
            evidence_id=evidence_id,
            reason_code=reason_code,
            evidence_available_at=evidence_available_at,
        )


__all__ = [
    "ArtifactEventReplayEvidence",
    "ArtifactRuleBookEvidence",
    "ContractKnowledgeEvidence",
    "DecisionMarketDataEvidence",
    "DecisionReplayCheckpoint",
    "DecisionReplayEvidence",
    "DecisionReplayPlan",
    "EventAvailabilityEvidence",
    "FeatureAvailabilityEvidence",
    "FeeMarginRuleEvidence",
    "LookaheadCertificate",
    "LookaheadGuard",
    "LookaheadGuardError",
    "LookaheadInputKind",
    "LookaheadInputUsage",
    "LookaheadInputUsageDeclaration",
    "LookaheadReport",
    "LookaheadViolation",
    "LookaheadViolationKind",
    "TargetDecisionEvidence",
    "replay_artifact_rulebook_evidence",
    "replay_artifact_event_evidence",
]

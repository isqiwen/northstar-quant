"""P2-WP07：人工可审计的研究决策状态机。

状态机只记录研究治理结论。它不提交订单、不修改交易配置，也不把验证指标自动解释为
晋级授权；任何从研究状态进入候选、paper、sim 或 production-candidate 都要求一份
显式、命名的人类批准记录。
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from datetime import UTC, datetime
from enum import Enum
import re

from northstar_quant.data.artifacts.fingerprints import canonical_json_sha256
from northstar_quant.research.validation.framework import ValidationReport


_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_EVIDENCE_ISSUER = object()


class ResearchDecisionError(ValueError):
    """研究决策、证据或状态迁移不满足治理约束。"""


class ResearchDecisionState(str, Enum):
    DRAFT = "draft"
    REJECTED = "rejected"
    RESEARCH_ONLY = "research_only"
    CANDIDATE = "candidate"
    PAPER_ELIGIBLE = "paper_eligible"
    SIM_ELIGIBLE = "sim_eligible"
    PRODUCTION_CANDIDATE = "production_candidate"


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _TEXT_RE.fullmatch(value.strip()) is None:
        raise ResearchDecisionError(f"{field_name} 必须是规范、非空标识符")
    return value.strip()


def _hash(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ResearchDecisionError(f"{field_name} 必须是小写 SHA-256")
    return value


def _utc_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ResearchDecisionError(f"{field_name} 必须是带时区 datetime")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class HumanResearchApproval:
    """一份明确的人类治理批准；构造记录不等于调用真实交易系统。"""

    approval_id: str
    approver_id: str
    approved_at: datetime
    target_state: ResearchDecisionState
    rationale: str
    approval_hash: str = field(init=False)

    def __post_init__(self) -> None:
        approval_id = _text(self.approval_id, "approval_id")
        approver_id = _text(self.approver_id, "approver_id")
        approved_at = _utc_datetime(self.approved_at, "approved_at")
        if not isinstance(self.target_state, ResearchDecisionState):
            raise ResearchDecisionError("target_state 必须是 ResearchDecisionState")
        if self.target_state in {ResearchDecisionState.DRAFT, ResearchDecisionState.REJECTED, ResearchDecisionState.RESEARCH_ONLY}:
            raise ResearchDecisionError("human approval 只能用于晋级到候选或更高研究状态")
        rationale = _text(self.rationale, "rationale")
        approval_hash = canonical_json_sha256(
            {
                "approval_id": approval_id,
                "approved_at": approved_at.isoformat(),
                "approver_id": approver_id,
                "format": "northstar.human-research-approval.v1",
                "rationale": rationale,
                "target_state": self.target_state.value,
            }
        )
        object.__setattr__(self, "approval_id", approval_id)
        object.__setattr__(self, "approver_id", approver_id)
        object.__setattr__(self, "approved_at", approved_at)
        object.__setattr__(self, "rationale", rationale)
        object.__setattr__(self, "approval_hash", approval_hash)


@dataclass(frozen=True, slots=True)
class ResearchDecisionEvidence:
    """候选研究决策的 hash-only 输入；不接受单个高 Sharpe 作为充分证据。"""

    experiment_spec_hash: str
    experiment_run_hash: str
    backtest_result_hash: str
    validation_report_hash: str
    admission_result_hash: str
    admission_status: str
    evidence_hash: str = field(init=False)
    _issuer: InitVar[object | None] = None

    @classmethod
    def from_validation_report(
        cls,
        *,
        experiment_spec_hash: str,
        experiment_run_hash: str,
        backtest_result_hash: str,
        validation_report: ValidationReport,
        admission_result: object,
    ) -> "ResearchDecisionEvidence":
        from northstar_quant.research.validation.admission import ResearchAdmissionResult

        if not isinstance(validation_report, ValidationReport):
            raise ResearchDecisionError("validation_report 必须是 ValidationReport")
        if not isinstance(admission_result, ResearchAdmissionResult):
            raise ResearchDecisionError("admission_result 必须是 ResearchAdmissionResult")
        if (
            experiment_spec_hash != validation_report.evidence.experiment_spec_hash
            or experiment_run_hash != validation_report.evidence.experiment_run_hash
            or backtest_result_hash != validation_report.evidence.backtest_result_hash
        ):
            raise ResearchDecisionError("decision evidence 必须精确绑定验证报告的实验与回测身份")
        admission_payload = admission_result.to_dict()
        return cls(
            experiment_spec_hash=experiment_spec_hash,
            experiment_run_hash=experiment_run_hash,
            backtest_result_hash=backtest_result_hash,
            validation_report_hash=validation_report.report_hash,
            admission_result_hash=canonical_json_sha256(admission_payload),
            admission_status=admission_result.status,
            _issuer=_EVIDENCE_ISSUER,
        )

    def __post_init__(self, _issuer: object | None) -> None:
        if _issuer is not _EVIDENCE_ISSUER:
            raise ResearchDecisionError(
                "ResearchDecisionEvidence 只能由验证报告与研究准入结果的受控工厂创建"
            )
        experiment_spec_hash = _hash(self.experiment_spec_hash, "experiment_spec_hash")
        experiment_run_hash = _hash(self.experiment_run_hash, "experiment_run_hash")
        backtest_result_hash = _hash(self.backtest_result_hash, "backtest_result_hash")
        validation_report_hash = _hash(self.validation_report_hash, "validation_report_hash")
        admission_result_hash = _hash(self.admission_result_hash, "admission_result_hash")
        if self.admission_status not in {"PASS", "NOT_ELIGIBLE", "INSUFFICIENT_EVIDENCE", "NOT_APPLICABLE", "NOT_CONFIGURED"}:
            raise ResearchDecisionError("admission_status 不受支持")
        evidence_hash = canonical_json_sha256(
            {
                "admission_status": self.admission_status,
                "admission_result_hash": admission_result_hash,
                "backtest_result_hash": backtest_result_hash,
                "experiment_run_hash": experiment_run_hash,
                "experiment_spec_hash": experiment_spec_hash,
                "format": "northstar.research-decision-evidence.v1",
                "validation_report_hash": validation_report_hash,
            }
        )
        object.__setattr__(self, "experiment_spec_hash", experiment_spec_hash)
        object.__setattr__(self, "experiment_run_hash", experiment_run_hash)
        object.__setattr__(self, "backtest_result_hash", backtest_result_hash)
        object.__setattr__(self, "validation_report_hash", validation_report_hash)
        object.__setattr__(self, "admission_result_hash", admission_result_hash)
        object.__setattr__(self, "evidence_hash", evidence_hash)


_ALLOWED_TRANSITIONS: dict[ResearchDecisionState, frozenset[ResearchDecisionState]] = {
    ResearchDecisionState.DRAFT: frozenset({ResearchDecisionState.REJECTED, ResearchDecisionState.RESEARCH_ONLY}),
    ResearchDecisionState.RESEARCH_ONLY: frozenset({ResearchDecisionState.REJECTED, ResearchDecisionState.CANDIDATE}),
    ResearchDecisionState.CANDIDATE: frozenset({ResearchDecisionState.REJECTED, ResearchDecisionState.PAPER_ELIGIBLE}),
    ResearchDecisionState.PAPER_ELIGIBLE: frozenset({ResearchDecisionState.REJECTED, ResearchDecisionState.SIM_ELIGIBLE}),
    ResearchDecisionState.SIM_ELIGIBLE: frozenset({ResearchDecisionState.REJECTED, ResearchDecisionState.PRODUCTION_CANDIDATE}),
    ResearchDecisionState.REJECTED: frozenset(),
    ResearchDecisionState.PRODUCTION_CANDIDATE: frozenset({ResearchDecisionState.REJECTED}),
}


@dataclass(frozen=True, slots=True)
class ResearchDecision:
    """一个不可变状态快照；转移必须经 :meth:`transition` 创建新对象。"""

    decision_id: str
    state: ResearchDecisionState
    evidence: ResearchDecisionEvidence | None
    predecessor_hash: str | None = None
    approval: HumanResearchApproval | None = None
    decision_hash: str = field(init=False)

    @classmethod
    def draft(cls, *, decision_id: str) -> "ResearchDecision":
        return cls(decision_id=decision_id, state=ResearchDecisionState.DRAFT, evidence=None)

    def __post_init__(self) -> None:
        decision_id = _text(self.decision_id, "decision_id")
        if not isinstance(self.state, ResearchDecisionState):
            raise ResearchDecisionError("state 必须是 ResearchDecisionState")
        if self.evidence is not None and not isinstance(self.evidence, ResearchDecisionEvidence):
            raise ResearchDecisionError("evidence 必须是 ResearchDecisionEvidence 或 None")
        predecessor = _hash(self.predecessor_hash, "predecessor_hash") if self.predecessor_hash is not None else None
        if self.approval is not None and not isinstance(self.approval, HumanResearchApproval):
            raise ResearchDecisionError("approval 必须是 HumanResearchApproval 或 None")
        requires_evidence = self.state in {
            ResearchDecisionState.CANDIDATE,
            ResearchDecisionState.PAPER_ELIGIBLE,
            ResearchDecisionState.SIM_ELIGIBLE,
            ResearchDecisionState.PRODUCTION_CANDIDATE,
        }
        if requires_evidence and self.evidence is None:
            raise ResearchDecisionError("候选及更高研究状态必须绑定完整 ResearchDecisionEvidence")
        if self.state is ResearchDecisionState.DRAFT and (self.evidence is not None or predecessor is not None or self.approval is not None):
            raise ResearchDecisionError("DRAFT 只能是无证据、无前驱、无批准的初始快照")
        if self.approval is not None and self.approval.target_state is not self.state:
            raise ResearchDecisionError("approval.target_state 必须与 decision.state 精确一致")
        if requires_evidence:
            if self.approval is None:
                raise ResearchDecisionError("候选及更高研究状态必须有显式人类批准")
            if self.evidence is None or self.evidence.admission_status != "PASS":
                raise ResearchDecisionError("候选及更高研究状态必须通过完整研究准入")
        decision_hash = canonical_json_sha256(
            {
                "approval_hash": self.approval.approval_hash if self.approval else None,
                "decision_id": decision_id,
                "evidence_hash": self.evidence.evidence_hash if self.evidence else None,
                "format": "northstar.research-decision.v1",
                "predecessor_hash": predecessor,
                "state": self.state.value,
            }
        )
        object.__setattr__(self, "decision_id", decision_id)
        object.__setattr__(self, "predecessor_hash", predecessor)
        object.__setattr__(self, "decision_hash", decision_hash)

    @property
    def eligible_for_trading(self) -> bool:
        """Research state 永远不能直接授予交易资格。"""

        return False

    def transition(
        self,
        *,
        target_state: ResearchDecisionState,
        evidence: ResearchDecisionEvidence | None = None,
        approval: HumanResearchApproval | None = None,
    ) -> "ResearchDecision":
        if not isinstance(target_state, ResearchDecisionState):
            raise ResearchDecisionError("target_state 必须是 ResearchDecisionState")
        if target_state not in _ALLOWED_TRANSITIONS[self.state]:
            raise ResearchDecisionError(f"不允许从 {self.state.value} 转移到 {target_state.value}")
        next_evidence = evidence if evidence is not None else self.evidence
        if target_state in {
            ResearchDecisionState.CANDIDATE,
            ResearchDecisionState.PAPER_ELIGIBLE,
            ResearchDecisionState.SIM_ELIGIBLE,
            ResearchDecisionState.PRODUCTION_CANDIDATE,
        }:
            if approval is None:
                raise ResearchDecisionError("晋级研究状态必须提供显式人类批准")
            if approval.target_state is not target_state:
                raise ResearchDecisionError("approval.target_state 与目标状态不一致")
        elif approval is not None:
            raise ResearchDecisionError("REJECTED/RESEARCH_ONLY 转移不得附加晋级批准")
        return ResearchDecision(
            decision_id=self.decision_id,
            state=target_state,
            evidence=next_evidence,
            predecessor_hash=self.decision_hash,
            approval=approval,
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "format": "northstar.research-decision.v1",
            "decision_id": self.decision_id,
            "state": self.state.value,
            "predecessor_hash": self.predecessor_hash,
            "evidence_hash": self.evidence.evidence_hash if self.evidence else None,
            "approval_hash": self.approval.approval_hash if self.approval else None,
            "eligible_for_trading": False,
            "decision_hash": self.decision_hash,
        }


__all__ = [
    "HumanResearchApproval",
    "ResearchDecision",
    "ResearchDecisionError",
    "ResearchDecisionEvidence",
    "ResearchDecisionState",
]

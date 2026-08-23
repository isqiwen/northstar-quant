"""P8-WP03 manual, auditable Research-to-StrategyTarget activation.

This is the only composition boundary that may inspect P2 research evidence
while constructing a P3 ``StrategyTarget``.  It is deliberately pure and
non-executable: issuing an activation receipt neither approves a portfolio nor
creates an execution plan, broker order, or trading authority.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from datetime import UTC, datetime
import re
from typing import Literal

from northstar_quant.data.artifacts.fingerprints import canonical_json_sha256
from northstar_quant.portfolio_risk.portfolio.targets import (
    StrategyTarget,
    StrategyTargetActivationRef,
    TargetPosition,
)
from northstar_quant.research.experiments.models import (
    STATIC_REPRODUCIBILITY_SELECTION_MODE,
    ExperimentRun,
    ExperimentRunStatus,
    ExperimentSpec,
    StrategyVersionReference,
)
from northstar_quant.research.reports import ResearchCard
from northstar_quant.research.validation.framework import (
    ResearchInputEvidenceKind,
    ResearchValidationEvidence,
    ValidationReport,
)
from northstar_quant.research.validation.research_decision import (
    HumanResearchApproval,
    ResearchDecisionEvidence,
    ResearchDecisionState,
)


__all__ = [
    "HumanStrategyTargetActivationApproval",
    "ResearchStrategyActivationError",
    "ResearchStrategyActivationReceipt",
    "ResearchStrategyActivationRequest",
    "ResearchStrategyTargetActivator",
    "StrategyTargetProposal",
]


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_RECEIPT_ISSUER = object()


class ResearchStrategyActivationError(ValueError):
    """Raised when a manual Research-to-StrategyTarget activation is unsafe."""


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value.strip()) is None:
        raise ResearchStrategyActivationError(f"{field_name} must be a non-empty identifier")
    return value.strip()


def _hash(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ResearchStrategyActivationError(f"{field_name} must be a lowercase SHA-256")
    return value


def _time(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ResearchStrategyActivationError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _rationale(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\x00" in value
        or "\r" in value
        or "\n" in value
        or len(value) > 512
    ):
        raise ResearchStrategyActivationError(
            f"{field_name} must be non-empty, single-line text of at most 512 characters"
        )
    return value.strip()


def _positions(value: object) -> tuple[TargetPosition, ...]:
    if not isinstance(value, tuple) or not value or not all(type(item) is TargetPosition for item in value):
        raise ResearchStrategyActivationError("positions must be a non-empty TargetPosition tuple")
    positions = tuple(sorted(value, key=lambda item: item.instrument_id))
    if len({item.instrument_id for item in positions}) != len(positions):
        raise ResearchStrategyActivationError("positions cannot contain duplicate instrument_id")
    return positions


def _hashes(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ResearchStrategyActivationError(f"{field_name} must be a tuple of SHA-256 hashes")
    hashes = tuple(sorted(_hash(item, field_name) for item in value))
    if not hashes or len(set(hashes)) != len(hashes):
        raise ResearchStrategyActivationError(f"{field_name} must be non-empty and unique")
    return hashes


@dataclass(frozen=True, slots=True)
class StrategyTargetProposal:
    """An immutable target snapshot prepared for a named human approval."""

    target_id: str
    source_strategy_id: str
    source_strategy_version: str
    generated_at: datetime
    effective_at: datetime
    expires_at: datetime
    positions: tuple[TargetPosition, ...]
    proposal_hash: str = field(init=False)

    def __post_init__(self) -> None:
        target_id = _identifier(self.target_id, "target_id")
        strategy_id = _identifier(self.source_strategy_id, "source_strategy_id")
        strategy_version = _identifier(self.source_strategy_version, "source_strategy_version")
        generated_at = _time(self.generated_at, "generated_at")
        effective_at = _time(self.effective_at, "effective_at")
        expires_at = _time(self.expires_at, "expires_at")
        if effective_at < generated_at:
            raise ResearchStrategyActivationError("effective_at cannot precede generated_at")
        if expires_at <= effective_at:
            raise ResearchStrategyActivationError("expires_at must be later than effective_at")
        positions = _positions(self.positions)
        proposal_hash = canonical_json_sha256(
            {
                "format": "northstar.strategy-target-proposal.v1",
                "target_id": target_id,
                "source_strategy_id": strategy_id,
                "source_strategy_version": strategy_version,
                "generated_at": generated_at.isoformat(),
                "effective_at": effective_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "positions": [item.as_mapping() for item in positions],
            }
        )
        object.__setattr__(self, "target_id", target_id)
        object.__setattr__(self, "source_strategy_id", strategy_id)
        object.__setattr__(self, "source_strategy_version", strategy_version)
        object.__setattr__(self, "generated_at", generated_at)
        object.__setattr__(self, "effective_at", effective_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "positions", positions)
        object.__setattr__(self, "proposal_hash", proposal_hash)

    def as_mapping(self) -> dict[str, object]:
        return {
            "format": "northstar.strategy-target-proposal.v1",
            "target_id": self.target_id,
            "source_strategy": {
                "id": self.source_strategy_id,
                "version": self.source_strategy_version,
            },
            "generated_at": self.generated_at.isoformat(),
            "effective_at": self.effective_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "positions": [item.as_mapping() for item in self.positions],
            "proposal_hash": self.proposal_hash,
        }


@dataclass(frozen=True, slots=True)
class HumanStrategyTargetActivationApproval:
    """A separate, named approval of one exact Research-backed target proposal."""

    activation_id: str
    approver_id: str
    approved_at: datetime
    target_proposal_hash: str
    research_card_hash: str
    research_decision_hash: str
    experiment_spec_hash: str
    strategy_version_hash: str
    rationale: str
    approval_hash: str = field(init=False)

    def __post_init__(self) -> None:
        activation_id = _identifier(self.activation_id, "activation_id")
        approver_id = _identifier(self.approver_id, "approver_id")
        approved_at = _time(self.approved_at, "approved_at")
        target_proposal_hash = _hash(self.target_proposal_hash, "target_proposal_hash")
        research_card_hash = _hash(self.research_card_hash, "research_card_hash")
        research_decision_hash = _hash(self.research_decision_hash, "research_decision_hash")
        experiment_spec_hash = _hash(self.experiment_spec_hash, "experiment_spec_hash")
        strategy_version_hash = _hash(self.strategy_version_hash, "strategy_version_hash")
        rationale = _rationale(self.rationale, "rationale")
        approval_hash = canonical_json_sha256(
            {
                "activation_id": activation_id,
                "approved_at": approved_at.isoformat(),
                "approver_id": approver_id,
                "experiment_spec_hash": experiment_spec_hash,
                "format": "northstar.human-strategy-target-activation-approval.v1",
                "rationale": rationale,
                "research_card_hash": research_card_hash,
                "research_decision_hash": research_decision_hash,
                "strategy_version_hash": strategy_version_hash,
                "target_proposal_hash": target_proposal_hash,
            }
        )
        object.__setattr__(self, "activation_id", activation_id)
        object.__setattr__(self, "approver_id", approver_id)
        object.__setattr__(self, "approved_at", approved_at)
        object.__setattr__(self, "target_proposal_hash", target_proposal_hash)
        object.__setattr__(self, "research_card_hash", research_card_hash)
        object.__setattr__(self, "research_decision_hash", research_decision_hash)
        object.__setattr__(self, "experiment_spec_hash", experiment_spec_hash)
        object.__setattr__(self, "strategy_version_hash", strategy_version_hash)
        object.__setattr__(self, "rationale", rationale)
        object.__setattr__(self, "approval_hash", approval_hash)

    def as_mapping(self) -> dict[str, object]:
        return {
            "format": "northstar.human-strategy-target-activation-approval.v1",
            "activation_id": self.activation_id,
            "approver_id": self.approver_id,
            "approved_at": self.approved_at.isoformat(),
            "target_proposal_hash": self.target_proposal_hash,
            "research_card_hash": self.research_card_hash,
            "research_decision_hash": self.research_decision_hash,
            "experiment_spec_hash": self.experiment_spec_hash,
            "strategy_version_hash": self.strategy_version_hash,
            "rationale": self.rationale,
            "approval_hash": self.approval_hash,
        }


@dataclass(frozen=True, slots=True)
class ResearchStrategyActivationRequest:
    """All immutable P2 and manual inputs needed to activate exactly one target."""

    research_card: ResearchCard
    experiment_spec: ExperimentSpec
    experiment_run: ExperimentRun
    target_proposal: StrategyTargetProposal
    activation_approval: HumanStrategyTargetActivationApproval

    def __post_init__(self) -> None:
        if type(self.research_card) is not ResearchCard:
            raise ResearchStrategyActivationError("research_card must be a ResearchCard")
        if type(self.experiment_spec) is not ExperimentSpec:
            raise ResearchStrategyActivationError("experiment_spec must be an ExperimentSpec")
        if type(self.experiment_run) is not ExperimentRun:
            raise ResearchStrategyActivationError("experiment_run must be an ExperimentRun")
        if type(self.target_proposal) is not StrategyTargetProposal:
            raise ResearchStrategyActivationError("target_proposal must be a StrategyTargetProposal")
        if type(self.activation_approval) is not HumanStrategyTargetActivationApproval:
            raise ResearchStrategyActivationError(
                "activation_approval must be a HumanStrategyTargetActivationApproval"
            )


def _activation_payload(
    *,
    target_proposal: StrategyTargetProposal,
    activation_approval: HumanStrategyTargetActivationApproval,
    research_card_id: str,
    research_card_hash: str,
    research_decision_id: str,
    research_decision_hash: str,
    research_decision_evidence_hash: str,
    research_approval_hash: str,
    research_approved_at: datetime,
    validation_report_hash: str,
    experiment_id: str,
    experiment_spec_hash: str,
    experiment_run_id: str,
    experiment_run_hash: str,
    backtest_result_hash: str,
    dataset_version_hashes: tuple[str, ...],
    feature_version_hashes: tuple[str, ...],
    strategy_id: str,
    strategy_version: str,
    strategy_version_hash: str,
    strategy_spec_hash: str,
    strategy_implementation_hash: str,
    strategy_code_revision: str,
    input_as_of: datetime,
    selection_mode: str,
    decision_time_safe: bool,
) -> dict[str, object]:
    return {
        "activation_approval_hash": activation_approval.approval_hash,
        "activation_id": activation_approval.activation_id,
        "backtest_result_hash": backtest_result_hash,
        "dataset_version_hashes": list(dataset_version_hashes),
        "decision_time_safe": decision_time_safe,
        "experiment": {
            "id": experiment_id,
            "run_hash": experiment_run_hash,
            "run_id": experiment_run_id,
            "spec_hash": experiment_spec_hash,
        },
        "feature_version_hashes": list(feature_version_hashes),
        "format": "northstar.research-strategy-activation-receipt.v1",
        "input_as_of": input_as_of.isoformat(),
        "research": {
            "candidate_approval_hash": research_approval_hash,
            "candidate_approved_at": research_approved_at.isoformat(),
            "card_hash": research_card_hash,
            "card_id": research_card_id,
            "decision_evidence_hash": research_decision_evidence_hash,
            "decision_hash": research_decision_hash,
            "decision_id": research_decision_id,
            "validation_report_hash": validation_report_hash,
        },
        "selection_mode": selection_mode,
        "strategy": {
            "code_revision": strategy_code_revision,
            "id": strategy_id,
            "implementation_hash": strategy_implementation_hash,
            "spec_hash": strategy_spec_hash,
            "version": strategy_version,
            "version_hash": strategy_version_hash,
        },
        "target_proposal_hash": target_proposal.proposal_hash,
    }


@dataclass(frozen=True, slots=True)
class ResearchStrategyActivationReceipt:
    """Hash-only audit receipt joining a P2 candidate to a P3 StrategyTarget.

    The receipt preserves P2's static-reproducibility/PIT status verbatim.  It
    has no execution authority and is intentionally not a broker-facing DTO.
    The private issuer prevents ordinary public construction only; it is not an
    authorization or trusted-storage boundary.  A future execution preflight
    must replay the underlying evidence rather than trust a receipt instance.
    """

    target_proposal: StrategyTargetProposal
    strategy_target: StrategyTarget
    activation_approval: HumanStrategyTargetActivationApproval
    research_card_id: str
    research_card_hash: str
    research_decision_id: str
    research_decision_hash: str
    research_decision_evidence_hash: str
    research_approval_hash: str
    research_approved_at: datetime
    validation_report_hash: str
    experiment_id: str
    experiment_spec_hash: str
    experiment_run_id: str
    experiment_run_hash: str
    backtest_result_hash: str
    dataset_version_hashes: tuple[str, ...]
    feature_version_hashes: tuple[str, ...]
    strategy_id: str
    strategy_version: str
    strategy_version_hash: str
    strategy_spec_hash: str
    strategy_implementation_hash: str
    strategy_code_revision: str
    input_as_of: datetime
    selection_mode: str
    decision_time_safe: bool
    activation_hash: str = field(init=False)
    eligible_for_trading: Literal[False] = field(default=False, init=False)
    _issuer: InitVar[object | None] = None

    def __post_init__(self, _issuer: object | None) -> None:
        if _issuer is not _RECEIPT_ISSUER:
            raise ResearchStrategyActivationError(
                "ResearchStrategyActivationReceipt can only be issued by the manual activator"
            )
        if type(self.target_proposal) is not StrategyTargetProposal:
            raise ResearchStrategyActivationError("target_proposal must be a StrategyTargetProposal")
        if type(self.strategy_target) is not StrategyTarget:
            raise ResearchStrategyActivationError("strategy_target must be a StrategyTarget")
        if type(self.activation_approval) is not HumanStrategyTargetActivationApproval:
            raise ResearchStrategyActivationError(
                "activation_approval must be a HumanStrategyTargetActivationApproval"
            )
        proposal = _replay_proposal(self.target_proposal)
        approval = _replay_activation_approval(self.activation_approval)
        target = _replay_target(self.strategy_target)
        research_card_id = _identifier(self.research_card_id, "research_card_id")
        research_card_hash = _hash(self.research_card_hash, "research_card_hash")
        research_decision_id = _identifier(self.research_decision_id, "research_decision_id")
        research_decision_hash = _hash(self.research_decision_hash, "research_decision_hash")
        research_decision_evidence_hash = _hash(
            self.research_decision_evidence_hash,
            "research_decision_evidence_hash",
        )
        research_approval_hash = _hash(self.research_approval_hash, "research_approval_hash")
        research_approved_at = _time(self.research_approved_at, "research_approved_at")
        validation_report_hash = _hash(self.validation_report_hash, "validation_report_hash")
        experiment_id = _identifier(self.experiment_id, "experiment_id")
        experiment_spec_hash = _hash(self.experiment_spec_hash, "experiment_spec_hash")
        experiment_run_id = _identifier(self.experiment_run_id, "experiment_run_id")
        experiment_run_hash = _hash(self.experiment_run_hash, "experiment_run_hash")
        backtest_result_hash = _hash(self.backtest_result_hash, "backtest_result_hash")
        dataset_version_hashes = _hashes(self.dataset_version_hashes, "dataset_version_hashes")
        feature_version_hashes = _hashes(self.feature_version_hashes, "feature_version_hashes")
        strategy_id = _identifier(self.strategy_id, "strategy_id")
        strategy_version = _identifier(self.strategy_version, "strategy_version")
        strategy_version_hash = _hash(self.strategy_version_hash, "strategy_version_hash")
        strategy_spec_hash = _hash(self.strategy_spec_hash, "strategy_spec_hash")
        strategy_implementation_hash = _hash(
            self.strategy_implementation_hash,
            "strategy_implementation_hash",
        )
        strategy_code_revision = _rationale(self.strategy_code_revision, "strategy_code_revision")
        input_as_of = _time(self.input_as_of, "input_as_of")
        if self.selection_mode != STATIC_REPRODUCIBILITY_SELECTION_MODE:
            raise ResearchStrategyActivationError("selection_mode must preserve static reproducibility")
        if self.decision_time_safe is not False:
            raise ResearchStrategyActivationError("decision_time_safe must remain False")
        if (
            approval.target_proposal_hash != proposal.proposal_hash
            or approval.research_card_hash != research_card_hash
            or approval.research_decision_hash != research_decision_hash
            or approval.experiment_spec_hash != experiment_spec_hash
            or approval.strategy_version_hash != strategy_version_hash
        ):
            raise ResearchStrategyActivationError("manual approval must bind the exact receipt identities")
        if (
            target.target_id != proposal.target_id
            or target.source_strategy_id != proposal.source_strategy_id
            or target.source_strategy_version != proposal.source_strategy_version
            or target.generated_at != proposal.generated_at
            or target.effective_at != proposal.effective_at
            or target.expires_at != proposal.expires_at
            or target.positions != proposal.positions
        ):
            raise ResearchStrategyActivationError("strategy target must exactly match its approved proposal")
        if (
            target.activation.activation_id != approval.activation_id
            or target.activation.approved_at != approval.approved_at
        ):
            raise ResearchStrategyActivationError("strategy target activation must bind the manual approval")
        if target.source_strategy_id != strategy_id or target.source_strategy_version != strategy_version:
            raise ResearchStrategyActivationError("strategy target must match the frozen strategy version")
        if not (
            input_as_of
            <= research_approved_at
            <= target.generated_at
            <= approval.approved_at
            < target.effective_at
        ):
            raise ResearchStrategyActivationError("activation times must preserve research and approval order")
        activation_hash = canonical_json_sha256(
            _activation_payload(
                target_proposal=proposal,
                activation_approval=approval,
                research_card_id=research_card_id,
                research_card_hash=research_card_hash,
                research_decision_id=research_decision_id,
                research_decision_hash=research_decision_hash,
                research_decision_evidence_hash=research_decision_evidence_hash,
                research_approval_hash=research_approval_hash,
                research_approved_at=research_approved_at,
                validation_report_hash=validation_report_hash,
                experiment_id=experiment_id,
                experiment_spec_hash=experiment_spec_hash,
                experiment_run_id=experiment_run_id,
                experiment_run_hash=experiment_run_hash,
                backtest_result_hash=backtest_result_hash,
                dataset_version_hashes=dataset_version_hashes,
                feature_version_hashes=feature_version_hashes,
                strategy_id=strategy_id,
                strategy_version=strategy_version,
                strategy_version_hash=strategy_version_hash,
                strategy_spec_hash=strategy_spec_hash,
                strategy_implementation_hash=strategy_implementation_hash,
                strategy_code_revision=strategy_code_revision,
                input_as_of=input_as_of,
                selection_mode=self.selection_mode,
                decision_time_safe=False,
            )
        )
        if target.activation.activation_hash != activation_hash:
            raise ResearchStrategyActivationError("strategy target activation hash does not match receipt")
        object.__setattr__(self, "target_proposal", proposal)
        object.__setattr__(self, "strategy_target", target)
        object.__setattr__(self, "activation_approval", approval)
        object.__setattr__(self, "research_card_id", research_card_id)
        object.__setattr__(self, "research_card_hash", research_card_hash)
        object.__setattr__(self, "research_decision_id", research_decision_id)
        object.__setattr__(self, "research_decision_hash", research_decision_hash)
        object.__setattr__(self, "research_decision_evidence_hash", research_decision_evidence_hash)
        object.__setattr__(self, "research_approval_hash", research_approval_hash)
        object.__setattr__(self, "research_approved_at", research_approved_at)
        object.__setattr__(self, "validation_report_hash", validation_report_hash)
        object.__setattr__(self, "experiment_id", experiment_id)
        object.__setattr__(self, "experiment_spec_hash", experiment_spec_hash)
        object.__setattr__(self, "experiment_run_id", experiment_run_id)
        object.__setattr__(self, "experiment_run_hash", experiment_run_hash)
        object.__setattr__(self, "backtest_result_hash", backtest_result_hash)
        object.__setattr__(self, "dataset_version_hashes", dataset_version_hashes)
        object.__setattr__(self, "feature_version_hashes", feature_version_hashes)
        object.__setattr__(self, "strategy_id", strategy_id)
        object.__setattr__(self, "strategy_version", strategy_version)
        object.__setattr__(self, "strategy_version_hash", strategy_version_hash)
        object.__setattr__(self, "strategy_spec_hash", strategy_spec_hash)
        object.__setattr__(self, "strategy_implementation_hash", strategy_implementation_hash)
        object.__setattr__(self, "strategy_code_revision", strategy_code_revision)
        object.__setattr__(self, "input_as_of", input_as_of)
        object.__setattr__(self, "activation_hash", activation_hash)

    def as_mapping(self) -> dict[str, object]:
        return {
            "format": "northstar.research-strategy-activation-receipt.v1",
            "activation": {
                "approval": self.activation_approval.as_mapping(),
                "activation_hash": self.activation_hash,
            },
            "research": {
                "card_id": self.research_card_id,
                "card_hash": self.research_card_hash,
                "decision_id": self.research_decision_id,
                "decision_hash": self.research_decision_hash,
                "decision_evidence_hash": self.research_decision_evidence_hash,
                "candidate_approval_hash": self.research_approval_hash,
                "candidate_approved_at": self.research_approved_at.isoformat(),
                "validation_report_hash": self.validation_report_hash,
                "dataset_version_hashes": list(self.dataset_version_hashes),
                "feature_version_hashes": list(self.feature_version_hashes),
            },
            "experiment": {
                "id": self.experiment_id,
                "spec_hash": self.experiment_spec_hash,
                "run_id": self.experiment_run_id,
                "run_hash": self.experiment_run_hash,
                "backtest_result_hash": self.backtest_result_hash,
                "input_as_of": self.input_as_of.isoformat(),
                "selection_mode": self.selection_mode,
                "decision_time_safe": False,
            },
            "strategy": {
                "id": self.strategy_id,
                "version": self.strategy_version,
                "version_hash": self.strategy_version_hash,
                "spec_hash": self.strategy_spec_hash,
                "implementation_hash": self.strategy_implementation_hash,
                "code_revision": self.strategy_code_revision,
            },
            "target_proposal": self.target_proposal.as_mapping(),
            "strategy_target": self.strategy_target.as_mapping(),
            "eligible_for_trading": False,
        }


def _replay_proposal(value: StrategyTargetProposal) -> StrategyTargetProposal:
    replayed = StrategyTargetProposal(
        target_id=value.target_id,
        source_strategy_id=value.source_strategy_id,
        source_strategy_version=value.source_strategy_version,
        generated_at=value.generated_at,
        effective_at=value.effective_at,
        expires_at=value.expires_at,
        positions=value.positions,
    )
    if replayed.proposal_hash != value.proposal_hash:
        raise ResearchStrategyActivationError("target proposal integrity mismatch")
    return replayed


def _replay_activation_approval(
    value: HumanStrategyTargetActivationApproval,
) -> HumanStrategyTargetActivationApproval:
    replayed = HumanStrategyTargetActivationApproval(
        activation_id=value.activation_id,
        approver_id=value.approver_id,
        approved_at=value.approved_at,
        target_proposal_hash=value.target_proposal_hash,
        research_card_hash=value.research_card_hash,
        research_decision_hash=value.research_decision_hash,
        experiment_spec_hash=value.experiment_spec_hash,
        strategy_version_hash=value.strategy_version_hash,
        rationale=value.rationale,
    )
    if replayed.approval_hash != value.approval_hash:
        raise ResearchStrategyActivationError("manual activation approval integrity mismatch")
    return replayed


def _replay_target(value: StrategyTarget) -> StrategyTarget:
    replayed = StrategyTarget(
        target_id=value.target_id,
        source_strategy_id=value.source_strategy_id,
        source_strategy_version=value.source_strategy_version,
        generated_at=value.generated_at,
        effective_at=value.effective_at,
        expires_at=value.expires_at,
        positions=value.positions,
        activation=value.activation,
    )
    if replayed.target_hash != value.target_hash:
        raise ResearchStrategyActivationError("strategy target integrity mismatch")
    return replayed


def _replay_card_and_validation(card: ResearchCard) -> ResearchValidationEvidence:
    validation = card.validation_report
    evidence = validation.evidence
    if evidence.input_kind is not ResearchInputEvidenceKind.DATASET_VERSIONED:
        raise ResearchStrategyActivationError(
            "fixture-only intelligence replay evidence cannot activate a StrategyTarget"
        )
    replayed_evidence = ResearchValidationEvidence(
        dataset_version_hashes=evidence.dataset_version_hashes,
        feature_version_hashes=evidence.feature_version_hashes,
        strategy_version_hash=evidence.strategy_version_hash,
        experiment_spec_hash=evidence.experiment_spec_hash,
        experiment_run_hash=evidence.experiment_run_hash,
        backtest_result_hash=evidence.backtest_result_hash,
        input_kind=evidence.input_kind,
        fixture_replay_binding_hash=evidence.fixture_replay_binding_hash,
        code_revision=evidence.code_revision,
    )
    if replayed_evidence.evidence_hash != evidence.evidence_hash:
        raise ResearchStrategyActivationError("research validation evidence integrity mismatch")
    replayed_validation = ValidationReport(
        evidence=replayed_evidence,
        split_hash=validation.split_hash,
        input_series_hash=validation.input_series_hash,
        stage_metrics=validation.stage_metrics,
        walk_forward_oos_metrics=validation.walk_forward_oos_metrics,
        rolling_metrics=validation.rolling_metrics,
        stress_metrics=validation.stress_metrics,
        parameter_neighbor_metrics=validation.parameter_neighbor_metrics,
        parameter_neighbor_hashes=validation.parameter_neighbor_hashes,
        regime_metrics=validation.regime_metrics,
        bootstrap=validation.bootstrap,
        monte_carlo=validation.monte_carlo,
    )
    if replayed_validation.report_hash != validation.report_hash:
        raise ResearchStrategyActivationError("validation report integrity mismatch")
    replayed_card = ResearchCard.create(
        card_id=card.card_id,
        run_manifest=card.run_manifest,
        validation_report=replayed_validation,
        decision=card.decision,
        product_contributions=card.product_contributions,
        limitations=card.limitations,
    )
    if replayed_card.card_hash != card.card_hash:
        raise ResearchStrategyActivationError("research card integrity mismatch")
    return replayed_evidence


def _replay_strategy(value: StrategyVersionReference) -> StrategyVersionReference:
    replayed = StrategyVersionReference(
        strategy_id=value.strategy_id,
        version=value.version,
        spec_hash=value.spec_hash,
        implementation_hash=value.implementation_hash,
        code_revision=value.code_revision,
    )
    if replayed.reference_hash != value.reference_hash:
        raise ResearchStrategyActivationError("strategy version integrity mismatch")
    return replayed


def _replay_experiment_spec(value: ExperimentSpec) -> ExperimentSpec:
    if (
        value.selection_mode != STATIC_REPRODUCIBILITY_SELECTION_MODE
        or value.decision_time_safe is not False
        or value.eligible_for_backtest is not False
        or value.eligible_for_admission is not False
    ):
        raise ResearchStrategyActivationError(
            "experiment spec cannot claim decision-safe or executable semantics"
        )
    strategy = _replay_strategy(value.strategy)
    replayed = ExperimentSpec(
        experiment_id=value.experiment_id,
        strategy=strategy,
        feature_inputs=value.feature_inputs,
        parameters_json=value.parameters_json,
        train_period=value.train_period,
        validation_period=value.validation_period,
        oos_period=value.oos_period,
        cost_model=value.cost_model,
        slippage_model=value.slippage_model,
        random_seed=value.random_seed,
        code_revision=value.code_revision,
        input_as_of=value.input_as_of,
    )
    if replayed.spec_hash != value.spec_hash:
        raise ResearchStrategyActivationError("experiment spec integrity mismatch")
    return replayed


def _replay_experiment_run(value: ExperimentRun) -> ExperimentRun:
    if (
        value.selection_mode != STATIC_REPRODUCIBILITY_SELECTION_MODE
        or value.decision_time_safe is not False
        or value.eligible_for_backtest is not False
        or value.eligible_for_admission is not False
    ):
        raise ResearchStrategyActivationError(
            "experiment run cannot claim decision-safe or executable semantics"
        )
    replayed = ExperimentRun(
        run_id=value.run_id,
        spec_hash=value.spec_hash,
        feature_input_hashes=value.feature_input_hashes,
        status=value.status,
        runner_id=value.runner_id,
        run_configuration_hash=value.run_configuration_hash,
        outcome_hash=value.outcome_hash,
        evidence_hashes=value.evidence_hashes,
    )
    if replayed.run_hash != value.run_hash:
        raise ResearchStrategyActivationError("experiment run integrity mismatch")
    return replayed


def _decision_evidence_hash(value: ResearchDecisionEvidence) -> str:
    if type(value) is not ResearchDecisionEvidence:
        raise ResearchStrategyActivationError("research decision evidence is incomplete")
    experiment_spec_hash = _hash(value.experiment_spec_hash, "decision.experiment_spec_hash")
    experiment_run_hash = _hash(value.experiment_run_hash, "decision.experiment_run_hash")
    backtest_result_hash = _hash(value.backtest_result_hash, "decision.backtest_result_hash")
    validation_report_hash = _hash(value.validation_report_hash, "decision.validation_report_hash")
    admission_result_hash = _hash(value.admission_result_hash, "decision.admission_result_hash")
    admission_status = value.admission_status
    evidence_hash = _hash(value.evidence_hash, "decision.evidence_hash")
    if admission_status != "PASS":
        raise ResearchStrategyActivationError("research decision evidence must have PASS admission")
    expected = canonical_json_sha256(
        {
            "admission_status": admission_status,
            "admission_result_hash": admission_result_hash,
            "backtest_result_hash": backtest_result_hash,
            "experiment_run_hash": experiment_run_hash,
            "experiment_spec_hash": experiment_spec_hash,
            "format": "northstar.research-decision-evidence.v1",
            "validation_report_hash": validation_report_hash,
        }
    )
    if expected != evidence_hash:
        raise ResearchStrategyActivationError("research decision evidence integrity mismatch")
    return evidence_hash


def _research_approval_hash(value: HumanResearchApproval) -> str:
    if type(value) is not HumanResearchApproval:
        raise ResearchStrategyActivationError("research approval is incomplete")
    approval_id = _identifier(value.approval_id, "research approval_id")
    approver_id = _identifier(value.approver_id, "research approver_id")
    approved_at = _time(value.approved_at, "research approved_at")
    target_state = value.target_state
    rationale = _identifier(value.rationale, "research rationale")
    approval_hash = _hash(value.approval_hash, "research approval_hash")
    if target_state is not ResearchDecisionState.CANDIDATE:
        raise ResearchStrategyActivationError("research approval must explicitly approve CANDIDATE")
    expected = canonical_json_sha256(
        {
            "approval_id": approval_id,
            "approved_at": approved_at.isoformat(),
            "approver_id": approver_id,
            "format": "northstar.human-research-approval.v1",
            "rationale": rationale,
            "target_state": ResearchDecisionState.CANDIDATE.value,
        }
    )
    if expected != approval_hash:
        raise ResearchStrategyActivationError("research approval integrity mismatch")
    return approval_hash


class ResearchStrategyTargetActivator:
    """Verify a manual activation request and issue one non-tradable receipt."""

    __slots__ = ()

    def activate(self, request: ResearchStrategyActivationRequest) -> ResearchStrategyActivationReceipt:
        """Activate one exact proposal only after every identity and PIT check passes."""

        if type(request) is not ResearchStrategyActivationRequest:
            raise ResearchStrategyActivationError(
                "request must be a ResearchStrategyActivationRequest"
            )
        card = request.research_card
        evidence = _replay_card_and_validation(card)
        spec = _replay_experiment_spec(request.experiment_spec)
        run = _replay_experiment_run(request.experiment_run)
        proposal = _replay_proposal(request.target_proposal)
        approval = _replay_activation_approval(request.activation_approval)
        decision = card.decision
        if decision.state is not ResearchDecisionState.CANDIDATE:
            raise ResearchStrategyActivationError("research decision must be exactly CANDIDATE")
        if decision.evidence is None or decision.approval is None:
            raise ResearchStrategyActivationError(
                "candidate research decision requires evidence and named approval"
            )
        decision_evidence_hash = _decision_evidence_hash(decision.evidence)
        research_approval_hash = _research_approval_hash(decision.approval)
        expected_decision_hash = canonical_json_sha256(
            {
                "approval_hash": research_approval_hash,
                "decision_id": _identifier(decision.decision_id, "research decision_id"),
                "evidence_hash": decision_evidence_hash,
                "format": "northstar.research-decision.v1",
                "predecessor_hash": (
                    _hash(decision.predecessor_hash, "research predecessor_hash")
                    if decision.predecessor_hash is not None
                    else None
                ),
                "state": ResearchDecisionState.CANDIDATE.value,
            }
        )
        if expected_decision_hash != decision.decision_hash:
            raise ResearchStrategyActivationError("research decision integrity mismatch")
        if (
            decision.evidence.experiment_spec_hash != evidence.experiment_spec_hash
            or decision.evidence.experiment_run_hash != evidence.experiment_run_hash
            or decision.evidence.backtest_result_hash != evidence.backtest_result_hash
            or decision.evidence.validation_report_hash != card.validation_report.report_hash
        ):
            raise ResearchStrategyActivationError(
                "research decision must bind the exact validation evidence chain"
            )
        if card.run_manifest.result.result_hash != evidence.backtest_result_hash:
            raise ResearchStrategyActivationError("research card backtest result does not match validation")
        if (
            spec.spec_hash != evidence.experiment_spec_hash
            or run.spec_hash != spec.spec_hash
            or run.run_hash != evidence.experiment_run_hash
            or run.status is not ExperimentRunStatus.RECORDED
        ):
            raise ResearchStrategyActivationError("experiment spec/run must exactly match the research card")
        if tuple(sorted(item.input_hash for item in spec.feature_inputs)) != run.feature_input_hashes:
            raise ResearchStrategyActivationError("experiment run feature inputs do not match experiment spec")
        if (
            spec.selection_mode != STATIC_REPRODUCIBILITY_SELECTION_MODE
            or run.selection_mode != STATIC_REPRODUCIBILITY_SELECTION_MODE
            or spec.decision_time_safe is not False
            or run.decision_time_safe is not False
        ):
            raise ResearchStrategyActivationError(
                "activation must preserve static, non-decision-safe research semantics"
            )
        dataset_version_hashes = _hashes(evidence.dataset_version_hashes, "dataset_version_hashes")
        feature_version_hashes = _hashes(evidence.feature_version_hashes, "feature_version_hashes")
        if spec.dataset_version_hashes != dataset_version_hashes:
            raise ResearchStrategyActivationError("experiment dataset versions do not match validation")
        if tuple(sorted(item.feature_version_hash for item in spec.feature_inputs)) != feature_version_hashes:
            raise ResearchStrategyActivationError("experiment feature versions do not match validation")
        strategy = _replay_strategy(spec.strategy)
        if (
            strategy.reference_hash != evidence.strategy_version_hash
            or strategy.code_revision != evidence.code_revision
            or spec.code_revision != strategy.code_revision
        ):
            raise ResearchStrategyActivationError("strategy version must exactly match validation evidence")
        if (
            proposal.source_strategy_id != strategy.strategy_id
            or proposal.source_strategy_version != strategy.version
        ):
            raise ResearchStrategyActivationError("target proposal must match the frozen strategy version")
        if (
            approval.target_proposal_hash != proposal.proposal_hash
            or approval.research_card_hash != card.card_hash
            or approval.research_decision_hash != decision.decision_hash
            or approval.experiment_spec_hash != spec.spec_hash
            or approval.strategy_version_hash != strategy.reference_hash
        ):
            raise ResearchStrategyActivationError("manual approval does not bind the exact activation")
        if any(item.available_at > decision.approval.approved_at for item in spec.feature_inputs):
            raise ResearchStrategyActivationError(
                "research feature input cannot be available after candidate approval"
            )
        if not (
            spec.input_as_of <= decision.approval.approved_at <= proposal.generated_at
            <= approval.approved_at < proposal.effective_at < proposal.expires_at
        ):
            raise ResearchStrategyActivationError("activation timestamps violate point-in-time order")
        activation_hash = canonical_json_sha256(
            _activation_payload(
                target_proposal=proposal,
                activation_approval=approval,
                research_card_id=card.card_id,
                research_card_hash=card.card_hash,
                research_decision_id=decision.decision_id,
                research_decision_hash=decision.decision_hash,
                research_decision_evidence_hash=decision_evidence_hash,
                research_approval_hash=research_approval_hash,
                research_approved_at=decision.approval.approved_at,
                validation_report_hash=card.validation_report.report_hash,
                experiment_id=spec.experiment_id,
                experiment_spec_hash=spec.spec_hash,
                experiment_run_id=run.run_id,
                experiment_run_hash=run.run_hash,
                backtest_result_hash=evidence.backtest_result_hash,
                dataset_version_hashes=dataset_version_hashes,
                feature_version_hashes=feature_version_hashes,
                strategy_id=strategy.strategy_id,
                strategy_version=strategy.version,
                strategy_version_hash=strategy.reference_hash,
                strategy_spec_hash=strategy.spec_hash,
                strategy_implementation_hash=strategy.implementation_hash,
                strategy_code_revision=strategy.code_revision,
                input_as_of=spec.input_as_of,
                selection_mode=spec.selection_mode,
                decision_time_safe=False,
            )
        )
        strategy_target = StrategyTarget(
            target_id=proposal.target_id,
            source_strategy_id=proposal.source_strategy_id,
            source_strategy_version=proposal.source_strategy_version,
            generated_at=proposal.generated_at,
            effective_at=proposal.effective_at,
            expires_at=proposal.expires_at,
            positions=proposal.positions,
            activation=StrategyTargetActivationRef(
                activation_id=approval.activation_id,
                activation_hash=activation_hash,
                approved_at=approval.approved_at,
            ),
        )
        return ResearchStrategyActivationReceipt(
            target_proposal=proposal,
            strategy_target=strategy_target,
            activation_approval=approval,
            research_card_id=card.card_id,
            research_card_hash=card.card_hash,
            research_decision_id=decision.decision_id,
            research_decision_hash=decision.decision_hash,
            research_decision_evidence_hash=decision_evidence_hash,
            research_approval_hash=research_approval_hash,
            research_approved_at=decision.approval.approved_at,
            validation_report_hash=card.validation_report.report_hash,
            experiment_id=spec.experiment_id,
            experiment_spec_hash=spec.spec_hash,
            experiment_run_id=run.run_id,
            experiment_run_hash=run.run_hash,
            backtest_result_hash=evidence.backtest_result_hash,
            dataset_version_hashes=dataset_version_hashes,
            feature_version_hashes=feature_version_hashes,
            strategy_id=strategy.strategy_id,
            strategy_version=strategy.version,
            strategy_version_hash=strategy.reference_hash,
            strategy_spec_hash=strategy.spec_hash,
            strategy_implementation_hash=strategy.implementation_hash,
            strategy_code_revision=strategy.code_revision,
            input_as_of=spec.input_as_of,
            selection_mode=spec.selection_mode,
            decision_time_safe=False,
            _issuer=_RECEIPT_ISSUER,
        )

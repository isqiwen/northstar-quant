"""Deep, research-only contracts for discovery selection and OOS release.

This module makes a deliberate domain separation:

``FactorCandidateProposal != DiscoveryResult != SelectionCommitment != OOSRelease``.

The discovery objects can contain only in-sample and validation evidence.  The
out-of-sample objects are constructed only by the trusted local composition
root after a selection commitment already exists.  None of these objects is a
strategy, portfolio target, execution plan, or trading authorization.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
import math

from northstar_quant.data.artifacts.fingerprints import (
    FingerprintError,
    canonical_json_sha256,
    require_sha256,
)
from northstar_quant.research.factor_mining.models import (
    FactorMiningCampaignSpec,
    FactorMiningError,
    FactorMiningSelectionPolicy,
    FactorMiningStageBoundaryMode,
)
from northstar_quant.research.validation.framework import ValidationStage


__all__ = [
    "CandidateDiscoveryDisposition",
    "FactorCandidateDiscoveryResult",
    "FactorDiscoveryStageCostResult",
    "FactorMiningDiscoveryResult",
    "FactorMiningOOSRelease",
    "FactorMiningOOSReleaseResult",
    "FactorMiningSelectionCommitment",
    "FactorMiningSelectionDisposition",
    "FactorMiningSelectionRecord",
    "FactorMiningStageEvidence",
    "select_discovery_candidates",
]


_REASON_CODE_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or value[0] not in "abcdefghijklmnopqrstuvwxyz":
        raise FactorMiningError(f"{field_name} must be a lower-case stable identifier")
    if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in value):
        raise FactorMiningError(f"{field_name} must be a lower-case stable identifier")
    return value


def _reason_code(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise FactorMiningError(f"{field_name} must be an upper-case stable reason code")
    if any(character not in _REASON_CODE_CHARS for character in value):
        raise FactorMiningError(f"{field_name} must be an upper-case stable reason code")
    return value


def _hash(value: object, field_name: str) -> str:
    try:
        return require_sha256(value, field_name=field_name)  # type: ignore[arg-type]
    except FingerprintError as exc:
        raise FactorMiningError(str(exc)) from exc


def _hashes(value: object, field_name: str, *, minimum: int = 1) -> tuple[str, ...]:
    if isinstance(value, str) or not isinstance(value, Iterable):
        raise FactorMiningError(f"{field_name} must be hashes, not a string")
    hashes = tuple(sorted(_hash(item, field_name) for item in value))
    if len(hashes) < minimum or len(hashes) != len(set(hashes)):
        raise FactorMiningError(f"{field_name} must contain unique hashes")
    return hashes


def _finite(value: object, field_name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FactorMiningError(f"{field_name} must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise FactorMiningError(f"{field_name} must be a finite number")
    if minimum is not None and normalized < minimum:
        raise FactorMiningError(f"{field_name} must be at least {minimum}")
    if maximum is not None and normalized > maximum:
        raise FactorMiningError(f"{field_name} must be at most {maximum}")
    return normalized


def _positive_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise FactorMiningError(f"{field_name} must be a positive integer")
    return value


def _nonnegative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FactorMiningError(f"{field_name} must be a non-negative integer")
    return value


class CandidateDiscoveryDisposition(str, Enum):
    """A candidate can be rejected before or after non-OOS discovery evaluation."""

    REJECTED_INPUT = "rejected_input"
    REJECTED_DISCOVERY = "rejected_discovery"
    DISCOVERY_EVALUATED = "discovery_evaluated"


@dataclass(frozen=True, slots=True)
class FactorDiscoveryStageCostResult:
    """One frozen cost scenario evaluated with a flat-start/forced-close stage."""

    cost_scenario_hash: str
    backtest_result_hash: str
    session_count: int
    total_return: float
    max_drawdown: float
    portfolio_turnover: float
    cost_result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        scenario_hash = _hash(self.cost_scenario_hash, "stage_cost.cost_scenario_hash")
        result_hash = _hash(self.backtest_result_hash, "stage_cost.backtest_result_hash")
        session_count = _positive_int(self.session_count, "stage_cost.session_count")
        total_return = _finite(self.total_return, "stage_cost.total_return")
        if total_return <= -1.0:
            raise FactorMiningError("stage_cost.total_return must exceed -1")
        max_drawdown = _finite(self.max_drawdown, "stage_cost.max_drawdown", minimum=-1.0, maximum=0.0)
        turnover = _finite(self.portfolio_turnover, "stage_cost.portfolio_turnover", minimum=0.0)
        cost_result_hash = canonical_json_sha256(
            {
                "backtest_result_hash": result_hash,
                "cost_scenario_hash": scenario_hash,
                "format": "northstar.factor-mining-stage-cost-result.v1",
                "max_drawdown": max_drawdown.hex(),
                "portfolio_turnover": turnover.hex(),
                "session_count": session_count,
                "total_return": total_return.hex(),
            }
        )
        object.__setattr__(self, "cost_scenario_hash", scenario_hash)
        object.__setattr__(self, "backtest_result_hash", result_hash)
        object.__setattr__(self, "session_count", session_count)
        object.__setattr__(self, "total_return", total_return)
        object.__setattr__(self, "max_drawdown", max_drawdown)
        object.__setattr__(self, "portfolio_turnover", turnover)
        object.__setattr__(self, "cost_result_hash", cost_result_hash)


@dataclass(frozen=True, slots=True)
class FactorMiningStageEvidence:
    """Hash-bound stage evidence without market data, features, or target weights."""

    campaign_id: str
    campaign_hash: str
    candidate_id: str
    candidate_hash: str
    factor_definition_hash: str
    pipeline_config_hash: str
    fold_id: str
    fold_hash: str
    stage: ValidationStage
    period_start: str
    period_end: str
    analysis_hash: str
    analysis_period_hashes: tuple[str, ...]
    outcome_hashes: tuple[str, ...]
    mean_rank_ic: float
    quantile_spread: float
    mean_factor_turnover: float
    positive_rank_ic_count: int
    purged_cross_boundary_outcome_count: int
    cost_results: tuple[FactorDiscoveryStageCostResult, ...]
    stage_boundary_mode: FactorMiningStageBoundaryMode
    stage_evidence_hash: str = field(init=False)

    def __post_init__(self) -> None:
        campaign_id = _identifier(self.campaign_id, "stage_evidence.campaign_id")
        campaign_hash = _hash(self.campaign_hash, "stage_evidence.campaign_hash")
        candidate_id = _identifier(self.candidate_id, "stage_evidence.candidate_id")
        candidate_hash = _hash(self.candidate_hash, "stage_evidence.candidate_hash")
        definition_hash = _hash(self.factor_definition_hash, "stage_evidence.factor_definition_hash")
        config_hash = _hash(self.pipeline_config_hash, "stage_evidence.pipeline_config_hash")
        fold_id = _identifier(self.fold_id, "stage_evidence.fold_id")
        fold_hash = _hash(self.fold_hash, "stage_evidence.fold_hash")
        if type(self.stage) is not ValidationStage:
            raise FactorMiningError("stage_evidence.stage must be ValidationStage")
        if not isinstance(self.period_start, str) or not isinstance(self.period_end, str):
            raise FactorMiningError("stage_evidence periods must be ISO date strings")
        try:
            period_start = date.fromisoformat(self.period_start)
            period_end = date.fromisoformat(self.period_end)
        except ValueError as exc:
            raise FactorMiningError("stage_evidence periods must be ISO date strings") from exc
        if period_end < period_start:
            raise FactorMiningError("stage_evidence period_end cannot precede period_start")
        analysis_hash = _hash(self.analysis_hash, "stage_evidence.analysis_hash")
        periods = _hashes(self.analysis_period_hashes, "stage_evidence.analysis_period_hashes")
        outcomes = _hashes(self.outcome_hashes, "stage_evidence.outcome_hashes")
        mean_rank_ic = _finite(self.mean_rank_ic, "stage_evidence.mean_rank_ic", minimum=-1.0, maximum=1.0)
        spread = _finite(self.quantile_spread, "stage_evidence.quantile_spread")
        factor_turnover = _finite(
            self.mean_factor_turnover,
            "stage_evidence.mean_factor_turnover",
            minimum=0.0,
        )
        positives = _nonnegative_int(
            self.positive_rank_ic_count,
            "stage_evidence.positive_rank_ic_count",
        )
        if positives > len(periods):
            raise FactorMiningError("stage_evidence positive rank-IC count exceeds periods")
        purged = _nonnegative_int(
            self.purged_cross_boundary_outcome_count,
            "stage_evidence.purged_cross_boundary_outcome_count",
        )
        cost_results = tuple(self.cost_results)
        if not cost_results or not all(type(item) is FactorDiscoveryStageCostResult for item in cost_results):
            raise FactorMiningError("stage_evidence.cost_results must contain exact cost results")
        if tuple(sorted(cost_results, key=lambda item: item.cost_scenario_hash)) != cost_results:
            raise FactorMiningError("stage_evidence.cost_results must be sorted by scenario hash")
        if len({item.cost_scenario_hash for item in cost_results}) != len(cost_results):
            raise FactorMiningError("stage_evidence.cost_results cannot contain duplicate scenarios")
        if type(self.stage_boundary_mode) is not FactorMiningStageBoundaryMode:
            raise FactorMiningError("stage_evidence.stage_boundary_mode must be FactorMiningStageBoundaryMode")
        stage_evidence_hash = canonical_json_sha256(
            {
                "analysis_hash": analysis_hash,
                "analysis_period_hashes": list(periods),
                "campaign_hash": campaign_hash,
                "candidate_hash": candidate_hash,
                "cost_result_hashes": [item.cost_result_hash for item in cost_results],
                "factor_definition_hash": definition_hash,
                "fold_hash": fold_hash,
                "fold_id": fold_id,
                "format": "northstar.factor-mining-stage-evidence.v1",
                "mean_factor_turnover": factor_turnover.hex(),
                "mean_rank_ic": mean_rank_ic.hex(),
                "outcome_hashes": list(outcomes),
                "period_end": period_end.isoformat(),
                "period_start": period_start.isoformat(),
                "pipeline_config_hash": config_hash,
                "positive_rank_ic_count": positives,
                "purged_cross_boundary_outcome_count": purged,
                "quantile_spread": spread.hex(),
                "stage": self.stage.value,
                "stage_boundary_mode": self.stage_boundary_mode.value,
            }
        )
        object.__setattr__(self, "campaign_id", campaign_id)
        object.__setattr__(self, "campaign_hash", campaign_hash)
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "candidate_hash", candidate_hash)
        object.__setattr__(self, "factor_definition_hash", definition_hash)
        object.__setattr__(self, "pipeline_config_hash", config_hash)
        object.__setattr__(self, "fold_id", fold_id)
        object.__setattr__(self, "fold_hash", fold_hash)
        object.__setattr__(self, "period_start", period_start.isoformat())
        object.__setattr__(self, "period_end", period_end.isoformat())
        object.__setattr__(self, "analysis_hash", analysis_hash)
        object.__setattr__(self, "analysis_period_hashes", periods)
        object.__setattr__(self, "outcome_hashes", outcomes)
        object.__setattr__(self, "mean_rank_ic", mean_rank_ic)
        object.__setattr__(self, "quantile_spread", spread)
        object.__setattr__(self, "mean_factor_turnover", factor_turnover)
        object.__setattr__(self, "positive_rank_ic_count", positives)
        object.__setattr__(self, "purged_cross_boundary_outcome_count", purged)
        object.__setattr__(self, "cost_results", cost_results)
        object.__setattr__(self, "stage_evidence_hash", stage_evidence_hash)


@dataclass(frozen=True, slots=True)
class FactorCandidateDiscoveryResult:
    """One candidate's non-OOS evaluation outcome.

    It intentionally cannot carry a run manifest, total backtest hash, or
    walk-forward/OOS evidence.  Those objects would couple an AI-facing
    discovery result to unsealed holdout information.
    """

    campaign_id: str
    campaign_hash: str
    candidate_id: str
    candidate_hash: str
    disposition: CandidateDiscoveryDisposition
    reason_code: str
    validation_hash: str
    factor_definition_hash: str | None = None
    pipeline_config_hash: str | None = None
    discovery_replay_hash: str | None = None
    stage_evidence: tuple[FactorMiningStageEvidence, ...] = ()
    discovery_hash: str = field(init=False)

    @property
    def research_only(self) -> bool:
        return True

    @property
    def candidate_admission_eligible(self) -> bool:
        return False

    @property
    def simnow_handoff_allowed(self) -> bool:
        return False

    def __post_init__(self) -> None:
        campaign_id = _identifier(self.campaign_id, "discovery.campaign_id")
        campaign_hash = _hash(self.campaign_hash, "discovery.campaign_hash")
        candidate_id = _identifier(self.candidate_id, "discovery.candidate_id")
        candidate_hash = _hash(self.candidate_hash, "discovery.candidate_hash")
        if type(self.disposition) is not CandidateDiscoveryDisposition:
            raise FactorMiningError("discovery.disposition must be CandidateDiscoveryDisposition")
        reason = _reason_code(self.reason_code, "discovery.reason_code")
        validation_hash = _hash(self.validation_hash, "discovery.validation_hash")
        optional = {
            "factor_definition_hash": self.factor_definition_hash,
            "pipeline_config_hash": self.pipeline_config_hash,
            "discovery_replay_hash": self.discovery_replay_hash,
        }
        normalized = {
            name: _hash(value, f"discovery.{name}") if value is not None else None
            for name, value in optional.items()
        }
        stages = tuple(self.stage_evidence)
        if stages and not all(type(item) is FactorMiningStageEvidence for item in stages):
            raise FactorMiningError("discovery.stage_evidence must contain exact stage records")
        if tuple(sorted(stages, key=lambda item: (item.fold_id, item.stage.value))) != stages:
            raise FactorMiningError("discovery.stage_evidence must be sorted by fold and stage")
        if len({(item.fold_id, item.stage) for item in stages}) != len(stages):
            raise FactorMiningError("discovery.stage_evidence cannot duplicate fold/stage")
        if any(item.stage is ValidationStage.OUT_OF_SAMPLE for item in stages):
            raise FactorMiningError("discovery.stage_evidence cannot contain out-of-sample evidence")
        if any(
            item.campaign_id != campaign_id
            or item.campaign_hash != campaign_hash
            or item.candidate_id != candidate_id
            or item.candidate_hash != candidate_hash
            for item in stages
        ):
            raise FactorMiningError("discovery stage evidence must bind the candidate and campaign")
        if self.disposition is CandidateDiscoveryDisposition.REJECTED_INPUT:
            if any(value is not None for value in normalized.values()) or stages:
                raise FactorMiningError("input rejection cannot carry discovery evidence")
        else:
            if any(value is None for value in normalized.values()):
                raise FactorMiningError("evaluated discovery requires replay identity")
            if self.disposition is CandidateDiscoveryDisposition.DISCOVERY_EVALUATED and not stages:
                raise FactorMiningError("evaluated discovery requires stage evidence")
        discovery_hash = canonical_json_sha256(
            {
                "campaign_hash": campaign_hash,
                "candidate_admission_eligible": False,
                "candidate_hash": candidate_hash,
                "discovery_replay_hash": normalized["discovery_replay_hash"],
                "disposition": self.disposition.value,
                "factor_definition_hash": normalized["factor_definition_hash"],
                "format": "northstar.factor-mining-discovery-result.v1",
                "pipeline_config_hash": normalized["pipeline_config_hash"],
                "reason_code": reason,
                "research_only": True,
                "simnow_handoff_allowed": False,
                "stage_evidence_hashes": [item.stage_evidence_hash for item in stages],
                "validation_hash": validation_hash,
            }
        )
        object.__setattr__(self, "campaign_id", campaign_id)
        object.__setattr__(self, "campaign_hash", campaign_hash)
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "candidate_hash", candidate_hash)
        object.__setattr__(self, "reason_code", reason)
        object.__setattr__(self, "validation_hash", validation_hash)
        for name, value in normalized.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "stage_evidence", stages)
        object.__setattr__(self, "discovery_hash", discovery_hash)


@dataclass(frozen=True, slots=True)
class FactorMiningDiscoveryResult:
    """The complete, AI-visible development-only result of one generator receipt."""

    campaign_id: str
    campaign_hash: str
    generation_receipt_hash: str
    selection_policy_hash: str
    results: tuple[FactorCandidateDiscoveryResult, ...]
    discovery_result_hash: str = field(init=False)

    @property
    def research_only(self) -> bool:
        return True

    @property
    def candidate_admission_eligible(self) -> bool:
        return False

    @property
    def simnow_handoff_allowed(self) -> bool:
        return False

    def __post_init__(self) -> None:
        campaign_id = _identifier(self.campaign_id, "discovery_batch.campaign_id")
        campaign_hash = _hash(self.campaign_hash, "discovery_batch.campaign_hash")
        receipt_hash = _hash(self.generation_receipt_hash, "discovery_batch.generation_receipt_hash")
        policy_hash = _hash(self.selection_policy_hash, "discovery_batch.selection_policy_hash")
        results = tuple(self.results)
        if not results or not all(type(item) is FactorCandidateDiscoveryResult for item in results):
            raise FactorMiningError("discovery_batch.results must contain exact discovery records")
        if tuple(sorted(results, key=lambda item: item.candidate_id)) != results:
            raise FactorMiningError("discovery_batch.results must be sorted by candidate_id")
        if len({item.candidate_id for item in results}) != len(results):
            raise FactorMiningError("discovery_batch.results cannot duplicate candidates")
        if any(item.campaign_id != campaign_id or item.campaign_hash != campaign_hash for item in results):
            raise FactorMiningError("discovery_batch results must bind this campaign")
        discovery_result_hash = canonical_json_sha256(
            {
                "campaign_hash": campaign_hash,
                "candidate_admission_eligible": False,
                "format": "northstar.factor-mining-discovery-batch.v1",
                "generation_receipt_hash": receipt_hash,
                "research_only": True,
                "result_hashes": [item.discovery_hash for item in results],
                "selection_policy_hash": policy_hash,
                "simnow_handoff_allowed": False,
            }
        )
        object.__setattr__(self, "campaign_id", campaign_id)
        object.__setattr__(self, "campaign_hash", campaign_hash)
        object.__setattr__(self, "generation_receipt_hash", receipt_hash)
        object.__setattr__(self, "selection_policy_hash", policy_hash)
        object.__setattr__(self, "results", results)
        object.__setattr__(self, "discovery_result_hash", discovery_result_hash)


class FactorMiningSelectionDisposition(str, Enum):
    """A commitment selects research evidence, never a tradable strategy."""

    REJECTED_INPUT_OR_EVIDENCE = "rejected_input_or_evidence"
    REJECTED_DISCOVERY_POLICY = "rejected_discovery_policy"
    NOT_SELECTED = "not_selected"
    SELECTED_FOR_OOS_RELEASE = "selected_for_oos_release"


@dataclass(frozen=True, slots=True)
class FactorMiningSelectionRecord:
    """A reason-coded inclusion/exclusion record within a frozen commitment."""

    candidate_id: str
    candidate_hash: str
    discovery_hash: str
    disposition: FactorMiningSelectionDisposition
    reason_code: str
    rank: int | None = None
    discovery_score: float | None = None
    record_hash: str = field(init=False)

    def __post_init__(self) -> None:
        candidate_id = _identifier(self.candidate_id, "selection_record.candidate_id")
        candidate_hash = _hash(self.candidate_hash, "selection_record.candidate_hash")
        discovery_hash = _hash(self.discovery_hash, "selection_record.discovery_hash")
        if type(self.disposition) is not FactorMiningSelectionDisposition:
            raise FactorMiningError("selection_record.disposition must be FactorMiningSelectionDisposition")
        reason = _reason_code(self.reason_code, "selection_record.reason_code")
        rank = self.rank
        if rank is not None:
            rank = _positive_int(rank, "selection_record.rank")
        score = self.discovery_score
        if score is not None:
            score = _finite(score, "selection_record.discovery_score", minimum=-1.0, maximum=1.0)
        if self.disposition is FactorMiningSelectionDisposition.SELECTED_FOR_OOS_RELEASE:
            if rank is None or score is None or reason != "SELECTED_FOR_OOS_RELEASE":
                raise FactorMiningError("selected record requires rank, score, and canonical reason")
        elif rank is not None or score is not None:
            raise FactorMiningError("non-selected record cannot carry rank or score")
        record_hash = canonical_json_sha256(
            {
                "candidate_hash": candidate_hash,
                "discovery_hash": discovery_hash,
                "discovery_score": score.hex() if score is not None else None,
                "disposition": self.disposition.value,
                "format": "northstar.factor-mining-selection-record.v1",
                "rank": rank,
                "reason_code": reason,
            }
        )
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "candidate_hash", candidate_hash)
        object.__setattr__(self, "discovery_hash", discovery_hash)
        object.__setattr__(self, "reason_code", reason)
        object.__setattr__(self, "rank", rank)
        object.__setattr__(self, "discovery_score", score)
        object.__setattr__(self, "record_hash", record_hash)


@dataclass(frozen=True, slots=True)
class FactorMiningSelectionCommitment:
    """A deterministic, immutable development-only decision to expose OOS evidence."""

    campaign_id: str
    campaign_hash: str
    generation_receipt_hash: str
    discovery_result_hash: str
    selection_policy_hash: str
    records: tuple[FactorMiningSelectionRecord, ...]
    commitment_hash: str = field(init=False)

    @property
    def research_only(self) -> bool:
        return True

    @property
    def candidate_admission_eligible(self) -> bool:
        return False

    @property
    def simnow_handoff_allowed(self) -> bool:
        return False

    @property
    def selected_records(self) -> tuple[FactorMiningSelectionRecord, ...]:
        return tuple(
            item
            for item in self.records
            if item.disposition is FactorMiningSelectionDisposition.SELECTED_FOR_OOS_RELEASE
        )

    def __post_init__(self) -> None:
        campaign_id = _identifier(self.campaign_id, "selection_commitment.campaign_id")
        campaign_hash = _hash(self.campaign_hash, "selection_commitment.campaign_hash")
        receipt_hash = _hash(
            self.generation_receipt_hash,
            "selection_commitment.generation_receipt_hash",
        )
        discovery_hash = _hash(self.discovery_result_hash, "selection_commitment.discovery_result_hash")
        policy_hash = _hash(self.selection_policy_hash, "selection_commitment.selection_policy_hash")
        records = tuple(self.records)
        if not records or not all(type(item) is FactorMiningSelectionRecord for item in records):
            raise FactorMiningError("selection_commitment.records must contain exact selection records")
        if tuple(sorted(records, key=lambda item: item.candidate_id)) != records:
            raise FactorMiningError("selection_commitment.records must be sorted by candidate_id")
        if len({item.candidate_id for item in records}) != len(records):
            raise FactorMiningError("selection_commitment.records cannot duplicate candidates")
        selected = tuple(
            item for item in records if item.disposition is FactorMiningSelectionDisposition.SELECTED_FOR_OOS_RELEASE
        )
        if len({item.rank for item in selected}) != len(selected):
            raise FactorMiningError("selection_commitment selected ranks cannot duplicate")
        if tuple(sorted(item.rank or 0 for item in selected)) != tuple(range(1, len(selected) + 1)):
            raise FactorMiningError("selection_commitment selected ranks must start at one")
        commitment_hash = canonical_json_sha256(
            {
                "candidate_admission_eligible": False,
                "campaign_hash": campaign_hash,
                "discovery_result_hash": discovery_hash,
                "format": "northstar.factor-mining-selection-commitment.v1",
                "generation_receipt_hash": receipt_hash,
                "record_hashes": [item.record_hash for item in records],
                "research_only": True,
                "selection_policy_hash": policy_hash,
                "simnow_handoff_allowed": False,
            }
        )
        object.__setattr__(self, "campaign_id", campaign_id)
        object.__setattr__(self, "campaign_hash", campaign_hash)
        object.__setattr__(self, "generation_receipt_hash", receipt_hash)
        object.__setattr__(self, "discovery_result_hash", discovery_hash)
        object.__setattr__(self, "selection_policy_hash", policy_hash)
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "commitment_hash", commitment_hash)


@dataclass(frozen=True, slots=True)
class FactorMiningOOSReleaseResult:
    """One selected candidate's newly exposed OOS research evidence."""

    campaign_id: str
    campaign_hash: str
    candidate_id: str
    candidate_hash: str
    selection_record_hash: str
    factor_definition_hash: str
    pipeline_config_hash: str
    run_manifest_hash: str
    lookahead_certificate_hash: str
    stage_evidence: tuple[FactorMiningStageEvidence, ...]
    release_result_hash: str = field(init=False)

    @property
    def research_only(self) -> bool:
        return True

    @property
    def candidate_admission_eligible(self) -> bool:
        return False

    @property
    def simnow_handoff_allowed(self) -> bool:
        return False

    def __post_init__(self) -> None:
        campaign_id = _identifier(self.campaign_id, "oos_result.campaign_id")
        campaign_hash = _hash(self.campaign_hash, "oos_result.campaign_hash")
        candidate_id = _identifier(self.candidate_id, "oos_result.candidate_id")
        candidate_hash = _hash(self.candidate_hash, "oos_result.candidate_hash")
        selection_record_hash = _hash(self.selection_record_hash, "oos_result.selection_record_hash")
        definition_hash = _hash(self.factor_definition_hash, "oos_result.factor_definition_hash")
        config_hash = _hash(self.pipeline_config_hash, "oos_result.pipeline_config_hash")
        manifest_hash = _hash(self.run_manifest_hash, "oos_result.run_manifest_hash")
        certificate_hash = _hash(
            self.lookahead_certificate_hash,
            "oos_result.lookahead_certificate_hash",
        )
        stages = tuple(self.stage_evidence)
        if not stages or not all(type(item) is FactorMiningStageEvidence for item in stages):
            raise FactorMiningError("oos_result.stage_evidence must contain exact stage records")
        if tuple(sorted(stages, key=lambda item: item.fold_id)) != stages:
            raise FactorMiningError("oos_result.stage_evidence must be sorted by fold")
        if any(item.stage is not ValidationStage.OUT_OF_SAMPLE for item in stages):
            raise FactorMiningError("oos_result can contain only out-of-sample evidence")
        if len({item.fold_id for item in stages}) != len(stages):
            raise FactorMiningError("oos_result cannot duplicate OOS folds")
        if any(
            item.campaign_id != campaign_id
            or item.campaign_hash != campaign_hash
            or item.candidate_id != candidate_id
            or item.candidate_hash != candidate_hash
            or item.factor_definition_hash != definition_hash
            or item.pipeline_config_hash != config_hash
            for item in stages
        ):
            raise FactorMiningError("oos result stage evidence must bind the selected candidate")
        release_result_hash = canonical_json_sha256(
            {
                "campaign_hash": campaign_hash,
                "candidate_admission_eligible": False,
                "candidate_hash": candidate_hash,
                "factor_definition_hash": definition_hash,
                "format": "northstar.factor-mining-oos-release-result.v1",
                "lookahead_certificate_hash": certificate_hash,
                "pipeline_config_hash": config_hash,
                "research_only": True,
                "run_manifest_hash": manifest_hash,
                "selection_record_hash": selection_record_hash,
                "simnow_handoff_allowed": False,
                "stage_evidence_hashes": [item.stage_evidence_hash for item in stages],
            }
        )
        object.__setattr__(self, "campaign_id", campaign_id)
        object.__setattr__(self, "campaign_hash", campaign_hash)
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "candidate_hash", candidate_hash)
        object.__setattr__(self, "selection_record_hash", selection_record_hash)
        object.__setattr__(self, "factor_definition_hash", definition_hash)
        object.__setattr__(self, "pipeline_config_hash", config_hash)
        object.__setattr__(self, "run_manifest_hash", manifest_hash)
        object.__setattr__(self, "lookahead_certificate_hash", certificate_hash)
        object.__setattr__(self, "stage_evidence", stages)
        object.__setattr__(self, "release_result_hash", release_result_hash)


@dataclass(frozen=True, slots=True)
class FactorMiningOOSRelease:
    """The single explicit research-only release of selected OOS evidence."""

    campaign_id: str
    campaign_hash: str
    selection_commitment_hash: str
    results: tuple[FactorMiningOOSReleaseResult, ...]
    release_hash: str = field(init=False)

    @property
    def research_only(self) -> bool:
        return True

    @property
    def candidate_admission_eligible(self) -> bool:
        return False

    @property
    def simnow_handoff_allowed(self) -> bool:
        return False

    def __post_init__(self) -> None:
        campaign_id = _identifier(self.campaign_id, "oos_release.campaign_id")
        campaign_hash = _hash(self.campaign_hash, "oos_release.campaign_hash")
        commitment_hash = _hash(self.selection_commitment_hash, "oos_release.selection_commitment_hash")
        results = tuple(self.results)
        if not results or not all(type(item) is FactorMiningOOSReleaseResult for item in results):
            raise FactorMiningError("oos_release.results must contain exact release results")
        if tuple(sorted(results, key=lambda item: item.candidate_id)) != results:
            raise FactorMiningError("oos_release.results must be sorted by candidate_id")
        if len({item.candidate_id for item in results}) != len(results):
            raise FactorMiningError("oos_release.results cannot duplicate candidates")
        if any(item.campaign_id != campaign_id or item.campaign_hash != campaign_hash for item in results):
            raise FactorMiningError("oos release results must bind this campaign")
        release_hash = canonical_json_sha256(
            {
                "candidate_admission_eligible": False,
                "campaign_hash": campaign_hash,
                "format": "northstar.factor-mining-oos-release.v1",
                "research_only": True,
                "result_hashes": [item.release_result_hash for item in results],
                "selection_commitment_hash": commitment_hash,
                "simnow_handoff_allowed": False,
            }
        )
        object.__setattr__(self, "campaign_id", campaign_id)
        object.__setattr__(self, "campaign_hash", campaign_hash)
        object.__setattr__(self, "selection_commitment_hash", commitment_hash)
        object.__setattr__(self, "results", results)
        object.__setattr__(self, "release_hash", release_hash)


@dataclass(frozen=True, slots=True)
class _StageSummary:
    period_count: int
    mean_rank_ic: float
    quantile_spread: float
    mean_factor_turnover: float
    positive_rank_ic_count: int
    cost_results: tuple[FactorDiscoveryStageCostResult, ...]


def _summarize_stage(
    result: FactorCandidateDiscoveryResult,
    *,
    stage: ValidationStage,
) -> _StageSummary | None:
    evidence = tuple(item for item in result.stage_evidence if item.stage is stage)
    if not evidence:
        return None
    periods = sum(len(item.analysis_period_hashes) for item in evidence)
    if periods < 1:
        return None
    scenario_values: dict[str, list[FactorDiscoveryStageCostResult]] = {}
    for item in evidence:
        for cost in item.cost_results:
            scenario_values.setdefault(cost.cost_scenario_hash, []).append(cost)
    if any(len(values) != len(evidence) for values in scenario_values.values()):
        return None
    costs: list[FactorDiscoveryStageCostResult] = []
    for scenario_hash, values in sorted(scenario_values.items()):
        session_count = sum(item.session_count for item in values)
        costs.append(
            FactorDiscoveryStageCostResult(
                cost_scenario_hash=scenario_hash,
                backtest_result_hash=canonical_json_sha256(
                    {
                        "format": "northstar.factor-mining-aggregate-stage-backtest.v1",
                        "source_hashes": [item.backtest_result_hash for item in values],
                    }
                ),
                session_count=session_count,
                total_return=min(item.total_return for item in values),
                max_drawdown=min(item.max_drawdown for item in values),
                portfolio_turnover=sum(
                    item.portfolio_turnover * item.session_count for item in values
                ) / session_count,
            )
        )
    return _StageSummary(
        period_count=periods,
        mean_rank_ic=sum(
            item.mean_rank_ic * len(item.analysis_period_hashes) for item in evidence
        ) / periods,
        quantile_spread=sum(
            item.quantile_spread * len(item.analysis_period_hashes) for item in evidence
        ) / periods,
        mean_factor_turnover=sum(
            item.mean_factor_turnover * len(item.analysis_period_hashes) for item in evidence
        ) / periods,
        positive_rank_ic_count=sum(item.positive_rank_ic_count for item in evidence),
        cost_results=tuple(costs),
    )


def _one_sided_sign_test_p_value(*, positives: int, periods: int) -> float:
    if not 1 <= periods or not 0 <= positives <= periods:
        raise FactorMiningError("sign-test inputs are invalid")
    numerator = sum(math.comb(periods, successes) for successes in range(positives, periods + 1))
    return numerator / float(2**periods)


def _policy_failure_reason(
    *,
    campaign: FactorMiningCampaignSpec,
    result: FactorCandidateDiscoveryResult,
) -> tuple[str, float] | None:
    policy: FactorMiningSelectionPolicy = campaign.selection_policy
    in_sample = _summarize_stage(result, stage=ValidationStage.IN_SAMPLE)
    validation = _summarize_stage(result, stage=ValidationStage.VALIDATION)
    if in_sample is None:
        return ("MISSING_IN_SAMPLE_EVIDENCE", 0.0)
    if validation is None:
        return ("MISSING_VALIDATION_EVIDENCE", 0.0)
    if in_sample.period_count < policy.minimum_in_sample_periods:
        return ("INSUFFICIENT_IN_SAMPLE_PERIODS", validation.mean_rank_ic)
    if validation.period_count < policy.minimum_validation_periods:
        return ("INSUFFICIENT_VALIDATION_PERIODS", validation.mean_rank_ic)
    if in_sample.mean_rank_ic < policy.minimum_in_sample_mean_rank_ic:
        return ("IN_SAMPLE_RANK_IC_BELOW_THRESHOLD", validation.mean_rank_ic)
    if validation.mean_rank_ic < policy.minimum_validation_mean_rank_ic:
        return ("VALIDATION_RANK_IC_BELOW_THRESHOLD", validation.mean_rank_ic)
    if validation.quantile_spread < policy.minimum_validation_quantile_spread:
        return ("VALIDATION_QUANTILE_SPREAD_BELOW_THRESHOLD", validation.mean_rank_ic)
    if validation.mean_factor_turnover > policy.maximum_validation_factor_turnover:
        return ("VALIDATION_FACTOR_TURNOVER_EXCEEDS_LIMIT", validation.mean_rank_ic)
    raw_p = _one_sided_sign_test_p_value(
        positives=validation.positive_rank_ic_count,
        periods=validation.period_count,
    )
    adjusted_p = min(1.0, raw_p * campaign.budget.max_candidates)
    if adjusted_p > policy.family_wise_alpha:
        return ("VALIDATION_BONFERRONI_SIGN_TEST_FAILED", validation.mean_rank_ic)
    if len(validation.cost_results) != len(policy.cost_scenarios):
        return ("MISSING_VALIDATION_COST_SCENARIO", validation.mean_rank_ic)
    expected_cost_hashes = {item.scenario_hash for item in policy.cost_scenarios}
    if {item.cost_scenario_hash for item in validation.cost_results} != expected_cost_hashes:
        return ("VALIDATION_COST_SCENARIO_MISMATCH", validation.mean_rank_ic)
    for cost in validation.cost_results:
        if cost.session_count < policy.minimum_stage_backtest_sessions:
            return ("INSUFFICIENT_VALIDATION_BACKTEST_SESSIONS", validation.mean_rank_ic)
        if cost.portfolio_turnover > policy.maximum_validation_portfolio_turnover:
            return ("VALIDATION_PORTFOLIO_TURNOVER_EXCEEDS_LIMIT", validation.mean_rank_ic)
        if cost.total_return < policy.minimum_validation_total_return:
            return ("VALIDATION_TOTAL_RETURN_BELOW_THRESHOLD", validation.mean_rank_ic)
        if cost.max_drawdown < policy.minimum_validation_max_drawdown:
            return ("VALIDATION_DRAWDOWN_BELOW_THRESHOLD", validation.mean_rank_ic)
    return None


def select_discovery_candidates(
    *,
    campaign: FactorMiningCampaignSpec,
    discovery: FactorMiningDiscoveryResult,
) -> FactorMiningSelectionCommitment:
    """Freeze a deterministic OOS-release subset using only discovery evidence."""

    if type(campaign) is not FactorMiningCampaignSpec:
        raise FactorMiningError("campaign must be an exact FactorMiningCampaignSpec")
    if type(discovery) is not FactorMiningDiscoveryResult:
        raise FactorMiningError("discovery must be an exact FactorMiningDiscoveryResult")
    if (
        discovery.campaign_id != campaign.campaign_id
        or discovery.campaign_hash != campaign.campaign_hash
        or discovery.selection_policy_hash != campaign.selection_policy.policy_hash
    ):
        raise FactorMiningError("discovery does not bind the sealed campaign policy")
    accepted: list[tuple[FactorCandidateDiscoveryResult, float, float, float]] = []
    records_by_candidate: dict[str, FactorMiningSelectionRecord] = {}
    for result in discovery.results:
        if result.disposition is not CandidateDiscoveryDisposition.DISCOVERY_EVALUATED:
            records_by_candidate[result.candidate_id] = FactorMiningSelectionRecord(
                candidate_id=result.candidate_id,
                candidate_hash=result.candidate_hash,
                discovery_hash=result.discovery_hash,
                disposition=FactorMiningSelectionDisposition.REJECTED_INPUT_OR_EVIDENCE,
                reason_code=result.reason_code,
            )
            continue
        failure = _policy_failure_reason(campaign=campaign, result=result)
        if failure is not None:
            reason, _score = failure
            records_by_candidate[result.candidate_id] = FactorMiningSelectionRecord(
                candidate_id=result.candidate_id,
                candidate_hash=result.candidate_hash,
                discovery_hash=result.discovery_hash,
                disposition=FactorMiningSelectionDisposition.REJECTED_DISCOVERY_POLICY,
                reason_code=reason,
            )
            continue
        validation = _summarize_stage(result, stage=ValidationStage.VALIDATION)
        if validation is None:  # pragma: no cover - guarded by _policy_failure_reason.
            raise FactorMiningError("validated discovery lost validation evidence")
        accepted.append(
            (
                result,
                validation.mean_rank_ic,
                validation.quantile_spread,
                validation.mean_factor_turnover,
            )
        )
    accepted.sort(key=lambda item: (-item[1], -item[2], item[3], item[0].candidate_id))
    for position, (result, score, _spread, _turnover) in enumerate(accepted, start=1):
        if position <= campaign.selection_policy.max_selected_candidates:
            records_by_candidate[result.candidate_id] = FactorMiningSelectionRecord(
                candidate_id=result.candidate_id,
                candidate_hash=result.candidate_hash,
                discovery_hash=result.discovery_hash,
                disposition=FactorMiningSelectionDisposition.SELECTED_FOR_OOS_RELEASE,
                reason_code="SELECTED_FOR_OOS_RELEASE",
                rank=position,
                discovery_score=score,
            )
        else:
            records_by_candidate[result.candidate_id] = FactorMiningSelectionRecord(
                candidate_id=result.candidate_id,
                candidate_hash=result.candidate_hash,
                discovery_hash=result.discovery_hash,
                disposition=FactorMiningSelectionDisposition.NOT_SELECTED,
                reason_code="RANKED_BELOW_SELECTION_CAP",
            )
    return FactorMiningSelectionCommitment(
        campaign_id=campaign.campaign_id,
        campaign_hash=campaign.campaign_hash,
        generation_receipt_hash=discovery.generation_receipt_hash,
        discovery_result_hash=discovery.discovery_result_hash,
        selection_policy_hash=campaign.selection_policy.policy_hash,
        records=tuple(records_by_candidate[key] for key in sorted(records_by_candidate)),
    )

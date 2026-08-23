"""P2-WP08: reproducible, research-only Research Cards.

Research Cards are immutable summaries of already-frozen research evidence.  They
are deliberately not trading instructions: a card never opens a broker path or
grants execution eligibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import re

from northstar_quant.data_platform.artifacts.fingerprints import canonical_json_sha256
from northstar_quant.research.backtest.models import RunManifest
from northstar_quant.research.intelligence_fixture_replay import (
    FixtureOnlyResearchRunManifest,
)
from northstar_quant.research.validation.framework import (
    ResearchInputEvidenceKind,
    ValidationReport,
    ValidationStage,
)
from northstar_quant.research.validation.research_decision import (
    ResearchDecision,
    ResearchDecisionState,
)


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class ResearchReportError(ValueError):
    """Research Card inputs are incomplete, inconsistent, or not reproducible."""


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value.strip()) is None:
        raise ResearchReportError(f"{field_name} must be a non-empty identifier")
    return value.strip()


def _finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ResearchReportError(f"{field_name} must be a finite number")
    return float(value)


@dataclass(frozen=True, slots=True)
class ProductContribution:
    """A per-product contribution included in an auditable Research Card."""

    product_id: str
    total_return: float
    turnover: float
    max_drawdown: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "product_id", _identifier(self.product_id, "product_id"))
        object.__setattr__(self, "total_return", _finite(self.total_return, "total_return"))
        turnover = _finite(self.turnover, "turnover")
        if turnover < 0:
            raise ResearchReportError("turnover must be non-negative")
        object.__setattr__(self, "turnover", turnover)
        object.__setattr__(self, "max_drawdown", _finite(self.max_drawdown, "max_drawdown"))

    def as_mapping(self) -> dict[str, object]:
        return {
            "product_id": self.product_id,
            "total_return": self.total_return,
            "turnover": self.turnover,
            "max_drawdown": self.max_drawdown,
        }


@dataclass(frozen=True, slots=True)
class ResearchCard:
    """A deterministic summary of one research decision and its full evidence chain."""

    card_id: str
    run_manifest: RunManifest | FixtureOnlyResearchRunManifest
    validation_report: ValidationReport
    decision: ResearchDecision
    product_contributions: tuple[ProductContribution, ...]
    limitations: tuple[str, ...]
    card_hash: str = field(init=False)

    @classmethod
    def create(
        cls,
        *,
        card_id: str,
        run_manifest: RunManifest | FixtureOnlyResearchRunManifest,
        validation_report: ValidationReport,
        decision: ResearchDecision,
        product_contributions: tuple[ProductContribution, ...],
        limitations: tuple[str, ...],
    ) -> "ResearchCard":
        return cls(
            card_id=card_id,
            run_manifest=run_manifest,
            validation_report=validation_report,
            decision=decision,
            product_contributions=product_contributions,
            limitations=limitations,
        )

    @property
    def eligible_for_trading(self) -> bool:
        """Neither a Research Card nor its decision can grant trading eligibility."""

        return False

    def __post_init__(self) -> None:
        card_id = _identifier(self.card_id, "card_id")
        if not isinstance(self.run_manifest, (RunManifest, FixtureOnlyResearchRunManifest)):
            raise ResearchReportError(
                "run_manifest must be a RunManifest or FixtureOnlyResearchRunManifest"
            )
        if not isinstance(self.validation_report, ValidationReport):
            raise ResearchReportError("validation_report must be a ValidationReport")
        if not isinstance(self.decision, ResearchDecision):
            raise ResearchReportError("decision must be a ResearchDecision")

        evidence = self.validation_report.evidence
        if evidence.backtest_result_hash != self.run_manifest.result.result_hash:
            raise ResearchReportError("validation evidence must bind this run manifest result")
        if isinstance(self.run_manifest, FixtureOnlyResearchRunManifest):
            if evidence.input_kind is not ResearchInputEvidenceKind.FIXTURE_ONLY_INTELLIGENCE_REPLAY:
                raise ResearchReportError("fixture-only replay manifest requires fixture-only evidence")
            if evidence.fixture_replay_binding_hash != self.run_manifest.plan.plan_hash:
                raise ResearchReportError("fixture-only evidence must bind the exact replay plan")
            if (
                evidence.feature_version_hashes != self.run_manifest.feature_version_hashes
                or evidence.strategy_version_hash != self.run_manifest.methodology_version_hash
                or evidence.experiment_spec_hash != self.run_manifest.experiment_spec_hash
                or evidence.experiment_run_hash != self.run_manifest.experiment_run_hash
                or evidence.code_revision != self.run_manifest.code_revision
            ):
                raise ResearchReportError("fixture-only evidence must bind the exact replay manifest")
            if (
                self.decision.state is not ResearchDecisionState.RESEARCH_ONLY
                or self.decision.evidence is not None
                or self.decision.approval is not None
            ):
                raise ResearchReportError(
                    "fixture-only replay cards must remain exactly RESEARCH_ONLY without approval"
                )
        elif evidence.input_kind is not ResearchInputEvidenceKind.DATASET_VERSIONED:
            raise ResearchReportError("dataset RunManifest requires dataset-versioned evidence")
        decision_evidence = self.decision.evidence
        if decision_evidence is not None and (
            decision_evidence.experiment_spec_hash != evidence.experiment_spec_hash
            or decision_evidence.experiment_run_hash != evidence.experiment_run_hash
            or decision_evidence.backtest_result_hash != self.run_manifest.result.result_hash
            or decision_evidence.validation_report_hash != self.validation_report.report_hash
        ):
            raise ResearchReportError("decision evidence must bind the same validation evidence chain")

        contributions = tuple(self.product_contributions)
        if not contributions or not all(isinstance(item, ProductContribution) for item in contributions):
            raise ResearchReportError("product_contributions must be a non-empty ProductContribution tuple")
        if len({item.product_id for item in contributions}) != len(contributions):
            raise ResearchReportError("product_contributions cannot contain duplicate product_id")
        canonical_contributions = tuple(sorted(contributions, key=lambda item: item.product_id))

        limitations = tuple(self.limitations)
        if not limitations or not all(isinstance(item, str) and item.strip() for item in limitations):
            raise ResearchReportError("limitations must be a non-empty tuple of text")
        canonical_limitations = tuple(sorted({item.strip() for item in limitations}))

        object.__setattr__(self, "card_id", card_id)
        object.__setattr__(self, "product_contributions", canonical_contributions)
        object.__setattr__(self, "limitations", canonical_limitations)
        object.__setattr__(self, "card_hash", canonical_json_sha256(self._payload()))

    def _payload(self) -> dict[str, object]:
        validation = self.validation_report
        evidence = validation.evidence
        stage_metrics = dict(validation.stage_metrics)
        if isinstance(self.run_manifest, FixtureOnlyResearchRunManifest):
            fixture_result = self.run_manifest.result
            execution_assumptions: dict[str, object] = {"not_applicable": True}
            backtest_summary: dict[str, object] = {
                "kind": "fixture_only_intelligence_replay",
                "mean_synthetic_alignment_score": fixture_result.mean_synthetic_alignment_score,
                "positive_score_fraction": fixture_result.positive_score_fraction,
                "order_count": 0,
                "trade_count": 0,
            }
            backtest_result_hash = fixture_result.result_hash
        else:
            request = self.run_manifest.request
            backtest_result = self.run_manifest.result
            execution_assumptions = {
                "commission_bps": request.assumptions.commission_bps,
                "min_commission": request.assumptions.min_commission,
                "slippage_bps": request.assumptions.slippage_bps,
                "slippage_ticks": request.assumptions.slippage_ticks,
            }
            backtest_summary = {
                "turnover": backtest_result.turnover_estimate,
                "max_drawdown": backtest_result.max_drawdown,
            }
            backtest_result_hash = backtest_result.result_hash
        return {
            "format": "northstar.research-card.v1",
            "card_id": self.card_id,
            "reproducibility": {
                "dataset_version_hashes": list(evidence.dataset_version_hashes),
                "feature_version_hashes": list(evidence.feature_version_hashes),
                "strategy_version_hash": evidence.strategy_version_hash,
                "code_revision": evidence.code_revision,
                "experiment_spec_hash": evidence.experiment_spec_hash,
                "experiment_run_hash": evidence.experiment_run_hash,
                "input_kind": evidence.input_kind.value,
                "fixture_replay_binding_hash": evidence.fixture_replay_binding_hash,
                "run_manifest_fingerprint": self.run_manifest.run_fingerprint,
                "backtest_result_hash": backtest_result_hash,
                "validation_report_hash": validation.report_hash,
                "decision_hash": self.decision.decision_hash,
            },
            "validation": {
                "in_sample": stage_metrics[ValidationStage.IN_SAMPLE].as_mapping(),
                "out_of_sample": stage_metrics[ValidationStage.OUT_OF_SAMPLE].as_mapping(),
                "regimes": {
                    regime_id: metrics.as_mapping()
                    for regime_id, metrics in validation.regime_metrics
                },
                "stress": {
                    scenario_id: metrics.as_mapping()
                    for scenario_id, metrics in validation.stress_metrics
                },
            },
            "execution_assumptions": execution_assumptions,
            "backtest_summary": backtest_summary,
            "product_contributions": [item.as_mapping() for item in self.product_contributions],
            "limitations": list(self.limitations),
            "decision": self.decision.as_mapping(),
            "eligible_for_trading": False,
        }

    def as_mapping(self) -> dict[str, object]:
        return {**self._payload(), "card_hash": self.card_hash}

    def to_json(self) -> str:
        return json.dumps(self.as_mapping(), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)


__all__ = ["ProductContribution", "ResearchCard", "ResearchReportError"]

"""P10-WP03 fixture-only Intelligence Feature research evidence builder.

The helper is intentionally test-only: it rebuilds the six WP02 upstream
handoffs, then supplies a separately declared synthetic response schedule to
the pure P10 replay boundary.  It never constructs a P1 FeatureValue,
DatasetVersion, market snapshot, target, approval, execution plan, or order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path

from northstar_quant.research.intelligence_fixture_replay import (
    FixtureFeatureDefinitionHandoff,
    FixtureObservationLifecycle,
    FixtureOnlyReplayPlan,
    FixtureOnlyReplayResult,
    FixtureOnlyReplayRunner,
    FixtureOnlyResearchRunManifest,
    FixtureReplayCheckpoint,
    FixtureReplayObservation,
    FixtureSyntheticOutcome,
)
from northstar_quant.research.reports import ProductContribution, ResearchCard
from northstar_quant.research.validation.framework import (
    ParameterNeighbor,
    ResearchInputEvidenceKind,
    ResearchValidationEvidence,
    ReturnObservation,
    RollingWindow,
    StressKind,
    StressScenario,
    ValidationPeriod,
    ValidationReport,
    ValidationReturnSeries,
    ValidationSplit,
    WalkForwardFold,
    evaluate_validation,
)
from northstar_quant.research.validation.research_decision import (
    ResearchDecision,
    ResearchDecisionState,
)
from tests.intelligence.golden._fixture_corpus import (
    FixtureCase,
    FixtureOnlyFeatureDefinitionHandoff,
    load_fixture_only_corpus,
    materialize_feature_definition_handoff,
)
from northstar_quant.intelligence.ontology import load_ontology


_ROOT = Path(__file__).resolve().parents[2]
_CORPUS_PATH = _ROOT / "tests" / "intelligence" / "golden" / "six_commodity_fixture_only_v1.json"
_REPLAY_PATH = _ROOT / "tests" / "research" / "golden" / "p10_intelligence_fixture_replay_v1.json"
_AUTHORITY_KEYS = frozenset(
    {
        "authorized_market_data",
        "actual_contract_data",
        "authoritative_calendar",
        "authoritative_dynamic_rules",
        "eligible_for_admission",
        "eligible_for_trading",
    }
)


class P10FixtureReplayError(ValueError):
    """The companion fixture drifted or attempted to claim authority."""


@dataclass(frozen=True, slots=True)
class P10IntelligenceFeatureResearchChain:
    handoffs: tuple[FixtureOnlyFeatureDefinitionHandoff, ...]
    plan: FixtureOnlyReplayPlan
    result: FixtureOnlyReplayResult
    manifest: FixtureOnlyResearchRunManifest
    validation: ValidationReport
    decision: ResearchDecision
    card: ResearchCard


def _mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise P10FixtureReplayError(f"{field_name} must be a JSON object")
    return value


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise P10FixtureReplayError(f"{field_name} must be a lowercase SHA-256")
    return value


def _utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise P10FixtureReplayError(f"{field_name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise P10FixtureReplayError(f"{field_name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise P10FixtureReplayError(f"{field_name} must include a timezone")
    return parsed.astimezone(UTC)


def _load_fixture() -> dict[str, object]:
    payload = _mapping(json.loads(_REPLAY_PATH.read_text(encoding="utf-8")), "replay fixture")
    expected = {
        "schema_version",
        "fixture_only",
        "research_only",
        "authority",
        "upstream_corpus_sha256",
        "upstream_handoff_hashes",
        "methodology",
        "expected",
        "schedule",
    }
    if set(payload) != expected:
        raise P10FixtureReplayError("replay fixture keys must be exact")
    if payload["schema_version"] != "northstar.p10.fixture-intelligence-replay-fixture.v1":
        raise P10FixtureReplayError("unsupported replay fixture schema")
    if payload["fixture_only"] is not True or payload["research_only"] is not True:
        raise P10FixtureReplayError("replay fixture must remain fixture_only and research_only")
    authority = _mapping(payload["authority"], "authority")
    if set(authority) != _AUTHORITY_KEYS or any(value is not False for value in authority.values()):
        raise P10FixtureReplayError("replay fixture cannot claim data, admission, or trading authority")
    corpus_hash = sha256(_CORPUS_PATH.read_bytes()).hexdigest()
    if _sha256(payload["upstream_corpus_sha256"], "upstream_corpus_sha256") != corpus_hash:
        raise P10FixtureReplayError("replay fixture must bind the exact WP02 corpus bytes")
    expected_hashes = _mapping(payload["expected"], "expected")
    if set(expected_hashes) != {
        "plan_hash",
        "result_hash",
        "run_fingerprint",
        "validation_report_hash",
        "research_card_hash",
    }:
        raise P10FixtureReplayError("golden replay expected hashes must be exact")
    for field_name, value in expected_hashes.items():
        _sha256(value, f"expected.{field_name}")
    return payload


def _retained_available_at(case: FixtureCase) -> datetime:
    """Use collected time of retained Event evidence, never source observed time."""

    return max(
        case.documents_by_key[document_key].collected_at
        for document_key in case.event.evidence_document_keys
    )


def _gold_retracted_at(case: FixtureCase) -> datetime:
    retractions = [
        document.collected_at
        for document in case.documents
        if document.lifecycle.value == "RETRACTED"
    ]
    if len(retractions) != 1:
        raise P10FixtureReplayError("gold fixture must contain one retraction")
    return retractions[0]


def _rebuild_handoffs(
    fixture: dict[str, object],
) -> tuple[tuple[FixtureOnlyFeatureDefinitionHandoff, ...], tuple[FixtureFeatureDefinitionHandoff, ...], dict[str, FixtureCase]]:
    corpus = load_fixture_only_corpus(_CORPUS_PATH)
    ontology = load_ontology(_ROOT / "ontology")
    expected_hashes = _mapping(fixture["upstream_handoff_hashes"], "upstream_handoff_hashes")
    cases = {case.commodity_id: case for case in corpus.cases}
    if set(cases) != set(expected_hashes) or len(cases) != 6:
        raise P10FixtureReplayError("replay fixture must bind exactly the six WP02 commodity handoffs")
    upstream: list[FixtureOnlyFeatureDefinitionHandoff] = []
    bindings: list[FixtureFeatureDefinitionHandoff] = []
    for commodity_id, case in sorted(cases.items()):
        handoff = materialize_feature_definition_handoff(
            case=case,
            ontology=ontology,
            code_revision="p10-wp02-fixture-only-corpus",
        )
        expected_hash = _sha256(expected_hashes[commodity_id], f"upstream_handoff_hashes.{commodity_id}")
        if handoff.handoff_hash != expected_hash:
            raise P10FixtureReplayError("WP02 Feature-definition handoff hash drifted")
        upstream.append(handoff)
        bindings.append(
            FixtureFeatureDefinitionHandoff(
                commodity_id=commodity_id,
                feature_id=handoff.feature_id,
                feature_version_hash=handoff.feature_version_hash,
                upstream_handoff_hash=handoff.handoff_hash,
            )
        )
    return tuple(upstream), tuple(bindings), cases


def build_p10_intelligence_feature_research_chain() -> P10IntelligenceFeatureResearchChain:
    """Build the complete, non-admissible WP02→P10 research evidence chain."""

    fixture = _load_fixture()
    upstream_handoffs, bindings, cases = _rebuild_handoffs(fixture)
    methodology = _mapping(fixture["methodology"], "methodology")
    if set(methodology) != {"methodology_id", "methodology_version_hash", "code_revision"}:
        raise P10FixtureReplayError("methodology keys must be exact")
    methodology_id = methodology["methodology_id"]
    methodology_version_hash = _sha256(
        methodology["methodology_version_hash"], "methodology_version_hash"
    )
    code_revision = methodology["code_revision"]
    if not isinstance(methodology_id, str) or not isinstance(code_revision, str):
        raise P10FixtureReplayError("methodology identifiers must be strings")
    schedule = _mapping(fixture["schedule"], "schedule")
    expected_schedule_keys = {
        "first_decision_at",
        "checkpoint_count",
        "checkpoint_interval_hours",
        "outcome_delay_minutes",
        "gold_retraction_from_checkpoint",
        "feature_values",
        "outcome_cycles",
    }
    if set(schedule) != expected_schedule_keys:
        raise P10FixtureReplayError("schedule keys must be exact")
    first_decision_at = _utc(schedule["first_decision_at"], "schedule.first_decision_at")
    count = schedule["checkpoint_count"]
    interval_hours = schedule["checkpoint_interval_hours"]
    outcome_delay_minutes = schedule["outcome_delay_minutes"]
    retraction_from = schedule["gold_retraction_from_checkpoint"]
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count < 18
        or isinstance(interval_hours, bool)
        or not isinstance(interval_hours, int)
        or interval_hours < 1
        or isinstance(outcome_delay_minutes, bool)
        or not isinstance(outcome_delay_minutes, int)
        or outcome_delay_minutes < 1
        or isinstance(retraction_from, bool)
        or not isinstance(retraction_from, int)
        or not 0 < retraction_from < count
    ):
        raise P10FixtureReplayError("schedule dimensions are invalid")
    feature_values = _mapping(schedule["feature_values"], "schedule.feature_values")
    outcome_cycles = _mapping(schedule["outcome_cycles"], "schedule.outcome_cycles")
    if set(feature_values) != set(cases) or set(outcome_cycles) != set(cases):
        raise P10FixtureReplayError("schedule must cover exactly the six fixture commodities")
    binding_by_commodity = {binding.commodity_id: binding for binding in bindings}
    checkpoints: list[FixtureReplayCheckpoint] = []
    outcomes: list[FixtureSyntheticOutcome] = []
    for index in range(count):
        checkpoint_id = f"fixture-replay-{index + 1:02d}"
        decision_at = first_decision_at + timedelta(hours=index * interval_hours)
        observations: list[FixtureReplayObservation] = []
        for commodity_id in sorted(cases):
            case = cases[commodity_id]
            binding = binding_by_commodity[commodity_id]
            if commodity_id == "gold" and index >= retraction_from:
                observation = FixtureReplayObservation(
                    checkpoint_id=checkpoint_id,
                    commodity_id=commodity_id,
                    feature_id=binding.feature_id,
                    feature_version_hash=binding.feature_version_hash,
                    upstream_handoff_hash=binding.upstream_handoff_hash,
                    available_at=_gold_retracted_at(case),
                    lifecycle=FixtureObservationLifecycle.RETRACTED,
                    value=None,
                    missing_reason="event_retracted",
                )
            else:
                value = feature_values[commodity_id]
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise P10FixtureReplayError("fixture feature values must be finite scalars")
                observation = FixtureReplayObservation(
                    checkpoint_id=checkpoint_id,
                    commodity_id=commodity_id,
                    feature_id=binding.feature_id,
                    feature_version_hash=binding.feature_version_hash,
                    upstream_handoff_hash=binding.upstream_handoff_hash,
                    available_at=_retained_available_at(case),
                    lifecycle=FixtureObservationLifecycle.CONFIRMED,
                    value=float(value),
                )
            observations.append(observation)
            cycle = outcome_cycles[commodity_id]
            if not isinstance(cycle, list) or len(cycle) != 3 or not all(
                not isinstance(item, bool) and isinstance(item, (int, float)) for item in cycle
            ):
                raise P10FixtureReplayError("every synthetic outcome cycle must contain three scalars")
            outcomes.append(
                FixtureSyntheticOutcome(
                    checkpoint_id=checkpoint_id,
                    commodity_id=commodity_id,
                    available_at=decision_at + timedelta(minutes=outcome_delay_minutes),
                    value=float(cycle[index % len(cycle)]),
                )
            )
        checkpoints.append(
            FixtureReplayCheckpoint.create(
                checkpoint_id=checkpoint_id,
                decision_at=decision_at,
                observations=observations,
            )
        )
    plan = FixtureOnlyReplayRunner.create_plan(
        upstream_corpus_sha256=fixture["upstream_corpus_sha256"],
        handoffs=bindings,
        checkpoints=checkpoints,
        outcomes=outcomes,
    )
    result = FixtureOnlyReplayRunner.run(plan)
    manifest = FixtureOnlyResearchRunManifest.create(
        plan=plan,
        result=result,
        methodology_id=methodology_id,
        methodology_version_hash=methodology_version_hash,
        code_revision=code_revision,
    )
    start = first_decision_at.date()
    returns = ValidationReturnSeries.create(
        ReturnObservation(
            session=start + timedelta(days=index),
            net_return=score.synthetic_alignment_score,
            regime="fixture_retracted" if index >= retraction_from else "fixture_confirmed",
        )
        for index, score in enumerate(result.scores)
    )
    split = ValidationSplit(
        ValidationPeriod(start, start + timedelta(days=5)),
        ValidationPeriod(start + timedelta(days=6), start + timedelta(days=11)),
        ValidationPeriod(start + timedelta(days=12), start + timedelta(days=17)),
    )
    evidence = ResearchValidationEvidence(
        dataset_version_hashes=(),
        feature_version_hashes=manifest.feature_version_hashes,
        strategy_version_hash=manifest.methodology_version_hash,
        experiment_spec_hash=manifest.experiment_spec_hash,
        experiment_run_hash=manifest.experiment_run_hash,
        backtest_result_hash=result.result_hash,
        input_kind=ResearchInputEvidenceKind.FIXTURE_ONLY_INTELLIGENCE_REPLAY,
        fixture_replay_binding_hash=plan.plan_hash,
        code_revision=manifest.code_revision,
    )
    validation = evaluate_validation(
        evidence=evidence,
        series=returns,
        split=split,
        walk_forward_folds=(
            WalkForwardFold(
                fold_id="fixture_wf_01",
                split=ValidationSplit(
                    ValidationPeriod(start, start + timedelta(days=1)),
                    ValidationPeriod(start + timedelta(days=2), start + timedelta(days=3)),
                    ValidationPeriod(start + timedelta(days=4), start + timedelta(days=5)),
                ),
            ),
        ),
        rolling_window=RollingWindow(window_sessions=6, stride_sessions=3),
        stress_scenarios=(StressScenario("fixture_baseline", StressKind.BASELINE),),
        parameter_neighbors=(
            ParameterNeighbor.create(
                neighbor_id="fixture_score_scale_1",
                parameters={"score_scale": 1.0},
                series=returns,
            ),
        ),
        bootstrap_iterations=10,
        monte_carlo_iterations=10,
        random_seed=10,
    )
    decision = ResearchDecision.draft(decision_id="p10-fixture-intelligence").transition(
        target_state=ResearchDecisionState.RESEARCH_ONLY
    )
    contributions = tuple(
        ProductContribution(
            product_id=f"fixture.{commodity_id}",
            total_return=sum(
                score.synthetic_alignment_score
                for score in result.scores
            )
            / len(result.scores),
            turnover=0.0,
            max_drawdown=0.0,
        )
        for commodity_id in sorted(cases)
    )
    card = ResearchCard.create(
        card_id="p10-fixture-intelligence-card",
        run_manifest=manifest,
        validation_report=validation,
        decision=decision,
        product_contributions=contributions,
        limitations=(
            "fixture-only synthetic alignment outcomes are not market returns",
            "no authorized market data, contracts, calendar, rules, target, approval, or order",
            "gold retraction is explicitly suppressed after its collected availability time",
        ),
    )
    expected_hashes = _mapping(fixture["expected"], "expected")
    actual_hashes = {
        "plan_hash": plan.plan_hash,
        "result_hash": result.result_hash,
        "run_fingerprint": manifest.run_fingerprint,
        "validation_report_hash": validation.report_hash,
        "research_card_hash": card.card_hash,
    }
    if actual_hashes != expected_hashes:
        raise P10FixtureReplayError("fixture-only replay golden hash drifted")
    return P10IntelligenceFeatureResearchChain(
        handoffs=upstream_handoffs,
        plan=plan,
        result=result,
        manifest=manifest,
        validation=validation,
        decision=decision,
        card=card,
    )


__all__ = [
    "P10FixtureReplayError",
    "P10IntelligenceFeatureResearchChain",
    "build_p10_intelligence_feature_research_chain",
]

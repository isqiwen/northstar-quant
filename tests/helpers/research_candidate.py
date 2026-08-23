"""Reusable, offline P2 research-candidate chain for cross-domain tests.

This fixture deliberately follows the real Dataset -> Feature -> Experiment ->
Backtest -> Validation -> ResearchCard path. It produces a human-approved
candidate only; it never grants trading authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import polars as pl

from northstar_quant.data.market.pit import (
    MarketDataKind,
    MarketDataPITSelector,
    MarketDataPITSpec,
)
from northstar_quant.research.backtest.models import (
    BacktestAssumptions,
    BacktestCodeReference,
    BacktestDataInputKind,
    BacktestDataReference,
    BacktestEngine,
    BacktestRequest,
    BacktestResult,
    RunManifest,
    TargetFrameReference,
)
from northstar_quant.research.experiments import (
    ExperimentModelAssumption,
    ExperimentPeriod,
    ExperimentRegistry,
    ExperimentRun,
    ExperimentRunStatus,
    ExperimentSpec,
    StrategyVersionReference,
)
from northstar_quant.research.features import (
    FeatureRegistry,
    FeatureSpec,
    FeatureValue,
    FeatureVersion,
)
from northstar_quant.research.reports import ProductContribution, ResearchCard
from northstar_quant.research.validation.admission import ResearchAdmissionResult
from northstar_quant.research.validation.framework import (
    ParameterNeighbor,
    ResearchInputEvidenceKind,
    ResearchValidationEvidence,
    ReturnObservation,
    RollingWindow,
    StressKind,
    StressScenario,
    ValidationPeriod,
    ValidationReturnSeries,
    ValidationSplit,
    WalkForwardFold,
    evaluate_validation,
)
from northstar_quant.research.validation.research_decision import (
    HumanResearchApproval,
    ResearchDecision,
    ResearchDecisionEvidence,
    ResearchDecisionState,
)
from tests.helpers.pit_publication import publish_authorized_pit_dataset


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _at(hour: int) -> datetime:
    return datetime(2026, 1, 5, hour, tzinfo=UTC)


class _CloseComputer:
    def __init__(self, *, feature_version_hash: str, implementation_hash: str) -> None:
        self.feature_version_hash = feature_version_hash
        self.implementation_hash = implementation_hash

    def compute(self, *, market_snapshot, parameters, lineage):
        return tuple(
            FeatureValue.from_lineage(
                lineage=lineage,
                key={"symbol": row["symbol"]},
                event_time=row["date"],
                value=float(row["close"]) * float(parameters["scale"]),
            )
            for row in market_snapshot.selected_frame().iter_rows(named=True)
        )


@dataclass(frozen=True, slots=True)
class ResearchCandidateChain:
    """Frozen P2 candidate identities that downstream boundary tests may inspect."""

    card: ResearchCard
    experiment: ExperimentSpec
    experiment_run: ExperimentRun
    strategy: StrategyVersionReference


def build_research_candidate_chain(
    root: Path,
    *,
    strategy_id: str = "futures.trend",
) -> ResearchCandidateChain:
    """Build one reproducible, human-approved P2 candidate research chain.

    ``strategy_id`` is explicit so cross-domain fixtures can construct
    independent activated sources for canonical multi-strategy composition.
    Each caller still receives only P2 candidate evidence; this helper never
    grants trading authority.
    """

    frame = pl.DataFrame(
        {
            "date": [date(2026, 1, 2), date(2026, 1, 5)],
            "symbol": ["RB", "RB"],
            "close": [3500.0, 3550.0],
            "available_at": [_at(8), _at(9)],
        }
    ).with_columns(pl.col("available_at").cast(pl.Datetime("us", "UTC")))
    store, dataset = publish_authorized_pit_dataset(
        root,
        frame,
        dataset_id="p2_e2e_market",
        source_id="p2_e2e_source",
        adapter_id="p2-e2e-adapter",
        schema_version="p2.e2e.market.v1",
        artifact_id="p2-e2e-normalized",
        key_columns=("date", "symbol"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=("close",),
        normalized_available_at=_at(10),
    )
    pit_spec = MarketDataPITSpec(
        kind=MarketDataKind.BAR,
        key_columns=("date", "symbol"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=("close",),
        schema_version="p2.e2e.market.v1",
    )
    snapshot = MarketDataPITSelector(store).select(
        dataset_version_hash=dataset.version_hash, spec=pit_spec, as_of=_at(11)
    )
    feature_spec = FeatureSpec(
        feature_id="technical.p2_e2e_close",
        family="technical",
        description="offline E2E close identity",
        input_columns=("date", "symbol", "close", "available_at"),
        input_schema_version="p2.e2e.market.v1",
        entity_key_columns=("symbol",),
        output_column="p2_e2e_close",
        event_time_column="date",
        available_at_column="available_at",
        lookback_semantics="immutable static as-of input only",
        missing_value_semantics="explicit missing",
    )
    feature_version = FeatureVersion.from_spec(
        feature_spec,
        version="1.0.0",
        implementation_hash=_hash("p2-e2e-feature"),
        code_revision="p2-e2e",
        parameter_schema={"scale": {"type": "number", "required": True, "minimum": 0}},
    )
    feature_registry = FeatureRegistry(artifact_store=store)
    feature_registry.register_spec(feature_spec)
    feature_registry.register_version(feature_version)
    lineage = feature_registry.create_market_data_lineage(
        feature_version_hash=feature_version.version_hash,
        market_snapshot=snapshot,
        parameters={"scale": 1.0},
    )
    feature_registry.register_computer(
        _CloseComputer(
            feature_version_hash=feature_version.version_hash,
            implementation_hash=feature_version.implementation_hash,
        )
    )
    feature_registry.materialize_deterministic_backfill(lineage)

    experiment_registry = ExperimentRegistry(feature_registry=feature_registry)
    strategy = StrategyVersionReference(
        strategy_id=strategy_id,
        version="1.0.0",
        spec_hash=_hash("p2-e2e-strategy-spec"),
        implementation_hash=_hash("p2-e2e-strategy-code"),
        code_revision="p2-e2e",
    )
    model = ExperimentModelAssumption.from_mapping(model_id="cost.fixed_bps", parameters={"bps": 1})
    experiment = experiment_registry.create_spec(
        experiment_id="p2_e2e",
        strategy=strategy,
        feature_lineage_hashes=(lineage.lineage_hash,),
        parameters={"lookback": 20},
        train_period=ExperimentPeriod(date(2024, 1, 1), date(2024, 6, 30)),
        validation_period=ExperimentPeriod(date(2024, 7, 1), date(2024, 9, 30)),
        oos_period=ExperimentPeriod(date(2024, 10, 1), date(2024, 12, 31)),
        cost_model=model,
        slippage_model=ExperimentModelAssumption.from_mapping(
            model_id="slippage.fixed_bps", parameters={"bps": 2}
        ),
        random_seed=7,
        code_revision="p2-e2e",
        input_as_of=_at(11),
    )
    experiment_run = experiment_registry.record_run(
        run_id="p2_e2e_run",
        spec_hash=experiment.spec_hash,
        status=ExperimentRunStatus.RECORDED,
        runner_id="static.reproducibility",
        run_configuration_hash=_hash("p2-e2e-run-config"),
        outcome_hash=_hash("p2-e2e-outcome"),
        evidence_hashes=(_hash("p2-e2e-evidence"),),
    )

    request = BacktestRequest(
        engine=BacktestEngine.WEIGHT_RETURN,
        profile_id="offline-profile",
        profile_config_sha256=_hash("profile"),
        profile_dimension_key="CN|FUTURES|1d|1d|trend_following",
        source_frequency="1d",
        signal_frequency="1d",
        execution_frequency="1d",
        settlement_frequency="1d_eod",
        result_frequency="1d_eod",
        selected_strategy_ids=("futures_trend",),
        target=TargetFrameReference.from_frame(
            pl.DataFrame(
                {
                    "date": ["2024-01-02"],
                    "symbol": ["RB_CONT"],
                    "target_weight": [0.1],
                }
            ),
            time_column="date",
        ),
        data=BacktestDataReference(
            input_kind=BacktestDataInputKind.LEGACY_MARKET_PROJECTION,
            dataset_id="p2_e2e_market",
            source_id="p2_e2e_source",
            adapter_id="p2-e2e-adapter",
            content_sha256=dataset.version_hash,
            schema_version="p2.e2e.market.v1",
            source_config_sha256=_hash("source-config"),
        ),
        assumptions=BacktestAssumptions(100_000, 1, 2, 3, 1, 0.2, 1, 1, 0, 2, 0.1),
        code=BacktestCodeReference("0.0.test", "deadbeef", False, _hash("worktree")),
    )
    result = BacktestResult(
        engine=BacktestEngine.WEIGHT_RETURN,
        total_return=0.1,
        annualized_return=0.2,
        max_drawdown=-0.05,
        turnover_estimate=0.3,
        equity_curve=(
            {"date": "2024-01-02", "equity": 1.0},
            {"date": "2024-01-03", "equity": 1.1},
        ),
    ).bind_request(request)
    manifest = RunManifest.create(
        request=request,
        result=result,
        analytics={"equity": 1.1},
        metrics={"return": 0.1},
        admission={"status": "INSUFFICIENT_EVIDENCE", "blocking_check_count": 1},
    )
    start = date(2024, 1, 1)
    series = ValidationReturnSeries.create(
        ReturnObservation(
            start + timedelta(days=index),
            0.003 if index % 4 else -0.001,
            "risk_on" if index % 2 else "risk_off",
        )
        for index in range(72)
    )
    split = ValidationSplit(
        ValidationPeriod(start, start + timedelta(days=23)),
        ValidationPeriod(start + timedelta(days=24), start + timedelta(days=47)),
        ValidationPeriod(start + timedelta(days=48), start + timedelta(days=71)),
    )
    validation = evaluate_validation(
        evidence=ResearchValidationEvidence(
            dataset_version_hashes=experiment.dataset_version_hashes,
            feature_version_hashes=(feature_version.version_hash,),
            strategy_version_hash=strategy.reference_hash,
            experiment_spec_hash=experiment.spec_hash,
            experiment_run_hash=experiment_run.run_hash,
            backtest_result_hash=manifest.result.result_hash,
            input_kind=ResearchInputEvidenceKind.DATASET_VERSIONED,
            fixture_replay_binding_hash=None,
            code_revision="p2-e2e",
        ),
        series=series,
        split=split,
        walk_forward_folds=(
            WalkForwardFold(
                "wf_01",
                ValidationSplit(
                    ValidationPeriod(start, start + timedelta(days=11)),
                    ValidationPeriod(start + timedelta(days=12), start + timedelta(days=17)),
                    ValidationPeriod(start + timedelta(days=18), start + timedelta(days=23)),
                ),
            ),
        ),
        rolling_window=RollingWindow(12, 6),
        stress_scenarios=(StressScenario("base", StressKind.BASELINE),),
        parameter_neighbors=(
            ParameterNeighbor.create(
                neighbor_id="lookback_19", parameters={"lookback": 19}, series=series
            ),
        ),
        bootstrap_iterations=10,
        monte_carlo_iterations=10,
        random_seed=7,
    )
    admission = ResearchAdmissionResult(
        "research-policy",
        _hash("policy"),
        "PASS",
        True,
        "p2_e2e_source",
        "cn-futures",
        (),
        "review complete",
    )
    decision_evidence = ResearchDecisionEvidence.from_validation_report(
        experiment_spec_hash=experiment.spec_hash,
        experiment_run_hash=experiment_run.run_hash,
        backtest_result_hash=manifest.result.result_hash,
        validation_report=validation,
        admission_result=admission,
    )
    decision = (
        ResearchDecision.draft(decision_id="p2-e2e")
        .transition(target_state=ResearchDecisionState.RESEARCH_ONLY)
        .transition(
            target_state=ResearchDecisionState.CANDIDATE,
            evidence=decision_evidence,
            approval=HumanResearchApproval(
                "p2-e2e-approval",
                "research-owner",
                _at(12),
                ResearchDecisionState.CANDIDATE,
                "reviewed",
            ),
        )
    )
    card = ResearchCard.create(
        card_id="p2-e2e-card",
        run_manifest=manifest,
        validation_report=validation,
        decision=decision,
        product_contributions=(ProductContribution("RB", 0.04, 0.2, -0.03),),
        limitations=("weight return is a continuous-return approximation",),
    )
    return ResearchCandidateChain(
        card=card,
        experiment=experiment,
        experiment_run=experiment_run,
        strategy=strategy,
    )


__all__ = ["ResearchCandidateChain", "build_research_candidate_chain"]

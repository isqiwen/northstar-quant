"""P2-WP03 静态可复现实验账本的回归。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from hashlib import sha256

import polars as pl
import pytest

from northstar_quant.data_platform.market.pit import (
    MarketDataKind,
    MarketDataPITSelector,
    MarketDataPITSpec,
)
from northstar_quant.research.experiments import (
    STATIC_REPRODUCIBILITY_SELECTION_MODE,
    ExperimentError,
    ExperimentModelAssumption,
    ExperimentPeriod,
    ExperimentRegistry,
    ExperimentRun,
    ExperimentRunStatus,
    ExperimentSpec,
    StrategyVersionReference,
)
from northstar_quant.research.features import (
    FeatureComputer,
    FeatureRegistry,
    FeatureSpec,
    FeatureValue,
    FeatureVersion,
)
from tests.helpers.pit_publication import publish_authorized_pit_dataset


def _hash(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _at(hour: int) -> datetime:
    return datetime(2026, 1, 5, hour, tzinfo=UTC)


def _pit_spec() -> MarketDataPITSpec:
    return MarketDataPITSpec(
        kind=MarketDataKind.BAR,
        key_columns=("date", "symbol"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=("close",),
        schema_version="experiment.market.v1",
    )


def _feature_spec() -> FeatureSpec:
    return FeatureSpec(
        feature_id="technical.experiment_close",
        family="technical",
        description="实验账本受控输入的收盘价恒等特征。",
        input_columns=("date", "symbol", "close", "available_at"),
        input_schema_version="experiment.market.v1",
        entity_key_columns=("symbol",),
        output_column="experiment_close",
        event_time_column="date",
        available_at_column="available_at",
        lookback_semantics="不使用窗口，只读取 immutable static as-of close。",
        missing_value_semantics="输入缺失时保留显式缺失。",
    )


class _CloseComputer:
    """仅供测试的受控 FeatureComputer。"""

    def __init__(self, *, feature_version_hash: str, implementation_hash: str) -> None:
        self.feature_version_hash = feature_version_hash
        self.implementation_hash = implementation_hash

    def compute(self, *, market_snapshot, parameters, lineage):
        frame = market_snapshot.selected_frame()
        scale = float(parameters["scale"])
        return tuple(
            FeatureValue.from_lineage(
                lineage=lineage,
                key={"symbol": row["symbol"]},
                event_time=row["date"],
                value=float(row["close"]) * scale,
            )
            for row in frame.iter_rows(named=True)
        )


def _registered_feature_registry(tmp_path) -> tuple[FeatureRegistry, str]:
    frame = pl.DataFrame(
        {
            "date": [date(2026, 1, 2), date(2026, 1, 5)],
            "symbol": ["RB", "RB"],
            "close": [3500.0, 3550.0],
            "available_at": [_at(8), _at(9)],
        }
    ).with_columns(pl.col("available_at").cast(pl.Datetime("us", "UTC")))
    store, dataset = publish_authorized_pit_dataset(
        tmp_path,
        frame,
        dataset_id="experiment_market_fixture",
        source_id="experiment_fixture_source",
        adapter_id="experiment-fixture-adapter",
        schema_version="experiment.market.v1",
        artifact_id="experiment-market-normalized",
        key_columns=("date", "symbol"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=("close",),
        normalized_available_at=_at(10),
    )
    snapshot = MarketDataPITSelector(store).select(
        dataset_version_hash=dataset.version_hash,
        spec=_pit_spec(),
        as_of=_at(11),
    )
    registry = FeatureRegistry(artifact_store=store)
    spec = _feature_spec()
    version = FeatureVersion.from_spec(
        spec,
        version="1.0.0",
        implementation_hash=_hash("experiment-close-computer-v1"),
        code_revision="build-20260820",
        parameter_schema={"scale": {"type": "number", "required": True, "minimum": 0}},
    )
    registry.register_spec(spec)
    registry.register_version(version)
    lineage = registry.create_market_data_lineage(
        feature_version_hash=version.version_hash,
        market_snapshot=snapshot,
        parameters={"scale": 1.0},
    )
    computer: FeatureComputer = _CloseComputer(
        feature_version_hash=version.version_hash,
        implementation_hash=version.implementation_hash,
    )
    registry.register_computer(computer)
    registry.materialize_deterministic_backfill(lineage)
    return registry, lineage.lineage_hash


def _strategy() -> StrategyVersionReference:
    return StrategyVersionReference(
        strategy_id="futures.trend",
        version="1.0.0",
        spec_hash=_hash("futures trend strategy spec"),
        implementation_hash=_hash("futures trend strategy implementation"),
        code_revision="build-20260820",
    )


def _model(model_id: str) -> ExperimentModelAssumption:
    return ExperimentModelAssumption.from_mapping(
        model_id=model_id,
        parameters={"currency": "CNY", "rate": 0.0002},
    )


def _create_spec(
    experiments: ExperimentRegistry,
    lineage_hash: str,
    *,
    experiment_id: str = "roc",
    parameters: dict[str, object] | None = None,
    input_as_of: datetime | None = None,
    random_seed: int = 7,
):
    return experiments.create_spec(
        experiment_id=experiment_id,
        strategy=_strategy(),
        feature_lineage_hashes=(lineage_hash,),
        parameters=(
            parameters if parameters is not None else {"holding_bars": 5, "threshold": 0.2}
        ),
        train_period=ExperimentPeriod(date(2024, 1, 1), date(2024, 12, 31)),
        validation_period=ExperimentPeriod(date(2025, 1, 1), date(2025, 6, 30)),
        oos_period=ExperimentPeriod(date(2025, 7, 1), date(2025, 12, 31)),
        cost_model=_model("cost.fixed_bps"),
        slippage_model=_model("slippage.fixed_bps"),
        random_seed=random_seed,
        code_revision="build-20260820",
        input_as_of=input_as_of if input_as_of is not None else _at(11),
    )


def test_experiment_registry_freezes_feature_lineage_backfill_and_full_dataset_evidence(tmp_path):
    feature_registry, lineage_hash = _registered_feature_registry(tmp_path)
    experiments = ExperimentRegistry(feature_registry=feature_registry)

    spec = _create_spec(experiments, lineage_hash)
    assert spec.selection_mode == STATIC_REPRODUCIBILITY_SELECTION_MODE
    assert spec.decision_time_safe is False
    assert spec.eligible_for_backtest is False
    assert spec.eligible_for_admission is False
    assert spec.input_as_of == _at(11)
    assert spec.dataset_version_hashes
    assert feature_registry.get_lineage(lineage_hash).lineage_hash == lineage_hash
    assert feature_registry.get_backfill(lineage_hash).lineage_hash == lineage_hash

    feature_input = spec.feature_inputs[0]
    assert feature_input.lineage_hash == lineage_hash
    assert feature_input.source_selection_mode == "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY"
    assert feature_input.source_decision_time_safe is False
    manifest_input = spec.as_manifest_mapping()["feature_inputs"][0]
    assert (
        manifest_input["dataset_inputs"][0]["dataset_version_hash"] in spec.dataset_version_hashes
    )
    assert manifest_input["dataset_inputs"][0]["publication_authorization_hash"]
    assert manifest_input["dataset_inputs"][0]["selected_frame_hash"]
    assert (
        manifest_input["dataset_inputs"][0]["publication_scope"]["purpose"] == "historical_backtest"
    )

    assert (
        _create_spec(
            experiments,
            lineage_hash,
            parameters={"threshold": 0.2, "holding_bars": 5},
        )
        is spec
    )
    with pytest.raises(ExperimentError, match="不同 experiment_id"):
        _create_spec(experiments, lineage_hash, experiment_id="same_content_other_name")

    with pytest.raises(ExperimentError, match="同一个静态 input_as_of"):
        _create_spec(experiments, lineage_hash, experiment_id="future_as_of", input_as_of=_at(12))
    with pytest.raises(ExperimentError, match="random_seed"):
        _create_spec(experiments, lineage_hash, experiment_id="boolean_seed", random_seed=True)
    with pytest.raises(ExperimentError, match="random_seed"):
        _create_spec(experiments, lineage_hash, experiment_id="negative_seed", random_seed=-1)


def test_experiment_run_is_static_record_only_idempotent_and_rejects_different_result(tmp_path):
    feature_registry, lineage_hash = _registered_feature_registry(tmp_path)
    experiments = ExperimentRegistry(feature_registry=feature_registry)
    spec = _create_spec(experiments, lineage_hash)
    kwargs = {
        "run_id": "roc_static_run",
        "spec_hash": spec.spec_hash,
        "status": ExperimentRunStatus.RECORDED,
        "runner_id": "static.reproducibility",
        "run_configuration_hash": _hash("static run configuration"),
        "outcome_hash": _hash("static metrics"),
        "evidence_hashes": (_hash("static evidence"),),
    }

    run = experiments.record_run(**kwargs)
    assert run.selection_mode == STATIC_REPRODUCIBILITY_SELECTION_MODE
    assert run.decision_time_safe is False
    assert run.eligible_for_backtest is False
    assert run.eligible_for_admission is False
    assert experiments.record_run(**kwargs) is run
    assert experiments.get_run("roc_static_run") is run

    same_result_other_id = ExperimentRun.from_spec(
        run_id="same_result_other_run",
        spec=spec,
        status=ExperimentRunStatus.RECORDED,
        runner_id="static.reproducibility",
        run_configuration_hash=_hash("static run configuration"),
        outcome_hash=_hash("static metrics"),
        evidence_hashes=(_hash("static evidence"),),
    )
    assert same_result_other_id.run_hash == run.run_hash

    with pytest.raises(ExperimentError, match="结果不同"):
        experiments.record_run(
            **{
                **kwargs,
                "outcome_hash": _hash("different static metrics"),
            }
        )
    with pytest.raises(ExperimentError, match="不同 run_id"):
        experiments.record_run(**{**kwargs, "run_id": "same_result_other_run"})


def test_experiment_run_accepts_only_hash_references_not_raw_payloads(tmp_path):
    feature_registry, lineage_hash = _registered_feature_registry(tmp_path)
    experiments = ExperimentRegistry(feature_registry=feature_registry)
    spec = _create_spec(experiments, lineage_hash)

    with pytest.raises(ExperimentError, match="SHA-256"):
        experiments.record_run(
            run_id="raw_outcome",
            spec_hash=spec.spec_hash,
            status=ExperimentRunStatus.RECORDED,
            runner_id="static.reproducibility",
            run_configuration_hash=_hash("safe configuration"),
            outcome_hash="RB=3550.0",
            evidence_hashes=(_hash("safe evidence"),),
        )
    with pytest.raises(ExperimentError, match="list 或 tuple"):
        experiments.record_run(
            run_id="raw_evidence",
            spec_hash=spec.spec_hash,
            status=ExperimentRunStatus.RECORDED,
            runner_id="static.reproducibility",
            run_configuration_hash=_hash("safe configuration"),
            outcome_hash=_hash("safe outcome"),
            evidence_hashes={"values": {"RB": 3550.0}},
        )
    with pytest.raises(ExperimentError, match="list 或 tuple"):
        experiments.record_run(
            run_id="mapping_with_valid_hash_key",
            spec_hash=spec.spec_hash,
            status=ExperimentRunStatus.RECORDED,
            runner_id="static.reproducibility",
            run_configuration_hash=_hash("safe configuration"),
            outcome_hash=_hash("safe outcome"),
            evidence_hashes={_hash("valid key"): {"values": {"RB": 3550.0}}},
        )
    with pytest.raises(ExperimentError, match="list 或 tuple"):
        ExperimentRun.from_spec(
            run_id="classmethod_mapping",
            spec=spec,
            status=ExperimentRunStatus.RECORDED,
            runner_id="static.reproducibility",
            run_configuration_hash=_hash("safe configuration"),
            outcome_hash=_hash("safe outcome"),
            evidence_hashes={_hash("valid key"): {"values": {"RB": 3550.0}}},
        )


@pytest.mark.parametrize(
    "feature_input_hashes",
    [
        {_hash("feature input"): {"values": {"RB": 3550.0}}},
        _hash("feature input"),
        _hash("feature input").encode("utf-8"),
    ],
)
def test_experiment_run_direct_constructor_rejects_non_tuple_feature_hashes(feature_input_hashes):
    with pytest.raises(ExperimentError, match="feature_input_hashes.*元组"):
        ExperimentRun(
            run_id="direct_constructor",
            spec_hash=_hash("spec"),
            feature_input_hashes=feature_input_hashes,
            status=ExperimentRunStatus.RECORDED,
            runner_id="static.reproducibility",
            run_configuration_hash=_hash("configuration"),
            outcome_hash=_hash("outcome"),
            evidence_hashes=(_hash("evidence"),),
        )


def test_experiment_input_and_spec_reject_mapping_iterables_without_losing_values(tmp_path):
    feature_registry, lineage_hash = _registered_feature_registry(tmp_path)
    experiments = ExperimentRegistry(feature_registry=feature_registry)
    spec = _create_spec(experiments, lineage_hash)
    feature_input = spec.feature_inputs[0]
    dataset_input = feature_input.dataset_inputs[0]

    with pytest.raises(ExperimentError, match="dataset_inputs.*元组"):
        replace(
            feature_input,
            dataset_inputs={dataset_input: {"values": {"RB": 3550.0}}},
        )
    with pytest.raises(ExperimentError, match="feature_inputs.*list 或 tuple"):
        ExperimentSpec.create(
            experiment_id="mapping_factory",
            strategy=spec.strategy,
            feature_inputs={feature_input: {"values": {"RB": 3550.0}}},
            parameters=spec.parameters,
            train_period=spec.train_period,
            validation_period=spec.validation_period,
            oos_period=spec.oos_period,
            cost_model=spec.cost_model,
            slippage_model=spec.slippage_model,
            random_seed=spec.random_seed,
            code_revision=spec.code_revision,
            input_as_of=spec.input_as_of,
        )
    with pytest.raises(ExperimentError, match="feature_inputs.*元组"):
        replace(spec, feature_inputs={feature_input: {"values": {"RB": 3550.0}}})


@pytest.mark.parametrize(
    "note",
    ["saved at /tmp/northstar.csv", "../northstar.csv", r"C:\\Users\\dev\\northstar.csv"],
)
def test_experiment_model_assumption_rejects_embedded_or_relative_paths(note):
    with pytest.raises(ExperimentError, match="路径"):
        ExperimentModelAssumption.from_mapping(
            model_id="cost.fixed_bps",
            parameters={"note": note},
        )


@pytest.mark.parametrize("raw_key", ["data", "bars", "feature_value", "raw_values", "values"])
def test_experiment_model_assumption_rejects_raw_content_fields(raw_key):
    with pytest.raises(ExperimentError, match="原始数据字段"):
        ExperimentModelAssumption.from_mapping(
            model_id="cost.fixed_bps",
            parameters={raw_key: {"RB": 3550.0}},
        )


def test_experiment_parameters_and_model_assumptions_reject_nested_raw_structures(tmp_path):
    feature_registry, lineage_hash = _registered_feature_registry(tmp_path)
    experiments = ExperimentRegistry(feature_registry=feature_registry)

    with pytest.raises(ExperimentError, match="扁平有限标量"):
        _create_spec(
            experiments,
            lineage_hash,
            experiment_id="nested_parameters",
            parameters={"x": {"v0": 3500.0, "v1": 3550.0}},
        )
    with pytest.raises(ExperimentError, match="扁平有限标量"):
        ExperimentModelAssumption.from_mapping(
            model_id="cost.fixed_bps",
            parameters={"x": {"v0": 3500.0, "v1": 3550.0}},
        )


def test_experiment_spec_rejects_unknown_inputs_period_overlap_and_build_mismatch(tmp_path):
    feature_registry, lineage_hash = _registered_feature_registry(tmp_path)
    experiments = ExperimentRegistry(feature_registry=feature_registry)

    with pytest.raises(ExperimentError, match="已登记的 FeatureLineage/Backfill"):
        _create_spec(experiments, _hash("unknown lineage"))

    with pytest.raises(ExperimentError, match="不可重叠"):
        experiments.create_spec(
            experiment_id="overlap",
            strategy=_strategy(),
            feature_lineage_hashes=(lineage_hash,),
            parameters={"holding_bars": 5},
            train_period=ExperimentPeriod(date(2024, 1, 1), date(2024, 12, 31)),
            validation_period=ExperimentPeriod(date(2024, 12, 31), date(2025, 6, 30)),
            oos_period=ExperimentPeriod(date(2025, 7, 1), date(2025, 12, 31)),
            cost_model=_model("cost.fixed_bps"),
            slippage_model=_model("slippage.fixed_bps"),
            random_seed=7,
            code_revision="build-20260820",
            input_as_of=_at(11),
        )

    with pytest.raises(ExperimentError, match="strategy.code_revision"):
        experiments.create_spec(
            experiment_id="wrong_build",
            strategy=_strategy(),
            feature_lineage_hashes=(lineage_hash,),
            parameters={"holding_bars": 5},
            train_period=ExperimentPeriod(date(2024, 1, 1), date(2024, 12, 31)),
            validation_period=ExperimentPeriod(date(2025, 1, 1), date(2025, 6, 30)),
            oos_period=ExperimentPeriod(date(2025, 7, 1), date(2025, 12, 31)),
            cost_model=_model("cost.fixed_bps"),
            slippage_model=_model("slippage.fixed_bps"),
            random_seed=7,
            code_revision="other-build",
            input_as_of=_at(11),
        )

"""P2-WP04：统一回测合同只统一审计边界，不抹平引擎真实性。"""

from __future__ import annotations

import copy
import subprocess

import polars as pl
import pytest

from northstar_quant.research.backtest.models import (
    BacktestAssumptions,
    BacktestCodeReference,
    BacktestContractError,
    BacktestDataInputKind,
    BacktestDataReference,
    BacktestEngine,
    BacktestRequest,
    BacktestResult,
    RunManifest,
    TargetFrameReference,
)
from northstar_quant.research.backtest.registry import register_target_backtester
from northstar_quant.application.backtest import _source_control_metadata
from tests.helpers.paths import PROJECT_ROOT


_HASH_A = "a" * 64
_HASH_B = "b" * 64
_HASH_C = "c" * 64
_HASH_D = "d" * 64
_HASH_E = "e" * 64
_HASH_F = "f" * 64


def _assumptions() -> BacktestAssumptions:
    return BacktestAssumptions(
        initial_cash=100_000.0,
        commission_bps=1.0,
        min_commission=2.0,
        slippage_bps=3.0,
        slippage_ticks=1.0,
        max_volume_participation=0.2,
        lot_size=1,
        execution_delay_sessions=1,
        sellable_after_sessions=0,
        order_ttl_bars=2,
        queue_ahead_ratio=0.1,
    )


def _target_reference() -> TargetFrameReference:
    return TargetFrameReference.from_frame(
        pl.DataFrame(
            {
                "date": ["2024-01-03", "2024-01-02"],
                "symbol": ["RB_CONT", "RB_CONT"],
                "target_weight": [0.2, 0.1],
            }
        ),
        time_column="date",
    )


def _request(engine: BacktestEngine = BacktestEngine.WEIGHT_RETURN) -> BacktestRequest:
    return BacktestRequest(
        engine=engine,
        profile_id="offline-profile",
        profile_config_sha256=_HASH_A,
        profile_dimension_key="CN|FUTURES|1d|1d|trend_following",
        source_frequency="1m" if engine is BacktestEngine.FUTURES_INTRADAY_REPLAY else "1d",
        signal_frequency="1d",
        execution_frequency="1m" if engine is BacktestEngine.FUTURES_INTRADAY_REPLAY else "1d",
        settlement_frequency="1d_eod",
        result_frequency="1d_eod",
        selected_strategy_ids=("futures_trend",),
        target=_target_reference(),
        data=BacktestDataReference(
            input_kind=BacktestDataInputKind.LEGACY_MARKET_PROJECTION,
            dataset_id="research-dataset",
            source_id="fixture-source",
            adapter_id="fixture-adapter",
            content_sha256=_HASH_B,
            schema_version="market_data_v2",
            source_config_sha256=_HASH_C,
        ),
        assumptions=_assumptions(),
        code=BacktestCodeReference(
            package_version="0.0.test",
            git_commit="deadbeef",
            git_dirty=False,
            worktree_sha256=_HASH_D,
        ),
    )


def _result(engine: BacktestEngine) -> BacktestResult:
    payload: dict[str, object] = {
        "engine": engine,
        "total_return": 0.1,
        "annualized_return": 0.2,
        "max_drawdown": -0.05,
        "turnover_estimate": 0.3,
        "equity_curve": [
            {"date": "2024-01-02", "equity": 1.0},
            {"date": "2024-01-03", "equity": 1.1},
        ],
        "orders": (
            [{"order_id": "o-1", "status": "FILLED", "requested_qty": 2, "filled_qty": 2}]
            if engine is BacktestEngine.FUTURES_INTRADAY_REPLAY
            else ()
        ),
    }
    if engine is not BacktestEngine.WEIGHT_RETURN:
        payload["trades"] = [
            {
                "date": "2024-01-03",
                "reason": "target_open",
                "qty": 2,
                "commission": 4.0,
                "notional": 20_000.0,
            }
        ]
    return BacktestResult(**payload)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("engine", "fidelity", "audit_level", "models_orders"),
    [
        (
            BacktestEngine.WEIGHT_RETURN,
            "continuous_return_approximation",
            "not_modeled",
            False,
        ),
        (
            BacktestEngine.FUTURES_DAILY,
            "actual_contract_daily_state_machine",
            "target_events_and_fill_events",
            False,
        ),
        (
            BacktestEngine.FUTURES_INTRADAY_REPLAY,
            "actual_contract_intraday_order_replay",
            "orders_and_fill_events",
            True,
        ),
    ],
)
def test_result_declares_engine_specific_fidelity_without_false_uniformity(
    engine: BacktestEngine,
    fidelity: str,
    audit_level: str,
    models_orders: bool,
) -> None:
    result = _result(engine)

    assert result.fidelity.value == fidelity
    assert result.execution_audit.level.value == audit_level
    assert result.semantics.models_orders is models_orders
    assert result.eligible_for_admission is False
    assert result.limitations


def test_target_reference_is_order_independent_but_schema_and_content_sensitive() -> None:
    original = _target_reference()
    reordered = TargetFrameReference.from_frame(
        pl.DataFrame(
            {
                "date": ["2024-01-02", "2024-01-03"],
                "symbol": ["RB_CONT", "RB_CONT"],
                "target_weight": [0.1, 0.2],
            }
        ),
        time_column="date",
    )
    changed_schema = TargetFrameReference.from_frame(
        pl.DataFrame(
            {
                "date": ["2024-01-02", "2024-01-03"],
                "symbol": ["RB_CONT", "RB_CONT"],
                "target_weight": [0.1, 0.2],
                "signal_value": [1.0, 1.0],
            }
        ),
        time_column="date",
    )

    assert reordered.target_frame_sha256 == original.target_frame_sha256
    assert changed_schema.target_frame_sha256 != original.target_frame_sha256


def test_target_reference_rejects_duplicate_or_non_finite_target_weight() -> None:
    duplicate = pl.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-02"],
            "symbol": ["RB_CONT", "RB_CONT"],
            "target_weight": [0.1, 0.2],
        }
    )
    non_finite = pl.DataFrame(
        {
            "date": ["2024-01-02"],
            "symbol": ["RB_CONT"],
            "target_weight": [float("nan")],
        }
    )

    with pytest.raises(BacktestContractError, match="重复"):
        TargetFrameReference.from_frame(duplicate, time_column="date")
    with pytest.raises(BacktestContractError, match="有限"):
        TargetFrameReference.from_frame(non_finite, time_column="date")


def test_backtest_data_reference_rejects_hand_declared_decision_safe_pit() -> None:
    point_in_time = {
        "as_of": "2024-01-02T10:00:00+00:00",
        "dataset_id": "research-dataset",
        "dataset_version_hash": _HASH_A,
        "format": "northstar.market_data_pit_snapshot.v1",
        "publication_authorization_hash": _HASH_B,
        "publication_scope": {"purpose": "historical_backtest"},
        "publication_scope_hash": _HASH_C,
        "revision_ids": [_HASH_D],
        "row_count": 1,
        "decision_time_safe": True,
        "selection_mode": "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY",
        "selected_frame_hash": _HASH_E,
        "snapshot_id": _HASH_F,
        "source_artifact_snapshot_hash": _HASH_A,
        "source_artifact_available_at": "2024-01-02T09:00:00+00:00",
        "source_config_sha256": _HASH_C,
        "source_id": "fixture-source",
        "spec": {"schema_version": "market_data_v2"},
    }

    with pytest.raises(BacktestContractError, match="静态 as-of"):
        BacktestDataReference.from_source_manifest(
            {
                "dataset_id": "research-dataset",
                "data_source": "fixture-adapter",
                "content_sha256": _HASH_E,
                "schema_version": "market_data_v2",
                "governance": {
                    "source_id": "fixture-source",
                    "source_config_sha256": _HASH_C,
                },
                "point_in_time": point_in_time,
            }
        )


def test_backtest_data_reference_binds_outer_content_and_schema_to_pit_evidence() -> None:
    point_in_time = {
        "as_of": "2024-01-02T10:00:00+00:00",
        "dataset_id": "research-dataset",
        "dataset_version_hash": _HASH_A,
        "format": "northstar.market_data_pit_snapshot.v1",
        "publication_authorization_hash": _HASH_B,
        "publication_scope": {"purpose": "historical_backtest"},
        "publication_scope_hash": _HASH_C,
        "revision_ids": [_HASH_D],
        "row_count": 1,
        "decision_time_safe": False,
        "selection_mode": "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY",
        "selected_frame_hash": _HASH_E,
        "snapshot_id": _HASH_F,
        "source_artifact_snapshot_hash": _HASH_A,
        "source_artifact_available_at": "2024-01-02T09:00:00+00:00",
        "source_config_sha256": _HASH_C,
        "source_id": "fixture-source",
        "spec": {"schema_version": "market_data_v2"},
    }
    source_manifest = {
        "dataset_id": "research-dataset",
        "data_source": "fixture-adapter",
        "content_sha256": _HASH_E,
        "schema_version": "market_data_v2",
        "governance": {
            "source_id": "fixture-source",
            "source_config_sha256": _HASH_C,
        },
        "point_in_time": point_in_time,
    }

    invalid_content = copy.deepcopy(source_manifest)
    invalid_content["content_sha256"] = _HASH_B
    with pytest.raises(BacktestContractError, match="selected_frame_hash"):
        BacktestDataReference.from_source_manifest(invalid_content)

    invalid_schema = copy.deepcopy(source_manifest)
    invalid_schema["schema_version"] = "contradictory-schema"
    with pytest.raises(BacktestContractError, match="spec.schema_version"):
        BacktestDataReference.from_source_manifest(invalid_schema)


def test_result_deep_freezes_output_records() -> None:
    rows = [{"date": "2024-01-02", "equity": 1.0}]
    result = BacktestResult(
        engine=BacktestEngine.WEIGHT_RETURN,
        total_return=0.0,
        annualized_return=0.0,
        max_drawdown=0.0,
        turnover_estimate=0.0,
        equity_curve=rows,
    )
    rows[0]["equity"] = 999.0

    assert result.equity_curve[0]["equity"] == 1.0


def test_result_rejects_event_payloads_that_contradict_engine_fidelity() -> None:
    with pytest.raises(BacktestContractError, match="weight_return"):
        BacktestResult(
            engine=BacktestEngine.WEIGHT_RETURN,
            total_return=0.0,
            annualized_return=0.0,
            max_drawdown=0.0,
            turnover_estimate=0.0,
            equity_curve=[{"date": "2024-01-02", "equity": 1.0}],
            trades=[{"qty": 1}],
        )
    with pytest.raises(BacktestContractError, match="futures_daily"):
        BacktestResult(
            engine=BacktestEngine.FUTURES_DAILY,
            total_return=0.0,
            annualized_return=0.0,
            max_drawdown=0.0,
            turnover_estimate=0.0,
            equity_curve=[{"date": "2024-01-02", "equity": 1.0}],
            orders=[{"order_id": "not-supported"}],
        )


def test_builtin_backtester_registry_is_sealed_after_bootstrap() -> None:
    def impostor(*_args: object, **_kwargs: object) -> BacktestResult:
        return _result(BacktestEngine.FUTURES_INTRADAY_REPLAY)

    with pytest.raises(RuntimeError, match="已封存"):
        register_target_backtester(
            "actual_futures_intraday_replay_backtest",
            BacktestEngine.FUTURES_INTRADAY_REPLAY,
            impostor,  # type: ignore[arg-type]
            replace=True,
        )


def test_source_control_metadata_uses_repository_root_and_worktree_identity() -> None:
    expected_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    metadata = _source_control_metadata()

    assert metadata["git_commit"] == expected_commit
    assert isinstance(metadata["worktree_sha256"], str)
    assert len(metadata["worktree_sha256"]) == 64


def test_manifest_binds_request_result_and_detects_post_build_analytics_mutation() -> None:
    request = _request()
    result = _result(request.engine).bind_request(request)
    analytics: dict[str, object] = {"equity_curve": [{"date": "2024-01-03", "equity": 1.1}]}
    metrics: dict[str, object] = {"总收益率": 0.1}
    admission: dict[str, object] = {
        "status": "INSUFFICIENT_EVIDENCE",
        "policy_id": "policy-v1",
        "policy_config_sha256": _HASH_E,
        "blocking_check_count": 1,
    }
    manifest = RunManifest.create(
        request=request,
        result=result,
        analytics=analytics,
        metrics=metrics,
        admission=admission,
    )

    mapping = manifest.as_mapping()
    assert mapping["schema_version"] == "northstar_backtest_manifest_v4"
    assert mapping["run_id"] == f"bt-{request.request_hash[:16]}"
    assert mapping["candidate_admission_eligible"] is False
    assert mapping["research_admission"]["status"] == "NOT_ELIGIBLE"
    assert mapping["research_admission"]["observed_policy_status"] == "INSUFFICIENT_EVIDENCE"
    manifest.verify_outputs(result=result, analytics=analytics, metrics=metrics)

    tampered_analytics = copy.deepcopy(analytics)
    tampered_analytics["equity_curve"] = [{"date": "2024-01-03", "equity": 9.9}]
    with pytest.raises(BacktestContractError, match="analytics"):
        manifest.verify_outputs(
            result=result,
            analytics=tampered_analytics,
            metrics=metrics,
        )


def test_result_cannot_bind_to_request_with_another_engine() -> None:
    with pytest.raises(BacktestContractError, match="引擎"):
        _result(BacktestEngine.WEIGHT_RETURN).bind_request(
            _request(BacktestEngine.FUTURES_DAILY)
        )

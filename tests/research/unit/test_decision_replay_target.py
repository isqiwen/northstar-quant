"""P2-WP05：逐决策 target trace 的不可变合同。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import hashlib

import polars as pl
import pytest

from northstar_quant.application.decision_replay_backtest import _code_reference_sources
from northstar_quant.data_platform.market.pit import MarketDataKind, MarketDataPITSpec
from northstar_quant.research.backtest.models import TargetFrameReference
from northstar_quant.research.validation.decision_replay import (
    DecisionReplayStrategyIdentity,
    DecisionReplayTargetError,
    DecisionReplayTargetTrace,
    DecisionTarget,
    DecisionTargetSlice,
    DecisionTargetStatus,
)
from northstar_quant.research.validation.lookahead import (
    DecisionReplayCheckpoint,
    DecisionReplayPlan,
    LookaheadGuardError,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _spec() -> MarketDataPITSpec:
    return MarketDataPITSpec(
        kind=MarketDataKind.BAR,
        key_columns=("date", "symbol"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=("close",),
        schema_version="decision_replay_fixture_v1",
    )


def _checkpoint(offset: int = 0) -> DecisionReplayCheckpoint:
    day = date(2026, 1, 5) + timedelta(days=offset)
    return DecisionReplayCheckpoint(
        decision_at=datetime(2026, 1, 5 + offset, 16, tzinfo=UTC),
        decision_event_time=day,
        dataset_version_hash=_hash(f"dataset-{offset}"),
        pit_spec=_spec(),
    )


def _identity() -> DecisionReplayStrategyIdentity:
    return DecisionReplayStrategyIdentity(
        strategy_id="futures_trend",
        output_type="target_weight",
        time_column="date",
        effective_parameters_json='{"lookback_days":2}',
        profile_strategy_config_hash=_hash("profile-strategy"),
        implementation_hash=_hash("implementation"),
        code_reference_hash=_hash("code"),
    )


def _slice(
    checkpoint: DecisionReplayCheckpoint,
    identity: DecisionReplayStrategyIdentity,
    *,
    status: DecisionTargetStatus = DecisionTargetStatus.TARGETS,
    target_event_time: date | None = None,
) -> DecisionTargetSlice:
    targets = (
        DecisionTarget(symbol="CU_CONT", signal_value=0.2, target_weight=0.4),
        DecisionTarget(symbol="RB_CONT", signal_value=-0.1, target_weight=-0.4),
    )
    if status is DecisionTargetStatus.NO_TARGET_WARMUP:
        targets = ()
    return DecisionTargetSlice(
        checkpoint_hash=checkpoint.checkpoint_hash,
        decision_at=checkpoint.decision_at,
        decision_event_time=target_event_time or checkpoint.decision_event_time,
        market_snapshot_id=_hash(f"snapshot-{checkpoint.checkpoint_hash}"),
        market_selected_frame_hash=_hash(f"frame-{checkpoint.checkpoint_hash}"),
        market_revision_ids_hash=_hash(f"revisions-{checkpoint.checkpoint_hash}"),
        source_artifact_snapshot_hash=_hash(f"source-{checkpoint.checkpoint_hash}"),
        strategy_identity_hash=identity.identity_hash,
        time_column="date",
        target_status=status,
        targets=targets,
    )


def _trace(
    plan: DecisionReplayPlan,
    identity: DecisionReplayStrategyIdentity,
    slices: tuple[DecisionTargetSlice, ...],
) -> DecisionReplayTargetTrace:
    frames = [item.targets_frame() for item in slices if item.targets]
    aggregate = TargetFrameReference.from_frame(
        pl.concat(frames, how="vertical").sort(["date", "symbol"]),
        time_column="date",
    )
    return DecisionReplayTargetTrace(
        plan=plan,
        profile_id="cn_futures_daily_trend_offline",
        profile_config_sha256=_hash("profile"),
        profile_dimension_key="cn::futures::1d::1d::trend_following",
        selected_strategy_ids=("futures_trend",),
        strategy_identity=identity,
        target_slices=slices,
        aggregate_target=aggregate,
    )


def test_checkpoint_requires_explicit_date_or_aware_datetime_event_time() -> None:
    with pytest.raises(LookaheadGuardError):
        # 该错误来自 checkpoint 领域合同，断言在这里确保 target replay 不会猜测 UTC 日期。
        DecisionReplayCheckpoint(
            decision_at=datetime(2026, 1, 5, 16, tzinfo=UTC),
            decision_event_time="2026-01-05",  # type: ignore[arg-type]
            dataset_version_hash=_hash("dataset"),
            pit_spec=_spec(),
        )


def test_strategy_code_reference_closure_covers_the_controlled_target_path() -> None:
    closure = _code_reference_sources()
    assert {
        "allocation",
        "composition_root",
        "futures_trend",
        "global_risk",
        "market_signal_transform",
        "multi_strategy",
        "risk_limits",
        "strategy_base",
        "strategy_pipeline",
        "strategy_risk",
    } == set(closure)
    assert all(source.strip() for source in closure.values())


def test_strategy_identity_rejects_parameter_credentials() -> None:
    with pytest.raises(DecisionReplayTargetError, match="凭据字段"):
        DecisionReplayStrategyIdentity(
            strategy_id="futures_trend",
            output_type="target_weight",
            time_column="date",
            effective_parameters_json='{"api_key":"safe-test-value"}',
            profile_strategy_config_hash=_hash("profile-strategy"),
            implementation_hash=_hash("implementation"),
            code_reference_hash=_hash("code"),
        )


def test_target_trace_binds_every_checkpoint_and_hides_raw_targets_from_manifest() -> None:
    first = _checkpoint()
    second = _checkpoint(1)
    plan = DecisionReplayPlan.create((first, second))
    identity = _identity()
    trace = _trace(
        plan,
        identity,
        (
            _slice(first, identity, status=DecisionTargetStatus.NO_TARGET_WARMUP),
            _slice(second, identity),
        ),
    )

    target_frame = trace.targets_frame()
    assert target_frame.select("date").unique().item() == date(2026, 1, 6)
    assert target_frame.get_column("symbol").to_list() == ["CU_CONT", "RB_CONT"]
    assert trace.targets_frame().equals(target_frame)
    manifest = trace.as_mapping()
    assert manifest["decision_time_safe"] is False
    assert manifest["candidate_admission_eligible"] is False
    assert "targets" not in manifest["target_slices"][1]  # type: ignore[index]

    # 返回的 DataFrame 是副本；调用方改写它不能回写冻结 trace。
    target_frame[0, "target_weight"] = 99.0
    assert trace.targets_frame()[0, "target_weight"] == 0.4


def test_target_trace_rejects_checkpoint_event_time_or_aggregate_mismatch() -> None:
    checkpoint = _checkpoint()
    plan = DecisionReplayPlan.create((checkpoint,))
    identity = _identity()
    mismatched_slice = _slice(
        checkpoint,
        identity,
        target_event_time=date(2026, 1, 6),
    )
    aggregate = TargetFrameReference.from_frame(
        mismatched_slice.targets_frame(),
        time_column="date",
    )
    with pytest.raises(DecisionReplayTargetError, match="decision_event_time"):
        DecisionReplayTargetTrace(
            plan=plan,
            profile_id="cn_futures_daily_trend_offline",
            profile_config_sha256=_hash("profile"),
            profile_dimension_key="cn::futures::1d::1d::trend_following",
            selected_strategy_ids=("futures_trend",),
            strategy_identity=identity,
            target_slices=(mismatched_slice,),
            aggregate_target=aggregate,
        )

    valid_slice = _slice(checkpoint, identity)
    wrong_aggregate = TargetFrameReference.from_frame(
        pl.DataFrame(
            {
                "date": [date(2026, 1, 5)],
                "symbol": ["RB_CONT"],
                "signal_value": [0.1],
                "target_weight": [0.1],
            }
        ),
        time_column="date",
    )
    with pytest.raises(DecisionReplayTargetError, match="aggregate_target"):
        DecisionReplayTargetTrace(
            plan=plan,
            profile_id="cn_futures_daily_trend_offline",
            profile_config_sha256=_hash("profile"),
            profile_dimension_key="cn::futures::1d::1d::trend_following",
            selected_strategy_ids=("futures_trend",),
            strategy_identity=identity,
            target_slices=(valid_slice,),
            aggregate_target=wrong_aggregate,
        )


def test_target_slice_rejects_implicit_empty_or_unsorted_targets() -> None:
    checkpoint = _checkpoint()
    identity = _identity()
    with pytest.raises(DecisionReplayTargetError, match="TARGETS"):
        DecisionTargetSlice(
            checkpoint_hash=checkpoint.checkpoint_hash,
            decision_at=checkpoint.decision_at,
            decision_event_time=checkpoint.decision_event_time,
            market_snapshot_id=_hash("snapshot"),
            market_selected_frame_hash=_hash("frame"),
            market_revision_ids_hash=_hash("revisions"),
            source_artifact_snapshot_hash=_hash("source"),
            strategy_identity_hash=identity.identity_hash,
            time_column="date",
            target_status=DecisionTargetStatus.TARGETS,
            targets=(),
        )
    with pytest.raises(DecisionReplayTargetError, match="升序"):
        DecisionTargetSlice(
            checkpoint_hash=checkpoint.checkpoint_hash,
            decision_at=checkpoint.decision_at,
            decision_event_time=checkpoint.decision_event_time,
            market_snapshot_id=_hash("snapshot"),
            market_selected_frame_hash=_hash("frame"),
            market_revision_ids_hash=_hash("revisions"),
            source_artifact_snapshot_hash=_hash("source"),
            strategy_identity_hash=identity.identity_hash,
            time_column="date",
            target_status=DecisionTargetStatus.TARGETS,
            targets=(
                DecisionTarget(symbol="RB_CONT", signal_value=0.1, target_weight=0.4),
                DecisionTarget(symbol="CU_CONT", signal_value=0.2, target_weight=-0.4),
            ),
        )

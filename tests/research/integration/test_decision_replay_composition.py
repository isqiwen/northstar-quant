"""P2-WP05：Application 逐 checkpoint target replay 组合测试。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

import northstar_quant.application.decision_replay_backtest as decision_replay_backtest
from northstar_quant.application.decision_replay_backtest import (
    DecisionReplayCompositionError,
    build_profile_decision_replay_backtest_request,
    build_profile_decision_replay_receipt,
    build_profile_decision_replay_targets,
)
from northstar_quant.data_platform.artifacts.immutable_store import ArtifactStore
from northstar_quant.data_platform.contracts.instrument_universes import (
    load_instrument_universe,
)
from northstar_quant.data_platform.market.pit import (
    MarketDataKind,
    MarketDataPITError,
    MarketDataPITSpec,
)
from northstar_quant.platform.config.trading_profile import load_trading_profile
from northstar_quant.research.validation.lookahead import (
    DecisionReplayCheckpoint,
    DecisionReplayPlan,
)
from northstar_quant.research.backtest.models import BacktestContractError, BacktestResult
from tests.helpers.pit_publication import publish_authorized_pit_dataset


PROFILE_ID = "cn_futures_daily_trend_offline"
START_DAY = date(2026, 1, 1)


def _full_daily_spec() -> MarketDataPITSpec:
    return MarketDataPITSpec(
        kind=MarketDataKind.BAR,
        key_columns=("date", "symbol"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=("open", "high", "low", "close", "adjusted_close", "volume"),
        schema_version="market_data_v2",
    )


def _days(count: int) -> list[date]:
    return [START_DAY + timedelta(days=index) for index in range(count)]


def _decision_at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 16, tzinfo=UTC)


def _daily_frame(
    *,
    symbols: tuple[str, ...],
    days: list[date],
    late_revision: bool = False,
    missing_final_symbol: str | None = None,
    missing_symbol_day_index: int | None = None,
    missing_symbol: str | None = None,
) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    final_available_at = _decision_at(days[-1]) - timedelta(minutes=1)
    for day_index, current_day in enumerate(days):
        for symbol_index, symbol in enumerate(symbols):
            if day_index == len(days) - 1 and symbol == missing_final_symbol:
                continue
            if day_index == missing_symbol_day_index and symbol == missing_symbol:
                continue
            close = 100.0 + (symbol_index * 10.0) + float(day_index)
            available_at = _decision_at(current_day) - timedelta(minutes=1)
            if late_revision and day_index == 1 and symbol == "RB_CONT":
                # 后续 DatasetVersion 对历史事实做修订；它只能出现在后续 checkpoint 的 pin 中。
                close += 50.0
                available_at = final_available_at
            rows.append(
                {
                    "date": current_day,
                    "symbol": symbol,
                    "open": close - 0.5,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "adjusted_close": close,
                    "volume": 1000.0 + day_index + symbol_index,
                    "available_at": available_at,
                }
            )
    return pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(pl.Datetime("us", "UTC"))
    )


def _scope(profile_id: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    profile = load_trading_profile(profile_id)
    universe = load_instrument_universe(profile.universe_id)
    exchanges = tuple(dict.fromkeys(member.exchange for member in universe.members))
    products = tuple(dict.fromkeys(member.product for member in universe.members))
    return exchanges, products


def _publish_prefix(
    *,
    root: Path,
    store: ArtifactStore,
    profile_id: str,
    days: list[date],
    artifact_id: str,
    late_revision: bool = False,
    missing_final_symbol: str | None = None,
    missing_symbol_day_index: int | None = None,
    missing_symbol: str | None = None,
):
    profile = load_trading_profile(profile_id)
    exchanges, products = _scope(profile_id)
    frame = _daily_frame(
        symbols=tuple(profile.data.download.symbols),
        days=days,
        late_revision=late_revision,
        missing_final_symbol=missing_final_symbol,
        missing_symbol_day_index=missing_symbol_day_index,
        missing_symbol=missing_symbol,
    )
    return publish_authorized_pit_dataset(
        root,
        frame,
        dataset_id=profile.data.dataset_id,
        source_id=profile.data.source_id,
        adapter_id=profile.data.provider,
        schema_version="market_data_v2",
        artifact_id=artifact_id,
        key_columns=("date", "symbol"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=("open", "high", "low", "close", "adjusted_close", "volume"),
        normalized_available_at=_decision_at(days[-1]),
        store=store,
        scope_exchanges=exchanges,
        scope_products=products,
    )


def _checkpoint(*, day: date, dataset_version_hash: str) -> DecisionReplayCheckpoint:
    return DecisionReplayCheckpoint(
        decision_at=_decision_at(day),
        decision_event_time=day,
        dataset_version_hash=dataset_version_hash,
        pit_spec=_full_daily_spec(),
    )


class _OverriddenReplayPlan(DecisionReplayPlan):
    """模拟调用方试图覆写 immutable replay 的攻击载体。"""

    def replay_market_data(self, artifact_store: ArtifactStore):  # type: ignore[override]
        raise AssertionError("受控组合根不得调用子类覆写的 replay 方法")


class _OverriddenArtifactStore(ArtifactStore):
    """模拟调用方试图替换 immutable replay 的存储依赖。"""

    def replay_dataset_version(self, version_hash: str):  # type: ignore[override]
        raise AssertionError("受控组合根不得调用子类覆写的 ArtifactStore")


def test_composition_replays_prefix_versions_and_only_emits_current_target_slices(
    tmp_path: Path,
) -> None:
    days = _days(63)
    store = ArtifactStore(tmp_path / "artifacts")
    _, early_version = _publish_prefix(
        root=tmp_path,
        store=store,
        profile_id=PROFILE_ID,
        days=days[:62],
        artifact_id="decision-replay-early",
    )
    _, later_version = _publish_prefix(
        root=tmp_path,
        store=store,
        profile_id=PROFILE_ID,
        days=days,
        artifact_id="decision-replay-later-revised",
        late_revision=True,
    )
    early_checkpoint = _checkpoint(day=days[61], dataset_version_hash=early_version.version_hash)
    later_checkpoint = _checkpoint(day=days[62], dataset_version_hash=later_version.version_hash)

    early_trace = build_profile_decision_replay_targets(
        profile_id=PROFILE_ID,
        artifact_store=store,
        plan=DecisionReplayPlan.create((early_checkpoint,)),
    )
    full_trace = build_profile_decision_replay_targets(
        profile_id=PROFILE_ID,
        artifact_store=store,
        plan=DecisionReplayPlan.create((early_checkpoint, later_checkpoint)),
    )

    assert full_trace.target_slices[0].slice_hash == early_trace.target_slices[0].slice_hash
    assert full_trace.target_slices[0].market_snapshot_id != full_trace.target_slices[1].market_snapshot_id
    assert full_trace.target_slices[0].decision_event_time == days[61]
    assert full_trace.target_slices[1].decision_event_time == days[62]
    assert full_trace.targets_frame().get_column("date").unique().sort().to_list() == [
        days[61],
        days[62],
    ]
    manifest = full_trace.as_mapping()
    assert manifest["decision_time_safe"] is False
    assert manifest["candidate_admission_eligible"] is False
    assert manifest["aggregate_target"]["row_count"] == full_trace.targets_frame().height  # type: ignore[index]

    receipt = build_profile_decision_replay_receipt(
        profile_id=PROFILE_ID,
        artifact_store=store,
        plan=DecisionReplayPlan.create((early_checkpoint, later_checkpoint)),
    )
    assert receipt.trace.trace_hash == full_trace.trace_hash
    assert receipt.certificate.decision_time_safe is False
    assert receipt.certificate.candidate_admission_eligible is False
    certified_target_hashes = [
        report.evidence.target.target_hash for report in receipt.certificate.reports
    ]
    assert certified_target_hashes == [
        item.target_frame_sha256 for item in receipt.trace.target_slices
    ]
    receipt_mapping = receipt.as_mapping()
    assert receipt_mapping["decision_time_safe"] is False
    assert receipt_mapping["receipt_hash"] == receipt.receipt_hash

    request = build_profile_decision_replay_backtest_request(
        profile_id=PROFILE_ID,
        artifact_store=store,
        plan=DecisionReplayPlan.create((early_checkpoint, later_checkpoint)),
    )
    assert request.engine.value == "weight_return"
    assert request.target == receipt.trace.aggregate_target
    assert request.data.input_kind.value == "decision_replay_receipt"
    assert request.data.decision_time_safe is False
    assert request.data.decision_replay is not None
    assert request.data.decision_replay.receipt_hash == receipt.receipt_hash
    assert request.code.strategy_identity_hash == receipt.trace.strategy_identity.identity_hash
    with pytest.raises(BacktestContractError, match="construction-only"):
        BacktestResult(
            engine=request.engine,
            total_return=0.0,
            annualized_return=0.0,
            max_drawdown=0.0,
            turnover_estimate=0.0,
            equity_curve=({"date": str(days[-1]), "equity": 1.0},),
        ).bind_request(request)


def test_composition_rejects_a_later_dataset_version_for_an_earlier_checkpoint(
    tmp_path: Path,
) -> None:
    days = _days(62)
    store = ArtifactStore(tmp_path / "artifacts")
    _, later_version = _publish_prefix(
        root=tmp_path,
        store=store,
        profile_id=PROFILE_ID,
        days=days,
        artifact_id="decision-replay-final-view",
    )
    invalid_plan = DecisionReplayPlan.create(
        (_checkpoint(day=days[60], dataset_version_hash=later_version.version_hash),)
    )

    with pytest.raises(MarketDataPITError, match="尚不可用"):
        build_profile_decision_replay_targets(
            profile_id=PROFILE_ID,
            artifact_store=store,
            plan=invalid_plan,
        )


def test_composition_rejects_plan_subclass_with_overridden_replay(tmp_path: Path) -> None:
    days = _days(62)
    store = ArtifactStore(tmp_path / "artifacts")
    _, version = _publish_prefix(
        root=tmp_path,
        store=store,
        profile_id=PROFILE_ID,
        days=days,
        artifact_id="decision-replay-subclass",
    )
    plan = _OverriddenReplayPlan(
        checkpoints=(_checkpoint(day=days[-1], dataset_version_hash=version.version_hash),)
    )

    with pytest.raises(DecisionReplayCompositionError, match="精确的 DecisionReplayPlan"):
        build_profile_decision_replay_targets(
            profile_id=PROFILE_ID,
            artifact_store=store,
            plan=plan,
        )


def test_composition_rejects_artifact_store_subclass(tmp_path: Path) -> None:
    with pytest.raises(DecisionReplayCompositionError, match="精确的 ArtifactStore"):
        build_profile_decision_replay_targets(
            profile_id=PROFILE_ID,
            artifact_store=_OverriddenArtifactStore(tmp_path / "artifacts"),
            plan=DecisionReplayPlan.create(
                (_checkpoint(day=_days(62)[61], dataset_version_hash="a" * 64),)
            ),
        )


def test_composition_rejects_missing_current_bar_for_a_configured_symbol(
    tmp_path: Path,
) -> None:
    days = _days(62)
    store = ArtifactStore(tmp_path / "artifacts")
    _, version = _publish_prefix(
        root=tmp_path,
        store=store,
        profile_id=PROFILE_ID,
        days=days,
        artifact_id="decision-replay-missing-current-rb",
        missing_final_symbol="RB_CONT",
    )

    with pytest.raises(DecisionReplayCompositionError, match="当前 decision_event_time"):
        build_profile_decision_replay_targets(
            profile_id=PROFILE_ID,
            artifact_store=store,
            plan=DecisionReplayPlan.create(
                (_checkpoint(day=days[-1], dataset_version_hash=version.version_hash),)
            ),
        )


def test_composition_rejects_historical_gap_that_shrinks_current_target_universe(
    tmp_path: Path,
) -> None:
    days = _days(63)
    store = ArtifactStore(tmp_path / "artifacts")
    _, version = _publish_prefix(
        root=tmp_path,
        store=store,
        profile_id=PROFILE_ID,
        days=days,
        artifact_id="decision-replay-historical-rb-gap",
        missing_symbol_day_index=2,
        missing_symbol="RB_CONT",
    )

    with pytest.raises(DecisionReplayCompositionError, match="历史缺口不得静默缩小"):
        build_profile_decision_replay_targets(
            profile_id=PROFILE_ID,
            artifact_store=store,
            plan=DecisionReplayPlan.create(
                (_checkpoint(day=days[-1], dataset_version_hash=version.version_hash),)
            ),
        )


def test_composition_rejects_portfolio_policy_that_removes_a_current_symbol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    days = _days(62)
    store = ArtifactStore(tmp_path / "artifacts")
    _, version = _publish_prefix(
        root=tmp_path,
        store=store,
        profile_id=PROFILE_ID,
        days=days,
        artifact_id="decision-replay-post-policy-removal",
    )

    def _remove_rb(frame: pl.DataFrame, profile: object) -> pl.DataFrame:
        del profile
        return frame.filter(pl.col("symbol") != "RB_CONT")

    monkeypatch.setattr(
        decision_replay_backtest,
        "enforce_profile_target_policy",
        _remove_rb,
    )
    with pytest.raises(DecisionReplayCompositionError, match="组合与风控后的当前 target"):
        build_profile_decision_replay_targets(
            profile_id=PROFILE_ID,
            artifact_store=store,
            plan=DecisionReplayPlan.create(
                (_checkpoint(day=days[-1], dataset_version_hash=version.version_hash),)
            ),
        )


def test_composition_rejects_any_uncontrolled_strategy_selection(tmp_path: Path) -> None:
    with pytest.raises(DecisionReplayCompositionError, match="futures_trend"):
        build_profile_decision_replay_targets(
            profile_id=PROFILE_ID,
            artifact_store=ArtifactStore(tmp_path / "artifacts"),
            plan=DecisionReplayPlan.create(
                (_checkpoint(day=_days(62)[61], dataset_version_hash="a" * 64),)
            ),
            strategy_ids=("not_registered",),
        )

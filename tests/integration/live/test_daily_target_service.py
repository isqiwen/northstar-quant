"""日频目标冻结与复用测试。"""

from dataclasses import replace
from datetime import date, timedelta

import polars as pl
from sqlalchemy import func, select

from northstar_quant.config.trading_profile import load_trading_profile
from northstar_quant.db.models import StrategyRunRecord, StrategySnapshotRecord
from northstar_quant.db.repositories import save_strategy_run_snapshot
from northstar_quant.live import target_service


def _daily_signal_frame() -> pl.DataFrame:
    start = date(2026, 4, 1)
    rows: list[dict[str, object]] = []
    for offset in range(90):
        current = start + timedelta(days=offset)
        rows.extend(
            (
                {
                    "date": current,
                    "symbol": "RB2610",
                    "close": 3000.0 + offset,
                },
                {
                    "date": current,
                    "symbol": "CU2610",
                    "close": 80_000.0 - offset * 10.0,
                },
            )
        )
    return pl.DataFrame(rows)


def test_daily_target_is_frozen_once_and_reused(
    monkeypatch,
    postgresql_session_factory,
):
    base_profile = load_trading_profile("cn_futures_daily_actual_offline")
    profile = replace(
        base_profile,
        lifecycle=replace(base_profile.lifecycle, role="production"),
        futures=replace(base_profile.futures, execution_allowed=True),
        data=replace(base_profile.data, live_trading_eligible=True),
        risk={
            **base_profile.risk,
            "enforce_available_cash": True,
            "enforce_tradeable_state": True,
            "enforce_price_limit": True,
        },
    )
    signal_frame = _daily_signal_frame()
    raw_frame = signal_frame.with_columns(
        pl.lit(True).alias("session_complete")
    )

    monkeypatch.setattr(target_service, "SessionLocal", postgresql_session_factory)
    monkeypatch.setattr(target_service, "load_trading_profile", lambda _profile_id: profile)
    monkeypatch.setattr(
        target_service,
        "load_profile_market_data",
        lambda _profile: raw_frame,
    )
    monkeypatch.setattr(
        target_service,
        "load_profile_signal_data",
        lambda _profile: signal_frame,
    )

    first = target_service.generate_daily_targets_once(profile.profile_id)
    second = target_service.generate_daily_targets_once(profile.profile_id)

    assert first.run_id == second.run_id
    assert first.bundle.frame.equals(second.bundle.frame)
    assert first.bundle.frame.height == 2

    with postgresql_session_factory() as session:
        save_strategy_run_snapshot(
            session,
            run_id="shadow-run-after-daily-target",
            profile_id=profile.profile_id,
            pipeline_strategy_id=first.bundle.strategy_id,
            output_type=first.bundle.output_type,
            time_column=first.bundle.time_column,
            output_frame=first.bundle.frame,
            selected_strategy_ids=["futures_trend"],
        )
        run_count = session.scalar(select(func.count(StrategyRunRecord.id)))
        snapshot_count = session.scalar(
            select(func.count(StrategySnapshotRecord.id))
        )

    loaded = target_service.load_latest_daily_targets(
        profile,
        require_fresh=False,
    )

    assert loaded.run_id == first.run_id
    assert run_count == 2
    assert snapshot_count == 4

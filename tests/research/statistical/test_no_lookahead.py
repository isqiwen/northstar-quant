"""P1-WP07：研究入口只能归档显式 PIT snapshot，不能让未来修订倒灌。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

import northstar_quant.application.backtest as backtest_app
from northstar_quant.data.artifacts.immutable_store import ArtifactStore
from northstar_quant.data.contracts.instrument_universes import load_instrument_universe
from northstar_quant.data.market.pit import (
    MarketDataKind,
    MarketDataPITSpec,
    MarketDataPITSelector,
    MarketDataSnapshot,
)
from northstar_quant.data.sources.protocol import PublicationPurpose, PublicationScope
from northstar_quant.foundation.config.trading_profile import load_trading_profile
from tests.helpers.pit_publication import publish_authorized_pit_dataset


UTC_TIME = datetime(2026, 1, 5, 16, tzinfo=UTC)


def _spec() -> MarketDataPITSpec:
    return MarketDataPITSpec(
        kind=MarketDataKind.BAR,
        key_columns=("date", "symbol"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=("close",),
        schema_version="market_data_v2",
    )


def _full_daily_spec() -> MarketDataPITSpec:
    return MarketDataPITSpec(
        kind=MarketDataKind.BAR,
        key_columns=("date", "symbol"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=("open", "high", "low", "close", "adjusted_close", "volume"),
        schema_version="market_data_v2",
    )


def _snapshot(*, close: float, available_at: datetime, as_of: datetime) -> MarketDataSnapshot:
    spec = _spec()
    frame = pl.DataFrame(
        {
            "date": [date(2026, 1, 5)],
            "symbol": ["RB_CONT"],
            "close": [close],
            "available_at": [available_at],
        }
    ).with_columns(pl.col("available_at").cast(pl.Datetime("us", "UTC")))
    return MarketDataSnapshot.from_selected_frame(
        dataset_id="continuous_research",
        dataset_version_hash="b" * 64,
        source_artifact_snapshot_hash="a" * 64,
        source_id="akshare_continuous_public_v1",
        source_config_sha256="c" * 64,
        publication_authorization_hash="d" * 64,
        publication_scope=PublicationScope(
            dataset_id="continuous_research",
            market="CN",
            asset_type="FUTURES",
            frequency="1d",
            purpose=PublicationPurpose.HISTORICAL_BACKTEST,
            environment="internal_server",
            exchanges=("SHFE", "DCE", "CZCE", "GFEX", "INE"),
            products=("RB", "CU", "I", "M", "TA", "SA", "SI", "SC"),
            actual_contract_data=False,
        ),
        spec=spec,
        source_artifact_available_at=as_of,
        as_of=as_of,
        frame=frame,
    )


def test_future_revision_keeps_old_snapshot_and_run_input_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    old = _snapshot(
        close=100.0,
        available_at=UTC_TIME,
        as_of=UTC_TIME + timedelta(minutes=1),
    )
    revised = _snapshot(
        close=120.0,
        available_at=UTC_TIME + timedelta(hours=2),
        as_of=UTC_TIME + timedelta(hours=2, minutes=1),
    )
    assert old.snapshot_id != revised.snapshot_id
    assert old.selected_frame_hash != revised.selected_frame_hash
    assert old.selected_frame().get_column("close").to_list() == [100.0]
    assert revised.selected_frame().get_column("close").to_list() == [120.0]

    profile = load_trading_profile("cn_futures_daily_trend_offline")
    captured: dict[str, object] = {}
    sentinel = object()
    selector = MarketDataPITSelector(ArtifactStore(tmp_path / "verification-store"))

    monkeypatch.setattr(backtest_app, "load_trading_profile", lambda _profile_id: profile)
    monkeypatch.setattr(backtest_app, "validate_market_dataset", lambda *_args: {})
    monkeypatch.setattr(selector, "select", lambda **_kwargs: old)

    def capture_run(**kwargs: object) -> object:
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(backtest_app, "_run_profile_backtest_with_input", capture_run)
    result = backtest_app.run_profile_backtest_from_pit_snapshot(
        "cn_futures_daily_trend_offline",
        market_snapshot=old,
        pit_selector=selector,
    )

    assert result is sentinel
    assert isinstance(captured["raw_market_df"], pl.DataFrame)
    assert captured["raw_market_df"].get_column("close").to_list() == [100.0]  # type: ignore[union-attr]
    manifest = captured["source_manifest"]
    assert isinstance(manifest, dict)
    assert manifest["point_in_time"]["snapshot_id"] == old.snapshot_id  # type: ignore[index]
    assert manifest["point_in_time"]["dataset_version_hash"] == "b" * 64  # type: ignore[index]

    # 已经传入的 old snapshot 不依赖后来发布的 revision；重放旧运行应继续引用旧身份。
    assert old.as_manifest_mapping()["snapshot_id"] != revised.as_manifest_mapping()["snapshot_id"]
    assert old.as_manifest_mapping()["selected_frame_hash"] != revised.as_manifest_mapping()[
        "selected_frame_hash"
    ]

    # 手工拼装或被替换的 snapshot 不能绕过 selector 对 immutable DatasetVersion 的重算。
    with pytest.raises(ValueError, match="重算验证"):
        backtest_app.run_profile_backtest_from_pit_snapshot(
            "cn_futures_daily_trend_offline",
            market_snapshot=revised,
            pit_selector=selector,
        )


def test_pit_research_entry_rejects_cross_source_or_cross_dataset_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    profile = load_trading_profile("cn_futures_daily_trend_offline")
    snapshot = _snapshot(
        close=100.0,
        available_at=UTC_TIME,
        as_of=UTC_TIME + timedelta(minutes=1),
    )
    monkeypatch.setattr(backtest_app, "load_trading_profile", lambda _profile_id: profile)
    selector = MarketDataPITSelector(ArtifactStore(tmp_path / "verification-store"))

    wrong_source = MarketDataSnapshot.from_selected_frame(
        dataset_id=snapshot.dataset_id,
        dataset_version_hash=snapshot.dataset_version_hash,
        source_artifact_snapshot_hash=snapshot.source_artifact_snapshot_hash,
        source_id="other-source",
        source_config_sha256=snapshot.source_config_sha256,
        publication_authorization_hash=snapshot.publication_authorization_hash,
        publication_scope=snapshot.publication_scope,
        spec=snapshot.spec,
        source_artifact_available_at=snapshot.source_artifact_available_at,
        as_of=snapshot.as_of,
        frame=snapshot.selected_frame(),
    )
    monkeypatch.setattr(selector, "select", lambda **_kwargs: wrong_source)
    with pytest.raises(ValueError, match="source_id"):
        backtest_app.run_profile_backtest_from_pit_snapshot(
            market_snapshot=wrong_source,
            pit_selector=selector,
        )


def test_full_static_pit_replay_records_immutable_snapshot_in_backtest_manifest(
    tmp_path,
) -> None:
    """真实策略/回测编排只读取 selector 固定的 frame，并归档 snapshot identity。"""

    profile = load_trading_profile("cn_futures_daily_trend_offline")
    universe = load_instrument_universe(profile.universe_id)
    symbols = tuple(profile.data.download.symbols)
    start = date(2024, 1, 2)
    rows: list[dict[str, object]] = []
    for day_offset in range(70):
        trading_day = start + timedelta(days=day_offset)
        for symbol_offset, symbol in enumerate(symbols):
            close = 100.0 + day_offset + symbol_offset * 10.0
            rows.append(
                {
                    "date": trading_day,
                    "symbol": symbol,
                    "open": close - 1.0,
                    "high": close + 1.0,
                    "low": close - 2.0,
                    "close": close,
                    "adjusted_close": close,
                    "volume": 1_000.0 + day_offset,
                    "available_at": UTC_TIME,
                }
            )
    frame = pl.DataFrame(rows).with_columns(pl.col("available_at").cast(pl.Datetime("us", "UTC")))
    store, dataset = publish_authorized_pit_dataset(
        tmp_path,
        frame,
        dataset_id=profile.data.dataset_id,
        source_id=profile.data.source_id,
        adapter_id=profile.data.provider,
        schema_version="market_data_v2",
        artifact_id="pit-research-normalized",
        key_columns=("date", "symbol"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=("open", "high", "low", "close", "adjusted_close", "volume"),
        normalized_available_at=UTC_TIME + timedelta(minutes=4),
        scope_exchanges=tuple(sorted({member.exchange for member in universe.members})),
        scope_products=tuple(member.product for member in universe.members),
    )
    snapshot = MarketDataPITSelector(store).select(
        dataset_version_hash=dataset.version_hash,
        spec=_full_daily_spec(),
        as_of=UTC_TIME + timedelta(minutes=5),
    )

    run = backtest_app.run_profile_backtest_from_pit_snapshot(
        "cn_futures_daily_trend_offline",
        market_snapshot=snapshot,
        pit_selector=MarketDataPITSelector(store),
    )

    data_manifest = run.manifest_mapping()["data"]
    assert isinstance(data_manifest, dict)
    point_in_time = data_manifest["point_in_time"]
    assert isinstance(point_in_time, dict)
    assert point_in_time["snapshot_id"] == snapshot.snapshot_id
    assert point_in_time["dataset_version_hash"] == dataset.version_hash
    assert point_in_time["selected_frame_hash"] == snapshot.selected_frame_hash
    assert point_in_time["selection_mode"] == "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY"
    assert point_in_time["decision_time_safe"] is False
    assert point_in_time["publication_scope"]["purpose"] == "historical_backtest"
    assert point_in_time["publication_authorization_hash"] == snapshot.publication_authorization_hash
    assert point_in_time == snapshot.as_manifest_mapping()


def test_internal_research_only_pit_authorization_cannot_enter_historical_backtest(
    tmp_path,
) -> None:
    """冻结为 internal_research 的制品不能在应用层被用途扩展为历史回测。"""

    profile = load_trading_profile("cn_futures_daily_trend_offline")
    frame = pl.DataFrame(
        {
            "date": [date(2026, 1, 5)],
            "symbol": [profile.data.download.symbols[0]],
            "open": [99.0],
            "high": [101.0],
            "low": [98.0],
            "close": [100.0],
            "adjusted_close": [100.0],
            "volume": [1_000.0],
            "available_at": [UTC_TIME],
        }
    ).with_columns(pl.col("available_at").cast(pl.Datetime("us", "UTC")))
    store, dataset = publish_authorized_pit_dataset(
        tmp_path,
        frame,
        dataset_id=profile.data.dataset_id,
        source_id=profile.data.source_id,
        adapter_id=profile.data.provider,
        schema_version="market_data_v2",
        artifact_id="pit-internal-research-only",
        key_columns=("date", "symbol"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=("open", "high", "low", "close", "adjusted_close", "volume"),
        normalized_available_at=UTC_TIME + timedelta(minutes=4),
        purpose=PublicationPurpose.INTERNAL_RESEARCH,
    )
    selector = MarketDataPITSelector(store)
    snapshot = selector.select(
        dataset_version_hash=dataset.version_hash,
        spec=_full_daily_spec(),
        as_of=UTC_TIME + timedelta(minutes=5),
    )

    assert snapshot.publication_scope.purpose is PublicationPurpose.INTERNAL_RESEARCH
    with pytest.raises(ValueError, match="historical_backtest"):
        backtest_app.run_profile_backtest_from_pit_snapshot(
            profile.profile_id,
            market_snapshot=snapshot,
            pit_selector=selector,
        )


def test_pit_backtest_rejects_profile_products_not_covered_by_frozen_authorization_scope(
    tmp_path,
) -> None:
    """scope 不能只声明 RB 却把画像中其余连续品种带入研究回测。"""

    profile = load_trading_profile("cn_futures_daily_trend_offline")
    frame = pl.DataFrame(
        [
            {
                "date": date(2026, 1, 5),
                "symbol": symbol,
                "open": 99.0,
                "high": 101.0,
                "low": 98.0,
                "close": 100.0,
                "adjusted_close": 100.0,
                "volume": 1_000.0,
                "available_at": UTC_TIME,
            }
            for symbol in profile.data.download.symbols
        ]
    ).with_columns(pl.col("available_at").cast(pl.Datetime("us", "UTC")))
    store, dataset = publish_authorized_pit_dataset(
        tmp_path,
        frame,
        dataset_id=profile.data.dataset_id,
        source_id=profile.data.source_id,
        adapter_id=profile.data.provider,
        schema_version="market_data_v2",
        artifact_id="pit-insufficient-scope",
        key_columns=("date", "symbol"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=("open", "high", "low", "close", "adjusted_close", "volume"),
        normalized_available_at=UTC_TIME + timedelta(minutes=4),
        # fixture helper 的默认范围仅 RB/SHFE，故必须在回测入口失败关闭。
    )
    selector = MarketDataPITSelector(store)
    snapshot = selector.select(
        dataset_version_hash=dataset.version_hash,
        spec=_full_daily_spec(),
        as_of=UTC_TIME + timedelta(minutes=5),
    )

    with pytest.raises(ValueError, match="publication scope 未覆盖"):
        backtest_app.run_profile_backtest_from_pit_snapshot(
            profile.profile_id,
            market_snapshot=snapshot,
            pit_selector=selector,
        )

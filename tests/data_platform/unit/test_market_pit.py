"""P1-WP07 行级市场数据 PIT/revision 选择测试。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from northstar_quant.data_platform.artifacts.fingerprints import content_sha256
from northstar_quant.data_platform.artifacts.immutable_store import ArtifactStore
from northstar_quant.data_platform.contracts.data_domain import DatasetVersion
from northstar_quant.data_platform.market.pit import (
    MarketDataKind,
    MarketDataPITError,
    MarketDataPITSelector,
    MarketDataPITSpec,
    MarketDataSnapshot,
)
from northstar_quant.data_platform.quality import canonical_frame_payload
from tests.helpers.pit_publication import publish_authorized_pit_dataset


UTC_TIME = datetime(2026, 1, 5, 8, tzinfo=UTC)


def _store_dataset(
    tmp_path: Path,
    frame: pl.DataFrame,
    *,
    artifact_id: str = "pit-normalized-v1",
    store: ArtifactStore | None = None,
    normalized_available_at: datetime | None = None,
) -> tuple[ArtifactStore, DatasetVersion]:
    """通过受控发布器构造可回放市场数据集。"""

    key_columns: tuple[str, ...]
    value_columns: tuple[str, ...]
    if "timestamp" in frame.columns:
        key_columns = ("timestamp", "symbol")
        event_time_column = "timestamp"
        value_columns = ("close", "volume")
    else:
        key_columns = ("date", "symbol")
        event_time_column = "date"
        value_columns = ("close",)

    return publish_authorized_pit_dataset(
        tmp_path,
        frame,
        dataset_id="pit-market-fixture",
        source_id="pit-fixture-source",
        adapter_id="pit-fixture-adapter",
        schema_version="market.pit.v1",
        artifact_id=artifact_id,
        key_columns=key_columns,
        event_time_column=event_time_column,
        available_at_column="available_at",
        value_columns=value_columns,
        normalized_available_at=normalized_available_at or (UTC_TIME + timedelta(hours=12)),
        store=store,
    )


def _bar_frame(*, include_revision: bool = False, conflict: bool = False) -> pl.DataFrame:
    base = UTC_TIME + timedelta(hours=2)
    rows: list[dict[str, object]] = [
        {
            "timestamp": base,
            "symbol": "RB",
            "close": 100.0,
            "volume": 10.0,
            "available_at": base + timedelta(minutes=5),
        },
        {
            "timestamp": base + timedelta(minutes=1),
            "symbol": "RB",
            "close": 102.0,
            "volume": 11.0,
            "available_at": base + timedelta(minutes=6),
        },
    ]
    if include_revision:
        rows.append(
            {
                "timestamp": base,
                "symbol": "RB",
                "close": 101.0,
                "volume": 10.0,
                "available_at": (
                    base + timedelta(minutes=5) if conflict else base + timedelta(hours=1)
                ),
            }
        )
    return pl.DataFrame(rows).with_columns(
        pl.col("timestamp").cast(pl.Datetime("us", "UTC")),
        pl.col("available_at").cast(pl.Datetime("us", "UTC")),
    )


def _bar_spec() -> MarketDataPITSpec:
    return MarketDataPITSpec(
        kind=MarketDataKind.BAR,
        key_columns=("timestamp", "symbol"),
        event_time_column="timestamp",
        available_at_column="available_at",
        value_columns=("close", "volume"),
        schema_version="market.pit.v1",
    )


def test_new_dataset_version_is_required_for_later_revision_and_old_version_remains_frozen(
    tmp_path: Path,
) -> None:
    store, old_version = _store_dataset(
        tmp_path,
        _bar_frame(),
        artifact_id="pit-normalized-v1",
    )
    _, revised_version = _store_dataset(
        tmp_path,
        _bar_frame(include_revision=True),
        artifact_id="pit-normalized-v2",
        store=store,
    )
    selector = MarketDataPITSelector(store)
    old_snapshot = selector.select(
        dataset_version_hash=old_version.version_hash,
        spec=_bar_spec(),
        as_of=UTC_TIME + timedelta(hours=13),
    )
    revised_snapshot = selector.select(
        dataset_version_hash=revised_version.version_hash,
        spec=_bar_spec(),
        as_of=UTC_TIME + timedelta(hours=13),
    )

    assert old_snapshot.selected_frame().sort("timestamp").get_column("close").to_list() == [
        100.0,
        102.0,
    ]
    assert revised_snapshot.selected_frame().sort("timestamp").get_column("close").to_list() == [
        101.0,
        102.0,
    ]
    assert old_snapshot.snapshot_id != revised_snapshot.snapshot_id
    assert old_snapshot.selected_frame_hash != revised_snapshot.selected_frame_hash
    assert old_snapshot.as_manifest_mapping()["dataset_version_hash"] == old_version.version_hash
    assert old_snapshot.as_manifest_mapping()["source_id"] == "pit-fixture-source"
    assert old_snapshot.as_manifest_mapping()["row_count"] == 2
    assert (
        old_snapshot.as_manifest_mapping()["selection_mode"]
        == "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY"
    )

    caller_copy = old_snapshot.selected_frame()
    caller_copy[0, "close"] = 999.0
    assert old_snapshot.selected_frame().sort("timestamp").get_column("close").to_list() == [
        100.0,
        102.0,
    ]
    assert not hasattr(old_snapshot, "_frame")


def test_later_revision_cannot_be_hidden_in_earlier_available_artifact(tmp_path: Path) -> None:
    store, version = _store_dataset(
        tmp_path,
        _bar_frame(include_revision=True),
        normalized_available_at=UTC_TIME + timedelta(hours=2, minutes=30),
    )

    with pytest.raises(MarketDataPITError, match="row.available_at"):
        MarketDataPITSelector(store).select(
            dataset_version_hash=version.version_hash,
            spec=_bar_spec(),
            as_of=UTC_TIME + timedelta(hours=3),
        )


def test_same_logical_key_and_available_time_with_distinct_content_fails_closed(
    tmp_path: Path,
) -> None:
    store, version = _store_dataset(tmp_path, _bar_frame(include_revision=True, conflict=True))

    with pytest.raises(MarketDataPITError, match="冲突修订"):
        MarketDataPITSelector(store).select(
            dataset_version_hash=version.version_hash,
            spec=_bar_spec(),
            as_of=UTC_TIME + timedelta(hours=13),
        )


def test_daily_bar_requires_explicit_available_at_timezone_and_exact_spec_columns(tmp_path: Path) -> None:
    daily = pl.DataFrame(
        {
            "date": [date(2026, 1, 5)],
            "symbol": ["RB"],
            "close": [100.0],
            "available_at": [UTC_TIME + timedelta(hours=1)],
        }
    ).with_columns(pl.col("available_at").cast(pl.Datetime("us", "UTC")))
    store, version = _store_dataset(tmp_path, daily)
    spec = MarketDataPITSpec(
        kind=MarketDataKind.BAR,
        key_columns=("date", "symbol"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=("close",),
        schema_version="market.pit.v1",
    )

    snapshot = MarketDataPITSelector(store).select(
        dataset_version_hash=version.version_hash,
        spec=spec,
        as_of=UTC_TIME + timedelta(hours=13),
    )
    assert snapshot.selected_frame().get_column("close").to_list() == [100.0]

    naive = daily.with_columns(pl.col("available_at").cast(pl.Datetime("us")))
    second_store, second_version = _store_dataset(tmp_path / "naive", naive)
    with pytest.raises(MarketDataPITError, match="带时区"):
        MarketDataPITSelector(second_store).select(
            dataset_version_hash=second_version.version_hash,
            spec=spec,
            as_of=UTC_TIME + timedelta(hours=13),
        )

    extra_store, extra_version = _store_dataset(
        tmp_path / "extra",
        daily.with_columns(pl.lit("not-in-spec").alias("untracked_value")),
    )
    with pytest.raises(MarketDataPITError, match="未在 spec 声明"):
        MarketDataPITSelector(extra_store).select(
            dataset_version_hash=extra_version.version_hash,
            spec=spec,
            as_of=UTC_TIME + timedelta(hours=13),
        )


def test_snapshot_constructor_binds_frame_to_selected_revisions(tmp_path: Path) -> None:
    store, version = _store_dataset(tmp_path, _bar_frame())
    snapshot = MarketDataPITSelector(store).select(
        dataset_version_hash=version.version_hash,
        spec=_bar_spec(),
        as_of=UTC_TIME + timedelta(hours=13),
    )
    changed_frame = snapshot.selected_frame().with_columns(pl.lit(777.0).alias("close"))

    with pytest.raises(MarketDataPITError, match="精确匹配 snapshot frame"):
        MarketDataSnapshot(
            dataset_id=snapshot.dataset_id,
            dataset_version_hash=snapshot.dataset_version_hash,
            source_artifact_snapshot_hash=snapshot.source_artifact_snapshot_hash,
            source_id=snapshot.source_id,
            source_config_sha256=snapshot.source_config_sha256,
            publication_authorization_hash=snapshot.publication_authorization_hash,
            publication_scope=snapshot.publication_scope,
            spec=snapshot.spec,
            source_artifact_available_at=snapshot.source_artifact_available_at,
            as_of=snapshot.as_of,
            revisions=snapshot.revisions,
            selected_frame_hash=content_sha256(canonical_frame_payload(changed_frame)),
            _frame=changed_frame,
        )


def test_spec_rejects_ambiguous_key_or_available_time_configuration() -> None:
    with pytest.raises(MarketDataPITError, match="event_time_column"):
        MarketDataPITSpec(
            kind=MarketDataKind.TICK,
            key_columns=("symbol",),
            event_time_column="timestamp",
            available_at_column="available_at",
            value_columns=("price",),
            schema_version="market.pit.v1",
        )
    with pytest.raises(MarketDataPITError, match="available_at_column"):
        MarketDataPITSpec(
            kind=MarketDataKind.TICK,
            key_columns=("timestamp", "symbol"),
            event_time_column="timestamp",
            available_at_column="symbol",
            value_columns=("price",),
            schema_version="market.pit.v1",
        )

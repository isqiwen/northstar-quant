"""Feature Registry 只能消费 immutable DatasetVersion 重算出的 P1 PIT 输入。"""

from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256

import polars as pl
import pytest

from northstar_quant.data_platform.market.pit import (
    MarketDataKind,
    MarketDataPITSelector,
    MarketDataPITSpec,
    MarketDataSnapshot,
)
from northstar_quant.research.features import (
    FeatureComputer,
    FeatureDeterminismError,
    FeatureLineage,
    FeatureRegistry,
    FeatureRegistryError,
    FeatureSpec,
    FeatureValue,
    FeatureVersion,
)
from tests.helpers.pit_publication import publish_authorized_pit_dataset


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _at(hour: int) -> datetime:
    return datetime(2026, 1, 5, hour, tzinfo=UTC)


def _pit_spec() -> MarketDataPITSpec:
    return MarketDataPITSpec(
        kind=MarketDataKind.BAR,
        key_columns=("date", "symbol"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=("close",),
        schema_version="feature.market.v1",
    )


def _feature_spec(
    *,
    feature_id: str = "technical.close_identity",
    input_columns: tuple[str, ...] = ("date", "symbol", "close", "available_at"),
    input_schema_version: str = "feature.market.v1",
) -> FeatureSpec:
    return FeatureSpec(
        feature_id=feature_id,
        family="technical",
        description="用于验证 immutable PIT 血缘的受控测试特征。",
        input_columns=input_columns,
        input_schema_version=input_schema_version,
        entity_key_columns=("symbol",),
        output_column="close_identity",
        event_time_column="date",
        available_at_column="available_at",
        lookback_semantics="只消费指定 as_of 前已可见的收盘价。",
        missing_value_semantics="输入缺失时输出显式缺失。",
    )


def _feature_version(spec: FeatureSpec) -> FeatureVersion:
    return FeatureVersion.from_spec(
        spec,
        version="1.0.0",
        implementation_hash=_hash("feature-registry-integration-v1"),
        code_revision="p2-wp01-integration-test",
        parameter_schema={"scale": {"type": "number", "required": True, "minimum": 0}},
    )


class _TestComputer:
    """测试内已登记实现；Registry 必须把刚重放的 snapshot 传入此处。"""

    def __init__(
        self,
        *,
        feature_version_hash: str,
        implementation_hash: str,
        value: float,
    ) -> None:
        self.feature_version_hash = feature_version_hash
        self.implementation_hash = implementation_hash
        self.value = value
        self.key_name = "symbol"
        self.unstable = False
        self._calls = 0
        self.seen_snapshot_ids: list[str] = []

    def compute(self, *, market_snapshot, parameters, lineage):
        assert parameters == {"scale": 1.0}
        self.seen_snapshot_ids.append(market_snapshot.snapshot_id)
        self._calls += 1
        value = self.value + self._calls if self.unstable else self.value
        return (
            FeatureValue.from_lineage(
                lineage=lineage,
                key={self.key_name: "RB"},
                event_time=date(2026, 1, 5),
                value=value,
            ),
        )


def test_registry_reselects_an_authorized_dataset_and_freezes_full_pit_evidence(tmp_path) -> None:
    frame = pl.DataFrame(
        {
            "date": [date(2026, 1, 5)],
            "symbol": ["RB"],
            "close": [3550.5],
            "available_at": [_at(9)],
        }
    ).with_columns(pl.col("available_at").cast(pl.Datetime("us", "UTC")))
    store, dataset = publish_authorized_pit_dataset(
        tmp_path,
        frame,
        dataset_id="feature_market_fixture",
        source_id="feature_fixture_source",
        adapter_id="feature-fixture-adapter",
        schema_version="feature.market.v1",
        artifact_id="feature-market-normalized",
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
    version = _feature_version(spec)
    registry.register_spec(spec)
    registry.register_version(version)

    incompatible_spec = _feature_spec(
        feature_id="technical.close_identity_schema_mismatch",
        input_schema_version="different.market.v1",
    )
    incompatible_version = _feature_version(incompatible_spec)
    registry.register_spec(incompatible_spec)
    registry.register_version(incompatible_version)
    with pytest.raises(FeatureRegistryError, match="input_schema_version"):
        registry.create_market_data_lineage(
            feature_version_hash=incompatible_version.version_hash,
            market_snapshot=snapshot,
            parameters={"scale": 1.0},
        )
    missing_column_spec = _feature_spec(
        feature_id="technical.close_identity_missing_column",
        input_columns=("date", "symbol", "open", "available_at"),
    )
    missing_column_version = _feature_version(missing_column_spec)
    registry.register_spec(missing_column_spec)
    registry.register_version(missing_column_version)
    with pytest.raises(FeatureRegistryError, match="input_columns"):
        registry.create_market_data_lineage(
            feature_version_hash=missing_column_version.version_hash,
            market_snapshot=snapshot,
            parameters={"scale": 1.0},
        )

    lineage = registry.create_market_data_lineage(
        feature_version_hash=version.version_hash,
        market_snapshot=snapshot,
        parameters={"scale": 1.0},
    )

    dependency = lineage.dependencies[0]
    assert dependency.dataset_evidence is not None
    evidence = dependency.dataset_evidence
    assert evidence.dataset_version_hash == dataset.version_hash
    assert evidence.snapshot_id == snapshot.snapshot_id
    assert evidence.selected_frame_hash == snapshot.selected_frame_hash
    assert evidence.pit_spec_hash == snapshot.spec.spec_hash
    assert evidence.pit_spec == snapshot.spec
    assert evidence.revision_ids == snapshot.revision_ids
    assert evidence.source_artifact_snapshot_hash == snapshot.source_artifact_snapshot_hash
    assert evidence.source_artifact_available_at == snapshot.source_artifact_available_at
    assert evidence.publication_authorization_hash == snapshot.publication_authorization_hash
    assert evidence.publication_scope == snapshot.as_manifest_mapping()["publication_scope"]
    assert lineage.selection_mode == "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY"
    assert lineage.decision_time_safe is False
    assert lineage.implementation_hash == version.implementation_hash

    manually_constructed_lineage = FeatureLineage.create(
        feature_version=version,
        dependencies=lineage.dependencies,
        parameters={"scale": 1.0},
        decision_at=lineage.decision_at,
        available_at=lineage.available_at.replace(hour=12),
    )
    with pytest.raises(FeatureRegistryError, match="必须由 Registry"):
        registry.materialize_deterministic_backfill(manually_constructed_lineage)

    with pytest.raises(FeatureRegistryError, match="尚未登记受控 FeatureComputer"):
        registry.materialize_deterministic_backfill(lineage)

    computer: FeatureComputer = _TestComputer(
        feature_version_hash=version.version_hash,
        implementation_hash=version.implementation_hash,
        value=3550.5,
    )
    registry.register_computer(computer)
    assert isinstance(computer, _TestComputer)
    with pytest.raises(FeatureRegistryError, match="implementation_hash"):
        registry.register_computer(
            _TestComputer(
                feature_version_hash=version.version_hash,
                implementation_hash=_hash("unrelated implementation"),
                value=3550.5,
            )
        )
    with pytest.raises(FeatureRegistryError, match="拒绝替换实现"):
        registry.register_computer(
            _TestComputer(
                feature_version_hash=version.version_hash,
                implementation_hash=version.implementation_hash,
                value=3550.5,
            )
        )

    computer.key_name = "unexpected_key"
    with pytest.raises(FeatureRegistryError, match="entity_key_columns"):
        registry.materialize_deterministic_backfill(lineage)
    computer.key_name = "symbol"

    backfill = registry.materialize_deterministic_backfill(lineage)
    assert backfill.lineage_hash == lineage.lineage_hash
    assert backfill.implementation_hash == version.implementation_hash
    assert computer.seen_snapshot_ids == [snapshot.snapshot_id] * 4

    strict = registry.materialize_per_decision_replay(
        feature_version_hash=version.version_hash,
        market_snapshot=snapshot,
        replay_checkpoint_hash=_hash("decision-checkpoint"),
        parameters={"scale": 1.0},
    )
    assert strict.lineage.selection_mode == "PER_DECISION_POINT_IN_TIME_REPLAY"
    assert strict.lineage.decision_time_safe is True
    assert strict.lineage.replay_checkpoint_hash == _hash("decision-checkpoint")
    assert strict.input_snapshot_hash == snapshot.snapshot_id
    assert strict.values[0].lineage_hash == strict.lineage.lineage_hash
    assert computer.seen_snapshot_ids == [snapshot.snapshot_id] * 6

    computer.unstable = True
    with pytest.raises(FeatureRegistryError, match="两次计算结果不同"):
        registry.materialize_per_decision_replay(
            feature_version_hash=version.version_hash,
            market_snapshot=snapshot,
            replay_checkpoint_hash=_hash("decision-checkpoint-unstable"),
            parameters={"scale": 1.0},
        )
    computer.unstable = False

    computer.value = 3551.5
    with pytest.raises(FeatureRegistryError, match="已登记不同的 deterministic backfill"):
        registry.materialize_deterministic_backfill(lineage)
    computer.value = 3550.5
    computer.unstable = True
    with pytest.raises(FeatureDeterminismError, match="两次回填结果不同"):
        registry.materialize_deterministic_backfill(lineage)
    computer.unstable = False
    computer.implementation_hash = _hash("mutated implementation identity")
    with pytest.raises(FeatureRegistryError, match="身份发生变化"):
        registry.materialize_deterministic_backfill(lineage)

    forged = MarketDataSnapshot.from_selected_frame(
        dataset_id=snapshot.dataset_id,
        dataset_version_hash=_hash("unpublished dataset version"),
        source_artifact_snapshot_hash=snapshot.source_artifact_snapshot_hash,
        source_id=snapshot.source_id,
        source_config_sha256=snapshot.source_config_sha256,
        publication_authorization_hash=snapshot.publication_authorization_hash,
        publication_scope=snapshot.publication_scope,
        spec=snapshot.spec,
        source_artifact_available_at=snapshot.source_artifact_available_at,
        as_of=snapshot.as_of,
        frame=snapshot.selected_frame(),
    )
    with pytest.raises(FeatureRegistryError, match="DatasetVersion/PIT 证据无法安全重算"):
        registry.create_market_data_lineage(
            feature_version_hash=version.version_hash,
            market_snapshot=forged,
            parameters={"scale": 1.0},
        )

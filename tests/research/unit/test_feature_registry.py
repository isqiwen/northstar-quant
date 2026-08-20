"""P2-WP01 Feature Registry 的领域与 PIT 回归。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from hashlib import sha256

import pytest
import polars as pl

from northstar_quant.data_platform.market.pit import (
    MarketDataKind,
    MarketDataPITSpec,
    MarketDataSnapshot,
)
from northstar_quant.data_platform.sources.protocol import PublicationPurpose, PublicationScope

from northstar_quant.research.features import (
    FeatureBackfill,
    FeatureDependency,
    FeatureLineage,
    FeatureRegistry,
    FeatureRegistryError,
    FeatureSpec,
    FeatureValue,
    FeatureVersion,
)


def _hash(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _at(hour: int) -> datetime:
    return datetime(2026, 1, 5, hour, tzinfo=UTC)


def _spec() -> FeatureSpec:
    return FeatureSpec(
        feature_id="technical.sma",
        family="technical",
        description="收盘价简单移动平均",
        input_columns=("available_at", "symbol", "close", "event_time"),
        input_schema_version="market.v1",
        entity_key_columns=("symbol",),
        output_column="sma_20",
        event_time_column="event_time",
        available_at_column="available_at",
        lookback_semantics="最近 20 个已可用交易日 bar，不含未来 bar。",
        missing_value_semantics="窗口不足 20 个有效值时输出缺失，并保留原因。",
    )


def _version(spec: FeatureSpec | None = None) -> FeatureVersion:
    spec = spec or _spec()
    return FeatureVersion.from_spec(
        spec,
        version="1.0.0",
        implementation_hash=_hash("technical.sma implementation v1"),
        code_revision="p2-wp01-test",
        parameter_schema={
            "min_periods": {"type": "integer", "required": True, "minimum": 1},
            "window": {"type": "integer", "required": True, "minimum": 1},
        },
    )


def _dataset_dependency(*, available_at: datetime | None = None) -> FeatureDependency:
    return FeatureDependency.from_market_data_snapshot(
        role="market_data",
        snapshot=_market_snapshot(as_of=available_at or _at(10)),
    )


def _lineage(*, available_at: datetime | None = None) -> FeatureLineage:
    return FeatureLineage.create(
        feature_version=_version(),
        dependencies=(_dataset_dependency(),),
        parameters={"window": 20, "min_periods": 20},
        decision_at=_at(10),
        available_at=available_at or _at(10),
    )


def _market_snapshot(*, as_of: datetime = _at(10)) -> MarketDataSnapshot:
    spec = MarketDataPITSpec(
        kind=MarketDataKind.BAR,
        key_columns=("symbol", "event_time"),
        event_time_column="event_time",
        available_at_column="available_at",
        value_columns=("close",),
        schema_version="market.v1",
    )
    frame = pl.DataFrame(
        {
            "symbol": ["RB"],
            "event_time": [_at(8)],
            "available_at": [_at(9)],
            "close": [3550.5],
        }
    ).with_columns(
        pl.col("event_time").cast(pl.Datetime("us", "UTC")),
        pl.col("available_at").cast(pl.Datetime("us", "UTC")),
    )
    return MarketDataSnapshot.from_selected_frame(
        dataset_id="market_data_v1",
        dataset_version_hash=_hash("market dataset version"),
        source_artifact_snapshot_hash=_hash("market artifact snapshot"),
        source_id="authorized_test_source",
        source_config_sha256=_hash("source config"),
        publication_authorization_hash=_hash("publication authorization"),
        publication_scope=PublicationScope(
            dataset_id="market_data_v1",
            market="CN",
            asset_type="FUTURES",
            frequency="1d",
            purpose=PublicationPurpose.INTERNAL_RESEARCH,
            environment="internal_server",
        ),
        spec=spec,
        source_artifact_available_at=as_of,
        as_of=as_of,
        frame=frame,
    )


def _value(
    lineage: FeatureLineage,
    *,
    symbol: str = "RB",
    value: float | None = 3550.5,
    missing_reason: str | None = None,
) -> FeatureValue:
    return FeatureValue.from_lineage(
        lineage=lineage,
        key={"symbol": symbol},
        event_time=_at(9),
        value=value,
        missing_reason=missing_reason,
    )


def test_feature_spec_and_version_are_canonical_and_parameter_traceable():
    first = _spec()
    reordered = FeatureSpec(
        feature_id="technical.sma",
        family="technical",
        description="收盘价简单移动平均",
        input_columns=("close", "event_time", "symbol", "available_at"),
        input_schema_version="market.v1",
        entity_key_columns=("symbol",),
        output_column="sma_20",
        event_time_column="event_time",
        available_at_column="available_at",
        lookback_semantics="最近 20 个已可用交易日 bar，不含未来 bar。",
        missing_value_semantics="窗口不足 20 个有效值时输出缺失，并保留原因。",
    )

    version = _version(first)

    assert first.spec_hash == reordered.spec_hash
    assert version.feature_id == first.feature_id
    assert version.spec_hash == first.spec_hash
    assert version.parameter_schema == {
        "min_periods": {"minimum": 1.0, "required": True, "type": "integer"},
        "window": {"minimum": 1.0, "required": True, "type": "integer"},
    }
    assert len(version.version_hash) == 64


def test_feature_registry_is_idempotent_but_never_overwrites_a_definition_or_version():
    registry = FeatureRegistry()
    spec = _spec()
    version = _version(spec)

    assert registry.register_spec(spec) is spec
    assert registry.register_spec(spec) is spec
    assert registry.register_version(version) is version
    assert registry.get_spec("technical.sma") == spec
    assert registry.get_version(version.version_hash) == version
    assert registry.list_versions() == (version,)

    conflicting_spec = FeatureSpec(
        feature_id="technical.sma",
        family="technical",
        description="不同语义，不得覆盖",
        input_columns=("available_at", "symbol", "close", "event_time"),
        input_schema_version="market.v1",
        entity_key_columns=("symbol",),
        output_column="sma_20",
        event_time_column="event_time",
        available_at_column="available_at",
        lookback_semantics="最近 20 个已可用交易日 bar，不含未来 bar。",
        missing_value_semantics="窗口不足 20 个有效值时输出缺失，并保留原因。",
    )
    with pytest.raises(FeatureRegistryError, match="拒绝覆盖"):
        registry.register_spec(conflicting_spec)

    conflicting_version = FeatureVersion.from_spec(
        spec,
        version="1.0.0",
        implementation_hash=_hash("different implementation"),
        code_revision="p2-wp01-test",
        parameter_schema={
            "min_periods": {"type": "integer", "required": True, "minimum": 1},
            "window": {"type": "integer", "required": True, "minimum": 1},
        },
    )
    with pytest.raises(FeatureRegistryError, match="拒绝覆盖"):
        registry.register_version(conflicting_version)


def test_feature_version_must_bind_to_a_registered_matching_spec():
    spec = _spec()
    registry = FeatureRegistry()

    with pytest.raises(FeatureRegistryError, match="尚未登记"):
        registry.register_version(_version(spec))

    registry.register_spec(spec)
    mismatched = FeatureVersion(
        feature_id=spec.feature_id,
        spec_hash=_hash("unrelated spec"),
        version="1.0.0",
        implementation_hash=_hash("technical.sma implementation v1"),
        code_revision="p2-wp01-test",
        parameter_schema_json=(
            '{"min_periods":{"minimum":1,"required":true,"type":"integer"},'
            '"window":{"minimum":1,"required":true,"type":"integer"}}'
        ),
    )
    with pytest.raises(FeatureRegistryError, match="spec_hash"):
        registry.register_version(mismatched)


def test_lineage_requires_a_traceable_dataset_and_blocks_future_inputs():
    version = _version()
    future_dependency = _dataset_dependency(available_at=_at(11))

    with pytest.raises(FeatureRegistryError, match="decision_at 后"):
        FeatureLineage.create(
            feature_version=version,
            dependencies=(future_dependency,),
            parameters={"window": 20, "min_periods": 20},
            decision_at=_at(10),
            available_at=_at(10),
        )

    with pytest.raises(FeatureRegistryError, match="不能直接依赖自身"):
        FeatureLineage.create(
            feature_version=version,
            dependencies=(
                _dataset_dependency(),
                FeatureDependency.feature(
                    role="self",
                    feature_version_hash=version.version_hash,
                    lineage_hash=_hash("self lineage"),
                    available_at=_at(10),
                ),
            ),
            parameters={"window": 20, "min_periods": 20},
            decision_at=_at(10),
            available_at=_at(10),
        )

    upstream_lineage_hash = _hash("upstream feature lineage")
    with pytest.raises(FeatureRegistryError, match="尚未接受 Feature 类型输入"):
        FeatureLineage.create(
            feature_version=version,
            dependencies=(
                FeatureDependency.feature(
                    role="upstream_sma",
                    feature_version_hash=_hash("upstream feature version"),
                    lineage_hash=upstream_lineage_hash,
                    available_at=_at(10),
                ),
            ),
            parameters={"window": 20, "min_periods": 20},
            decision_at=_at(10),
            available_at=_at(10),
        )

    with pytest.raises(FeatureRegistryError, match="尚未接受 Feature 类型输入"):
        FeatureLineage.create(
            feature_version=version,
            dependencies=(
                _dataset_dependency(),
                FeatureDependency.feature(
                    role="upstream_sma",
                    feature_version_hash=_hash("upstream feature version"),
                    lineage_hash=_hash("upstream feature lineage with dataset"),
                    available_at=_at(10),
                ),
            ),
            parameters={"window": 20, "min_periods": 20},
            decision_at=_at(10),
            available_at=_at(10),
        )


def test_market_data_snapshot_dependency_preserves_static_pit_semantics():
    snapshot = _market_snapshot()
    dependency = FeatureDependency.from_market_data_snapshot(
        role="market_data",
        snapshot=snapshot,
    )

    assert dependency.dataset_version_hash == snapshot.dataset_version_hash
    assert dependency.reference_hash == snapshot.snapshot_id
    assert dependency.available_at == snapshot.as_of
    assert dependency.selection_mode == "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY"
    assert dependency.decision_time_safe is False
    assert dependency.dataset_evidence is not None
    evidence = dependency.dataset_evidence
    assert evidence.dataset_id == snapshot.dataset_id
    assert evidence.snapshot_id == snapshot.snapshot_id
    assert evidence.selected_frame_hash == snapshot.selected_frame_hash
    assert evidence.pit_spec_hash == snapshot.spec.spec_hash
    assert evidence.revision_ids == snapshot.revision_ids
    assert evidence.source_artifact_snapshot_hash == snapshot.source_artifact_snapshot_hash
    assert evidence.source_config_sha256 == snapshot.source_config_sha256
    assert evidence.publication_authorization_hash == snapshot.publication_authorization_hash
    assert evidence.publication_scope == snapshot.as_manifest_mapping()["publication_scope"]


def test_lineage_is_parameter_and_dataset_version_traceable_without_order_dependence():
    version = _version()
    market = _dataset_dependency()
    auxiliary = FeatureDependency.from_market_data_snapshot(
        role="auxiliary_data",
        snapshot=_market_snapshot(as_of=_at(9)),
    )
    first = FeatureLineage.create(
        feature_version=version,
        dependencies=(market, auxiliary),
        parameters={"window": 20, "min_periods": 20},
        decision_at=_at(10),
        available_at=_at(10),
    )
    second = FeatureLineage.create(
        feature_version=version,
        dependencies=(auxiliary, market),
        parameters={"min_periods": 20, "window": 20},
        decision_at=_at(10),
        available_at=_at(10),
    )

    assert first.lineage_hash == second.lineage_hash
    assert first.input_dataset_version_hashes == (
        _hash("market dataset version"),
        _hash("market dataset version"),
    )
    assert first.parameters == {"min_periods": 20, "window": 20}
    assert first.selection_mode == "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY"
    assert first.decision_time_safe is False


@pytest.mark.parametrize(
    ("parameters", "error"),
    [
        ({"window": 20}, "缺少必需字段"),
        ({"window": 20, "min_periods": 20, "unexpected": 1}, "未声明字段"),
        ({"window": "20", "min_periods": 20}, "不符合声明类型"),
        ({"window": 0, "min_periods": 1}, "不能小于 minimum"),
    ],
)
def test_lineage_parameters_must_match_the_feature_version_contract(
    parameters: dict[str, object],
    error: str,
):
    with pytest.raises(FeatureRegistryError, match=error):
        FeatureLineage.create(
            feature_version=_version(),
            dependencies=(_dataset_dependency(),),
            parameters=parameters,
            decision_at=_at(10),
            available_at=_at(10),
        )


def test_feature_value_has_explicit_missing_semantics_and_cannot_precede_its_event():
    lineage = _lineage()
    missing = _value(lineage, value=None, missing_reason="lookback_not_ready")

    assert missing.value is None
    assert missing.missing_reason == "lookback_not_ready"

    with pytest.raises(FeatureRegistryError, match="不能设置 missing_reason"):
        _value(lineage, missing_reason="not_needed")

    with pytest.raises(FeatureRegistryError, match="event_time 不能晚于 available_at"):
        FeatureValue(
            feature_version_hash=lineage.feature_version_hash,
            lineage_hash=lineage.lineage_hash,
            key_json='{"symbol":"RB"}',
            event_time=_at(11),
            available_at=_at(10),
            value=1.0,
        )

    daily = FeatureValue.from_lineage(
        lineage=lineage,
        key={"symbol": "RB"},
        event_time=date(2026, 1, 5),
        value=3550.5,
    )
    assert daily.event_time == date(2026, 1, 5)


def test_backfill_has_one_lineage_and_is_deterministic_for_same_values():
    lineage = _lineage()
    first = _value(lineage, symbol="RB")
    second = _value(lineage, symbol="CU", value=72000.0)

    backfill = FeatureBackfill.from_values(lineage=lineage, values=(second, first))
    repeat = FeatureBackfill.from_values(
        lineage=lineage,
        values=(_value(lineage, symbol="CU", value=72000.0), _value(lineage, symbol="RB")),
    )

    assert backfill.backfill_hash == repeat.backfill_hash
    assert [value.key["symbol"] for value in backfill.values] == ["CU", "RB"]
    assert backfill.available_at == lineage.available_at
    assert backfill.selection_mode == "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY"
    assert backfill.decision_time_safe is False

    duplicate = _value(lineage, symbol="RB", value=3500.0)
    with pytest.raises(FeatureRegistryError, match="重复的 key/event_time"):
        FeatureBackfill.from_values(lineage=lineage, values=(first, duplicate))

    with pytest.raises(FeatureRegistryError, match="只能是 STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY"):
        FeatureBackfill(
            lineage_hash=lineage.lineage_hash,
            feature_version_hash=lineage.feature_version_hash,
            implementation_hash=lineage.implementation_hash,
            available_at=lineage.available_at,
            selection_mode="PER_DECISION_POINT_IN_TIME_REPLAY",
            decision_time_safe=True,
            values=(first,),
        )


def test_registry_rejects_unregistered_lineage() -> None:
    unregistered_lineage = _lineage()
    registry = FeatureRegistry()

    with pytest.raises(FeatureRegistryError, match="未登记"):
        registry.materialize_deterministic_backfill(unregistered_lineage)


def test_registry_requires_an_immutable_artifact_store() -> None:
    snapshot = _market_snapshot()
    spec = _spec()
    version = _version(spec)
    registry = FeatureRegistry()
    registry.register_spec(spec)
    registry.register_version(version)
    with pytest.raises(FeatureRegistryError, match="缺少受控 immutable ArtifactStore"):
        registry.create_market_data_lineage(
            feature_version_hash=version.version_hash,
            market_snapshot=snapshot,
            parameters={"min_periods": 20, "window": 20},
        )


def test_feature_backfill_rejects_values_from_another_lineage_or_output_time():
    lineage = _lineage()
    later_lineage = _lineage(available_at=_at(10) + timedelta(minutes=1))

    with pytest.raises(FeatureRegistryError, match="同一 lineage"):
        FeatureBackfill.from_values(lineage=lineage, values=(_value(later_lineage),))

    later_value = FeatureValue(
        feature_version_hash=lineage.feature_version_hash,
        lineage_hash=lineage.lineage_hash,
        key_json='{"symbol":"RB"}',
        event_time=_at(9),
        available_at=_at(10) + timedelta(minutes=1),
        value=1.0,
    )
    with pytest.raises(FeatureRegistryError, match="输出时间一致"):
        FeatureBackfill.from_values(lineage=lineage, values=(later_value,))

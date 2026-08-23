"""Canonical feature family 必须经 P1 immutable DatasetVersion/PIT/Registry 物化。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime

import polars as pl
import pytest

from northstar_quant.data.market.pit import MarketDataPITSelector, MarketDataPITSpec
from northstar_quant.research.features.basis import RELATIVE_BASIS
from northstar_quant.research.features.canonical import CanonicalFeatureDefinition
from northstar_quant.research.features.carry import (
    ANNUALIZED_ROLL_YIELD,
    TERM_STRUCTURE_SLOPE,
)
from northstar_quant.research.features.catalog import register_canonical_feature
from northstar_quant.research.features.inventory import INVENTORY_LEVEL_CHANGE
from northstar_quant.research.features.momentum import MOMENTUM_ROC
from northstar_quant.research.features.positioning import NET_POSITION_RATIO
from northstar_quant.research.features.models import FeatureRegistryError
from northstar_quant.research.features.registry import FeatureRegistry
from northstar_quant.research.features.technical import (
    OPEN_INTEREST_CHANGE,
    REALIZED_VOLATILITY,
    VOLUME_RATIO,
)
from tests.helpers.pit_publication import publish_authorized_pit_dataset


def _at(day: int, hour: int = 9) -> datetime:
    return datetime(2026, 1, day, hour, tzinfo=UTC)


def _frame(rows: list[dict[str, object]], definition: CanonicalFeatureDefinition) -> pl.DataFrame:
    frame = pl.DataFrame(rows)
    return frame.with_columns(
        pl.col(definition.input_contract.available_at_column).cast(pl.Datetime("us", "UTC"))
    )


@pytest.mark.parametrize(
    ("definition", "rows", "parameters"),
    [
        (
            MOMENTUM_ROC,
            [
                {"date": date(2026, 1, 2), "symbol": "RB", "close": 100.0, "available_at": _at(2)},
                {"date": date(2026, 1, 3), "symbol": "RB", "close": 110.0, "available_at": _at(3)},
                {"date": date(2026, 1, 4), "symbol": "RB", "close": 121.0, "available_at": _at(4)},
            ],
            {"lookback_bars": 2},
        ),
        (
            REALIZED_VOLATILITY,
            [
                {"date": date(2026, 1, 2), "symbol": "RB", "close": 100.0, "available_at": _at(2)},
                {"date": date(2026, 1, 3), "symbol": "RB", "close": 110.0, "available_at": _at(3)},
                {"date": date(2026, 1, 4), "symbol": "RB", "close": 132.0, "available_at": _at(4)},
            ],
            {"window_bars": 2},
        ),
        (
            VOLUME_RATIO,
            [
                {"date": date(2026, 1, 2), "symbol": "RB", "volume": 10.0, "available_at": _at(2)},
                {"date": date(2026, 1, 3), "symbol": "RB", "volume": 20.0, "available_at": _at(3)},
                {"date": date(2026, 1, 4), "symbol": "RB", "volume": 30.0, "available_at": _at(4)},
            ],
            {"window_bars": 2},
        ),
        (
            OPEN_INTEREST_CHANGE,
            [
                {
                    "date": date(2026, 1, 2),
                    "contract_id": "SHFE.RB.2605",
                    "open_interest": 100.0,
                    "available_at": _at(2),
                },
                {
                    "date": date(2026, 1, 3),
                    "contract_id": "SHFE.RB.2605",
                    "open_interest": 100.0,
                    "available_at": _at(3),
                },
                {
                    "date": date(2026, 1, 4),
                    "contract_id": "SHFE.RB.2605",
                    "open_interest": 120.0,
                    "available_at": _at(4),
                },
            ],
            {"lookback_bars": 2},
        ),
        (
            ANNUALIZED_ROLL_YIELD,
            [
                {
                    "date": date(2026, 1, 5),
                    "product": "RB",
                    "near_contract_id": "SHFE.RB.2602",
                    "next_contract_id": "SHFE.RB.2603",
                    "far_contract_id": "SHFE.RB.2605",
                    "near_settlement": 110.0,
                    "next_settlement": 100.0,
                    "far_settlement": 90.0,
                    "near_expiry": date(2026, 2, 15),
                    "next_expiry": date(2026, 3, 15),
                    "far_expiry": date(2026, 5, 15),
                    "available_at": _at(5),
                }
            ],
            {},
        ),
        (
            TERM_STRUCTURE_SLOPE,
            [
                {
                    "date": date(2026, 1, 5),
                    "product": "RB",
                    "near_contract_id": "SHFE.RB.2602",
                    "next_contract_id": "SHFE.RB.2603",
                    "far_contract_id": "SHFE.RB.2605",
                    "near_settlement": 90.0,
                    "next_settlement": 100.0,
                    "far_settlement": 110.0,
                    "near_expiry": date(2026, 2, 15),
                    "next_expiry": date(2026, 3, 15),
                    "far_expiry": date(2026, 5, 15),
                    "available_at": _at(5),
                }
            ],
            {},
        ),
        (
            RELATIVE_BASIS,
            [
                {
                    "date": date(2026, 1, 5),
                    "product": "RB",
                    "spot_price": 100.0,
                    "futures_settlement": 105.0,
                    "futures_contract_id": "SHFE.RB.2605",
                    "spot_unit": "CNY/TON",
                    "futures_unit": "CNY/TON",
                    "spot_currency": "CNY",
                    "futures_currency": "CNY",
                    "available_at": _at(5),
                }
            ],
            {},
        ),
        (
            INVENTORY_LEVEL_CHANGE,
            [
                {
                    "observation_date": date(2026, 1, 2),
                    "product": "RB",
                    "inventory_scope": "social",
                    "inventory_unit": "TON",
                    "inventory_level": 100.0,
                    "available_at": _at(2),
                },
                {
                    "observation_date": date(2026, 1, 3),
                    "product": "RB",
                    "inventory_scope": "social",
                    "inventory_unit": "TON",
                    "inventory_level": 120.0,
                    "available_at": _at(3),
                },
            ],
            {"lookback_observations": 1},
        ),
        (
            NET_POSITION_RATIO,
            [
                {
                    "report_date": date(2026, 1, 5),
                    "product": "RB",
                    "participant_group": "managed_money",
                    "long_open_interest": 80.0,
                    "short_open_interest": 20.0,
                    "total_open_interest": 100.0,
                    "available_at": _at(5),
                }
            ],
            {},
        ),
    ],
    ids=lambda item: item.feature_id if isinstance(item, CanonicalFeatureDefinition) else None,
)
def test_canonical_family_materializes_only_after_p1_pit_replay(
    tmp_path,
    definition: CanonicalFeatureDefinition,
    rows: list[dict[str, object]],
    parameters: Mapping[str, object],
) -> None:
    frame = _frame(rows, definition)
    contract = definition.input_contract
    value_columns = tuple(
        column
        for column in frame.columns
        if column
        not in {
            *contract.entity_key_columns,
            contract.event_time_column,
            contract.available_at_column,
        }
    )
    store, dataset = publish_authorized_pit_dataset(
        tmp_path,
        frame,
        dataset_id=f"canonical_{definition.feature_id.replace('.', '_')}",
        source_id="canonical_feature_fixture_source",
        adapter_id="canonical-feature-fixture-adapter",
        schema_version=contract.schema_version,
        artifact_id=f"canonical-{definition.feature_id.replace('.', '-')}",
        key_columns=(*contract.entity_key_columns, contract.event_time_column),
        event_time_column=contract.event_time_column,
        available_at_column=contract.available_at_column,
        value_columns=value_columns,
        normalized_available_at=_at(10),
        actual_contract_data=contract.requires_actual_contract_data,
    )
    spec = MarketDataPITSpec(
        kind=contract.kind,
        key_columns=(*contract.entity_key_columns, contract.event_time_column),
        event_time_column=contract.event_time_column,
        available_at_column=contract.available_at_column,
        value_columns=value_columns,
        schema_version=contract.schema_version,
    )
    snapshot = MarketDataPITSelector(store).select(
        dataset_version_hash=dataset.version_hash,
        spec=spec,
        as_of=_at(11),
    )
    registry = FeatureRegistry(artifact_store=store)
    version = register_canonical_feature(
        registry,
        feature_id=definition.feature_id,
        version="1.0.0",
        code_revision="p2-wp02-integration",
    )
    lineage = registry.create_market_data_lineage(
        feature_version_hash=version.version_hash,
        market_snapshot=snapshot,
        parameters=parameters,
    )
    backfill = registry.materialize_deterministic_backfill(lineage)

    assert len(backfill.values) == len(rows)
    assert backfill.lineage_hash == lineage.lineage_hash
    assert backfill.available_at == snapshot.as_of
    assert backfill.decision_time_safe is False
    assert backfill.selection_mode == "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY"


def test_registry_materialization_rejects_open_interest_without_actual_contract_scope(
    tmp_path,
) -> None:
    definition = OPEN_INTEREST_CHANGE
    frame = _frame(
        [
            {
                "date": date(2026, 1, 2),
                "contract_id": "SHFE.RB.2605",
                "open_interest": 100.0,
                "available_at": _at(2),
            },
            {
                "date": date(2026, 1, 3),
                "contract_id": "SHFE.RB.2605",
                "open_interest": 120.0,
                "available_at": _at(3),
            },
        ],
        definition,
    )
    value_columns = ("open_interest",)
    store, dataset = publish_authorized_pit_dataset(
        tmp_path,
        frame,
        dataset_id="canonical_open_interest_nonactual",
        source_id="canonical_feature_fixture_source",
        adapter_id="canonical-feature-fixture-adapter",
        schema_version=definition.input_contract.schema_version,
        artifact_id="canonical-open-interest-nonactual",
        key_columns=("contract_id", "date"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=value_columns,
        normalized_available_at=_at(10),
        actual_contract_data=False,
    )
    spec = MarketDataPITSpec(
        kind=definition.input_contract.kind,
        key_columns=("contract_id", "date"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=value_columns,
        schema_version=definition.input_contract.schema_version,
    )
    snapshot = MarketDataPITSelector(store).select(
        dataset_version_hash=dataset.version_hash,
        spec=spec,
        as_of=_at(11),
    )
    registry = FeatureRegistry(artifact_store=store)
    version = register_canonical_feature(
        registry,
        feature_id=definition.feature_id,
        version="1.0.0",
        code_revision="p2-wp02-integration",
    )
    lineage = registry.create_market_data_lineage(
        feature_version_hash=version.version_hash,
        market_snapshot=snapshot,
        parameters={"lookback_bars": 1},
    )

    with pytest.raises(FeatureRegistryError, match="actual_contract_data=true"):
        registry.materialize_deterministic_backfill(lineage)

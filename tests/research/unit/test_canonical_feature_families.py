"""P2-WP02 canonical feature 的公式、缺失语义和受控 catalog 回归。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from hashlib import sha256
import math

import polars as pl
import pytest

from northstar_quant.data_platform.market.pit import (
    MarketDataPITError,
    MarketDataPITSpec,
    MarketDataSnapshot,
)
from northstar_quant.data_platform.sources.protocol import PublicationPurpose, PublicationScope
from northstar_quant.research.features.basis import RELATIVE_BASIS, RelativeBasisComputer
from northstar_quant.research.features.canonical import CanonicalFeatureDefinition
from northstar_quant.research.features.carry import (
    ANNUALIZED_ROLL_YIELD,
    TERM_STRUCTURE_SLOPE,
    AnnualizedRollYieldComputer,
    TermStructureSlopeComputer,
)
from northstar_quant.research.features.catalog import (
    list_canonical_feature_registrations,
    register_all_canonical_features,
)
from northstar_quant.research.features.inventory import (
    INVENTORY_LEVEL_CHANGE,
    InventoryLevelChangeComputer,
)
from northstar_quant.research.features.models import (
    FeatureDependency,
    FeatureLineage,
    FeatureRegistryError,
    FeatureSpec,
    FeatureVersion,
)
from northstar_quant.research.features.momentum import MOMENTUM_ROC, MomentumRocComputer
from northstar_quant.research.features.positioning import (
    NET_POSITION_RATIO,
    NetPositionRatioComputer,
)
from northstar_quant.research.features.registry import FeatureRegistry
from northstar_quant.research.features.technical import (
    OPEN_INTEREST_CHANGE,
    REALIZED_VOLATILITY,
    VOLUME_RATIO,
    OpenInterestChangeComputer,
    RealizedVolatilityComputer,
    VolumeRatioComputer,
)


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _at(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 1, day, hour, tzinfo=UTC)


def _snapshot(
    definition: CanonicalFeatureDefinition,
    rows: list[dict[str, object]],
    *,
    actual_contract_data: bool | None = None,
    scope_exchanges: tuple[str, ...] | None = None,
    scope_products: tuple[str, ...] | None = None,
) -> MarketDataSnapshot:
    contract = definition.input_contract
    frame = pl.DataFrame(rows)
    if contract.available_at_column in frame.columns:
        frame = frame.with_columns(
            pl.col(contract.available_at_column).cast(pl.Datetime("us", "UTC"))
        )
    event_values = frame[contract.event_time_column].to_list()
    if event_values and isinstance(event_values[0], datetime):
        frame = frame.with_columns(
            pl.col(contract.event_time_column).cast(pl.Datetime("us", "UTC"))
        )
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
    spec = MarketDataPITSpec(
        kind=contract.kind,
        key_columns=(*contract.entity_key_columns, contract.event_time_column),
        event_time_column=contract.event_time_column,
        available_at_column=contract.available_at_column,
        value_columns=value_columns,
        schema_version=contract.schema_version,
    )
    available = max(frame[contract.available_at_column].to_list())
    assert isinstance(available, datetime)
    actual_contract_data = (
        contract.requires_actual_contract_data
        if actual_contract_data is None
        else actual_contract_data
    )
    scope_exchanges = (
        (("SHFE",) if actual_contract_data else ()) if scope_exchanges is None else scope_exchanges
    )
    scope_products = (
        (("RB",) if actual_contract_data else ()) if scope_products is None else scope_products
    )
    return MarketDataSnapshot.from_selected_frame(
        dataset_id=f"{definition.family}_fixture",
        dataset_version_hash=_hash(f"{definition.feature_id}:dataset"),
        source_artifact_snapshot_hash=_hash(f"{definition.feature_id}:artifact"),
        source_id="canonical_feature_test_source",
        source_config_sha256=_hash("canonical-feature-test-config"),
        publication_authorization_hash=_hash("canonical-feature-test-authorization"),
        publication_scope=PublicationScope(
            dataset_id=f"{definition.family}_fixture",
            market="CN",
            asset_type="FUTURES",
            frequency="1d",
            purpose=PublicationPurpose.INTERNAL_RESEARCH,
            environment="internal_server",
            actual_contract_data=actual_contract_data,
            exchanges=scope_exchanges,
            products=scope_products,
        ),
        spec=spec,
        source_artifact_available_at=available,
        as_of=available,
        frame=frame,
    )


def _compute(
    definition: CanonicalFeatureDefinition,
    computer_type,
    rows: list[dict[str, object]],
    parameters: Mapping[str, object],
    *,
    scope_exchanges: tuple[str, ...] | None = None,
    scope_products: tuple[str, ...] | None = None,
):
    version = definition.feature_version(version="1.0.0", code_revision="p2-wp02-unit")
    snapshot = _snapshot(
        definition,
        rows,
        scope_exchanges=scope_exchanges,
        scope_products=scope_products,
    )
    lineage = FeatureLineage.create(
        feature_version=version,
        dependencies=(
            FeatureDependency.from_market_data_snapshot(role="market_data", snapshot=snapshot),
        ),
        parameters=parameters,
        decision_at=snapshot.as_of,
        available_at=snapshot.as_of,
    )
    return tuple(
        computer_type(version).compute(
            market_snapshot=snapshot,
            parameters=parameters,
            lineage=lineage,
        )
    )


def _bar_rows() -> list[dict[str, object]]:
    return [
        {
            "date": date(2026, 1, 2),
            "symbol": "RB",
            "close": 100.0,
            "volume": 10.0,
            "open_interest": 100.0,
            "available_at": _at(2),
        },
        {
            "date": date(2026, 1, 3),
            "symbol": "RB",
            "close": 110.0,
            "volume": 20.0,
            "open_interest": 100.0,
            "available_at": _at(3),
        },
        {
            "date": date(2026, 1, 4),
            "symbol": "RB",
            "close": 132.0,
            "volume": 30.0,
            "open_interest": 120.0,
            "available_at": _at(4),
        },
    ]


def _actual_contract_oi_rows() -> list[dict[str, object]]:
    return [
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
    ]


def test_bar_feature_families_have_explicit_warmup_and_no_cross_symbol_state():
    rows = list(reversed(_bar_rows())) + [
        {
            "date": date(2026, 1, 2),
            "symbol": "CU",
            "close": 200.0,
            "volume": 5.0,
            "open_interest": 50.0,
            "available_at": _at(2),
        }
    ]
    momentum = _compute(
        MOMENTUM_ROC,
        MomentumRocComputer,
        rows,
        {"lookback_bars": 2},
    )
    momentum_by_key = {(item.key["symbol"], item.event_time): item for item in momentum}
    assert momentum_by_key[("RB", date(2026, 1, 4))].value == pytest.approx(0.32)
    assert momentum_by_key[("CU", date(2026, 1, 2))].missing_reason == "lookback_not_ready"

    volatility = _compute(
        REALIZED_VOLATILITY,
        RealizedVolatilityComputer,
        _bar_rows(),
        {"window_bars": 2},
    )
    assert volatility[0].missing_reason == "lookback_not_ready"
    assert volatility[1].missing_reason == "lookback_not_ready"
    assert volatility[2].value == pytest.approx(math.sqrt(0.005))

    volume_ratio = _compute(
        VOLUME_RATIO,
        VolumeRatioComputer,
        _bar_rows(),
        {"window_bars": 2},
    )
    assert volume_ratio[2].value == pytest.approx(2.0)

    oi_change = _compute(
        OPEN_INTEREST_CHANGE,
        OpenInterestChangeComputer,
        _actual_contract_oi_rows(),
        {"lookback_bars": 2},
    )
    assert oi_change[2].value == pytest.approx(0.2)


def test_open_interest_change_requires_actual_contract_publication_scope():
    version = OPEN_INTEREST_CHANGE.feature_version(version="1.0.0", code_revision="p2-wp02-unit")
    snapshot = _snapshot(
        OPEN_INTEREST_CHANGE,
        _actual_contract_oi_rows(),
        actual_contract_data=False,
    )
    lineage = FeatureLineage.create(
        feature_version=version,
        dependencies=(
            FeatureDependency.from_market_data_snapshot(role="market_data", snapshot=snapshot),
        ),
        parameters={"lookback_bars": 2},
        decision_at=snapshot.as_of,
        available_at=snapshot.as_of,
    )

    with pytest.raises(FeatureRegistryError, match="actual_contract_data=true"):
        tuple(
            OpenInterestChangeComputer(version).compute(
                market_snapshot=snapshot,
                parameters={"lookback_bars": 2},
                lineage=lineage,
            )
        )


def test_open_interest_change_rejects_bare_market_symbol_as_contract_id():
    version = OPEN_INTEREST_CHANGE.feature_version(version="1.0.0", code_revision="p2-wp02-unit")
    rows = _actual_contract_oi_rows()
    rows[0]["contract_id"] = "RB2605"
    snapshot = _snapshot(OPEN_INTEREST_CHANGE, rows)
    lineage = FeatureLineage.create(
        feature_version=version,
        dependencies=(
            FeatureDependency.from_market_data_snapshot(role="market_data", snapshot=snapshot),
        ),
        parameters={"lookback_bars": 2},
        decision_at=snapshot.as_of,
        available_at=snapshot.as_of,
    )

    with pytest.raises(FeatureRegistryError, match="Contract Master contract_id"):
        tuple(
            OpenInterestChangeComputer(version).compute(
                market_snapshot=snapshot,
                parameters={"lookback_bars": 2},
                lineage=lineage,
            )
        )


def test_open_interest_change_rejects_contract_outside_frozen_publication_scope():
    version = OPEN_INTEREST_CHANGE.feature_version(version="1.0.0", code_revision="p2-wp02-unit")
    snapshot = _snapshot(
        OPEN_INTEREST_CHANGE,
        _actual_contract_oi_rows(),
        scope_exchanges=("DCE",),
        scope_products=("I",),
    )
    lineage = FeatureLineage.create(
        feature_version=version,
        dependencies=(
            FeatureDependency.from_market_data_snapshot(role="market_data", snapshot=snapshot),
        ),
        parameters={"lookback_bars": 2},
        decision_at=snapshot.as_of,
        available_at=snapshot.as_of,
    )

    with pytest.raises(FeatureRegistryError, match="PublicationScope"):
        tuple(
            OpenInterestChangeComputer(version).compute(
                market_snapshot=snapshot,
                parameters={"lookback_bars": 2},
                lineage=lineage,
            )
        )


@pytest.mark.parametrize(
    ("definition", "rows"),
    [
        (OPEN_INTEREST_CHANGE, _actual_contract_oi_rows()),
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
                    "near_settlement": 110.0,
                    "next_settlement": 100.0,
                    "far_settlement": 90.0,
                    "near_expiry": date(2026, 2, 15),
                    "next_expiry": date(2026, 3, 15),
                    "far_expiry": date(2026, 5, 15),
                    "available_at": _at(5),
                }
            ],
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
        ),
    ],
    ids=lambda item: item.feature_id if isinstance(item, CanonicalFeatureDefinition) else None,
)
def test_actual_contract_features_reject_non_actual_publication_scope(
    definition: CanonicalFeatureDefinition,
    rows: list[dict[str, object]],
):
    snapshot = _snapshot(definition, rows, actual_contract_data=False)

    with pytest.raises(FeatureRegistryError, match="actual_contract_data=true"):
        definition.input_contract.validate_snapshot(
            snapshot,
            required_columns=definition.required_columns,
        )


def test_bar_feature_missing_and_invalid_inputs_fail_closed_or_remain_explicit_missing():
    zero_volume = _bar_rows()
    zero_volume[0]["volume"] = 0.0
    zero_volume[1]["volume"] = 0.0
    result = _compute(VOLUME_RATIO, VolumeRatioComputer, zero_volume, {"window_bars": 2})
    assert result[2].value is None
    assert result[2].missing_reason == "zero_baseline_volume"

    missing_close = _bar_rows()
    missing_close[1]["close"] = None
    result = _compute(MOMENTUM_ROC, MomentumRocComputer, missing_close, {"lookback_bars": 2})
    assert result[2].value is None
    assert result[2].missing_reason == "input_missing"

    invalid_close = _bar_rows()
    invalid_close[0]["close"] = 0.0
    with pytest.raises(FeatureRegistryError, match="close 必须大于 0"):
        _compute(MOMENTUM_ROC, MomentumRocComputer, invalid_close, {"lookback_bars": 1})


def test_curve_features_are_distinct_and_validate_contract_ordering():
    rows = [
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
    ]
    roll = _compute(ANNUALIZED_ROLL_YIELD, AnnualizedRollYieldComputer, rows, {})
    slope = _compute(TERM_STRUCTURE_SLOPE, TermStructureSlopeComputer, rows, {})
    assert roll[0].value == pytest.approx(math.log(1.1) / (28 / 365.25))
    assert slope[0].value is not None and slope[0].value < 0
    assert slope[0].value != pytest.approx(-roll[0].value)

    invalid = [dict(rows[0], next_expiry=date(2026, 2, 1))]
    result = _compute(ANNUALIZED_ROLL_YIELD, AnnualizedRollYieldComputer, invalid, {})
    assert result[0].missing_reason == "invalid_curve_pair"
    cross_exchange = [dict(rows[0], next_contract_id="DCE.RB.2603")]
    result = _compute(
        ANNUALIZED_ROLL_YIELD,
        AnnualizedRollYieldComputer,
        cross_exchange,
        {},
        scope_exchanges=("SHFE", "DCE"),
    )
    assert result[0].missing_reason == "invalid_curve_pair"
    result = _compute(
        TERM_STRUCTURE_SLOPE,
        TermStructureSlopeComputer,
        cross_exchange,
        {},
        scope_exchanges=("SHFE", "DCE"),
    )
    assert result[0].missing_reason == "insufficient_or_invalid_curve"
    for invalid_price in (0.0, -1.0, "not-a-price"):
        invalid_slope = [dict(rows[0], near_settlement=invalid_price)]
        assert (
            _compute(TERM_STRUCTURE_SLOPE, TermStructureSlopeComputer, invalid_slope, {})[
                0
            ].missing_reason
            == "insufficient_or_invalid_curve"
        )
    with pytest.raises(MarketDataPITError, match="NaN"):
        _compute(
            TERM_STRUCTURE_SLOPE,
            TermStructureSlopeComputer,
            [dict(rows[0], near_settlement=float("nan"))],
            {},
        )


def test_basis_inventory_and_positioning_require_their_own_feature_ready_contracts():
    basis_rows = [
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
    ]
    basis = _compute(RELATIVE_BASIS, RelativeBasisComputer, basis_rows, {})
    assert basis[0].value == pytest.approx(0.05)
    mismatched_basis = [dict(basis_rows[0], futures_unit="USD/TON")]
    assert (
        _compute(RELATIVE_BASIS, RelativeBasisComputer, mismatched_basis, {})[0].missing_reason
        == "unit_or_currency_mismatch"
    )
    for invalid_price in (0.0, -1.0, "not-a-price"):
        invalid_basis = [dict(basis_rows[0], spot_price=invalid_price)]
        assert (
            _compute(RELATIVE_BASIS, RelativeBasisComputer, invalid_basis, {})[0].missing_reason
            == "invalid_basis_input"
        )
    with pytest.raises(MarketDataPITError, match="NaN"):
        _compute(
            RELATIVE_BASIS,
            RelativeBasisComputer,
            [dict(basis_rows[0], spot_price=float("nan"))],
            {},
        )

    inventory_rows = [
        {
            "observation_date": date(2026, 1, day),
            "product": "RB",
            "inventory_scope": "social",
            "inventory_unit": "TON",
            "inventory_level": level,
            "available_at": _at(day),
        }
        for day, level in ((2, 100.0), (3, 120.0))
    ]
    inventory = _compute(
        INVENTORY_LEVEL_CHANGE,
        InventoryLevelChangeComputer,
        inventory_rows,
        {"lookback_observations": 1},
    )
    assert inventory[0].missing_reason == "lookback_not_ready"
    assert inventory[1].value == pytest.approx(0.2)

    positioning_rows = [
        {
            "report_date": date(2026, 1, 5),
            "product": "RB",
            "participant_group": "managed_money",
            "long_open_interest": 80.0,
            "short_open_interest": 20.0,
            "total_open_interest": 100.0,
            "available_at": _at(5),
        }
    ]
    positioning = _compute(
        NET_POSITION_RATIO,
        NetPositionRatioComputer,
        positioning_rows,
        {},
    )
    assert positioning[0].value == pytest.approx(0.6)
    invalid_positioning = [dict(positioning_rows[0], total_open_interest=0.0)]
    assert (
        _compute(
            NET_POSITION_RATIO,
            NetPositionRatioComputer,
            invalid_positioning,
            {},
        )[0].missing_reason
        == "invalid_position_denominator"
    )
    with pytest.raises(FeatureRegistryError, match="total_open_interest 不能小于 0"):
        _compute(
            NET_POSITION_RATIO,
            NetPositionRatioComputer,
            [dict(positioning_rows[0], total_open_interest=-1.0)],
            {},
        )


def test_catalog_is_unique_and_registers_only_explicit_versions():
    registrations = list_canonical_feature_registrations()
    assert {item.definition.feature_id for item in registrations} == {
        "momentum.roc",
        "technical.realized_volatility",
        "technical.volume_ratio",
        "technical.open_interest_change",
        "carry.annualized_roll_yield",
        "carry.term_structure_slope",
        "basis.relative_basis",
        "inventory.level_change",
        "positioning.net_position_ratio",
    }
    assert len({item.definition.implementation_hash for item in registrations}) == len(
        registrations
    )
    assert all(
        item.definition.input_contract.schema_version != "market_data_v2" for item in registrations
    )
    registry = FeatureRegistry()
    versions = register_all_canonical_features(
        registry,
        version="1.0.0",
        code_revision="p2-wp02-unit",
    )
    assert len(versions) == len(registrations)
    assert (
        register_all_canonical_features(
            registry,
            version="1.0.0",
            code_revision="p2-wp02-unit",
        )
        == versions
    )


def test_catalog_definition_and_computer_identity_cannot_be_mutated_in_process():
    with pytest.raises(TypeError):
        MOMENTUM_ROC.parameter_schema["lookback_bars"] = {"type": "string", "required": True}

    version = MOMENTUM_ROC.feature_version(version="1.0.0", code_revision="p2-wp02-unit")
    computer = MomentumRocComputer(version)
    with pytest.raises(AttributeError, match="不可变"):
        computer.implementation_hash = "0" * 64


def test_canonical_computer_rejects_version_with_a_forged_feature_spec():
    canonical_spec = MOMENTUM_ROC.feature_spec()
    forged_spec = FeatureSpec(
        feature_id=canonical_spec.feature_id,
        family=canonical_spec.family,
        description=canonical_spec.description,
        input_columns=canonical_spec.input_columns,
        input_schema_version=canonical_spec.input_schema_version,
        entity_key_columns=canonical_spec.entity_key_columns,
        output_column="forged_output",
        event_time_column=canonical_spec.event_time_column,
        available_at_column=canonical_spec.available_at_column,
        lookback_semantics=canonical_spec.lookback_semantics,
        missing_value_semantics=canonical_spec.missing_value_semantics,
    )
    forged_version = FeatureVersion.from_spec(
        forged_spec,
        version="1.0.0",
        implementation_hash=MOMENTUM_ROC.implementation_hash,
        code_revision="p2-wp02-unit",
        parameter_schema=MOMENTUM_ROC.parameter_schema,
    )

    with pytest.raises(FeatureRegistryError, match="spec_hash"):
        MomentumRocComputer(forged_version)

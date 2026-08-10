"""研究品种池配置测试。"""

from __future__ import annotations

import pytest

from northstar_quant.config.instrument_universes import (
    InstrumentUniverseConfigError,
    load_instrument_universe,
    validate_actual_product_membership,
)


def test_candidate_core_universe_is_explicit_and_extensions_do_not_count_as_core():
    universe = load_instrument_universe("cn_commodity_futures_research_core_v1")

    assert [member.product for member in universe.members_for_tier("core")] == [
        "RB",
        "CU",
        "I",
        "M",
        "TA",
    ]
    assert [member.product for member in universe.members_for_tier("extension")] == [
        "SA",
        "SC",
        "SI",
    ]
    assert universe.product_coverage({"RB", "CU", "TA"}, tier="core") == pytest.approx(0.6)


def test_actual_data_rejects_product_outside_bound_universe():
    universe = load_instrument_universe("cn_commodity_actual_contracts_core")

    with pytest.raises(InstrumentUniverseConfigError, match="不属于画像品种池"):
        validate_actual_product_membership(universe, {"RB": "SHFE", "I": "DCE"})

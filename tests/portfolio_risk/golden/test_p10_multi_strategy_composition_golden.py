"""Golden regression for P10-WP04 canonical composition serialization."""

from __future__ import annotations

import json
from pathlib import Path

from tests.helpers.canonical_multi_strategy_portfolio import (
    build_canonical_two_strategy_fixture,
)


FIXTURE = Path(__file__).with_name("p10_canonical_multi_strategy_composition_v1.json")


def test_canonical_multi_strategy_composition_matches_the_golden_identity() -> None:
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    evidence = build_canonical_two_strategy_fixture().evidence
    rendered = evidence.as_mapping()

    assert expected["format"] == "northstar.canonical-portfolio-composition-golden.v1"
    assert evidence.allocation_result.allocation_hash == expected["allocation_result"]["allocation_hash"]
    assert rendered["allocation_result"] == expected["allocation_result"]
    assert evidence.portfolio_target.as_mapping() == expected["portfolio_target"]
    assert [item.contribution_hash for item in evidence.contributions] == expected["contribution_hashes"]
    assert evidence.composition_hash == expected["composition_hash"]
    assert evidence.evidence_hash == expected["evidence_hash"]

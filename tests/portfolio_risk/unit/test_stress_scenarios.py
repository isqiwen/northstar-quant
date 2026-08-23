import pytest

from northstar_quant.portfolio_risk.risk import ScenarioError, ScenarioKind, StressScenario, evaluate_scenarios


def test_all_required_stress_kinds_are_explicit_and_deterministic():
    scenarios = tuple(StressScenario(kind.value, kind, 0.1) for kind in ScenarioKind)
    results = evaluate_scenarios(gross_notional=100, margin_required=20, scenarios=scenarios)
    assert len(results) == 7
    assert next(item for item in results if item.scenario_id == "margin_increase").stressed_margin == 22


def test_unknown_exposure_fails_closed():
    with pytest.raises(ScenarioError, match="must be known"):
        evaluate_scenarios(gross_notional=None, margin_required=20, scenarios=(StressScenario("gap", ScenarioKind.GAP, 0.1),))

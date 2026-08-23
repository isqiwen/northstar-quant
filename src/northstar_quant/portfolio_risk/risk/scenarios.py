"""P3-WP06 deterministic, offline portfolio stress scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


class ScenarioKind(str, Enum):
    GAP = "gap"
    LIMIT_MOVE = "limit_move"
    VOLATILITY_SHOCK = "volatility_shock"
    LIQUIDITY_COLLAPSE = "liquidity_collapse"
    CORRELATED_COMMODITY_SHOCK = "correlated_commodity_shock"
    MARGIN_INCREASE = "margin_increase"
    FX_SHOCK = "fx_shock"


class ScenarioError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class StressScenario:
    scenario_id: str
    kind: ScenarioKind
    shock_fraction: float

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, str) or not self.scenario_id.strip():
            raise ScenarioError("scenario_id is required")
        if not isinstance(self.kind, ScenarioKind):
            raise ScenarioError("kind must be a ScenarioKind")
        if isinstance(self.shock_fraction, bool) or not isinstance(self.shock_fraction, (int, float)) or not math.isfinite(self.shock_fraction) or self.shock_fraction < 0:
            raise ScenarioError("shock_fraction must be a non-negative finite number")


@dataclass(frozen=True, slots=True)
class StressResult:
    scenario_id: str
    stressed_loss: float
    stressed_margin: float


def evaluate_scenarios(*, gross_notional: float | None, margin_required: float | None, scenarios: tuple[StressScenario, ...]) -> tuple[StressResult, ...]:
    if gross_notional is None or margin_required is None or not all(isinstance(value, (int, float)) and math.isfinite(value) and value >= 0 for value in (gross_notional, margin_required)):
        raise ScenarioError("gross_notional and margin_required must be known non-negative finite values")
    if not isinstance(scenarios, tuple) or not scenarios or not all(isinstance(item, StressScenario) for item in scenarios):
        raise ScenarioError("scenarios must be a non-empty StressScenario tuple")
    if len({item.scenario_id for item in scenarios}) != len(scenarios):
        raise ScenarioError("scenarios cannot duplicate scenario_id")
    return tuple(StressResult(item.scenario_id, float(gross_notional) * item.shock_fraction, float(margin_required) * (1 + item.shock_fraction if item.kind is ScenarioKind.MARGIN_INCREASE else 1)) for item in sorted(scenarios, key=lambda item: item.scenario_id))


__all__ = ["ScenarioError", "ScenarioKind", "StressResult", "StressScenario", "evaluate_scenarios"]

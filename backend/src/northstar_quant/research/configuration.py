"""Compose fixed strategy, risk and simulation configurations without owning algorithms."""

from dataclasses import asdict, dataclass, field

from northstar_quant.risk.configuration import RiskConfig
from northstar_quant.risk.sizing import RiskPolicy
from northstar_quant.simulation.configuration import SimulationConfig
from northstar_quant.strategies.configuration import StrategyConfig


@dataclass(frozen=True)
class ResearchConfig:
    strategy: StrategyConfig = field(default_factory=StrategyConfig.create)
    risk: RiskConfig = field(default_factory=RiskConfig)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)

    @classmethod
    def from_mapping(cls, value: dict[str, object]) -> "ResearchConfig":
        if not isinstance(value, dict) or set(value) - {"strategy", "risk", "simulation"}:
            raise ValueError("research requires separate strategy, risk and simulation objects")
        strategy, risk, simulation = (
            value.get("strategy"),
            value.get("risk", {}),
            value.get("simulation", {}),
        )
        if (
            not isinstance(risk, dict)
            or not isinstance(simulation, dict)
            or (strategy is not None and not isinstance(strategy, dict))
        ):
            raise ValueError("research configurations must be objects")
        return cls(
            StrategyConfig.create() if strategy is None else StrategyConfig.from_request(strategy),
            RiskConfig.from_dict(risk),
            SimulationConfig.from_dict(simulation),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "strategy": self.strategy.to_dict(),
            "risk": self.risk.to_dict(),
            "simulation": self.simulation.to_dict(),
        }

    def risk_policy(self) -> RiskPolicy:
        return RiskPolicy(
            **asdict(self.risk),
            fee_per_lot=self.simulation.fee_per_lot,
            slippage_ticks=self.simulation.slippage_ticks,
        )

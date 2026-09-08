"""Fixed strategy parameters and explicit factor revisions; state belongs to instances."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from northstar_quant import code_revision, validate_code_revision
from northstar_quant.factors.definition import content_id, parameters
from northstar_quant.factors.evaluation import Binding

from .registry import resolve


@dataclass(frozen=True)
class StrategyConfig:
    strategy_id: str
    revision: str
    code_revision: str
    values: tuple[tuple[str, str | int], ...]
    factors: tuple[tuple[str, Binding], ...]

    def __post_init__(self) -> None:
        validate_code_revision(self.code_revision)
        strategy = resolve(self.strategy_id)
        if self.revision != strategy.revision or self.values != parameters(
            strategy.parameters, dict(self.values)
        ):
            raise ValueError("strategy configuration is not a canonical installed computation")
        if len(dict(self.factors)) != len(self.factors) or set(dict(self.factors)) != set(
            dict(strategy.factor_slots)
        ):
            raise ValueError("strategy factor aliases do not match its requirements")
        for alias, binding in self.factors:
            if binding.factor_id != dict(strategy.factor_slots)[alias]:
                raise ValueError("strategy factor meaning mismatch")

    @classmethod
    def create(
        cls,
        strategy_id: str = "trend.momentum",
        supplied: dict[str, object] | None = None,
        factors: dict[str, Binding] | None = None,
    ) -> StrategyConfig:
        strategy = resolve(strategy_id)
        bindings = (
            factors
            if factors is not None
            else {alias: Binding.create(identity) for alias, identity in strategy.factor_slots}
        )
        if set(bindings) != dict(strategy.factor_slots).keys():
            raise ValueError("strategy requires its declared factor aliases")
        for alias, identity in strategy.factor_slots:
            if bindings[alias].factor_id != identity:
                raise ValueError("factor binding does not meet strategy input meaning")
        return cls(
            strategy_id,
            strategy.revision,
            code_revision(),
            parameters(strategy.parameters, supplied or {}),
            tuple(sorted(bindings.items())),
        )

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> StrategyConfig:
        if set(value) != {
            "strategy_id",
            "revision",
            "code_revision",
            "parameters",
            "factor_bindings",
        }:
            raise ValueError(
                "strategy requires fixed implementation, parameters and factor bindings"
            )
        raw, supplied = value["factor_bindings"], value["parameters"]
        if not isinstance(raw, dict) or not isinstance(supplied, dict):
            raise ValueError("strategy parameters and bindings must be objects")
        bindings = {
            str(alias): Binding.from_dict(cast(dict[str, object], binding))
            for alias, binding in raw.items()
        }
        normalized = cls.create(str(value["strategy_id"]), supplied, bindings)
        if value["revision"] != normalized.revision:
            raise ValueError("strategy computation revision mismatch")
        return cls(
            normalized.strategy_id,
            normalized.revision,
            str(value["code_revision"]),
            normalized.values,
            normalized.factors,
        )

    @classmethod
    def from_request(cls, value: dict[str, object]) -> StrategyConfig:
        if "code_revision" in value:
            return cls.from_dict(value)
        if set(value) - {"strategy_id", "parameters", "factor_bindings"}:
            raise ValueError("unknown strategy request fields")
        raw = value.get("factor_bindings")
        supplied = value.get("parameters", {})
        if not isinstance(supplied, dict) or (raw is not None and not isinstance(raw, dict)):
            raise ValueError("strategy request parameters and factors must be objects")
        bindings = None
        if raw is not None:
            bindings = {}
            for alias, item in raw.items():
                if not isinstance(item, dict) or set(item) - {"factor_id", "parameters"}:
                    raise ValueError("factor requests contain an ID and parameters only")
                values = item.get("parameters", {})
                if not isinstance(values, dict):
                    raise ValueError("factor parameters must be an object")
                bindings[str(alias)] = Binding.create(str(item.get("factor_id", "")), values)
        return cls.create(str(value.get("strategy_id", "trend.momentum")), supplied, bindings)

    def to_dict(self) -> dict[str, object]:
        return {
            "strategy_id": self.strategy_id,
            "revision": self.revision,
            "code_revision": self.code_revision,
            "parameters": dict(self.values),
            "factor_bindings": {alias: binding.to_dict() for alias, binding in self.factors},
        }

    @property
    def identity(self) -> str:
        return content_id(self.to_dict())

    @property
    def history_bars(self) -> int:
        return max(binding.history_bars for _, binding in self.factors)

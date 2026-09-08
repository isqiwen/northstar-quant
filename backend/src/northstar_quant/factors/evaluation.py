"""Bounded causal evaluation and reuse within one exact, immutable input context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import cast

from northstar_quant import code_revision, validate_code_revision

from .definition import Inputs, Result, Status, content_id, parameters
from .registry import resolve


@dataclass(frozen=True)
class Binding:
    factor_id: str
    revision: str
    code_revision: str
    values: tuple[tuple[str, str | int], ...]

    def __post_init__(self) -> None:
        validate_code_revision(self.code_revision)
        factor = resolve(self.factor_id)
        if self.revision != factor.revision or self.values != parameters(
            factor.parameters, dict(self.values)
        ):
            raise ValueError("factor binding is not a canonical installed computation")

    @classmethod
    def create(cls, factor_id: str, supplied: dict[str, object] | None = None) -> Binding:
        factor = resolve(factor_id)
        return cls(
            factor_id,
            factor.revision,
            code_revision(),
            parameters(factor.parameters, supplied or {}),
        )

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> Binding:
        if set(value) != {"factor_id", "revision", "code_revision", "parameters"}:
            raise ValueError("factor binding requires fixed implementation and parameters")
        factor = resolve(str(value["factor_id"]))
        supplied = value["parameters"]
        if not isinstance(supplied, dict) or value["revision"] != factor.revision:
            raise ValueError("factor parameters or computation revision mismatch")
        return cls(
            factor.factor_id,
            factor.revision,
            str(value["code_revision"]),
            parameters(factor.parameters, supplied),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "factor_id": self.factor_id,
            "revision": self.revision,
            "code_revision": self.code_revision,
            "parameters": dict(self.values),
        }

    @property
    def identity(self) -> str:
        return content_id(self.to_dict())

    @property
    def history_bars(self) -> int:
        return resolve(self.factor_id).requirements(dict(self.values)).history_bars


def evaluate(binding: Binding, inputs: Inputs) -> Result:
    if binding.code_revision != code_revision():
        raise ValueError("factor execution requires the fixed Git code revision")
    factor = resolve(binding.factor_id)
    if binding.revision != factor.revision:
        raise ValueError("factor computation revision mismatch")
    required = factor.requirements(dict(binding.values))
    bars = inputs.bars[-required.history_bars :]

    def result(status: Status, reason: str, value: Decimal | None = None) -> Result:
        return Result(
            binding.identity,
            status,
            value,
            max(
                (bar.available_at for bar in bars if bar.available_at.utcoffset() == timedelta(0)),
                default=None,
            ),
            tuple(bar.observation_id for bar in bars),
            reason,
        )

    if (
        inputs.at.utcoffset() != timedelta(0)
        or inputs.interval_seconds != required.interval_seconds
        or inputs.price_basis != required.price_basis
    ):
        return result(Status.INVALID_INPUT, "INPUT_SEMANTICS_MISMATCH")
    if any(
        bar.contract_id != inputs.contract_id
        or bar.completed_at.utcoffset() != timedelta(0)
        or bar.available_at.utcoffset() != timedelta(0)
        or bar.available_at > inputs.at
        or bar.completed_at > bar.available_at
        for bar in bars
    ):
        return result(Status.INVALID_INPUT, "UNAVAILABLE_OR_WRONG_CONTRACT")
    if any(
        bars[index].completed_at <= bars[index - 1].completed_at
        or bars[index].available_at < bars[index - 1].available_at
        for index in range(1, len(bars))
    ):
        return result(Status.INVALID_INPUT, "UNORDERED_INPUT")
    if any(bar.close is None for bar in bars):
        return result(Status.MISSING_INPUT, "CLOSE_MISSING")
    if any(
        not cast(Decimal, bar.close).is_finite() or cast(Decimal, bar.close) <= 0 for bar in bars
    ):
        return result(Status.INVALID_INPUT, "CLOSE_NOT_POSITIVE_FINITE")
    if bars and inputs.at - bars[-1].available_at > timedelta(seconds=required.max_age_seconds):
        return result(Status.STALE_INPUT, "LAST_INPUT_TOO_OLD")
    if len(bars) < required.history_bars:
        return result(Status.WARMING_UP, "INSUFFICIENT_COMPLETED_BARS")
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        value = factor.compute(
            tuple(cast(Decimal, bar.close) for bar in bars), dict(binding.values)
        )
    return result(Status.READY, "COMPLETED_VISIBLE_INPUTS", value)


class Evaluation:
    """An event-local calculation scope, reusable by multiple fixed strategies."""

    def __init__(self, inputs: Inputs) -> None:
        self.inputs = inputs
        self._results: dict[str, Result] = {}

    def compute(self, binding: Binding) -> Result:
        if binding.identity not in self._results:
            self._results[binding.identity] = evaluate(binding, self.inputs)
        return self._results[binding.identity]


def evaluate_bindings(
    bindings: tuple[tuple[str, Binding], ...], inputs: Inputs, evaluation: Evaluation | None = None
) -> dict[str, Result]:
    scope = Evaluation(inputs) if evaluation is None else evaluation
    if scope.inputs != inputs:
        raise ValueError("factor reuse requires the exact same immutable input context")
    results: dict[str, Result] = {}
    for alias, binding in bindings:
        if alias in results:
            raise ValueError("duplicate factor alias")
        results[alias] = scope.compute(binding)
    return results

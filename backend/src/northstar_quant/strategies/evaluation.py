"""One dispatch path for Research, Paper and bounded Live inputs."""

from decimal import ROUND_HALF_EVEN, localcontext

from northstar_quant import code_revision
from northstar_quant.factors.definition import Inputs, Status
from northstar_quant.factors.evaluation import Evaluation, evaluate_bindings

from .configuration import StrategyConfig
from .definition import Decision, DecisionKind, Step, intent_from
from .registry import resolve


def step(
    config: StrategyConfig,
    inputs: Inputs,
    state: tuple[tuple[str, str | int], ...] = (),
    *,
    evaluation: Evaluation | None = None,
) -> Step:
    if config.code_revision != code_revision():
        raise ValueError("strategy execution requires the fixed Git code revision")
    strategy = resolve(config.strategy_id)
    if config.revision != strategy.revision:
        raise ValueError("strategy computation revision mismatch")
    strategy.validate_state(state)
    factors = evaluate_bindings(config.factors, inputs, evaluation)
    unavailable = next((item for item in factors.values() if item.status != Status.READY), None)
    if unavailable is not None:
        decision = Decision(DecisionKind.INPUT_UNAVAILABLE, None, unavailable.status.value, state)
    else:
        with localcontext() as context:
            context.prec = 96
            context.rounding = ROUND_HALF_EVEN
            decision = strategy.decide(factors, dict(config.values), state)
    strategy.validate_state(decision.state)
    return Step(
        decision,
        intent_from(decision, inputs, config.strategy_id, dict(config.values), factors),
        tuple(factors.items()),
    )

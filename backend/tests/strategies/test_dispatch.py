"""Different strategies share the engine while preserving causal evidence and isolated state."""

from dataclasses import replace
from decimal import Decimal, localcontext

import pytest

from northstar_quant.factors.evaluation import Binding
from northstar_quant.research.backtesting import TradingSession, run_research
from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.strategies.configuration import StrategyConfig
from northstar_quant.strategies.definition import DecisionKind
from northstar_quant.strategies.evaluation import step
from northstar_quant.strategies.registry import catalog
from tests.factors.test_calculation import inputs
from tests.test_research import dataset


@pytest.mark.parametrize("strategy_id", [item["strategy_id"] for item in catalog()])
def test_second_algorithm_needs_no_engine_branch_and_instances_are_isolated(
    strategy_id: str,
) -> None:
    config = ResearchConfig(strategy=StrategyConfig.create(strategy_id))
    data = dataset(("100", "103", "107", "110", "115", "121", "125", "130"))
    expected = run_research(data, config).to_dict()
    instance = TradingSession(
        data.market, config, snapshot_id=data.snapshot_id, content_hash=data.content_hash
    )
    other = TradingSession(
        data.market, config, snapshot_id=data.snapshot_id, content_hash=data.content_hash
    )
    before = other.checkpoint()
    steps = [instance.advance(bar) for bar in data.bars]
    assert other.checkpoint() == before
    assert instance.result(steps).to_dict() == expected
    decisions = expected["decisions"]
    assert all(item["strategy_id"] == strategy_id and item["factors"] for item in decisions)
    target = Decimal(decisions[-1]["target_fraction"])
    assert target > 0 if strategy_id == "trend.momentum" else target < 0
    changed = replace(data, bars=(*data.bars[:-1], replace(data.bars[-1], close=Decimal("128"))))
    assert run_research(changed, config).to_dict()["decisions"][:-1] == decisions[:-1]


def test_zero_target_is_distinct_from_unavailable_and_state_is_not_shared() -> None:
    config = StrategyConfig.create()
    unavailable = step(config, inputs(("100",)))
    zero = step(config, inputs(("100", "100")))
    assert (
        unavailable.decision.kind == DecisionKind.INPUT_UNAVAILABLE and unavailable.intent is None
    )
    assert zero.decision.kind == DecisionKind.SET_TARGET and zero.intent.target_fraction == 0
    precise = StrategyConfig.create(
        supplied={"threshold": "0.000000000000000001", "target_fraction": "0.123456789123456789"}
    )
    expected = step(precise, inputs(("100", "99")))
    with localcontext() as context:
        context.prec = 3
        assert step(precise, inputs(("100", "99"))) == expected
    with pytest.raises(ValueError):
        StrategyConfig.create(factors={"momentum": Binding.create("range.position")})


def test_no_new_target_keeps_existing_authorization_until_fill_and_survives_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from northstar_quant.accounting.fifo import Account
    from northstar_quant.strategies.definition import Decision
    from northstar_quant.strategies.registry import resolve

    data = dataset(("100", "110", "200", "111"))
    config = ResearchConfig()
    session = TradingSession(
        data.market, config, snapshot_id=data.snapshot_id, content_hash=data.content_hash
    )
    session.advance(data.bars[0])
    session.advance(data.bars[1])
    pending = session.pending
    assert pending is not None
    monkeypatch.setattr(
        resolve(config.strategy.strategy_id),
        "decide",
        lambda *args: Decision(DecisionKind.NO_NEW_TARGET, None, "KEEP_TARGET"),
    )
    third = session.advance(data.bars[2])
    assert third.fill is None and third.decision is None
    assert third.point["strategy"]["kind"] == "NO_NEW_TARGET"
    assert session.pending == pending
    restarted = TradingSession.from_checkpoint(
        data.market,
        config,
        snapshot_id=data.snapshot_id,
        content_hash=data.content_hash,
        checkpoint=session.checkpoint(),
        account=Account(config.simulation.initial_cash, data.market),
    )
    fourth = restarted.advance(data.bars[3])
    assert fourth.fill is not None and restarted.pending is None


def test_parameter_variants_share_one_exact_event_factor_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from northstar_quant.factors.evaluation import Evaluation
    from northstar_quant.factors.registry import resolve

    first = StrategyConfig.create()
    second = StrategyConfig.create(supplied={"threshold": "0.5"}, factors=dict(first.factors))
    data = inputs(("100", "110"))
    scope = Evaluation(data)
    factor = resolve("trend.return")
    original = factor.compute
    calls = []

    def counted(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(factor, "compute", counted)
    assert step(first, data, evaluation=scope).intent.target_fraction > 0
    assert step(second, data, evaluation=scope).intent.target_fraction == 0
    assert len(calls) == 1
    with pytest.raises(ValueError, match="exact same"):
        step(second, inputs(("100", "120")), evaluation=scope)

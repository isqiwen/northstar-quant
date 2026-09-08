"""All installed factors obey prefix causality, explicit availability and exact numerics."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import UUID

import pytest

from northstar_quant.factors.definition import Bar, Inputs, Status
from northstar_quant.factors.evaluation import Binding, evaluate, evaluate_bindings
from northstar_quant.factors.registry import catalog, resolve

CONTRACT = UUID(int=1)
AT = datetime(2026, 1, 7, 1, tzinfo=UTC)


def inputs(prices: tuple[str, ...]) -> Inputs:
    bars = tuple(
        Bar(
            UUID(int=index + 10),
            CONTRACT,
            AT + timedelta(minutes=index),
            AT + timedelta(minutes=index, seconds=1),
            Decimal(price),
        )
        for index, price in enumerate(prices)
    )
    return Inputs(bars, bars[-1].available_at, CONTRACT)


@pytest.mark.parametrize("factor_id", [item["factor_id"] for item in catalog()])
def test_registered_factor_prefix_availability_and_batch_window_agree(factor_id: str) -> None:
    binding = Binding.create(factor_id, {"window_bars": 2})
    data = inputs(("100", "110", "120", "100", "150", "80"))
    previous = []
    for index, bar in enumerate(data.bars):
        visible = replace(data, bars=data.bars[: index + 1], at=bar.available_at)
        full = evaluate(binding, visible)
        bounded = evaluate(binding, replace(visible, bars=visible.bars[-binding.history_bars :]))
        assert full == bounded
        assert full.status == (Status.WARMING_UP if index < 2 else Status.READY)
        assert full.available_at <= visible.at
        previous.append(full)
    changed = replace(data, bars=(*data.bars[:-1], replace(data.bars[-1], close=Decimal("999999"))))
    for index, bar in enumerate(data.bars[:-1]):
        assert (
            evaluate(binding, replace(changed, bars=changed.bars[: index + 1], at=bar.available_at))
            == previous[index]
        )
    assert (
        evaluate(binding, replace(data, at=data.at - timedelta(seconds=1))).status
        == Status.INVALID_INPUT
    )
    assert (
        evaluate(binding, replace(data, at=data.at + timedelta(seconds=61))).status
        == Status.STALE_INPUT
    )
    assert (
        evaluate(binding, replace(data, price_basis="ADJUSTED_CONTINUOUS")).status
        == Status.INVALID_INPUT
    )
    assert evaluate(binding, replace(data, contract_id=UUID(int=2))).status == Status.INVALID_INPUT
    assert (
        evaluate(
            binding, replace(data, bars=(*data.bars[:-1], replace(data.bars[-1], close=None)))
        ).status
        == Status.MISSING_INPUT
    )
    assert (
        evaluate(
            binding, replace(data, bars=(*data.bars[:-1], replace(data.bars[-1], close=Decimal(0))))
        ).status
        == Status.INVALID_INPUT
    )
    flat = evaluate(binding, inputs(("100", "100", "100")))
    assert flat.status == Status.READY
    assert flat.value == (Decimal(0) if factor_id == "trend.return" else Decimal("0.5"))


def test_exact_values_and_shared_results_never_use_name_only_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = Binding.create("trend.return", {"window_bars": 2})
    short = Binding.create("trend.return", {"window_bars": 1})
    data = inputs(("100", "110", "120"))
    assert evaluate(binding, data).value == Decimal("0.2")
    expected = evaluate(binding, data)
    with localcontext() as context:
        context.prec = 3
        assert evaluate(binding, data) == expected
    implementation = resolve(binding.factor_id)
    original = implementation.compute
    calls = []

    def counted(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(implementation, "compute", counted)
    result = evaluate_bindings((("a", binding), ("b", binding), ("c", short)), data)
    assert len(calls) == 2 and result["a"] is result["b"]
    assert result["c"].binding_id != result["a"].binding_id
    assert evaluate(binding, inputs(("100", "110", "130"))).value == Decimal("0.3")
    with pytest.raises(ValueError, match="unknown"):
        Binding.create("os.system")
    with pytest.raises(ValueError, match="unknown"):
        Binding.create("trend.return", {"unexpected": 1})
    with pytest.raises(ValueError):
        Binding.create("trend.return", {"window_bars": True})
    with pytest.raises(ValueError):
        replace(binding, code_revision="latest")

    with pytest.raises(ValueError, match="fixed Git"):
        evaluate(replace(binding, code_revision="b" * 40), data)
    malformed = replace(
        data,
        bars=(
            replace(data.bars[0], available_at=data.bars[0].available_at.replace(tzinfo=None)),
            *data.bars[1:],
        ),
    )
    assert evaluate(binding, malformed).status == Status.INVALID_INPUT

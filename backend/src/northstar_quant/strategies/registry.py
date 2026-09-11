"""Small reviewed strategy registry; listing metadata does not load algorithms."""

from dataclasses import asdict
from importlib import import_module
from typing import Any, cast

from .definition import Strategy

_IMPLEMENTATIONS = {
    "learned.linear_return": (
        "northstar_quant.strategies.learned.linear",
        "固定线性收益模型",
        "学习型研究",
        "训练后固定系数，预测只消费已完成因子；研究结果不是实盘资格",
    ),
    "trend.momentum": (
        "northstar_quant.strategies.trend.momentum",
        "窗口动量",
        "趋势",
        "阈值外顺势，阈值内归零",
    ),
    "mean_reversion.range": (
        "northstar_quant.strategies.mean_reversion.range",
        "区间反转",
        "均值回归",
        "在区间低端做多、高端做空，中部归零；仅研究假设",
    ),
}


def catalog() -> list[dict[str, object]]:
    return [
        {"strategy_id": key, "name": item[1], "category": item[2], "description": item[3]}
        for key, item in _IMPLEMENTATIONS.items()
    ]


def resolve(strategy_id: str) -> Strategy:
    if strategy_id not in _IMPLEMENTATIONS:
        raise ValueError(f"unknown installed strategy: {strategy_id}")
    strategy = cast(Strategy, import_module(_IMPLEMENTATIONS[strategy_id][0]).STRATEGY)
    if strategy.strategy_id != strategy_id or not strategy.revision:
        raise ValueError("strategy registration identity mismatch")
    return strategy


def describe(strategy_id: str) -> dict[str, Any]:
    strategy = resolve(strategy_id)
    return {
        **next(item for item in catalog() if item["strategy_id"] == strategy_id),
        "revision": strategy.revision,
        "parameters": [asdict(item) for item in strategy.parameters],
        "factor_slots": dict(strategy.factor_slots),
    }

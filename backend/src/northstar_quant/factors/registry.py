"""Reviewed lazy registrations. User input selects IDs, never import paths."""

from dataclasses import asdict
from importlib import import_module
from typing import Any, cast

from .definition import Factor

_IMPLEMENTATIONS = {
    "trend.return": (
        "northstar_quant.factors.trend.returns",
        "窗口收益率",
        "趋势",
        "末价 / 窗口起价 − 1",
    ),
    "range.position": (
        "northstar_quant.factors.range.position",
        "区间位置",
        "区间",
        "收盘价在历史收盘区间中的位置，平坦区间为 0.5",
    ),
}


def catalog() -> list[dict[str, object]]:
    return [
        {
            "factor_id": key,
            "name": item[1],
            "category": item[2],
            "description": item[3],
            "capabilities": ["batch", "bounded_window", "single_contract"],
            "limitations": "真实合约一分钟收盘价；不支持连续合约、截面、拟合或多腿",
        }
        for key, item in _IMPLEMENTATIONS.items()
    ]


def resolve(factor_id: str) -> Factor:
    if factor_id not in _IMPLEMENTATIONS:
        raise ValueError(f"unknown installed factor: {factor_id}")
    factor = cast(Factor, import_module(_IMPLEMENTATIONS[factor_id][0]).FACTOR)
    if factor.factor_id != factor_id or not factor.revision:
        raise ValueError("factor registration identity mismatch")
    return factor


def describe(factor_id: str) -> dict[str, Any]:
    factor = resolve(factor_id)
    return {
        **next(item for item in catalog() if item["factor_id"] == factor_id),
        "revision": factor.revision,
        "parameters": [asdict(item) for item in factor.parameters],
    }

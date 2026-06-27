"""Execution pricing helpers."""

from __future__ import annotations

import math
from collections.abc import Iterable

from northstar_quant.execution.models import MarketQuoteSnapshot


def normalize_symbols(symbols: Iterable[str]) -> list[str]:
    """标准化 symbol 列表并去重。"""

    cleaned = {
        str(symbol).strip().upper()
        for symbol in symbols
        if str(symbol).strip()
    }
    return sorted(cleaned)


def _valid_price(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        price = float(value)
    except Exception:
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    return price


def execution_reference_price_from_quote(quote: MarketQuoteSnapshot) -> float | None:
    """从券商报价中提取可执行参考价。

    优先级：
    1. bid/ask 有效时，用 last-in-spread 或 midpoint
    2. 退回到券商给出的 market_price
    3. 再退回到 last / ask / bid / close
    """

    bid = _valid_price(quote.bid)
    ask = _valid_price(quote.ask)
    last = _valid_price(quote.last)

    if bid is not None and ask is not None and bid <= ask:
        if last is not None and bid <= last <= ask:
            return last
        return (bid + ask) * 0.5

    for candidate in (
        _valid_price(quote.market_price),
        last,
        ask,
        bid,
        _valid_price(quote.close),
    ):
        if candidate is not None:
            return candidate
    return None


def build_execution_reference_price_map(
    quotes: list[MarketQuoteSnapshot],
    fallback_prices: dict[str, float],
) -> tuple[dict[str, float], dict[str, str]]:
    """构建执行参考价映射，并保留每个 symbol 的价格来源。"""

    price_map: dict[str, float] = {}
    source_map: dict[str, str] = {}

    for quote in quotes:
        symbol = str(quote.symbol or "").strip().upper()
        if not symbol:
            continue
        reference_price = execution_reference_price_from_quote(quote)
        if reference_price is None:
            continue
        price_map[symbol] = reference_price
        source_map[symbol] = quote.source

    for symbol, price in fallback_prices.items():
        normalized_symbol = str(symbol).strip().upper()
        if not normalized_symbol or normalized_symbol in price_map:
            continue
        valid_price = _valid_price(price)
        if valid_price is None:
            continue
        price_map[normalized_symbol] = valid_price
        source_map[normalized_symbol] = "local_valuation_fallback"

    return price_map, source_map

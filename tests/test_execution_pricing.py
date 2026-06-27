from northstar_quant.execution.models import MarketQuoteSnapshot
from northstar_quant.execution.pricing import (
    build_execution_reference_price_map,
    execution_reference_price_from_quote,
    normalize_symbols,
)


def test_execution_reference_price_prefers_last_inside_spread():
    quote = MarketQuoteSnapshot(
        symbol="SPY",
        bid=499.9,
        ask=500.1,
        last=500.0,
        market_price=499.5,
    )

    assert execution_reference_price_from_quote(quote) == 500.0


def test_execution_reference_price_falls_back_to_midpoint():
    quote = MarketQuoteSnapshot(
        symbol="SPY",
        bid=499.9,
        ask=500.1,
        last=501.0,
    )

    assert execution_reference_price_from_quote(quote) == 500.0


def test_build_execution_reference_price_map_uses_broker_quote_then_local_fallback():
    quotes = [
        MarketQuoteSnapshot(
            symbol="SPY",
            bid=499.9,
            ask=500.1,
            last=500.0,
        )
    ]
    fallback_prices = {"SPY": 490.0, "QQQ": 400.0}

    price_map, source_map = build_execution_reference_price_map(quotes, fallback_prices)

    assert price_map == {"QQQ": 400.0, "SPY": 500.0}
    assert source_map == {
        "QQQ": "local_valuation_fallback",
        "SPY": "broker_snapshot",
    }


def test_normalize_symbols_deduplicates_and_uppercases():
    assert normalize_symbols([" spy ", "SPY", "qqq", ""]) == ["QQQ", "SPY"]

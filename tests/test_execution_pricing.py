from northstar_quant.execution.models import MarketQuoteSnapshot
from northstar_quant.execution.pricing import (
    build_execution_reference_price_map,
    execution_reference_price_from_quote,
    normalize_symbols,
)


def test_execution_reference_price_prefers_last_inside_spread():
    quote = MarketQuoteSnapshot(
        symbol="RB2405",
        bid=499.9,
        ask=500.1,
        last=500.0,
        market_price=499.5,
    )

    assert execution_reference_price_from_quote(quote) == 500.0


def test_execution_reference_price_falls_back_to_midpoint():
    quote = MarketQuoteSnapshot(
        symbol="RB2405",
        bid=499.9,
        ask=500.1,
        last=501.0,
    )

    assert execution_reference_price_from_quote(quote) == 500.0


def test_build_execution_reference_price_map_uses_broker_quote_then_local_fallback():
    quotes = [
        MarketQuoteSnapshot(
            symbol="RB2405",
            bid=499.9,
            ask=500.1,
            last=500.0,
        )
    ]
    fallback_prices = {"RB2405": 490.0, "I2405": 400.0}

    price_map, source_map = build_execution_reference_price_map(quotes, fallback_prices)

    assert price_map == {"I2405": 400.0, "RB2405": 500.0}
    assert source_map == {
        "I2405": "local_valuation_fallback",
        "RB2405": "broker_snapshot",
    }


def test_normalize_symbols_deduplicates_and_uppercases():
    assert normalize_symbols([" RB2405 ", "RB2405", "I2405", ""]) == ["I2405", "RB2405"]

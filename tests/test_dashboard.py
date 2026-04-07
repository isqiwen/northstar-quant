import polars as pl

from northstar_quant.monitoring.dashboard import _build_price_options


def test_build_price_options_uses_explicit_price_labels_only():
    market_df = pl.DataFrame(
        {
            "date": ["2024-01-02"],
            "symbol": ["SPY"],
            "close": [100.0],
            "adjusted_close": [98.5],
        }
    )

    options = _build_price_options(market_df, {"price_field": "adjusted_close"})

    assert list(options) == [
        "复权收盘价（adjusted_close）",
        "原始收盘价（close）",
    ]
    assert "研究视角（adjusted_close）" not in options

"""标准行情的数值、OHLC 与标的池校验测试。"""

from datetime import date
import math

import polars as pl
import pytest

from northstar_quant.config.trading_profile import load_trading_profile
from northstar_quant.data.schema import validate_market_dataset


def _valid_daily_frame() -> pl.DataFrame:
    symbols = list(load_trading_profile().data.download.symbols)
    return pl.DataFrame(
        {
            "date": [date(2024, 1, 2)] * len(symbols),
            "symbol": symbols,
            "open": [100.0] * len(symbols),
            "high": [102.0] * len(symbols),
            "low": [99.0] * len(symbols),
            "close": [101.0] * len(symbols),
            "adjusted_close": [101.0] * len(symbols),
            "volume": [1_000.0] * len(symbols),
        }
    )


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("close", math.nan, "非有限"),
        ("open", -1.0, "非正值"),
        ("volume", -1.0, "负值"),
    ],
)
def test_market_schema_rejects_invalid_numeric_values(column, value, message):
    frame = _valid_daily_frame().with_row_index("_row").with_columns(
        pl.when(pl.col("_row") == 0)
        .then(pl.lit(value))
        .otherwise(pl.col(column))
        .alias(column)
    ).drop("_row")

    with pytest.raises(ValueError, match=message):
        validate_market_dataset(load_trading_profile(), frame)


def test_market_schema_rejects_inconsistent_ohlc():
    frame = _valid_daily_frame().with_row_index("_row").with_columns(
        pl.when(pl.col("_row") == 0)
        .then(pl.lit(98.0))
        .otherwise(pl.col("high"))
        .alias("high")
    ).drop("_row")

    with pytest.raises(ValueError, match="OHLC"):
        validate_market_dataset(load_trading_profile(), frame)


def test_market_schema_rejects_missing_and_unexpected_universe_symbols():
    frame = _valid_daily_frame().with_row_index("_row").with_columns(
        pl.when(pl.col("_row") == 0)
        .then(pl.lit("UNKNOWN_CONT"))
        .otherwise(pl.col("symbol"))
        .alias("symbol")
    ).drop("_row")

    with pytest.raises(ValueError, match="标的集合"):
        validate_market_dataset(load_trading_profile(), frame)

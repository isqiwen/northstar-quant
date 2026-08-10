"""实际合约数据契约与无跳跃连续信号测试。"""

from datetime import date

import polars as pl
import pytest

from northstar_quant.config.trading_profile import load_trading_profile
from northstar_quant.data.futures_actual import (
    ACTUAL_FUTURES_DAILY_SCHEMA_VERSION,
    build_adjusted_continuous_signal_data,
)
from northstar_quant.data.schema import validate_market_dataset


DAY_1 = date(2024, 1, 2)
DAY_2 = date(2024, 1, 3)
DAY_3 = date(2024, 1, 4)


def test_actual_contract_schema_and_signal_chain_do_not_include_roll_gap():
    profile = load_trading_profile("cn_futures_daily_actual_offline")
    frame = _actual_frame()

    validation = validate_market_dataset(profile, frame)
    signal = build_adjusted_continuous_signal_data(frame)

    assert validation["schema_version"] == ACTUAL_FUTURES_DAILY_SCHEMA_VERSION
    assert signal.get_column("symbol").unique().to_list() == ["RB_CONT"]
    assert signal.get_column("source_contract").to_list() == [
        "RB2405",
        "RB2405",
        "RB2410",
    ]
    assert signal.get_column("close").to_list() == pytest.approx(
        [1.0, 1.02, 1.02 * 203 / 201]
    )


def test_actual_contract_schema_rejects_same_day_active_selection():
    profile = load_trading_profile("cn_futures_daily_actual_offline")
    frame = _actual_frame().with_columns(
        pl.when(pl.col("date") == DAY_3)
        .then(pl.lit(DAY_3))
        .otherwise(pl.col("selection_date"))
        .alias("selection_date")
    )

    with pytest.raises(ValueError, match="selection_date"):
        validate_market_dataset(profile, frame)


def test_actual_contract_schema_rejects_incomplete_sessions():
    profile = load_trading_profile("cn_futures_daily_actual_offline")
    frame = _actual_frame().with_columns(
        pl.when((pl.col("date") == DAY_2) & (pl.col("symbol") == "RB2405"))
        .then(pl.lit(False))
        .otherwise(pl.col("session_complete"))
        .alias("session_complete")
    )

    with pytest.raises(ValueError, match="夜盘/日盘"):
        validate_market_dataset(profile, frame)


def test_actual_contract_schema_rejects_product_outside_profile_universe():
    profile = load_trading_profile("cn_futures_daily_actual_offline")
    frame = _actual_frame().with_columns(
        pl.lit("I").alias("product"),
        pl.lit("DCE").alias("exchange"),
    )

    with pytest.raises(ValueError, match="不属于画像品种池"):
        validate_market_dataset(profile, frame)


def _actual_frame() -> pl.DataFrame:
    prices = {
        DAY_1: {"RB2405": 100.0, "RB2410": 200.0},
        DAY_2: {"RB2405": 102.0, "RB2410": 201.0},
        DAY_3: {"RB2405": 103.0, "RB2410": 203.0},
    }
    active = {DAY_1: "RB2405", DAY_2: "RB2405", DAY_3: "RB2410"}
    selection = {
        DAY_1: date(2023, 12, 29),
        DAY_2: date(2023, 12, 29),
        DAY_3: DAY_2,
    }
    previous_settlement = {"RB2405": 99.0, "RB2410": 199.0}
    rows: list[dict[str, object]] = []
    for current_day in (DAY_1, DAY_2, DAY_3):
        for symbol in ("RB2405", "RB2410"):
            close = prices[current_day][symbol]
            rows.append(
                {
                    "date": current_day,
                    "symbol": symbol,
                    "product": "RB",
                    "exchange": "SHFE",
                    "open": close - 0.5,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "settlement": close,
                    "pre_settlement": previous_settlement[symbol],
                    "volume": 100_000.0,
                    "open_interest": 50_000.0,
                    "upper_limit": close + 20.0,
                    "lower_limit": close - 20.0,
                    "margin_rate": 0.1,
                    "commission_open_per_lot": 1.0,
                    "commission_open_rate": 0.0,
                    "commission_close_per_lot": 1.0,
                    "commission_close_rate": 0.0,
                    "commission_close_today_per_lot": 2.0,
                    "commission_close_today_rate": 0.0,
                    "max_position_lots": 1_000,
                    "active_contract": active[current_day],
                    "selection_date": selection[current_day],
                    "first_session": "night",
                    "session_complete": True,
                }
            )
            previous_settlement[symbol] = close
    return pl.DataFrame(rows)

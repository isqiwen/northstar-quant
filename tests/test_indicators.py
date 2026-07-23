from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from northstar_quant.data.features import add_basic_features
from northstar_quant.indicators import (
    average_true_range,
    bollinger_bands,
    chaikin_money_flow,
    donchian_channel,
    exponential_moving_average,
    get_indicator_spec,
    historical_volatility,
    list_indicator_specs,
    moving_average_convergence_divergence,
    on_balance_volume,
    prior_rolling_max,
    rate_of_change,
    relative_strength_index,
    simple_moving_average,
    stochastic_oscillator,
    volume_weighted_average_price,
    williams_r,
)


def test_grouped_indicators_sort_and_do_not_cross_symbol_boundaries():
    frame = pl.DataFrame(
        {
            "date": [date(2024, 1, 2), date(2024, 1, 1), date(2024, 1, 1), date(2024, 1, 2)],
            "symbol": ["AAA", "AAA", "BBB", "BBB"],
            "close": [12.0, 10.0, 20.0, 22.0],
        }
    )

    result = rate_of_change(
        frame,
        value_column="close",
        periods=1,
        output_column="ret_1",
        group_by="symbol",
        order_by="date",
    )

    assert result["symbol"].to_list() == ["AAA", "AAA", "BBB", "BBB"]
    assert result["ret_1"].to_list() == [None, pytest.approx(0.2), None, pytest.approx(0.1)]


def test_trend_indicators_and_prior_rolling_max_exclude_current_bar():
    frame = pl.DataFrame({"timestamp": [1, 2, 3, 4], "close": [10.0, 12.0, 11.0, 13.0]})

    result = simple_moving_average(
        frame,
        value_column="close",
        window=2,
        output_column="sma_2",
        order_by="timestamp",
    )
    result = exponential_moving_average(
        result,
        value_column="close",
        span=2,
        output_column="ema_2",
        order_by="timestamp",
    )
    result = prior_rolling_max(
        result,
        value_columns=("close",),
        window=2,
        order_by="timestamp",
    )

    assert result["sma_2"].to_list() == [None, 11.0, 11.5, 12.0]
    assert result["ema_2"][0] == pytest.approx(10.0)
    assert result["close_prior_rolling_max"].to_list() == [None, None, 12.0, 12.0]


def test_volatility_and_vwap_handle_grouped_data_and_zero_volume():
    frame = pl.DataFrame(
        {
            "timestamp": [1, 2, 3],
            "symbol": ["AAA", "AAA", "AAA"],
            "close": [10.0, 12.0, 9.0],
            "volume": [0.0, 0.0, 10.0],
        }
    )

    result = historical_volatility(
        frame,
        value_column="close",
        window=2,
        output_column="vol_2",
        group_by="symbol",
        order_by="timestamp",
    )
    result = volume_weighted_average_price(
        result,
        price_column="close",
        volume_column="volume",
        window=2,
        output_column="vwap_2",
        group_by="symbol",
        order_by="timestamp",
    )

    assert result["vol_2"].to_list()[:2] == [None, None]
    assert result["vol_2"][2] is not None
    assert result["vwap_2"].to_list() == [None, None, 9.0]


def test_features_are_composed_from_indicator_functions():
    frame = pl.DataFrame(
        {
            "date": [date(2024, 1, day) for day in range(1, 23)],
            "symbol": ["AAA"] * 22,
            "close": [float(day) for day in range(1, 23)],
        }
    )

    result = add_basic_features(frame)

    assert {"ret_1", "mom_20", "vol_20"}.issubset(result.columns)
    assert result["ret_1"][-1] == pytest.approx(1 / 21)
    assert result["mom_20"][-1] == pytest.approx(10.0)


def test_invalid_indicator_input_fails_with_explicit_error():
    with pytest.raises(ValueError, match="window 必须是大于 0 的整数"):
        simple_moving_average(
            pl.DataFrame({"close": [1.0]}),
            value_column="close",
            window=0,
            output_column="sma",
        )

    with pytest.raises(ValueError, match="缺少必需行情列: volume"):
        volume_weighted_average_price(
            pl.DataFrame({"close": [1.0]}),
            price_column="close",
            volume_column="volume",
            window=2,
            output_column="vwap",
        )

    assert get_indicator_spec("vwap").input_columns == ("price", "volume")
    with pytest.raises(ValueError, match="未注册的指标"):
        get_indicator_spec("not_found")


def test_trend_indicators_cover_macd_and_donchian_channel():
    frame = _ohlcv_frame()

    result = moving_average_convergence_divergence(
        frame,
        value_column="close",
        fast_span=2,
        slow_span=3,
        signal_span=2,
        order_by="timestamp",
    )
    result = donchian_channel(
        result,
        high_column="high",
        low_column="low",
        window=2,
        order_by="timestamp",
    )

    assert result["macd_histogram"][-1] == pytest.approx(
        result["macd"][-1] - result["macd_signal"][-1]
    )
    assert result["donchian_upper"].to_list() == [None, 13.0, 13.0, 16.0]
    assert result["donchian_lower"].to_list() == [None, 9.0, 10.0, 10.0]
    assert result["donchian_middle"][-1] == 13.0


def test_momentum_indicators_cover_rsi_stochastic_and_williams_r():
    frame = _ohlcv_frame()

    result = relative_strength_index(
        frame,
        value_column="close",
        window=2,
        order_by="timestamp",
    )
    result = stochastic_oscillator(
        result,
        high_column="high",
        low_column="low",
        close_column="close",
        window=3,
        smoothing=2,
        order_by="timestamp",
    )
    result = williams_r(
        result,
        high_column="high",
        low_column="low",
        close_column="close",
        window=3,
        order_by="timestamp",
    )

    assert result["rsi"].drop_nulls().min() >= 0
    assert result["rsi"].drop_nulls().max() <= 100
    assert result["stochastic_k"][-1] == pytest.approx(100 * 5 / 6)
    assert result["stochastic_d"][-1] == pytest.approx((50 + 100 * 5 / 6) / 2)
    assert result["williams_r"][-1] == pytest.approx(-100 / 6)
    assert not [column for column in result.columns if column.startswith("__indicator_")]


def test_volatility_and_volume_indicators_cover_atr_bollinger_obv_and_cmf():
    frame = _ohlcv_frame()

    result = average_true_range(
        frame,
        high_column="high",
        low_column="low",
        close_column="close",
        window=2,
        order_by="timestamp",
    )
    result = bollinger_bands(
        result,
        value_column="close",
        window=2,
        order_by="timestamp",
    )
    result = on_balance_volume(
        result,
        close_column="close",
        volume_column="volume",
        order_by="timestamp",
    )
    result = chaikin_money_flow(
        result,
        high_column="high",
        low_column="low",
        close_column="close",
        volume_column="volume",
        window=2,
        order_by="timestamp",
    )

    assert result["atr"].to_list()[:1] == [None]
    assert result["atr"][-1] == pytest.approx(3.625)
    assert result["bollinger_middle"][1] == 11.0
    assert result["bollinger_upper"][1] == 13.0
    assert result["bollinger_lower"][1] == 9.0
    assert result["obv"].to_list() == [0.0, 200.0, 50.0, 350.0]
    assert result["cmf"][-1] == pytest.approx(1 / 3)


def test_indicator_registry_lists_all_builtin_categories():
    names = {spec.name for spec in list_indicator_specs()}

    assert {"macd", "rsi", "atr", "cmf"}.issubset(names)


def _ohlcv_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "timestamp": [1, 2, 3, 4],
            "high": [11.0, 13.0, 12.0, 16.0],
            "low": [9.0, 10.0, 10.0, 12.0],
            "close": [10.0, 12.0, 11.0, 15.0],
            "volume": [100.0, 200.0, 150.0, 300.0],
        }
    )

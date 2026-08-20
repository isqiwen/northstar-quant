from datetime import date

import pandas as pd
import pytest

from northstar_quant.data_platform.sources.providers.akshare import _standardize_main_continuous_history


def test_standardize_main_continuous_history_uses_internal_symbol_and_close_price():
    raw = pd.DataFrame(
        {
            "日期": ["2024-01-02", "2024-01-03"],
            "开盘价": [3500, 3510],
            "最高价": [3520, 3530],
            "最低价": [3490, 3500],
            "收盘价": [3510, 3520],
            "成交量": [123456, 234567],
            "持仓量": [100, 101],
        }
    )

    result = _standardize_main_continuous_history(raw, "RB_CONT", "RB0")

    assert result.columns == [
        "date",
        "symbol",
        "open",
        "high",
        "low",
        "close",
        "adjusted_close",
        "volume",
    ]
    assert result.to_dicts() == [
        {
            "date": date(2024, 1, 2),
            "symbol": "RB_CONT",
            "open": 3500.0,
            "high": 3520.0,
            "low": 3490.0,
            "close": 3510.0,
            "adjusted_close": 3510.0,
            "volume": 123456.0,
        },
        {
            "date": date(2024, 1, 3),
            "symbol": "RB_CONT",
            "open": 3510.0,
            "high": 3530.0,
            "low": 3500.0,
            "close": 3520.0,
            "adjusted_close": 3520.0,
            "volume": 234567.0,
        },
    ]


def test_standardize_main_continuous_history_rejects_missing_required_columns():
    raw = pd.DataFrame({"日期": ["2024-01-02"], "收盘价": [3510]})

    with pytest.raises(ValueError, match="缺少字段"):
        _standardize_main_continuous_history(raw, "RB_CONT", "RB0")

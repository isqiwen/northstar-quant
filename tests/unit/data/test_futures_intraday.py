"""实际期货分钟回放数据契约测试。"""

import polars as pl
import pytest

from northstar_quant.config.trading_profile import load_trading_profile
from northstar_quant.data.futures_intraday import (
    build_intraday_continuous_signal_data,
    validate_actual_futures_intraday_dataset,
)
from tests.support.futures_actual import actual_futures_intraday_frame


def test_intraday_contract_validates_and_builds_daily_gap_free_signal():
    profile = load_trading_profile("cn_futures_intraday_replay_offline")
    market = actual_futures_intraday_frame()

    validation = validate_actual_futures_intraday_dataset(profile, market)
    signal = build_intraday_continuous_signal_data(market)

    assert validation["schema_version"] == "actual_futures_intraday_v1"
    assert validation["quote_replay_ready"] is True
    assert signal.height == 5
    assert signal.get_column("symbol").unique().to_list() == ["RB_CONT"]
    assert signal.get_column("source_contract").to_list() == [
        "RB2405",
        "RB2405",
        "RB2405",
        "RB2410",
        "RB2410",
    ]


def test_intraday_contract_rejects_missing_day_end_marker():
    profile = load_trading_profile("cn_futures_intraday_replay_offline")
    market = actual_futures_intraday_frame().with_columns(
        pl.lit(False).alias("is_trading_day_end")
    )

    with pytest.raises(ValueError, match="必须且只能有一根"):
        validate_actual_futures_intraday_dataset(profile, market)


def test_intraday_contract_rejects_future_active_selection():
    profile = load_trading_profile("cn_futures_intraday_replay_offline")
    market = actual_futures_intraday_frame().with_columns(
        pl.col("date").alias("selection_date")
    )

    with pytest.raises(ValueError, match="selection_date"):
        validate_actual_futures_intraday_dataset(profile, market)

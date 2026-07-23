"""特征工程模块。"""

import polars as pl

from northstar_quant.indicators.momentum import rate_of_change
from northstar_quant.indicators.volatility import historical_volatility


def add_basic_features(df: pl.DataFrame) -> pl.DataFrame:
    """给行情数据增加基础特征。

    这里演示的是最常见的一组基础特征：
    - 单日收益
    - 20 日动量
    - 20 日波动率

    注意：
    这里默认输入已经按 symbol、date 排序。
    """

    result = rate_of_change(
        df,
        value_column="close",
        periods=1,
        output_column="ret_1",
        group_by="symbol",
        order_by="date",
    )
    result = rate_of_change(
        result,
        value_column="close",
        periods=20,
        output_column="mom_20",
        group_by="symbol",
        order_by="date",
    )
    return historical_volatility(
        result,
        value_column="close",
        window=20,
        output_column="vol_20",
        group_by="symbol",
        order_by="date",
    )

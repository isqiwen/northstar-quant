"""特征工程模块。"""

import polars as pl


def add_basic_features(df: pl.DataFrame) -> pl.DataFrame:
    """给行情数据增加基础特征。

    这里演示的是最常见的一组基础特征：
    - 单日收益
    - 20 日动量
    - 20 日波动率

    注意：
    这里默认输入已经按 symbol、date 排序。
    """

    return (
        df.sort(["symbol", "date"])
        .with_columns(
            [
                (pl.col("close") / pl.col("close").shift(1) - 1)
                .over("symbol")
                .alias("ret_1"),
                (pl.col("close") / pl.col("close").shift(20) - 1)
                .over("symbol")
                .alias("mom_20"),
                pl.col("close")
                .pct_change()
                .rolling_std(window_size=20)
                .over("symbol")
                .alias("vol_20"),
            ]
        )
    )

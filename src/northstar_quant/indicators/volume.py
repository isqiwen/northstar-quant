"""成交量类技术指标。"""

from __future__ import annotations

import polars as pl

from northstar_quant.indicators.validation import (
    grouped,
    prepare_frame,
    temporary_column_name,
    validate_window,
)


def volume_weighted_average_price(
    data: pl.DataFrame,
    *,
    price_column: str,
    volume_column: str,
    window: int,
    output_column: str,
    group_by: str | None = None,
    order_by: str | None = None,
) -> pl.DataFrame:
    """计算滚动 VWAP 列。

    对窗口 ``N = window``，公式为
    ``VWAP_t = Σ(P_i * Volume_i) / Σ(Volume_i)``，求和范围为 ``t-N+1`` 到 ``t``。
    ``price_column`` 应是策略选定的价格（例如收盘价或典型价），本函数不会隐式
    替换为其他价格。前 ``N - 1`` 个 bar 为 null；滚动成交量为零时也返回 null，
    避免用无效价格填充交易决策输入。
    """

    validate_window(window)
    prepared = prepare_frame(
        data,
        required_columns=(price_column, volume_column),
        group_by=group_by,
        order_by=order_by,
    )
    numerator = grouped(
        (pl.col(price_column) * pl.col(volume_column)).rolling_sum(window_size=window),
        group_by,
    )
    denominator = grouped(pl.col(volume_column).rolling_sum(window_size=window), group_by)
    expression = pl.when(denominator == 0).then(None).otherwise(numerator / denominator)
    return prepared.with_columns(expression.alias(output_column))


def on_balance_volume(
    data: pl.DataFrame,
    *,
    close_column: str,
    volume_column: str,
    output_column: str = "obv",
    group_by: str | None = None,
    order_by: str | None = None,
) -> pl.DataFrame:
    """计算能量潮（OBV）累计成交量列。

    令 ``V_t`` 为成交量、``C_t`` 为收盘价，增量为：上涨时 ``+V_t``，下跌时
    ``-V_t``，价格不变时为 0；``OBV_t = OBV_{t-1} + 增量_t``。首个 bar 没有前
    收盘价，增量定义为 0，因此 OBV 从 0 开始。OBV 是累计量而非归一化指标，
    应在同一标的的时间序列内比较，``group_by`` 可确保不同标的不会串联累计。
    """

    prepared = prepare_frame(
        data,
        required_columns=(close_column, volume_column),
        group_by=group_by,
        order_by=order_by,
    )
    previous_close_column = temporary_column_name(prepared, "obv_previous_close")
    direction_column = temporary_column_name(prepared, "obv_direction")
    result = prepared.with_columns(
        grouped(pl.col(close_column).shift(1), group_by).alias(previous_close_column)
    ).with_columns(
        pl.when(pl.col(close_column) > pl.col(previous_close_column))
        .then(pl.col(volume_column))
        .when(pl.col(close_column) < pl.col(previous_close_column))
        .then(-pl.col(volume_column))
        .otherwise(0.0)
        .alias(direction_column)
    )
    return result.with_columns(
        grouped(pl.col(direction_column).cum_sum(), group_by).alias(output_column)
    ).drop([previous_close_column, direction_column])


def chaikin_money_flow(
    data: pl.DataFrame,
    *,
    high_column: str,
    low_column: str,
    close_column: str,
    volume_column: str,
    window: int = 20,
    output_column: str = "cmf",
    group_by: str | None = None,
    order_by: str | None = None,
) -> pl.DataFrame:
    """计算 Chaikin Money Flow（CMF）列。

    当日资金流乘数
    ``MFM_t = ((Close_t - Low_t) - (High_t - Close_t)) / (High_t - Low_t)``，
    资金流量 ``MFV_t = MFM_t * Volume_t``；窗口 ``N`` 的
    ``CMF_t = Σ MFV_i / Σ Volume_i``。若 ``High_t == Low_t``，该日乘数按 0
    处理；若窗口累计成交量为零则返回 null。结果通常在 -1 到 1 之间，前
    ``window - 1`` 个 bar 为 null。
    """

    validate_window(window)
    prepared = prepare_frame(
        data,
        required_columns=(high_column, low_column, close_column, volume_column),
        group_by=group_by,
        order_by=order_by,
    )
    price_range = pl.col(high_column) - pl.col(low_column)
    multiplier = pl.when(price_range == 0).then(0.0).otherwise(
        ((pl.col(close_column) - pl.col(low_column)) - (pl.col(high_column) - pl.col(close_column)))
        / price_range
    )
    money_flow_volume = multiplier * pl.col(volume_column)
    numerator = grouped(money_flow_volume.rolling_sum(window_size=window), group_by)
    denominator = grouped(pl.col(volume_column).rolling_sum(window_size=window), group_by)
    expression = pl.when(denominator == 0).then(None).otherwise(numerator / denominator)
    return prepared.with_columns(expression.alias(output_column))

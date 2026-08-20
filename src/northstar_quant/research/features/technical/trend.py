"""趋势类技术指标。"""

from __future__ import annotations

import polars as pl

from northstar_quant.research.features.technical.validation import (
    grouped,
    prepare_frame,
    temporary_column_name,
    validate_window,
)


def simple_moving_average(
    data: pl.DataFrame,
    *,
    value_column: str,
    window: int,
    output_column: str,
    group_by: str | None = None,
    order_by: str | None = None,
) -> pl.DataFrame:
    """计算简单移动平均（SMA）列。

    对当前时点 ``t`` 和窗口 ``N = window``，以价格 ``P`` 计算：

    ``SMA_t = (P_t + P_{t-1} + ... + P_{t-N+1}) / N``。

    前 ``N - 1`` 个 bar 没有完整窗口，结果为 null。设置 ``group_by`` 后，每个
    分组独立计算；设置 ``order_by`` 后先排序再计算，通常应分别传入 ``symbol``
    和 ``date`` / ``timestamp``。
    """

    validate_window(window)
    prepared = prepare_frame(
        data,
        required_columns=(value_column,),
        group_by=group_by,
        order_by=order_by,
    )
    expression = grouped(pl.col(value_column).rolling_mean(window_size=window), group_by)
    return prepared.with_columns(expression.alias(output_column))


def exponential_moving_average(
    data: pl.DataFrame,
    *,
    value_column: str,
    span: int,
    output_column: str,
    group_by: str | None = None,
    order_by: str | None = None,
) -> pl.DataFrame:
    """计算指数移动平均（EMA）列。

    ``span`` 为 ``S`` 时，平滑系数 ``α = 2 / (S + 1)``；首个值取 ``P_0``，
    后续递推为 ``EMA_t = α * P_t + (1 - α) * EMA_{t-1}``。该实现使用
    ``adjust=False``，因此与交易软件常见的递推 EMA 定义一致。``group_by`` 和
    ``order_by`` 的分组、排序语义与 :func:`simple_moving_average` 相同。
    """

    validate_window(span, parameter="span")
    prepared = prepare_frame(
        data,
        required_columns=(value_column,),
        group_by=group_by,
        order_by=order_by,
    )
    expression = grouped(pl.col(value_column).ewm_mean(span=span, adjust=False), group_by)
    return prepared.with_columns(expression.alias(output_column))


def prior_rolling_max(
    data: pl.DataFrame,
    *,
    value_columns: tuple[str, ...],
    window: int,
    suffix: str = "_prior_rolling_max",
    order_by: str | None = None,
) -> pl.DataFrame:
    """计算不含当前 bar 的滚动最高值。

    该函数适用于已透视的宽表。每个 ``value_columns`` 列独立计算，输出列名为
    ``{列名}{suffix}``。对价格 ``P``，结果为
    ``max(P_{t-window}, ..., P_{t-1})``，明确排除 ``P_t``，可避免将当前价格
    纳入突破阈值而产生前视偏差。前 ``window`` 个 bar 结果为 null；``order_by``
    必须指向正确的时间顺序。
    """

    validate_window(window)
    if not value_columns:
        raise ValueError("value_columns 至少需要一个数值列")
    prepared = prepare_frame(
        data,
        required_columns=value_columns,
        order_by=order_by,
    )
    expressions = [
        pl.col(column)
        .shift(1)
        .rolling_max(window_size=window, min_samples=window)
        .alias(f"{column}{suffix}")
        for column in value_columns
    ]
    return prepared.with_columns(expressions)


def moving_average_convergence_divergence(
    data: pl.DataFrame,
    *,
    value_column: str,
    fast_span: int = 12,
    slow_span: int = 26,
    signal_span: int = 9,
    macd_column: str = "macd",
    signal_column: str = "macd_signal",
    histogram_column: str = "macd_histogram",
    group_by: str | None = None,
    order_by: str | None = None,
) -> pl.DataFrame:
    """计算 MACD 线、信号线和柱状图。

    设快、慢 EMA 的周期分别为 ``F``、``S``，信号周期为 ``G``：

    ``MACD_t = EMA(P, F)_t - EMA(P, S)_t``；
    ``Signal_t = EMA(MACD, G)_t``；
    ``Histogram_t = MACD_t - Signal_t``。

    默认 ``12 / 26 / 9`` 是常见日线参数。窗口须满足 ``fast_span < slow_span``，
    以避免含义相反的配置。所有 EMA 在每个 ``group_by`` 分组中按 ``order_by``
    独立递推，输出不保留内部快慢 EMA 临时列。
    """

    validate_window(fast_span, parameter="fast_span")
    validate_window(slow_span, parameter="slow_span")
    validate_window(signal_span, parameter="signal_span")
    if fast_span >= slow_span:
        raise ValueError("fast_span 必须小于 slow_span")

    prepared = prepare_frame(
        data,
        required_columns=(value_column,),
        group_by=group_by,
        order_by=order_by,
    )
    fast_column = temporary_column_name(prepared, "macd_fast_ema")
    slow_column = temporary_column_name(prepared, "macd_slow_ema")
    result = exponential_moving_average(
        prepared,
        value_column=value_column,
        span=fast_span,
        output_column=fast_column,
        group_by=group_by,
        order_by=order_by,
    )
    result = exponential_moving_average(
        result,
        value_column=value_column,
        span=slow_span,
        output_column=slow_column,
        group_by=group_by,
        order_by=order_by,
    ).with_columns((pl.col(fast_column) - pl.col(slow_column)).alias(macd_column))
    result = exponential_moving_average(
        result,
        value_column=macd_column,
        span=signal_span,
        output_column=signal_column,
        group_by=group_by,
        order_by=order_by,
    )
    return result.with_columns(
        (pl.col(macd_column) - pl.col(signal_column)).alias(histogram_column)
    ).drop([fast_column, slow_column])


def donchian_channel(
    data: pl.DataFrame,
    *,
    high_column: str,
    low_column: str,
    window: int,
    upper_column: str = "donchian_upper",
    lower_column: str = "donchian_lower",
    middle_column: str = "donchian_middle",
    group_by: str | None = None,
    order_by: str | None = None,
) -> pl.DataFrame:
    """计算 Donchian 通道上下轨和中轨，当前 bar 纳入窗口。

    对窗口 ``N``，上轨 ``U_t = max(High_{t-N+1}, ..., High_t)``，下轨
    ``L_t = min(Low_{t-N+1}, ..., Low_t)``，中轨 ``M_t = (U_t + L_t) / 2``。
    前 ``N - 1`` 个 bar 为 null。它与 :func:`prior_rolling_max` 不同，当前 bar
    会参与通道计算；若用作突破入场阈值，应使用前序版本避免前视偏差。
    """

    validate_window(window)
    prepared = prepare_frame(
        data,
        required_columns=(high_column, low_column),
        group_by=group_by,
        order_by=order_by,
    )
    upper = grouped(pl.col(high_column).rolling_max(window_size=window), group_by)
    lower = grouped(pl.col(low_column).rolling_min(window_size=window), group_by)
    return prepared.with_columns(
        [
            upper.alias(upper_column),
            lower.alias(lower_column),
            ((upper + lower) / 2.0).alias(middle_column),
        ]
    )

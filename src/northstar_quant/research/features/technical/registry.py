"""指标元数据注册表。

注册表只提供稳定名称和输入说明，不在运行时动态执行任意函数。这样可支持
配置和展示，同时避免把策略逻辑隐式地移入指标层。
"""

from __future__ import annotations

from northstar_quant.research.features.technical.base import IndicatorSpec


_SPECS: tuple[IndicatorSpec, ...] = (
    IndicatorSpec("sma", "trend", ("value",), "简单移动平均"),
    IndicatorSpec("ema", "trend", ("value",), "指数移动平均"),
    IndicatorSpec("macd", "trend", ("value",), "MACD、信号线和柱状图"),
    IndicatorSpec("donchian_channel", "trend", ("high", "low"), "Donchian 通道"),
    IndicatorSpec("prior_rolling_max", "trend", ("value",), "不含当前 bar 的滚动最高值"),
    IndicatorSpec("rate_of_change", "momentum", ("value",), "指定周期价格变动率"),
    IndicatorSpec("rsi", "momentum", ("value",), "Wilder 平滑 RSI"),
    IndicatorSpec("stochastic", "momentum", ("high", "low", "close"), "随机指标 %K / %D"),
    IndicatorSpec("williams_r", "momentum", ("high", "low", "close"), "Williams %R"),
    IndicatorSpec("historical_volatility", "volatility", ("value",), "滚动历史波动率"),
    IndicatorSpec("atr", "volatility", ("high", "low", "close"), "Wilder 平滑 ATR"),
    IndicatorSpec("bollinger_bands", "volatility", ("value",), "布林带"),
    IndicatorSpec("vwap", "volume", ("price", "volume"), "滚动成交量加权平均价格"),
    IndicatorSpec("obv", "volume", ("close", "volume"), "能量潮"),
    IndicatorSpec("cmf", "volume", ("high", "low", "close", "volume"), "Chaikin Money Flow"),
)

_SPECS_BY_NAME = {spec.name: spec for spec in _SPECS}


def list_indicator_specs() -> tuple[IndicatorSpec, ...]:
    """返回所有内置指标的不可变元数据。"""

    return _SPECS


def get_indicator_spec(name: str) -> IndicatorSpec:
    """按稳定名称读取指标元数据。"""

    try:
        return _SPECS_BY_NAME[name]
    except KeyError as exc:
        raise ValueError(f"未注册的指标: {name}") from exc

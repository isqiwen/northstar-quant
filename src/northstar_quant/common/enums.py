"""项目中的常用枚举定义。"""

from __future__ import annotations

from enum import Enum


class StringEnum(str, Enum):
    """可直接当字符串使用的枚举基类。"""

    @classmethod
    def parse(cls, value: str) -> "StringEnum":
        normalized = str(value).strip().lower()
        for member in cls:
            if member.value.lower() == normalized:
                return member
        supported = ", ".join(member.value for member in cls)
        raise ValueError(f"{cls.__name__} 不支持取值 {value!r}，可选值：{supported}")


class Environment(StringEnum):
    """运行环境枚举。"""

    DEV = "dev"
    TEST = "test"
    PROD = "prod"


class BrokerMode(StringEnum):
    """券商模式枚举。"""

    PAPER = "paper"
    LIVE = "live"


class Market(StringEnum):
    """交易市场枚举。"""

    US = "US"
    CN = "CN"
    HK = "HK"


class AssetType(StringEnum):
    """资产类型枚举。"""

    ETF = "ETF"
    EQUITY = "EQUITY"
    FUTURES = "FUTURES"
    OPTIONS = "OPTIONS"


class DataFrequency(StringEnum):
    """数据频率枚举。"""

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    D1 = "1d"
    W1 = "1w"


class RebalanceFrequency(StringEnum):
    """再平衡频率枚举。"""

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    D1 = "1d"
    W1 = "1w"


class StrategyFamily(StringEnum):
    """策略家族枚举。"""

    TREND_FOLLOWING = "trend_following"
    MOMENTUM_ROTATION = "momentum_rotation"
    MEAN_REVERSION = "mean_reversion"
    CROSS_SECTIONAL_SELECTION = "cross_sectional_selection"
    INTRADAY_BREAKOUT = "intraday_breakout"


class StrategyOutputType(StringEnum):
    """策略输出类型枚举。"""

    TARGET_WEIGHT = "target_weight"
    EXECUTION_INTENT = "execution_intent"


class OrderSemantic(StringEnum):
    """执行型订单语义枚举。"""

    ENTRY = "entry"
    EXIT = "exit"
    REDUCE = "reduce"
    REVERSE = "reverse"

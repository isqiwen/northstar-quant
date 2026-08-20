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

    CN = "CN"


class AssetType(StringEnum):
    """本项目支持的资产类型。"""

    FUTURES = "FUTURES"


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
    MEAN_REVERSION = "mean_reversion"


class StrategyOutputType(StringEnum):
    """策略输出类型枚举。"""

    TARGET_WEIGHT = "target_weight"
    EXECUTION_INTENT = "execution_intent"
    TRADE_PLAN = "trade_plan"


class OrderSemantic(StringEnum):
    """执行型订单语义枚举。"""

    ENTRY = "entry"
    EXIT = "exit"
    REDUCE = "reduce"
    REVERSE = "reverse"


class CtpOffset(StringEnum):
    """CTP 开平仓标志。"""

    OPEN = "open"
    CLOSE = "close"
    CLOSE_TODAY = "close_today"
    CLOSE_YESTERDAY = "close_yesterday"


class ProductSessionRule(StringEnum):
    """品种卡中交易时段的权威解析规则。"""

    EXCHANGE_DAILY_SCHEDULE = "exchange_daily_schedule"


class LastTradeDayRule(StringEnum):
    """品种卡中最后交易日的规则来源。"""

    EXCHANGE_CONTRACT_SPECIFICATION = "exchange_contract_specification"


class IndividualInvestorRule(StringEnum):
    """个人投资者临近交割月的限制规则来源。"""

    BROKER_AND_EXCHANGE_DELIVERY_MONTH_RESTRICTION = (
        "broker_and_exchange_delivery_month_restriction"
    )


class RolloverMethod(StringEnum):
    """具体合约换月的可审计方法。"""

    EXPLICIT_DAILY_CONTRACT_CHAIN = "explicit_daily_contract_chain"


class RolloverReferenceSignal(StringEnum):
    """换月候选合约的参考流动性信号。"""

    OPEN_INTEREST = "open_interest"
    VOLUME = "volume"
    TERM_LIQUIDITY = "term_liquidity"


class DynamicProductSnapshotField(StringEnum):
    """每个交易日必须重新获取的品种交易事实。"""

    TRADING_SESSIONS = "trading_sessions"
    MARGIN_RATE = "margin_rate"
    COMMISSION = "commission"
    PRICE_LIMITS = "price_limits"
    POSITION_LIMITS = "position_limits"
    ACTIVE_CONTRACT = "active_contract"

"""项目常用类型定义。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import polars as pl

from northstar_quant.common.enums import (
    AssetType,
    DataFrequency,
    Market,
    RebalanceFrequency,
    StrategyFamily,
    StrategyOutputType,
)


@dataclass(frozen=True, slots=True)
class TradingDimensions:
    """统一的五维交易类型。"""

    market: Market
    asset_type: AssetType
    data_frequency: DataFrequency
    rebalance_frequency: RebalanceFrequency
    strategy_family: StrategyFamily

    @property
    def key(self) -> str:
        return (
            f"{self.market.value.lower()}::"
            f"{self.asset_type.value.lower()}::"
            f"{self.data_frequency.value.lower()}::"
            f"{self.rebalance_frequency.value.lower()}::"
            f"{self.strategy_family.value.lower()}"
        )

    @property
    def slug(self) -> str:
        return (
            f"{self.market.value.lower()}_"
            f"{self.asset_type.value.lower()}_"
            f"{self.data_frequency.value.lower()}_"
            f"{self.rebalance_frequency.value.lower()}_"
            f"{self.strategy_family.value.lower()}"
        )


@dataclass(slots=True)
class PriceBar:
    """统一的 K 线数据结构。"""

    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(slots=True)
class StrategyOutputBundle:
    """统一的策略输出包装。"""

    strategy_id: str
    output_type: StrategyOutputType
    time_column: str
    frame: pl.DataFrame

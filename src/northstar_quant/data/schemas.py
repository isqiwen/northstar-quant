"""数据层模型。"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class MarketBar:
    """统一行情条结构。"""

    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

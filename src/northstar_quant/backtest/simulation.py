"""仿真回测接口分层。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from northstar_quant.common.enums import DataFrequency
from northstar_quant.config.trading_profile import TradingProfile


def periods_per_year_for_frequency(data_frequency: DataFrequency) -> int:
    """把数据频率换算成年化周期数。"""

    mapping = {
        DataFrequency.M1: 252 * 390,
        DataFrequency.M5: 252 * 78,
        DataFrequency.M15: 252 * 26,
        DataFrequency.H1: round(252 * 6.5),
        DataFrequency.D1: 252,
        DataFrequency.W1: 52,
    }
    return mapping[data_frequency]


class SimulationBacktesterBase(ABC):
    """统一的仿真回测接口。"""

    backtester_id: str = "simulation"
    supported_data_frequencies: tuple[DataFrequency, ...] = ()

    @abstractmethod
    def run(
        self,
        profile: TradingProfile,
        *,
        strategy_name: str,
        symbol: str = "SPY",
    ) -> dict:
        """按画像和策略运行一次仿真回测。"""


class BarSimulationBacktesterBase(SimulationBacktesterBase):
    """日频/周频条形回测接口。"""

    supported_data_frequencies = (DataFrequency.D1, DataFrequency.W1)


class IntradaySimulationBacktesterBase(SimulationBacktesterBase):
    """盘中条形回测接口。"""

    supported_data_frequencies = (
        DataFrequency.M1,
        DataFrequency.M5,
        DataFrequency.M15,
        DataFrequency.H1,
    )


class MinuteSimulationBacktesterBase(IntradaySimulationBacktesterBase):
    """分钟级仿真回测接口。"""

    supported_data_frequencies = (
        DataFrequency.M1,
        DataFrequency.M5,
        DataFrequency.M15,
    )

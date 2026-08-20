"""回测指标口径工具。"""

from northstar_quant.platform.common.enums import DataFrequency


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

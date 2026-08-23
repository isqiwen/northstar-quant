"""AKShare 实际期货合约日线提供器。

交易所日线由 AKShare 获取；保证金、手续费、涨跌停和参考主力来自 AKShare 暴露的
金十数据。后者只有主力合约级参考规则，因此输出只用于离线研究。
"""

from northstar_quant.data.sources.providers.akshare_actual.builder import (
    assemble_actual_daily_dataset as _assemble_actual_daily_dataset,
)
from northstar_quant.data.sources.providers.akshare_actual.normalization import (
    standardize_actual_daily_market as _standardize_actual_daily_market,
    standardize_jin10_rule_snapshot as _standardize_jin10_rule_snapshot,
)
from northstar_quant.data.sources.providers.akshare_actual.provider import (
    download_akshare_actual_daily,
)

__all__ = [
    "_assemble_actual_daily_dataset",
    "_standardize_actual_daily_market",
    "_standardize_jin10_rule_snapshot",
    "download_akshare_actual_daily",
]

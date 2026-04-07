"""基于 vectorbt 的研究扫描示例。"""

from __future__ import annotations

import numpy as np
import vectorbt as vbt

from northstar_quant.common.enums import DataFrequency
from northstar_quant.config.trading_profile import load_trading_profile
from northstar_quant.data.storage import load_profile_signal_data


def _mean_metric(value: object) -> float | None:
    if hasattr(value, "to_numpy"):
        array = np.asarray(value.to_numpy(), dtype=float)
    else:
        array = np.asarray(value, dtype=float)

    valid = array[~np.isnan(array)]
    if valid.size == 0:
        return None
    return float(valid.mean())


def run_momentum_research(profile_id: str | None = None) -> dict:
    """运行一个简化的动量研究扫描。"""

    profile = load_trading_profile(profile_id)
    market_df = load_profile_signal_data(profile)
    wide = market_df.pivot(index="date", on="symbol", values="close").sort("date")
    close = wide.to_pandas().set_index("date")

    ma_fast = vbt.MA.run(close, window=20)
    ma_slow = vbt.MA.run(close, window=100)

    entries = ma_fast.ma_crossed_above(ma_slow)
    exits = ma_fast.ma_crossed_below(ma_slow)

    freq = "1W" if profile.data_frequency == DataFrequency.W1 else "1D"

    pf = vbt.Portfolio.from_signals(close, entries, exits, freq=freq)

    return {
        "profile_id": profile.profile_id,
        "price_field": profile.data.price_field,
        "total_return": _mean_metric(pf.total_return()),
        "max_drawdown": _mean_metric(pf.max_drawdown()),
        "win_rate": _mean_metric(pf.trades.win_rate()),
        "symbols": list(close.columns),
    }

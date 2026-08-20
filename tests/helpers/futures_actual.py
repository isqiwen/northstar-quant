"""实际期货合约测试数据工厂。"""

from datetime import date, datetime, time, timedelta

import polars as pl


def actual_futures_frame(
    *,
    day_count: int = 5,
    roll_offset: int = 3,
    start: date = date(2024, 1, 2),
    old_start: float = 3_000.0,
    new_start: float = 3_200.0,
) -> pl.DataFrame:
    """生成 RB 两个实际合约及一份前序交易日确定的主力日历。"""

    if day_count < 2 or not 1 <= roll_offset < day_count:
        raise ValueError("测试数据需要至少两个交易日，且换月偏移必须位于数据区间内")
    days = [start + timedelta(days=offset) for offset in range(day_count)]
    previous_settlement = {
        "RB2405": old_start - 1,
        "RB2410": new_start - 1,
    }
    rows: list[dict[str, object]] = []
    for offset, current_day in enumerate(days):
        active_contract = "RB2405" if offset < roll_offset else "RB2410"
        selection_date = (
            start - timedelta(days=1)
            if offset < roll_offset
            else days[roll_offset - 1]
        )
        for symbol, base in (("RB2405", old_start), ("RB2410", new_start)):
            close = base + offset * 2.0
            rows.append(
                {
                    "date": current_day,
                    "symbol": symbol,
                    "product": "RB",
                    "exchange": "SHFE",
                    "open": close - 1.0,
                    "high": close + 5.0,
                    "low": close - 5.0,
                    "close": close,
                    "settlement": close,
                    "pre_settlement": previous_settlement[symbol],
                    "volume": 100_000.0,
                    "open_interest": 50_000.0,
                    "upper_limit": close + 300.0,
                    "lower_limit": close - 300.0,
                    "margin_rate": 0.1,
                    "commission_open_per_lot": 1.0,
                    "commission_open_rate": 0.0,
                    "commission_close_per_lot": 1.0,
                    "commission_close_rate": 0.0,
                    "commission_close_today_per_lot": 2.0,
                    "commission_close_today_rate": 0.0,
                    "max_position_lots": 1_000,
                    "active_contract": active_contract,
                    "selection_date": selection_date,
                    "first_session": "night",
                    "session_complete": True,
                }
            )
            previous_settlement[symbol] = close
    return pl.DataFrame(rows)


def actual_futures_intraday_frame(
    *,
    day_count: int = 5,
    roll_offset: int = 3,
    start: date = date(2024, 1, 2),
    old_start: float = 3_000.0,
    new_start: float = 3_200.0,
) -> pl.DataFrame:
    """生成包含夜盘、日盘和买一卖一的 RB 实际合约分钟测试数据。"""

    if day_count < 2 or not 1 <= roll_offset < day_count:
        raise ValueError("测试数据需要至少两个交易日，且换月偏移必须位于数据区间内")
    days = [start + timedelta(days=offset) for offset in range(day_count)]
    previous_settlement = {
        "RB2405": old_start - 1,
        "RB2410": new_start - 1,
    }
    rows: list[dict[str, object]] = []
    for offset, trading_day in enumerate(days):
        active_contract = "RB2405" if offset < roll_offset else "RB2410"
        selection_date = (
            start - timedelta(days=1)
            if offset < roll_offset
            else days[roll_offset - 1]
        )
        for symbol, base in (("RB2405", old_start), ("RB2410", new_start)):
            settlement = base + offset * 2.0 + 2.0
            timestamps = (
                datetime.combine(trading_day - timedelta(days=1), time(21, 0)),
                datetime.combine(trading_day, time(9, 0)),
                datetime.combine(trading_day, time(14, 59)),
            )
            for bar_offset, timestamp in enumerate(timestamps):
                close = base + offset * 2.0 + bar_offset
                rows.append(
                    {
                        "date": trading_day,
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "product": "RB",
                        "exchange": "SHFE",
                        "session": "night" if bar_offset == 0 else "day",
                        "open": close - 0.5,
                        "high": close + 2.0,
                        "low": close - 2.0,
                        "close": close,
                        "volume": 10_000.0,
                        "open_interest": 50_000.0,
                        "bid_price": close - 1.0,
                        "ask_price": close + 1.0,
                        "bid_volume": 1_000.0,
                        "ask_volume": 1_000.0,
                        "settlement": settlement,
                        "pre_settlement": previous_settlement[symbol],
                        "upper_limit": settlement + 300.0,
                        "lower_limit": settlement - 300.0,
                        "margin_rate": 0.1,
                        "commission_open_per_lot": 1.0,
                        "commission_open_rate": 0.0,
                        "commission_close_per_lot": 1.0,
                        "commission_close_rate": 0.0,
                        "commission_close_today_per_lot": 2.0,
                        "commission_close_today_rate": 0.0,
                        "max_position_lots": 1_000,
                        "active_contract": active_contract,
                        "selection_date": selection_date,
                        "is_trading_day_end": bar_offset == len(timestamps) - 1,
                        "session_complete": True,
                    }
                )
            previous_settlement[symbol] = settlement
    return pl.DataFrame(rows).sort(["timestamp", "symbol"])

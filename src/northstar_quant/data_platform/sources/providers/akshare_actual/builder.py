"""实际合约日线的前日规则选择与最小换月窗口构造。"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
import math
from typing import Any

import polars as pl

from northstar_quant.data_platform.contracts.product_cards import ProductCard


def required_rule_dates(
    bars: pl.DataFrame,
    *,
    products: set[str],
    start: date,
    end: date,
) -> list[date]:
    """只请求会被输出交易日实际引用的前序交易日规则。"""

    required: set[date] = set()
    for product in sorted(products):
        product_days = sorted(
            value
            for value in bars.filter(pl.col("product") == product)
            .get_column("date")
            .unique()
            .to_list()
            if isinstance(value, date)
        )
        output_days = [value for value in product_days if start <= value <= end]
        for current_day in output_days:
            previous_days = [value for value in product_days if value < current_day]
            if not previous_days:
                raise ValueError(f"{current_day}/{product} 缺少前序交易日行情")
            required.add(previous_days[-1])
    return sorted(required)


def assemble_actual_daily_dataset(
    bars: pl.DataFrame,
    rules: pl.DataFrame,
    *,
    products: dict[str, str],
    cards: dict[str, ProductCard],
    position_limits: dict[str, int],
    start: date,
    end: date,
) -> pl.DataFrame:
    """将行情与前一交易日参考规则合并为实际合约日线契约。"""

    bars_by_product_day: dict[tuple[str, date], list[dict[str, Any]]] = defaultdict(list)
    for row in bars.to_dicts():
        bars_by_product_day[(str(row["product"]), row["date"])].append(row)
    rules_by_key = {
        (str(row["product"]), row["selection_date"]): row
        for row in rules.to_dicts()
    }

    output: list[dict[str, object]] = []
    for product, exchange in products.items():
        product_days = sorted(
            day for row_product, day in bars_by_product_day if row_product == product
        )
        output_days = [day for day in product_days if start <= day <= end]
        schedule: dict[date, tuple[date, dict[str, Any]]] = {}
        for current_day in output_days:
            previous_days = [day for day in product_days if day < current_day]
            if not previous_days:
                raise ValueError(f"{current_day}/{product} 缺少前序交易日行情")
            selection_date = previous_days[-1]
            rule = rules_by_key.get((product, selection_date))
            if rule is None:
                raise ValueError(f"{current_day}/{product} 缺少 {selection_date} 的参考交易规则")
            schedule[current_day] = (selection_date, rule)

        active_by_day = {
            current_day: str(rule["active_contract"])
            for current_day, (_, rule) in schedule.items()
        }
        for day_index, current_day in enumerate(output_days):
            selection_date, rule = schedule[current_day]
            required_contracts = {active_by_day[current_day]}
            if day_index:
                required_contracts.add(active_by_day[output_days[day_index - 1]])
            if day_index + 1 < len(output_days):
                required_contracts.add(active_by_day[output_days[day_index + 1]])
            current_rows = bars_by_product_day[(product, current_day)]
            previous_symbols = {
                str(row["symbol"]) for row in bars_by_product_day[(product, selection_date)]
            }
            current_symbols = {str(row["symbol"]) for row in current_rows}
            active_contract = str(rule["active_contract"])
            if active_contract not in current_symbols or active_contract not in previous_symbols:
                raise ValueError(
                    f"{current_day}/{product} 的前日参考主力 {active_contract} "
                    "未同时出现在前日和当日实际合约链"
                )

            card = cards[product]
            for row in current_rows:
                if str(row["symbol"]) not in required_contracts:
                    continue
                upper_limit = _round_down_to_tick(
                    float(row["pre_settlement"]) * (1 + float(rule["upper_limit_rate"])),
                    card.tick_size,
                )
                lower_limit = _round_up_to_tick(
                    float(row["pre_settlement"]) * (1 - float(rule["lower_limit_rate"])),
                    card.tick_size,
                )
                if (
                    upper_limit <= lower_limit
                    or float(row["high"]) > upper_limit
                    or float(row["low"]) < lower_limit
                ):
                    raise ValueError(
                        f"{current_day}/{row['symbol']} 的行情超出参考规则推导的涨跌停；"
                        "公开规则粒度不足，已拒绝生成回测数据"
                    )
                output.append(
                    {
                        **row,
                        "exchange": exchange,
                        "upper_limit": upper_limit,
                        "lower_limit": lower_limit,
                        "margin_rate": float(rule["margin_rate"]),
                        "commission_open_per_lot": float(rule["commission_open_per_lot"]),
                        "commission_open_rate": float(rule["commission_open_rate"]),
                        "commission_close_per_lot": float(rule["commission_close_per_lot"]),
                        "commission_close_rate": float(rule["commission_close_rate"]),
                        "commission_close_today_per_lot": float(
                            rule["commission_close_today_per_lot"]
                        ),
                        "commission_close_today_rate": float(
                            rule["commission_close_today_rate"]
                        ),
                        "max_position_lots": position_limits[product],
                        "active_contract": active_contract,
                        "selection_date": selection_date,
                        "first_session": "night" if card.has_night_session else "day",
                        "session_complete": True,
                        "market_data_source": "akshare_exchange_daily",
                        "trading_rule_source": "akshare_jin10_main_contract_reference",
                        "position_limit_source": "profile_research_cap",
                    }
                )
    if not output:
        raise ValueError("指定日期范围内没有可发布的实际合约日线")
    return pl.DataFrame(output).sort(["date", "product", "symbol"])


def _round_down_to_tick(value: float, tick_size: float) -> float:
    return math.floor((value + 1e-12) / tick_size) * tick_size


def _round_up_to_tick(value: float, tick_size: float) -> float:
    return math.ceil((value - 1e-12) / tick_size) * tick_size

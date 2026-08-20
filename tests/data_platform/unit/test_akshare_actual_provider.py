"""AKShare 实际合约日线提供器测试。"""

from datetime import date

import pandas as pd
import polars as pl
import pytest

from northstar_quant.data_platform.contracts.product_cards import load_product_cards
from northstar_quant.data_platform.sources.providers.akshare_actual import (
    _assemble_actual_daily_dataset,
    _standardize_actual_daily_market,
    _standardize_jin10_rule_snapshot,
)


DAY_1 = date(2026, 1, 5)
DAY_2 = date(2026, 1, 6)
DAY_3 = date(2026, 1, 7)


def test_standardize_actual_daily_market_keeps_only_traded_actual_contracts():
    raw = pd.DataFrame(
        [
            _market_row(DAY_1, "RB2605", 100.0),
            _market_row(DAY_1, "RB2610", 200.0),
            _market_row(DAY_1, "RB小计", 0.0),
            {**_market_row(DAY_1, "CU2602", 500.0), "variety": "CU"},
        ]
    )

    result = _standardize_actual_daily_market(
        raw,
        exchange="SHFE",
        products={"RB"},
    )

    assert result.get_column("symbol").to_list() == ["RB2605", "RB2610"]
    assert result.get_column("product").unique().to_list() == ["RB"]
    assert result.get_column("exchange").unique().to_list() == ["SHFE"]


def test_jin10_rules_parse_per_lot_and_notional_commissions():
    raw = pd.DataFrame(
        [
            {
                "日期": DAY_1,
                "合约代码": "rb2605",
                "现价": 100.0,
                "涨停板": 110.0,
                "跌停板": 90.0,
                "保证金/买开": "8%",
                "保证金/卖开": "10%",
                "开仓": "1/万分之(3.1元)",
                "平今": "3元",
                "平昨": "1.5元",
                "手续费公布时间": "2026-01-05 21:30:00",
                "价格公布时间": "2026-01-05 23:00:00",
            }
        ]
    )

    result = _standardize_jin10_rule_snapshot(
        raw,
        selection_date=DAY_1,
        products={"RB"},
    ).row(0, named=True)

    assert result["active_contract"] == "RB2605"
    assert result["margin_rate"] == pytest.approx(0.1)
    assert result["commission_open_rate"] == pytest.approx(0.0001)
    assert result["commission_open_per_lot"] == 0
    assert result["commission_close_today_per_lot"] == 3
    assert result["commission_close_per_lot"] == 1.5


def test_jin10_rules_choose_the_latest_published_contract_during_roll():
    older = {
        **_rule_row(DAY_1, "RB2605"),
        "手续费公布时间": "2026-01-05 15:00:00",
        "价格公布时间": "2026-01-05 15:00:00",
    }
    newer = _rule_row(DAY_1, "RB2610")

    result = _standardize_jin10_rule_snapshot(
        pd.DataFrame([older, newer]),
        selection_date=DAY_1,
        products={"RB"},
    )

    assert result.get_column("active_contract").to_list() == ["RB2610"]


def test_actual_daily_dataset_uses_previous_day_rule_and_records_provenance():
    bars = pl.concat(
        [
            _standardize_actual_daily_market(
                pd.DataFrame(
                    [
                        _market_row(day, symbol, price)
                        for day, prices in (
                            (DAY_1, {"RB2605": 100.0, "RB2610": 200.0}),
                            (DAY_2, {"RB2605": 101.0, "RB2610": 202.0}),
                            (DAY_3, {"RB2605": 102.0, "RB2610": 204.0}),
                        )
                        for symbol, price in prices.items()
                    ]
                ),
                exchange="SHFE",
                products={"RB"},
            )
        ]
    )
    rules = pl.concat(
        [
            _standardize_jin10_rule_snapshot(
                pd.DataFrame([_rule_row(DAY_1, "RB2605")]),
                selection_date=DAY_1,
                products={"RB"},
            ),
            _standardize_jin10_rule_snapshot(
                pd.DataFrame([_rule_row(DAY_2, "RB2610")]),
                selection_date=DAY_2,
                products={"RB"},
            ),
        ],
        how="vertical",
    )
    cards = {card.product: card for card in load_product_cards()}

    result = _assemble_actual_daily_dataset(
        bars,
        rules,
        products={"RB": "SHFE"},
        cards=cards,
        position_limits={"RB": 100},
        start=DAY_2,
        end=DAY_3,
    )

    schedule = (
        result.select("date", "active_contract", "selection_date")
        .unique()
        .sort("date")
        .to_dicts()
    )
    assert schedule == [
        {
            "date": DAY_2,
            "active_contract": "RB2605",
            "selection_date": DAY_1,
        },
        {
            "date": DAY_3,
            "active_contract": "RB2610",
            "selection_date": DAY_2,
        },
    ]
    assert result.get_column("market_data_source").unique().to_list() == [
        "akshare_exchange_daily"
    ]
    assert result.get_column("position_limit_source").unique().to_list() == [
        "profile_research_cap"
    ]


def test_jin10_rule_snapshot_rejects_mismatched_source_date():
    raw = pd.DataFrame([_rule_row(DAY_2, "RB2605")])

    with pytest.raises(ValueError, match="实际日期"):
        _standardize_jin10_rule_snapshot(
            raw,
            selection_date=DAY_1,
            products={"RB"},
        )


def _market_row(day: date, symbol: str, close: float) -> dict[str, object]:
    return {
        "symbol": symbol,
        "date": day.strftime("%Y%m%d"),
        "open": close,
        "high": close + 1,
        "low": max(close - 1, 0),
        "close": close,
        "volume": 10_000,
        "open_interest": 20_000,
        "turnover": 1_000_000,
        "settle": close,
        "pre_settle": close,
        "variety": "RB",
    }


def _rule_row(day: date, symbol: str) -> dict[str, object]:
    return {
        "日期": day,
        "合约代码": symbol,
        "现价": 100.0,
        "涨停板": 120.0,
        "跌停板": 80.0,
        "保证金/买开": "10%",
        "保证金/卖开": "10%",
        "开仓": "1元",
        "平今": "2元",
        "平昨": "1元",
        "手续费公布时间": f"{day.isoformat()} 21:30:00",
        "价格公布时间": f"{day.isoformat()} 23:00:00",
    }

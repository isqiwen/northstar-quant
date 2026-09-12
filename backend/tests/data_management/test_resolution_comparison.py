"""Incomplete labels or different OHLCV must not become a matching research input."""

import json
from datetime import datetime, timedelta
from decimal import localcontext

import pytest

from northstar_quant.data_management.tushare.quality import InvalidResponse
from northstar_quant.data_management.tushare.resolutions import compare_resolutions


def encoded(rows):
    return json.dumps(
        {
            "code": 0,
            "data": {
                "fields": list(rows[0]),
                "items": [list(row.values()) for row in rows],
            },
        }
    ).encode()


def sample():
    rows = [
        {
            "ts_code": "RB2610.SHF",
            "trade_time": (datetime(2026, 9, 8, 9, 1) + timedelta(minutes=i)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "open": str(100 + i),
            "high": str(101 + i),
            "low": str(99 + i),
            "close": str(100 + i),
            "vol": "10000000000000000000000.000000000001",
        }
        for i in range(15)
    ]
    coarse = [
        {**rows[-1], "open": "100", "low": "99", "vol": "150000000000000000000000.000000000015"}
    ]
    return rows, coarse


def compare(fine, coarse):
    return compare_resolutions(
        encoded(fine),
        encoded(coarse),
        contract="RB2610.SHF",
        minutes=15,
        start="2026-09-08",
        end="2026-09-08",
    )


def test_exact_agreement_reports_both_hypotheses_without_admitting_data():
    fine, coarse = sample()
    before = encoded(fine), encoded(coarse)
    with localcontext() as context:
        context.prec = 2
        result = compare(list(reversed(fine)), coarse)
    assert result["candidates"]["BAR_END"]["counts"] == {
        "MATCHED": 1,
        "MISMATCH": 0,
        "INCOMPLETE_MINUTES": 0,
    }
    assert result["candidates"]["BAR_START"]["counts"]["INCOMPLETE_MINUTES"] == 1
    assert result["admitted"] is False
    assert (encoded(fine), encoded(coarse)) == before


def test_missing_minute_cannot_match_even_when_observed_prices_and_total_volume_agree():
    fine, coarse = sample()
    fine.pop(7)
    fine[0]["vol"] = "20000000000000000000000.000000000002"
    result = compare(fine, coarse)
    observation = result["candidates"]["BAR_END"]["observations"][0]
    assert observation["status"] == "INCOMPLETE_MINUTES"
    assert observation["observed_minutes"] == 14
    coarse[0]["trade_time"] = "2026-09-08 21:00:00"
    assert compare(fine, coarse)["candidates"]["BAR_END"]["counts"]["MATCHED"] == 0


def test_conflicts_wrong_contract_and_value_mismatch_are_not_silently_accepted():
    fine, coarse = sample()
    coarse[0]["vol"] = "1"
    observation = compare(fine, coarse)["candidates"]["BAR_END"]["observations"][0]
    assert observation["status"] == "MISMATCH" and observation["different_fields"] == ["vol"]
    with pytest.raises(InvalidResponse):
        compare([*fine, {**fine[0], "vol": "1"}], coarse)
    with pytest.raises(InvalidResponse):
        compare(fine, [{**coarse[0], "ts_code": "OTHER.SHF"}])

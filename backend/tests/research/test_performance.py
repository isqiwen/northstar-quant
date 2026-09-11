"""Account returns retain explicit trading days, capital loss and unfinished drawdowns."""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, localcontext

import pytest

from northstar_quant.research.backtesting.performance import performance


def points(values, days):
    start = datetime(2026, 1, 30, 13, tzinfo=UTC)
    return [
        dict(
            event_time=(start + timedelta(days=i)).isoformat(),
            at=(start + timedelta(days=i, minutes=1)).isoformat(),
            trading_day=day,
            equity=str(value),
        )
        for i, (value, day) in enumerate(zip(values, days))
    ]


def test_monthly_returns_use_trading_day_and_preserve_unrecovered_drawdown():
    rows = points([100, 90, 110, 80], ["2026-02-02", "2026-02-02", "2026-02-03", "2026-03-02"])
    result = performance(Decimal(100), rows)
    assert [r["period"] for r in result["monthly"]] == ["2026-02", "2026-03"]
    assert result["monthly"][0]["return_fraction"] == "0.1"
    assert [r["pnl"] for r in result["daily"]] == ["-10", "20", "-30"]
    assert sum(Decimal(r["pnl"]) for r in result["monthly"]) == Decimal(-20)
    first, last = result["drawdowns"]
    assert first["recovered"] is True and first["drawdown"] == "10"
    assert last["recovered"] is False and last["drawdown"] == "30"
    assert first["end_at"] == rows[2]["at"]
    assert last["end_at"] == rows[-1]["at"]
    assert last["elapsed_seconds"] == "86400"
    assert result["rolling"] == []
    with localcontext() as context:
        context.prec, context.rounding = 6, ROUND_DOWN
        assert performance(Decimal(100), rows) == result


def test_capital_exhaustion_does_not_invent_a_return_or_hide_drawdown():
    result = performance(
        Decimal(100), points([0, -10, 20], ["2026-02-02", "2026-02-03", "2026-02-04"])
    )
    assert [r["return_fraction"] for r in result["daily"]] == ["-1", None, None]
    assert result["drawdowns"][0]["drawdown_fraction"] == "1.1"
    assert result["drawdowns"][0]["recovered"] is False


def test_rolling_observed_days_do_not_fill_missing_calendar_days():
    days = [(datetime(2026, 2, 1) + timedelta(days=2 * i)).date().isoformat() for i in range(21)]
    result = performance(Decimal(100), points(list(range(101, 122)), days))
    assert len(result["daily"]) == 21 and len(result["rolling"]) == 2
    assert result["rolling"][0] == dict(
        start_day=days[0], end_day=days[19], observed_days=20, return_fraction="0.2"
    )
    assert result["rolling"][1]["start_day"] == days[1]
    assert result["drawdowns"] == []


def test_regressing_ledger_clock_and_trading_day_are_rejected():
    rows = points([100, 101], ["2026-02-02", "2026-02-01"])
    with pytest.raises(ValueError, match="order"):
        performance(Decimal(100), rows)
    rows[1]["trading_day"] = "2026-02-03"
    rows[1]["at"] = "2025-01-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="order"):
        performance(Decimal(100), rows)

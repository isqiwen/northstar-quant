"""Observed-period returns and drawdown spells from audited account valuations.

No exchange calendar, settlement close, annualization or missing return is inferred.
The owning report verifies the ledger first; these are derived descriptive values.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any

from northstar_quant.accounting.amounts import decimal_text


def performance(initial_cash: Decimal, points: Sequence[dict[str, object]]) -> dict[str, Any]:
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        return _calculate(initial_cash, points)


def _calculate(initial_cash: Decimal, points: Sequence[dict[str, object]]) -> dict[str, Any]:
    if initial_cash <= 0 or not points:
        raise ValueError("performance requires initial cash and audited observations")
    daily: dict[str, dict[str, Any]] = {}
    monthly: dict[str, dict[str, Any]] = {}
    spells: list[dict[str, Any]] = []
    previous = initial_cash
    previous_day = ""
    previous_at: datetime | None = None
    peak, peak_at = initial_cash, str(points[0]["event_time"])
    active: dict[str, Any] | None = None
    for point in points:
        day, at = str(point["trading_day"]), str(point["at"])
        moment = datetime.fromisoformat(at)
        if (
            moment.utcoffset() != timedelta(0)
            or (previous_at is not None and moment < previous_at)
            or day < previous_day
        ):
            raise ValueError("performance observations must retain trading-day and UTC order")
        previous_at, previous_day = moment, day
        equity = Decimal(str(point["equity"]))
        for groups, period in ((daily, day), (monthly, day[:7])):
            if period not in groups:
                groups[period] = dict(
                    period=period,
                    start_at=at,
                    end_at=at,
                    opening_equity=decimal_text(previous),
                    closing_equity=decimal_text(equity),
                    observations=0,
                )
            row = groups[period]
            row.update(end_at=at, closing_equity=decimal_text(equity))
            row["observations"] += 1
        if equity >= peak:
            if active is not None:
                active.update(end_at=at, recovered=True)
                spells.append(active)
                active = None
            peak, peak_at = equity, at
        else:
            amount = peak - equity
            if active is None:
                active = dict(
                    peak_at=peak_at,
                    start_at=at,
                    trough_at=at,
                    end_at=at,
                    peak_equity=decimal_text(peak),
                    trough_equity=decimal_text(equity),
                    drawdown=decimal_text(amount),
                    drawdown_fraction=decimal_text(amount / peak),
                    recovered=False,
                )
            if equity < Decimal(active["trough_equity"]):
                active.update(
                    trough_at=at,
                    trough_equity=decimal_text(equity),
                    drawdown=decimal_text(amount),
                    drawdown_fraction=decimal_text(amount / peak),
                )
            active["end_at"] = at
        previous = equity
    if active is not None:
        spells.append(active)
    for groups in (daily, monthly):
        for row in groups.values():
            opening, closing = Decimal(row["opening_equity"]), Decimal(row["closing_equity"])
            row["pnl"] = decimal_text(closing - opening)
            row["return_fraction"] = decimal_text(closing / opening - 1) if opening > 0 else None
    for spell in spells:
        elapsed = datetime.fromisoformat(spell["end_at"]) - datetime.fromisoformat(spell["peak_at"])
        spell["elapsed_seconds"] = decimal_text(
            Decimal(elapsed.days * 86400 + elapsed.seconds)
            + Decimal(elapsed.microseconds) / 1000000
        )
    days = list(daily.values())
    rolling = []
    for index in range(19, len(days)):
        first, last = days[index - 19], days[index]
        opening, closing = Decimal(first["opening_equity"]), Decimal(last["closing_equity"])
        rolling.append(
            dict(
                start_day=first["period"],
                end_day=last["period"],
                observed_days=20,
                return_fraction=decimal_text(closing / opening - 1) if opening > 0 else None,
            )
        )
    return dict(
        daily=days,
        monthly=list(monthly.values()),
        drawdowns=spells,
        rolling=rolling,
        basis="OBSERVED_TRADING_DAY_MARKS_NO_CASH_FLOWS",
    )

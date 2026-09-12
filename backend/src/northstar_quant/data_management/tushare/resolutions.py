"""Compare retained minute resolutions without declaring a supplier time convention.

Exact cross-resolution agreement is supporting evidence, not a calendar, a
source availability guarantee or admission of a row into a trading dataset.
"""

import hashlib
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from typing import Any

from .quality import normalize


def compare_resolutions(
    fine: bytes, coarse: bytes, *, contract: str, minutes: int, start: str, end: str
) -> dict[str, Any]:
    if type(minutes) is not int or minutes not in {5, 15, 30, 60}:
        raise ValueError("分钟核对仅支持 1 分钟与 5/15/30/60 分钟")
    if not isinstance(contract, str) or not 1 <= len(contract) <= 64:
        raise ValueError("必须固定一个合约")
    if date.fromisoformat(start) > date.fromisoformat(end):
        raise ValueError("核对日期范围倒置")
    job = {"parameters": {"ts_code": contract}, "start_at": start, "end_at": end}
    small, fine_quality = normalize(fine, {**job, "dataset": "1min"})
    large, coarse_quality = normalize(coarse, {**job, "dataset": f"{minutes}min"})
    if len(large) > 512:
        raise ValueError("单次核对最多 512 条大周期记录；请缩小已保存样本区间")
    by_time = {datetime.fromisoformat(row["trade_time"]): row for row in small}
    candidates = {}
    for basis in ("BAR_END", "BAR_START"):
        observations = []
        coverage: dict[datetime, list[dict[str, str]]] = {at: [] for at in by_time}
        for row in large:
            label = datetime.fromisoformat(row["trade_time"])
            first = label - timedelta(minutes=minutes - 1) if basis == "BAR_END" else label
            labels = [first + timedelta(minutes=i) for i in range(minutes)]
            observed = [by_time[at] for at in labels if at in by_time]
            differences = []
            values: dict[str, Decimal] = {}
            if len(observed) == minutes:
                with localcontext() as context:
                    context.prec = 96
                    values = {
                        "open": Decimal(observed[0]["open"]),
                        "close": Decimal(observed[-1]["close"]),
                        "high": max(Decimal(item["high"]) for item in observed),
                        "low": min(Decimal(item["low"]) for item in observed),
                        "vol": sum((Decimal(item["vol"]) for item in observed), Decimal(0)),
                    }
                differences = [
                    name for name, value in values.items() if value != Decimal(row[name])
                ]
            status = (
                "INCOMPLETE_MINUTES"
                if len(observed) != minutes
                else "MISMATCH"
                if differences
                else "MATCHED"
            )
            for at in labels:
                if at in coverage:
                    coverage[at].append({"label": row["trade_time"], "status": status})
            observations.append(
                {
                    "label": row["trade_time"],
                    "status": status,
                    "observed_minutes": len(observed),
                    "different_fields": differences,
                    "missing_labels": [at.isoformat(sep=" ") for at in labels if at not in by_time],
                    "field_differences": {
                        name: {"aggregated": str(values[name]), "reported": row[name]}
                        for name in differences
                    },
                }
            )
        unresolved = [
            {"label": by_time[at]["trade_time"], "windows": windows}
            for at, windows in sorted(coverage.items())
            if len(windows) != 1 or windows[0]["status"] != "MATCHED"
        ]
        candidates[basis] = {
            "fine_coverage": {
                "total": len(small),
                "matched_once": len(small) - len(unresolved),
                "unreferenced": sum(not windows for windows in coverage.values()),
                "multiple_windows": sum(len(windows) > 1 for windows in coverage.values()),
                "unresolved": unresolved,
            },
            "counts": {
                state: sum(item["status"] == state for item in observations)
                for state in ("MATCHED", "MISMATCH", "INCOMPLETE_MINUTES")
            },
            "observations": observations,
        }
    return {
        "rule": "tushare-resolution-comparison/2",
        "contract": contract,
        "minutes": minutes,
        "start": start,
        "end": end,
        "fine": {"sha256": hashlib.sha256(fine).hexdigest(), "quality": fine_quality},
        "coarse": {"sha256": hashlib.sha256(coarse).hexdigest(), "quality": coarse_quality},
        "candidates": candidates,
        "admitted": False,
        "limitations": [
            "CROSS_RESOLUTION_AGREEMENT_DOES_NOT_CONFIRM_SOURCE_TIME_CONVENTION",
            "NO_CALENDAR_TRADING_DAY_OR_AVAILABILITY_INFERENCE",
            "INCOMPLETE_INTERVALS_ARE_NOT_FILLED_OR_DROPPED",
        ],
    }

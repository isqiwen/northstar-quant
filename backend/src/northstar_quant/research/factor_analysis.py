"""Bounded time-series factor diagnostics; labels never enter factor decisions."""

from __future__ import annotations

import math
from collections import Counter
from decimal import Decimal
from typing import Any

from northstar_quant.data_management.research import ResearchDataset


def analyze(dataset: ResearchDataset, values: list[dict[str, Any]]) -> dict[str, Any]:
    import duckdb
    import pyarrow as pa  # type: ignore[import-untyped]

    bars = dataset.bars
    if not 1 <= len(bars) <= 10000 or len(values) != len(bars):
        raise ValueError("factor analysis requires complete bounded observations")
    indexed = {str(row["observation_id"]): row for row in values}
    if len(indexed) != len(bars):
        raise ValueError("factor analysis repeats observation identities")
    for index, bar in enumerate(bars):
        bar.validate(interval_seconds=dataset.market.interval_seconds)
        if index and bar.event_time < bars[index - 1].completed_at:
            raise ValueError("factor labels require ordered nonoverlapping bars")
        row = indexed.get(str(bar.observation_id))
        if row is None or row["at"] != bar.available_at.isoformat():
            raise ValueError("factor result does not match its fixed market observation")
    horizons = []
    with duckdb.connect() as connection:
        for horizon in (1, 5, 15):
            records = []
            excluded: Counter[str] = Counter()
            for index, bar in enumerate(bars):
                row = indexed[str(bar.observation_id)]
                if row["status"] != "READY" or row["value"] is None:
                    excluded["FACTOR_UNAVAILABLE"] += 1
                    continue
                end = index + horizon
                if end >= len(bars):
                    excluded["LABEL_OUTSIDE_FIXED_WINDOW"] += 1
                    continue
                if bar.available_at > bars[index + 1].event_time:
                    excluded["FACTOR_NOT_AVAILABLE_BEFORE_LABEL_INTERVAL"] += 1
                    continue
                segment = bars[index : end + 1]
                if any(
                    after.event_time != before.completed_at or after.trading_day != bar.trading_day
                    for before, after in zip(segment, segment[1:])
                ):
                    excluded["LABEL_CROSSES_SESSION_GAP_OR_TRADING_DAY"] += 1
                    continue
                factor = float(Decimal(str(row["value"])))
                label = float(bars[end].close) / float(bar.close) - 1
                if not math.isfinite(factor) or not math.isfinite(label):
                    raise ValueError("factor analysis requires finite dimensionless values")
                records.append((index, bar.trading_day.isoformat(), factor, label))
            columns = list(zip(*records)) if records else ((), (), (), ())
            table = pa.table(
                {
                    "n": pa.array(columns[0], type=pa.int64()),
                    "day": pa.array(columns[1], type=pa.string()),
                    "factor": pa.array(columns[2], type=pa.float64()),
                    "label": pa.array(columns[3], type=pa.float64()),
                }
            )
            connection.register("paired", table)
            # Average ranks preserve ties. Quantile membership uses factor ranks
            # only; neither labels nor arbitrary row order break a factor tie.
            connection.execute("""CREATE OR REPLACE TEMP VIEW ranked AS
                SELECT *, rank() OVER (ORDER BY factor)
                    + (count(*) OVER (PARTITION BY factor)-1)/2.0 AS factor_rank,
                    rank() OVER (ORDER BY label)
                    + (count(*) OVER (PARTITION BY label)-1)/2.0 AS label_rank,
                    rank() OVER (PARTITION BY day ORDER BY factor)
                    + (count(*) OVER (PARTITION BY day,factor)-1)/2.0 AS day_factor_rank,
                    rank() OVER (PARTITION BY day ORDER BY label)
                    + (count(*) OVER (PARTITION BY day,label)-1)/2.0 AS day_label_rank
                FROM paired""")
            connection.execute("""CREATE OR REPLACE TEMP VIEW grouped AS
                SELECT *, least(5, 1+floor(5*(factor_rank-1)
                    /greatest(1,count(*) OVER ()-1)))::INTEGER AS quantile FROM ranked""")

            def finite(value: Any) -> float | None:
                return (
                    round(float(value), 12) if value is not None and math.isfinite(value) else None
                )

            summary = connection.execute(
                "SELECT corr(factor,label ORDER BY n),"
                "corr(factor_rank,label_rank ORDER BY n) FROM ranked"
            ).fetchone()
            assert summary is not None
            pearson, spearman = summary
            groups = connection.execute("""SELECT quantile,count(*),avg(label ORDER BY n)
                FROM grouped GROUP BY quantile ORDER BY quantile""").fetchall()
            days = connection.execute("""SELECT day,count(*),
                corr(day_factor_rank,day_label_rank ORDER BY n),
                avg(label ORDER BY n) FROM ranked GROUP BY day ORDER BY day""").fetchall()
            transitions = connection.execute("""WITH steps AS (
                SELECT n,quantile,lag(n) OVER (ORDER BY n) AS previous_n,
                    lag(quantile) OVER (ORDER BY n) AS previous_group FROM grouped)
                SELECT avg(CASE WHEN quantile != previous_group THEN 1.0 ELSE 0.0 END ORDER BY n)
                FROM steps WHERE n=previous_n+1""").fetchone()
            assert transitions is not None
            changes = transitions[0]
            pearson = finite(pearson) if len(records) >= 3 else None
            spearman = finite(spearman) if len(records) >= 3 else None
            horizons.append(
                {
                    "bars": horizon,
                    "samples": len(records),
                    "excluded": dict(sorted(excluded.items())),
                    "status": "INSUFFICIENT_SAMPLE"
                    if len(records) < 3
                    else "INSUFFICIENT_VARIATION"
                    if spearman is None
                    else "DESCRIPTIVE_ONLY",
                    "pearson": pearson,
                    "spearman": spearman,
                    "group_change_fraction": finite(changes),
                    "groups": [
                        {"group": group, "samples": count, "mean_forward_return": finite(mean)}
                        for group, count, mean in groups
                    ],
                    "days": [
                        {
                            "trading_day": day,
                            "samples": count,
                            "spearman": finite(ic) if count >= 3 else None,
                            "mean_forward_return": finite(mean),
                        }
                        for day, count, ic, mean in days
                    ],
                }
            )
    return {
        "plan": "SINGLE_CONTRACT_TIME_SERIES_FORWARD_RETURNS_V1",
        "numeric": "FLOAT64_ORDERED_REDUCTIONS_12_DECIMAL_REPORT",
        "horizons": horizons,
        "limitations": [
            "单合约时间序列相关，不是横截面 IC；样本自相关，未给独立样本显著性结论。",
            "标签只取固定快照内连续同交易日的后续收盘价；未计手续费、滑点、持仓与结算。",
            "分组使用全窗口因子平均秩，仅供事后描述；不能将分组边界用于本窗口历史交易。",
            "这是粗筛诊断，不能替代训练/验证隔离及完整事件回测。",
        ],
    }

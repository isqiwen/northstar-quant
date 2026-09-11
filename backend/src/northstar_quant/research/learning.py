"""Small train-only ridge fit; vectorized features never replace execution/accounting."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any

from northstar_quant.data_management.research import ResearchDataset
from northstar_quant.factors.definition import content_id
from northstar_quant.factors.evaluation import Binding
from northstar_quant.strategies.configuration import StrategyConfig

from .configuration import ResearchConfig


@dataclass(frozen=True)
class LearningRecipe:
    fast_bars: int = 1
    slow_bars: int = 15
    horizon_bars: int = 1
    penalties: tuple[str, ...] = ("0.001", "0.01", "0.1")
    threshold: str = "0.001"
    target_fraction: str = "0.5"

    def __post_init__(self) -> None:
        if any(type(v) is not int for v in (self.fast_bars, self.slow_bars, self.horizon_bars)):
            raise ValueError("learning windows must be integers")
        if not 1 <= self.fast_bars < self.slow_bars <= 1000 or not 1 <= self.horizon_bars <= 100:
            raise ValueError("learning windows exceed the bounded recipe")
        if not 2 <= len(self.penalties) <= 16 or len(set(self.penalties)) != len(self.penalties):
            raise ValueError("choose 2 to 16 distinct fixed ridge penalties")
        if len(
            set(
                Decimal(v)
                for v in self.penalties
                if isinstance(v, str)
                and re.fullmatch(r"(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,12})?", v)
            )
        ) != len(self.penalties):
            raise ValueError("ridge penalties must be distinct plain positive decimals")
        for value in self.penalties:
            if (
                not isinstance(value, str)
                or not Decimal(value).is_finite()
                or not Decimal("0.00000001") <= Decimal(value) <= 1000000
            ):
                raise ValueError("ridge penalty must be an exact positive decimal")
        # Use the actual runtime's parameter boundary before admitting a recipe.
        StrategyConfig.create(
            "learned.linear_return",
            {"threshold": self.threshold, "target_fraction": self.target_fraction},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fast_bars": self.fast_bars,
            "slow_bars": self.slow_bars,
            "horizon_bars": self.horizon_bars,
            "penalties": list(self.penalties),
            "threshold": self.threshold,
            "target_fraction": self.target_fraction,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> LearningRecipe:
        if set(value) != {
            "fast_bars",
            "slow_bars",
            "horizon_bars",
            "penalties",
            "threshold",
            "target_fraction",
        }:
            raise ValueError("unknown learning recipe fields")
        return cls(**{**value, "penalties": tuple(value["penalties"])})


def training_rows(
    dataset: ResearchDataset, recipe: LearningRecipe
) -> list[tuple[float, float, float, int, int]]:
    """Past-only factors and future labels remain separate, with explicit label indexes."""
    import duckdb
    import pyarrow as pa  # type: ignore[import-untyped]

    if not recipe.slow_bars + recipe.horizon_bars + 10 <= len(dataset.bars) <= 10000:
        raise ValueError(
            "training requires 10 labelled observations after warmup, at most 10000 bars"
        )
    if dataset.market.interval_seconds != 60:
        raise ValueError("current learned factors require one-minute fixed inputs")
    for index, bar in enumerate(dataset.bars):
        bar.validate(
            interval_seconds=dataset.market.interval_seconds, price_tick=dataset.market.price_tick
        )
        if index and (
            bar.event_time < dataset.bars[index - 1].completed_at
            or bar.available_at < dataset.bars[index - 1].available_at
        ):
            raise ValueError("training input must have causal ordered bars")
    table = pa.table(
        {
            "n": list(range(len(dataset.bars))),
            "price": pa.array([bar.close for bar in dataset.bars], type=pa.decimal128(38, 12)),
        }
    )
    with duckdb.connect() as connection:
        connection.register("fixed_training", table)
        # Parameters are bounded integers. DuckDB computes dimensionless floating-point
        # research features; financial fills and the live strategy remain Decimal.
        result = connection.execute(
            """WITH features AS (
            SELECT n, price/lag(price, ?) OVER (ORDER BY n)-1 AS fast,
                price/lag(price, ?) OVER (ORDER BY n)-1 AS slow,
                lead(price, ?) OVER (ORDER BY n)/price-1 AS label
            FROM fixed_training)
            SELECT fast,slow,label,n,n+? FROM features
            WHERE fast IS NOT NULL AND slow IS NOT NULL AND label IS NOT NULL ORDER BY n""",
            [recipe.fast_bars, recipe.slow_bars, recipe.horizon_bars, recipe.horizon_bars],
        ).fetchall()
    if any(not all(math.isfinite(v) for v in row[:3]) for row in result):
        raise ValueError("nonfinite training features")
    return result


def fit(dataset: ResearchDataset, recipe: LearningRecipe, base: ResearchConfig) -> dict[str, Any]:
    rows = training_rows(dataset, recipe)
    x0, x1, y = ([row[i] for row in rows] for i in range(3))
    n = len(rows)
    means = [math.fsum(values) / n for values in (x0, x1)]
    scales = [
        math.sqrt(math.fsum((v - m) ** 2 for v in values) / n) for values, m in zip((x0, x1), means)
    ]
    if any(scale <= 1e-14 for scale in scales):
        raise ValueError("training features have no measurable variation")
    a, b = ([(v - m) / s for v in values] for values, m, s in zip((x0, x1), means, scales))
    target = math.fsum(y) / n
    aa, bb, ab = (
        math.fsum(v * v for v in a) / n,
        math.fsum(v * v for v in b) / n,
        math.fsum(v * w for v, w in zip(a, b)) / n,
    )
    ay = math.fsum(v * (t - target) for v, t in zip(a, y)) / n
    by = math.fsum(v * (t - target) for v, t in zip(b, y)) / n
    candidates: dict[str, Any] = {}
    fitted = []
    for penalty in recipe.penalties:
        ridge = float(penalty)
        determinant = (aa + ridge) * (bb + ridge) - ab * ab
        if determinant <= 0 or not math.isfinite(determinant):
            raise ValueError("ridge fit is numerically singular")
        w0 = ((bb + ridge) * ay - ab * by) / determinant / scales[0]
        w1 = ((aa + ridge) * by - ab * ay) / determinant / scales[1]
        intercept = target - w0 * means[0] - w1 * means[1]

        def exact(value: float) -> str:
            if not math.isfinite(value) or abs(value) > 1000000:
                raise ValueError("fitted coefficient exceeds runtime bounds")
            with localcontext() as c:
                c.prec = 40
                return format(
                    Decimal(str(value)).quantize(
                        Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN
                    ),
                    "f",
                )

        frozen_intercept, frozen_w0, frozen_w1 = map(exact, (intercept, w0, w1))
        config = replace(
            base,
            strategy=StrategyConfig.create(
                "learned.linear_return",
                {
                    "intercept": frozen_intercept,
                    "fast_weight": frozen_w0,
                    "slow_weight": frozen_w1,
                    "threshold": recipe.threshold,
                    "target_fraction": recipe.target_fraction,
                },
                {
                    "fast": Binding.create("trend.return", {"window_bars": recipe.fast_bars}),
                    "slow": Binding.create("trend.return", {"window_bars": recipe.slow_bars}),
                },
            ),
        )
        identity = content_id(config.to_dict())
        candidates[identity] = config.to_dict()
        fitted.append(
            {
                "penalty": penalty,
                "candidate_id": identity,
                "training_mse": math.fsum(
                    (float(frozen_intercept) + float(frozen_w0) * u + float(frozen_w1) * v - t) ** 2
                    for u, v, t, _, _ in rows
                )
                / n,
            }
        )
    if len(candidates) < 2:
        raise ValueError("rounded fits produce fewer than two distinct candidates")
    return {
        "revision": "ridge-two-returns/1",
        "training_snapshot": str(dataset.snapshot_id),
        "training_content": dataset.content_hash,
        "recipe": recipe.to_dict(),
        "row_count": n,
        "dropped_warmup": recipe.slow_bars,
        "dropped_unavailable_labels": recipe.horizon_bars,
        "last_feature_observation": str(dataset.bars[rows[-1][3]].observation_id),
        "last_label_observation": str(dataset.bars[rows[-1][4]].observation_id),
        "last_label_available_at": dataset.bars[rows[-1][4]].available_at.isoformat(),
        "scaler_mean": means,
        "scaler_scale": scales,
        "candidates": dict(sorted(candidates.items())),
        "fits": fitted,
        "numeric_policy": "FLOAT64_RESEARCH_FIT_DECIMAL12_FROZEN_RUNTIME_COEFFICIENTS",
        "label_policy": "FUTURE_CLOSE_RETURN_WITHIN_TRAINING_WINDOW_ONLY",
        "limitation": "训练误差不是策略收益；胜出配置必须走原风控、撮合和账本验证。",
    }

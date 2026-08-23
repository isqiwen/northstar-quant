"""P3-WP04 fail-closed risk-limit evaluation evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import math


class LimitStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"


@dataclass(frozen=True, slots=True)
class LimitCheck:
    limit_id: str
    status: LimitStatus
    observed: float | None
    threshold: float
    evidence_hash: str


@dataclass(frozen=True, slots=True)
class RiskLimitSet:
    per_contract: float
    per_commodity: float
    per_sector: float
    per_exchange: float
    per_strategy: float
    per_account: float
    gross_leverage: float
    net_leverage: float
    margin_utilization: float

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a non-negative finite number")


@dataclass(frozen=True, slots=True)
class RiskMeasurements:
    contract: float | None
    commodity: float | None
    sector: float | None
    exchange: float | None
    strategy: float | None
    account: float | None
    gross_leverage: float | None
    net_leverage: float | None
    margin_utilization: float | None


def evaluate_limits(*, limits: RiskLimitSet, measurements: RiskMeasurements) -> tuple[LimitCheck, ...]:
    if not isinstance(limits, RiskLimitSet) or not isinstance(measurements, RiskMeasurements):
        raise ValueError("limits and measurements must be typed risk snapshots")
    bindings = (
        ("per_contract", measurements.contract), ("per_commodity", measurements.commodity),
        ("per_sector", measurements.sector), ("per_exchange", measurements.exchange),
        ("per_strategy", measurements.strategy), ("per_account", measurements.account),
        ("gross_leverage", measurements.gross_leverage), ("net_leverage", measurements.net_leverage),
        ("margin_utilization", measurements.margin_utilization),
    )
    return tuple(evaluate_limit(limit_id=name, observed=observed, threshold=getattr(limits, name)) for name, observed in bindings)


def evaluate_limit(*, limit_id: str, observed: float | None, threshold: float, warn_ratio: float = 0.8) -> LimitCheck:
    """Unknown or non-finite measurements fail closed; callers cannot infer safety."""
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or not math.isfinite(threshold) or threshold < 0:
        raise ValueError("threshold must be a non-negative finite number")
    if not 0 <= warn_ratio < 1:
        raise ValueError("warn_ratio must be in [0, 1)")
    value = None if observed is None else float(observed)
    if value is None or not math.isfinite(value):
        status = LimitStatus.BLOCK
    elif value > threshold:
        status = LimitStatus.BLOCK
    elif value >= threshold * warn_ratio:
        status = LimitStatus.WARN
    else:
        status = LimitStatus.PASS
    payload = {"limit_id": limit_id, "observed": value, "threshold": threshold, "status": status.value}
    return LimitCheck(limit_id, status, value, float(threshold), hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest())


__all__ = ["LimitCheck", "LimitStatus", "RiskLimitSet", "RiskMeasurements", "evaluate_limit", "evaluate_limits"]

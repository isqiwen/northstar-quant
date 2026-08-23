"""P3-WP03 deterministic, fail-closed portfolio exposure snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import math
import re


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class ExposureError(ValueError):
    """Exposure inputs omit a required classification or contain invalid risk data."""


def _id(value: object, name: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value.strip()) is None:
        raise ExposureError(f"{name} must be a non-empty identifier")
    return value.strip()


def _number(value: object, name: str, *, non_negative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ExposureError(f"{name} must be a finite number")
    value = float(value)
    if non_negative and value < 0:
        raise ExposureError(f"{name} must be non-negative")
    return value


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


@dataclass(frozen=True, slots=True)
class ExposurePosition:
    instrument_id: str
    commodity_id: str
    sector_id: str
    exchange_id: str
    correlation_cluster_id: str
    direction: Direction
    notional: float
    margin_required: float

    def __post_init__(self) -> None:
        for name in ("instrument_id", "commodity_id", "sector_id", "exchange_id", "correlation_cluster_id"):
            object.__setattr__(self, name, _id(getattr(self, name), name))
        if not isinstance(self.direction, Direction):
            raise ExposureError("direction must be a Direction")
        object.__setattr__(self, "notional", _number(self.notional, "notional", non_negative=True))
        object.__setattr__(self, "margin_required", _number(self.margin_required, "margin_required", non_negative=True))


@dataclass(frozen=True, slots=True)
class ExposureSnapshot:
    gross: float
    net: float
    by_commodity: tuple[tuple[str, float], ...]
    by_sector: tuple[tuple[str, float], ...]
    by_exchange: tuple[tuple[str, float], ...]
    by_direction: tuple[tuple[str, float], ...]
    by_correlation_cluster: tuple[tuple[str, float], ...]
    margin_required: float
    concentration: float
    snapshot_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("gross", "net", "margin_required", "concentration"):
            object.__setattr__(self, name, _number(getattr(self, name), name, non_negative=name != "net"))
        if self.concentration > 1:
            raise ExposureError("concentration cannot exceed 1")
        payload = self.as_mapping(include_hash=False)
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        object.__setattr__(self, "snapshot_hash", digest)

    def as_mapping(self, *, include_hash: bool = True) -> dict[str, object]:
        data: dict[str, object] = {
            "format": "northstar.exposure-snapshot.v1", "gross": self.gross, "net": self.net,
            "by_commodity": dict(self.by_commodity), "by_sector": dict(self.by_sector),
            "by_exchange": dict(self.by_exchange), "by_direction": dict(self.by_direction),
            "by_correlation_cluster": dict(self.by_correlation_cluster),
            "margin_required": self.margin_required, "concentration": self.concentration,
        }
        if include_hash:
            data["snapshot_hash"] = self.snapshot_hash
        return data


def calculate_exposure(*, positions: tuple[ExposurePosition, ...]) -> ExposureSnapshot:
    if not isinstance(positions, tuple) or not positions or not all(isinstance(item, ExposurePosition) for item in positions):
        raise ExposureError("positions must be a non-empty ExposurePosition tuple")
    if len({item.instrument_id for item in positions}) != len(positions):
        raise ExposureError("positions cannot contain duplicate instrument_id")
    gross = sum(item.notional for item in positions)
    def signed(item: ExposurePosition) -> float:
        return item.notional if item.direction is Direction.LONG else -item.notional

    def grouped(attribute: str, *, signed_values: bool = True) -> tuple[tuple[str, float], ...]:
        values: dict[str, float] = {}
        for item in positions:
            key = getattr(item, attribute)
            values[key] = values.get(key, 0.0) + (signed(item) if signed_values else item.notional)
        return tuple(sorted(values.items()))
    by_commodity = grouped("commodity_id")
    return ExposureSnapshot(
        gross=gross, net=sum(signed(item) for item in positions), by_commodity=by_commodity,
        by_sector=grouped("sector_id"), by_exchange=grouped("exchange_id"),
        by_direction=grouped("direction", signed_values=False),
        by_correlation_cluster=grouped("correlation_cluster_id"),
        margin_required=sum(item.margin_required for item in positions),
        concentration=(max(abs(value) for _, value in by_commodity) / gross if gross else 0.0),
    )


__all__ = ["Direction", "ExposureError", "ExposurePosition", "ExposureSnapshot", "calculate_exposure"]

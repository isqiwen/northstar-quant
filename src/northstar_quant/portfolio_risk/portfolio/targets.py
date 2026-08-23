"""P3-WP01 immutable portfolio-target contracts.

These objects deliberately stop before execution.  A strategy proposal, an
aggregated portfolio target, and an approved target have different provenance
and are never interchangeable with an execution plan or broker order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import math
import re


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class PortfolioTargetError(ValueError):
    """A portfolio target is malformed or breaks its immutable lineage."""


def _canonical_json_sha256(payload: object) -> str:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PortfolioTargetError("target identity payload must be canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value.strip()) is None:
        raise PortfolioTargetError(f"{field_name} must be a non-empty identifier")
    return value.strip()


def _hash(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise PortfolioTargetError(f"{field_name} must be a lowercase SHA-256")
    return value


def _time(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PortfolioTargetError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _weight(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PortfolioTargetError(f"{field_name} must be a finite number")
    return float(value)


@dataclass(frozen=True, slots=True)
class TargetPosition:
    """A desired instrument weight, without any execution instruction."""

    instrument_id: str
    target_weight: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument_id", _identifier(self.instrument_id, "instrument_id"))
        object.__setattr__(self, "target_weight", _weight(self.target_weight, "target_weight"))

    def as_mapping(self) -> dict[str, object]:
        return {"instrument_id": self.instrument_id, "target_weight": self.target_weight}


def _positions(value: object) -> tuple[TargetPosition, ...]:
    if not isinstance(value, tuple) or not value or not all(isinstance(item, TargetPosition) for item in value):
        raise PortfolioTargetError("positions must be a non-empty TargetPosition tuple")
    positions = tuple(sorted(value, key=lambda item: item.instrument_id))
    if len({item.instrument_id for item in positions}) != len(positions):
        raise PortfolioTargetError("positions cannot contain duplicate instrument_id")
    return positions


def _validate_window(generated_at: datetime, effective_at: datetime, expires_at: datetime) -> None:
    if effective_at < generated_at:
        raise PortfolioTargetError("effective_at cannot precede generated_at")
    if expires_at <= effective_at:
        raise PortfolioTargetError("expires_at must be later than effective_at")


@dataclass(frozen=True, slots=True)
class StrategyTargetActivationRef:
    """Opaque, immutable reference to the approval that activated a strategy target.

    P3 deliberately retains only this small cross-domain commitment.  The full
    Research Card and manual-activation evidence remain owned by the
    application composition boundary, so Portfolio/Risk never imports Research
    or application state.
    """

    activation_id: str
    activation_hash: str
    approved_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "activation_id", _identifier(self.activation_id, "activation_id"))
        object.__setattr__(
            self,
            "activation_hash",
            _hash(self.activation_hash, "activation_hash"),
        )
        object.__setattr__(self, "approved_at", _time(self.approved_at, "approved_at"))

    def as_mapping(self) -> dict[str, object]:
        return {
            "activation_id": self.activation_id,
            "activation_hash": self.activation_hash,
            "approved_at": self.approved_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class StrategyTarget:
    """A manually activated strategy target snapshot.

    A target is still only a P3 proposal: its activation reference is not a
    PortfolioTarget approval, ExecutionPlan, or BrokerOrder authorization.
    """

    target_id: str
    source_strategy_id: str
    source_strategy_version: str
    generated_at: datetime
    effective_at: datetime
    expires_at: datetime
    positions: tuple[TargetPosition, ...]
    activation: StrategyTargetActivationRef
    target_hash: str = field(init=False)

    def __post_init__(self) -> None:
        target_id = _identifier(self.target_id, "target_id")
        strategy_id = _identifier(self.source_strategy_id, "source_strategy_id")
        version = _identifier(self.source_strategy_version, "source_strategy_version")
        generated_at = _time(self.generated_at, "generated_at")
        effective_at = _time(self.effective_at, "effective_at")
        expires_at = _time(self.expires_at, "expires_at")
        _validate_window(generated_at, effective_at, expires_at)
        positions = _positions(self.positions)
        if type(self.activation) is not StrategyTargetActivationRef:
            raise PortfolioTargetError("activation must be a StrategyTargetActivationRef")
        activation = self.activation
        if activation.approved_at < generated_at:
            raise PortfolioTargetError("activation approval cannot precede target generation")
        if activation.approved_at >= effective_at:
            raise PortfolioTargetError("activation approval must precede target effectiveness")
        target_hash = _canonical_json_sha256(
            {
                "format": "northstar.strategy-target.v2",
                "target_id": target_id,
                "source_strategy_id": strategy_id,
                "source_strategy_version": version,
                "generated_at": generated_at.isoformat(),
                "effective_at": effective_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "positions": [item.as_mapping() for item in positions],
                "activation": activation.as_mapping(),
            }
        )
        object.__setattr__(self, "target_id", target_id)
        object.__setattr__(self, "source_strategy_id", strategy_id)
        object.__setattr__(self, "source_strategy_version", version)
        object.__setattr__(self, "generated_at", generated_at)
        object.__setattr__(self, "effective_at", effective_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "positions", positions)
        object.__setattr__(self, "activation", activation)
        object.__setattr__(self, "target_hash", target_hash)

    def as_mapping(self) -> dict[str, object]:
        return {
            "format": "northstar.strategy-target.v2",
            "target_id": self.target_id,
            "source_strategy": {"id": self.source_strategy_id, "version": self.source_strategy_version},
            "generated_at": self.generated_at.isoformat(),
            "effective_at": self.effective_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "positions": [item.as_mapping() for item in self.positions],
            "activation": self.activation.as_mapping(),
            "target_hash": self.target_hash,
        }


@dataclass(frozen=True, slots=True)
class PortfolioTarget:
    """The allocator's aggregate target, bound to immutable composition evidence.

    ``composition_hash`` is an explicit commitment to the structured
    composition that produced this target.  A source-hash set plus netted
    positions is not sufficient provenance: distinct allocation policies can
    otherwise collapse to the same final positions.  The canonical
    multi-strategy composer owns the full replayable evidence; this contract
    retains its immutable hash so downstream approval can bind it too.
    """

    target_id: str
    generated_at: datetime
    effective_at: datetime
    expires_at: datetime
    source_strategy_target_hashes: tuple[str, ...]
    positions: tuple[TargetPosition, ...]
    composition_hash: str
    target_hash: str = field(init=False)

    def __post_init__(self) -> None:
        target_id = _identifier(self.target_id, "target_id")
        generated_at = _time(self.generated_at, "generated_at")
        effective_at = _time(self.effective_at, "effective_at")
        expires_at = _time(self.expires_at, "expires_at")
        _validate_window(generated_at, effective_at, expires_at)
        sources = tuple(sorted(_hash(item, "source_strategy_target_hash") for item in self.source_strategy_target_hashes))
        if not sources or len(set(sources)) != len(sources):
            raise PortfolioTargetError("source_strategy_target_hashes must be a non-empty unique tuple")
        positions = _positions(self.positions)
        composition_hash = _hash(self.composition_hash, "composition_hash")
        target_hash = _canonical_json_sha256(
            {
                "format": "northstar.portfolio-target.v2",
                "target_id": target_id,
                "generated_at": generated_at.isoformat(),
                "effective_at": effective_at.isoformat(),
                "expires_at": expires_at.isoformat(),
                "source_strategy_target_hashes": list(sources),
                "positions": [item.as_mapping() for item in positions],
                "composition_hash": composition_hash,
            }
        )
        object.__setattr__(self, "target_id", target_id)
        object.__setattr__(self, "generated_at", generated_at)
        object.__setattr__(self, "effective_at", effective_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "source_strategy_target_hashes", sources)
        object.__setattr__(self, "positions", positions)
        object.__setattr__(self, "composition_hash", composition_hash)
        object.__setattr__(self, "target_hash", target_hash)

    def as_mapping(self) -> dict[str, object]:
        return {
            "format": "northstar.portfolio-target.v2",
            "target_id": self.target_id,
            "generated_at": self.generated_at.isoformat(),
            "effective_at": self.effective_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "source_strategy_target_hashes": list(self.source_strategy_target_hashes),
            "positions": [item.as_mapping() for item in self.positions],
            "composition_hash": self.composition_hash,
            "target_hash": self.target_hash,
        }


__all__ = [
    "PortfolioTarget",
    "PortfolioTargetError",
    "StrategyTargetActivationRef",
    "StrategyTarget",
    "TargetPosition",
]

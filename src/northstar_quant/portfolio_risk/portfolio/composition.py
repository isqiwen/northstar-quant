"""P10-WP04 canonical, replayable multi-strategy portfolio composition.

This module is deliberately a structured P3 boundary.  It accepts only
already-activated :class:`StrategyTarget` snapshots, derives allocation
internally, and returns an unapproved :class:`PortfolioTarget` together with
hash-bound evidence.  It does not resolve market data, assess risk, approve a
target, build an execution plan, or communicate with a broker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import math
import re

from northstar_quant.portfolio_risk.allocation.models import (
    AllocationError,
    AllocationPolicy,
    AllocationResult,
    StrategyAllocation,
    StrategyAllocationInput,
    allocate,
)
from northstar_quant.portfolio_risk.portfolio.targets import (
    PortfolioTarget,
    PortfolioTargetError,
    StrategyTarget,
    StrategyTargetActivationRef,
    TargetPosition,
    _canonical_json_sha256,
)


__all__ = [
    "CanonicalPortfolioComposer",
    "PortfolioCompositionError",
    "PortfolioCompositionEvidence",
    "PortfolioCompositionRequest",
    "StrategyTargetContribution",
]


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class PortfolioCompositionError(ValueError):
    """A multi-strategy composition cannot be replayed safely."""


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value.strip()) is None:
        raise PortfolioCompositionError(f"{field_name} must be a non-empty identifier")
    return value.strip()


def _hash(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise PortfolioCompositionError(f"{field_name} must be a lowercase SHA-256")
    return value


def _time(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PortfolioCompositionError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _finite(value: object, field_name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PortfolioCompositionError(f"{field_name} must be a finite number")
    parsed = float(value)
    if parsed < 0 or (positive and parsed <= 0):
        requirement = "positive" if positive else "non-negative"
        raise PortfolioCompositionError(f"{field_name} must be {requirement}")
    return parsed


def _positions(value: object, field_name: str) -> tuple[TargetPosition, ...]:
    if not isinstance(value, tuple) or not value or not all(type(item) is TargetPosition for item in value):
        raise PortfolioCompositionError(f"{field_name} must be a non-empty TargetPosition tuple")
    positions = tuple(sorted(value, key=lambda item: item.instrument_id))
    if len({item.instrument_id for item in positions}) != len(positions):
        raise PortfolioCompositionError(f"{field_name} cannot contain duplicate instrument_id")
    return positions


def _policy_mapping(policy: AllocationPolicy) -> dict[str, float]:
    if type(policy) is not AllocationPolicy:
        raise PortfolioCompositionError("allocation_policy must be an exact AllocationPolicy")
    return {
        "cash_reserve": _finite(policy.cash_reserve, "allocation_policy.cash_reserve"),
        "target_volatility": _finite(
            policy.target_volatility,
            "allocation_policy.target_volatility",
            positive=True,
        ),
    }


def _replay_strategy_target(target: StrategyTarget) -> StrategyTarget:
    """Reconstruct the immutable source snapshot before it influences risk."""

    if type(target) is not StrategyTarget:
        raise PortfolioCompositionError("strategy_target must be an exact StrategyTarget")
    if type(target.activation) is not StrategyTargetActivationRef:
        raise PortfolioCompositionError("strategy_target activation must be exact")
    if not all(type(position) is TargetPosition for position in target.positions):
        raise PortfolioCompositionError("strategy_target positions must be exact TargetPosition values")
    try:
        replay = StrategyTarget(
            target_id=target.target_id,
            source_strategy_id=target.source_strategy_id,
            source_strategy_version=target.source_strategy_version,
            generated_at=target.generated_at,
            effective_at=target.effective_at,
            expires_at=target.expires_at,
            positions=tuple(
                TargetPosition(position.instrument_id, position.target_weight)
                for position in target.positions
            ),
            activation=StrategyTargetActivationRef(
                activation_id=target.activation.activation_id,
                activation_hash=target.activation.activation_hash,
                approved_at=target.activation.approved_at,
            ),
        )
    except PortfolioTargetError as exc:
        raise PortfolioCompositionError("strategy_target cannot be replayed") from exc
    if replay != target:
        raise PortfolioCompositionError("strategy_target replay mismatch")
    return replay


def _replay_input(item: StrategyAllocationInput) -> StrategyAllocationInput:
    if type(item) is not StrategyAllocationInput:
        raise PortfolioCompositionError("allocation_inputs must contain exact StrategyAllocationInput values")
    try:
        replay = StrategyAllocationInput(
            strategy_target=_replay_strategy_target(item.strategy_target),
            fixed_budget=item.fixed_budget,
            realized_volatility=item.realized_volatility,
            risk_budget=item.risk_budget,
            max_allocation=item.max_allocation,
        )
    except AllocationError as exc:
        raise PortfolioCompositionError("allocation input cannot be replayed") from exc
    if replay != item:
        raise PortfolioCompositionError("allocation input replay mismatch")
    return replay


def _input_mapping(item: StrategyAllocationInput) -> dict[str, object]:
    replay = _replay_input(item)
    return {
        "strategy_target": replay.strategy_target.as_mapping(),
        "fixed_budget": replay.fixed_budget,
        "realized_volatility": replay.realized_volatility,
        "risk_budget": replay.risk_budget,
        "max_allocation": replay.max_allocation,
    }


def _allocation_mapping(result: AllocationResult) -> dict[str, object]:
    if type(result) is not AllocationResult:
        raise PortfolioCompositionError("allocation_result must be an exact AllocationResult")
    if not isinstance(result.allocations, tuple) or not result.allocations:
        raise PortfolioCompositionError("allocation_result.allocations must be non-empty")
    allocations: list[dict[str, object]] = []
    allocation_values: list[float] = []
    for item in result.allocations:
        if type(item) is not StrategyAllocation:
            raise PortfolioCompositionError("allocation_result allocations must be exact StrategyAllocation")
        allocation = _finite(item.allocation, "allocation")
        allocation_values.append(allocation)
        allocations.append(
            {
                "strategy_target_hash": _hash(
                    item.strategy_target_hash,
                    "allocation strategy_target_hash",
                ),
                "allocation": allocation,
                "volatility_scale": _finite(item.volatility_scale, "volatility_scale", positive=True),
            }
        )
    allocations.sort(key=lambda item: str(item["strategy_target_hash"]))
    hashes = [str(item["strategy_target_hash"]) for item in allocations]
    if len(hashes) != len(set(hashes)):
        raise PortfolioCompositionError("allocation_result cannot duplicate a strategy target")
    cash = _finite(result.unallocated_cash, "unallocated_cash")
    if cash > 1:
        raise PortfolioCompositionError("unallocated_cash cannot exceed 1")
    if math.fsum(allocation_values) + cash > 1 + 1e-12:
        raise PortfolioCompositionError("allocation plus cash cannot exceed 1")
    return {
        "format": "northstar.allocation-result.v1",
        "allocations": allocations,
        "unallocated_cash": cash,
        "allocation_hash": _hash(result.allocation_hash, "allocation_hash"),
    }


def _validate_source_windows(
    *,
    generated_at: datetime,
    effective_at: datetime,
    expires_at: datetime,
    inputs: tuple[StrategyAllocationInput, ...],
) -> None:
    for item in inputs:
        target = item.strategy_target
        if target.generated_at > generated_at:
            raise PortfolioCompositionError("source target generation is after portfolio generation")
        if target.activation.approved_at > generated_at:
            raise PortfolioCompositionError("source activation approval is after portfolio generation")
        if target.effective_at > generated_at:
            raise PortfolioCompositionError("source target is not effective at portfolio generation")
        if expires_at > target.expires_at:
            raise PortfolioCompositionError("portfolio expiry exceeds a source target expiry")
        if effective_at > target.expires_at:
            raise PortfolioCompositionError("portfolio effectiveness exceeds a source target expiry")


def _validate_source_uniqueness(inputs: tuple[StrategyAllocationInput, ...]) -> None:
    targets = tuple(item.strategy_target for item in inputs)
    identity_sets = (
        ("strategy target hash", tuple(target.target_hash for target in targets)),
        ("strategy target id", tuple(target.target_id for target in targets)),
        ("source strategy", tuple(target.source_strategy_id for target in targets)),
        ("activation id", tuple(target.activation.activation_id for target in targets)),
        ("activation hash", tuple(target.activation.activation_hash for target in targets)),
    )
    for label, identities in identity_sets:
        if len(identities) != len(set(identities)):
            raise PortfolioCompositionError(f"duplicate {label} is not permitted")


def _normalized_inputs(value: object) -> tuple[StrategyAllocationInput, ...]:
    if not isinstance(value, tuple) or len(value) < 2:
        raise PortfolioCompositionError("allocation_inputs must be a tuple containing at least two sources")
    inputs = tuple(sorted((_replay_input(item) for item in value), key=lambda item: item.strategy_target.target_hash))
    _validate_source_uniqueness(inputs)
    return inputs


@dataclass(frozen=True, slots=True)
class PortfolioCompositionRequest:
    """Typed input for exactly one canonical multi-strategy composition.

    The request deliberately owns the allocation inputs rather than accepting
    a caller-produced aggregate target or allocation result.  Therefore every
    output can be replayed from the activated source snapshots and policy.
    """

    target_id: str
    generated_at: datetime
    effective_at: datetime
    expires_at: datetime
    allocation_policy: AllocationPolicy
    allocation_inputs: tuple[StrategyAllocationInput, ...]

    def __post_init__(self) -> None:
        target_id = _identifier(self.target_id, "target_id")
        generated_at = _time(self.generated_at, "generated_at")
        effective_at = _time(self.effective_at, "effective_at")
        expires_at = _time(self.expires_at, "expires_at")
        if not generated_at < effective_at < expires_at:
            raise PortfolioCompositionError(
                "portfolio composition requires generated_at < effective_at < expires_at"
            )
        policy = self.allocation_policy
        _policy_mapping(policy)
        inputs = _normalized_inputs(self.allocation_inputs)
        _validate_source_windows(
            generated_at=generated_at,
            effective_at=effective_at,
            expires_at=expires_at,
            inputs=inputs,
        )
        object.__setattr__(self, "target_id", target_id)
        object.__setattr__(self, "generated_at", generated_at)
        object.__setattr__(self, "effective_at", effective_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "allocation_inputs", inputs)

    def as_mapping(self) -> dict[str, object]:
        return {
            "format": "northstar.portfolio-composition-request.v1",
            "target_id": self.target_id,
            "generated_at": self.generated_at.isoformat(),
            "effective_at": self.effective_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "allocation_policy": _policy_mapping(self.allocation_policy),
            "allocation_inputs": [_input_mapping(item) for item in self.allocation_inputs],
        }


@dataclass(frozen=True, slots=True)
class StrategyTargetContribution:
    """One source target's allocation-scaled positions, retained before netting."""

    strategy_target_hash: str
    allocation: float
    volatility_scale: float
    weighted_positions: tuple[TargetPosition, ...]
    contribution_hash: str = field(init=False)

    def __post_init__(self) -> None:
        strategy_target_hash = _hash(self.strategy_target_hash, "strategy_target_hash")
        allocation = _finite(self.allocation, "allocation")
        if allocation > 1:
            raise PortfolioCompositionError("allocation cannot exceed 1")
        volatility_scale = _finite(self.volatility_scale, "volatility_scale", positive=True)
        weighted_positions = _positions(self.weighted_positions, "weighted_positions")
        contribution_hash = _canonical_json_sha256(
            {
                "format": "northstar.strategy-target-contribution.v1",
                "strategy_target_hash": strategy_target_hash,
                "allocation": allocation,
                "volatility_scale": volatility_scale,
                "weighted_positions": [item.as_mapping() for item in weighted_positions],
            }
        )
        object.__setattr__(self, "strategy_target_hash", strategy_target_hash)
        object.__setattr__(self, "allocation", allocation)
        object.__setattr__(self, "volatility_scale", volatility_scale)
        object.__setattr__(self, "weighted_positions", weighted_positions)
        object.__setattr__(self, "contribution_hash", contribution_hash)

    def as_mapping(self) -> dict[str, object]:
        return {
            "format": "northstar.strategy-target-contribution.v1",
            "strategy_target_hash": self.strategy_target_hash,
            "allocation": self.allocation,
            "volatility_scale": self.volatility_scale,
            "weighted_positions": [item.as_mapping() for item in self.weighted_positions],
            "contribution_hash": self.contribution_hash,
        }


def _contributions(
    *,
    inputs: tuple[StrategyAllocationInput, ...],
    allocation_result: AllocationResult,
) -> tuple[StrategyTargetContribution, ...]:
    allocation_mapping = _allocation_mapping(allocation_result)
    allocation_rows = allocation_mapping["allocations"]
    if not isinstance(allocation_rows, list):  # Defensive narrowing for JSON-safe evidence.
        raise PortfolioCompositionError("allocation evidence is malformed")
    by_source = {
        str(row["strategy_target_hash"]): row
        for row in allocation_rows
        if isinstance(row, dict)
    }
    expected_sources = {item.strategy_target.target_hash for item in inputs}
    if set(by_source) != expected_sources or len(by_source) != len(inputs):
        raise PortfolioCompositionError("allocation sources must match composition sources exactly")
    contributions: list[StrategyTargetContribution] = []
    for item in inputs:
        row = by_source[item.strategy_target.target_hash]
        allocation = _finite(row.get("allocation"), "allocation")
        volatility_scale = _finite(row.get("volatility_scale"), "volatility_scale", positive=True)
        weighted_positions = tuple(
            TargetPosition(
                position.instrument_id,
                _normalize_zero(position.target_weight * allocation),
            )
            for position in item.strategy_target.positions
        )
        contributions.append(
            StrategyTargetContribution(
                strategy_target_hash=item.strategy_target.target_hash,
                allocation=allocation,
                volatility_scale=volatility_scale,
                weighted_positions=weighted_positions,
            )
        )
    return tuple(sorted(contributions, key=lambda item: item.strategy_target_hash))


def _normalize_zero(value: float) -> float:
    if not math.isfinite(value):
        raise PortfolioCompositionError("weighted target position must be finite")
    return 0.0 if value == 0 else float(value)


def _aggregate_positions(
    contributions: tuple[StrategyTargetContribution, ...],
) -> tuple[TargetPosition, ...]:
    values_by_instrument: dict[str, list[float]] = {}
    for contribution in contributions:
        for position in contribution.weighted_positions:
            values_by_instrument.setdefault(position.instrument_id, []).append(position.target_weight)
    if not values_by_instrument:
        raise PortfolioCompositionError("composition must retain at least one instrument position")
    return tuple(
        TargetPosition(instrument_id, _normalize_zero(math.fsum(values_by_instrument[instrument_id])))
        for instrument_id in sorted(values_by_instrument)
    )


def _composition_hash(
    *,
    request: PortfolioCompositionRequest,
    allocation_result: AllocationResult,
    contributions: tuple[StrategyTargetContribution, ...],
    positions: tuple[TargetPosition, ...],
) -> str:
    try:
        return _canonical_json_sha256(
            {
                "format": "northstar.canonical-portfolio-composition.v1",
                "request": request.as_mapping(),
                "allocation_result": _allocation_mapping(allocation_result),
                "contributions": [item.as_mapping() for item in contributions],
                "netted_positions": [item.as_mapping() for item in positions],
            }
        )
    except PortfolioTargetError as exc:
        raise PortfolioCompositionError("composition evidence cannot be fingerprinted") from exc


def _replay_allocation(request: PortfolioCompositionRequest) -> AllocationResult:
    try:
        allocation_result = allocate(
            policy=request.allocation_policy,
            inputs=request.allocation_inputs,
        )
    except AllocationError as exc:
        raise PortfolioCompositionError("allocation cannot be derived from composition inputs") from exc
    _allocation_mapping(allocation_result)
    return allocation_result


@dataclass(frozen=True, slots=True)
class PortfolioCompositionEvidence:
    """Replayable proof for one canonical multi-strategy ``PortfolioTarget``.

    It intentionally remains unapproved and non-executable.  P10-WP05 must
    consume this typed evidence before it can consider portfolio-wide risk or
    approval; a bare ``PortfolioTarget`` is not an allocation replay record.
    """

    request: PortfolioCompositionRequest
    portfolio_target: PortfolioTarget
    allocation_result: AllocationResult
    contributions: tuple[StrategyTargetContribution, ...]
    composition_hash: str
    evidence_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.request) is not PortfolioCompositionRequest:
            raise PortfolioCompositionError("request must be an exact PortfolioCompositionRequest")
        if type(self.portfolio_target) is not PortfolioTarget:
            raise PortfolioCompositionError("portfolio_target must be an exact PortfolioTarget")
        composition_hash = _hash(self.composition_hash, "composition_hash")
        allocation_result = _replay_allocation(self.request)
        if allocation_result != self.allocation_result:
            raise PortfolioCompositionError("allocation result replay mismatch")
        contributions = self.contributions
        if not isinstance(contributions, tuple) or not contributions:
            raise PortfolioCompositionError("contributions must be a non-empty tuple")
        if not all(type(item) is StrategyTargetContribution for item in contributions):
            raise PortfolioCompositionError("contributions must contain exact StrategyTargetContribution values")
        contributions = tuple(sorted(contributions, key=lambda item: item.strategy_target_hash))
        if len({item.strategy_target_hash for item in contributions}) != len(contributions):
            raise PortfolioCompositionError("contributions cannot duplicate a source target")
        expected_contributions = _contributions(
            inputs=self.request.allocation_inputs,
            allocation_result=allocation_result,
        )
        if contributions != expected_contributions:
            raise PortfolioCompositionError("source contribution replay mismatch")
        positions = _aggregate_positions(contributions)
        expected_hash = _composition_hash(
            request=self.request,
            allocation_result=allocation_result,
            contributions=contributions,
            positions=positions,
        )
        if composition_hash != expected_hash:
            raise PortfolioCompositionError("composition hash replay mismatch")
        try:
            replay_target = PortfolioTarget(
                target_id=self.request.target_id,
                generated_at=self.request.generated_at,
                effective_at=self.request.effective_at,
                expires_at=self.request.expires_at,
                source_strategy_target_hashes=tuple(
                    item.strategy_target.target_hash for item in self.request.allocation_inputs
                ),
                positions=positions,
                composition_hash=composition_hash,
            )
        except PortfolioTargetError as exc:
            raise PortfolioCompositionError("portfolio target cannot be replayed") from exc
        if replay_target != self.portfolio_target:
            raise PortfolioCompositionError("portfolio target replay mismatch")
        evidence_hash = _canonical_json_sha256(
            {
                "format": "northstar.portfolio-composition-evidence.v1",
                "composition_hash": composition_hash,
                "portfolio_target_hash": replay_target.target_hash,
                "allocation_hash": allocation_result.allocation_hash,
                "contribution_hashes": [item.contribution_hash for item in contributions],
            }
        )
        object.__setattr__(self, "allocation_result", allocation_result)
        object.__setattr__(self, "contributions", contributions)
        object.__setattr__(self, "composition_hash", composition_hash)
        object.__setattr__(self, "evidence_hash", evidence_hash)

    @property
    def eligible_for_portfolio_approval(self) -> bool:
        """Composition has not evaluated portfolio-wide risk or approval."""

        return False

    @property
    def eligible_for_execution(self) -> bool:
        """A composed target cannot become an execution plan by itself."""

        return False

    @property
    def eligible_for_broker_order(self) -> bool:
        """No composition artifact carries broker-side authority."""

        return False

    def as_mapping(self) -> dict[str, object]:
        return {
            "format": "northstar.portfolio-composition-evidence.v1",
            "request": self.request.as_mapping(),
            "portfolio_target": self.portfolio_target.as_mapping(),
            "allocation_result": _allocation_mapping(self.allocation_result),
            "contributions": [item.as_mapping() for item in self.contributions],
            "composition_hash": self.composition_hash,
            "eligible_for_portfolio_approval": False,
            "eligible_for_execution": False,
            "eligible_for_broker_order": False,
            "evidence_hash": self.evidence_hash,
        }


class CanonicalPortfolioComposer:
    """Derive one deterministic, unapproved multi-strategy portfolio target."""

    __slots__ = ()

    def compose(self, request: PortfolioCompositionRequest) -> PortfolioCompositionEvidence:
        if type(request) is not PortfolioCompositionRequest:
            raise PortfolioCompositionError("request must be an exact PortfolioCompositionRequest")
        allocation_result = _replay_allocation(request)
        contributions = _contributions(
            inputs=request.allocation_inputs,
            allocation_result=allocation_result,
        )
        positions = _aggregate_positions(contributions)
        composition_hash = _composition_hash(
            request=request,
            allocation_result=allocation_result,
            contributions=contributions,
            positions=positions,
        )
        try:
            portfolio_target = PortfolioTarget(
                target_id=request.target_id,
                generated_at=request.generated_at,
                effective_at=request.effective_at,
                expires_at=request.expires_at,
                source_strategy_target_hashes=tuple(
                    item.strategy_target.target_hash for item in request.allocation_inputs
                ),
                positions=positions,
                composition_hash=composition_hash,
            )
        except PortfolioTargetError as exc:
            raise PortfolioCompositionError("portfolio target cannot be constructed") from exc
        return PortfolioCompositionEvidence(
            request=request,
            portfolio_target=portfolio_target,
            allocation_result=allocation_result,
            contributions=contributions,
            composition_hash=composition_hash,
        )

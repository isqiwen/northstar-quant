"""P10-WP05 canonical portfolio-wide risk evidence and approval gate.

The public entry point in this module accepts only a replayable
``PortfolioCompositionEvidence`` plus typed, point-in-time risk inputs.  It
derives exposure, all nine limits, and every required stress scenario itself.
The resulting review remains an offline P3 artefact.  A separate named human
attestation is required before an ``ApprovedPortfolioTarget`` is constructed,
and the result is still never an execution or broker capability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
import math
import re

from northstar_quant.portfolio_risk.exposure.models import (
    Direction,
    ExposurePosition,
    ExposureSnapshot,
    calculate_exposure,
)
from northstar_quant.portfolio_risk.limits.evaluator import (
    LimitCheck,
    LimitStatus,
    RiskLimitSet,
    RiskMeasurements,
    evaluate_limit,
    evaluate_limits,
)
from northstar_quant.portfolio_risk.portfolio.composition import (
    PortfolioCompositionError,
    PortfolioCompositionEvidence,
    PortfolioCompositionRequest,
    StrategyTargetContribution,
)
from northstar_quant.portfolio_risk.portfolio.targets import (
    PortfolioTarget,
    PortfolioTargetError,
    _canonical_json_sha256,
)
from northstar_quant.portfolio_risk.risk.scenarios import (
    ScenarioError,
    ScenarioKind,
    StressResult,
    StressScenario,
    evaluate_scenarios,
)
from northstar_quant.portfolio_risk.risk.state_machine import RiskState, RiskStateSnapshot


__all__ = [
    "AccountScopedRiskStateEvidence",
    "ApprovedPortfolioTarget",
    "PortfolioRiskAccountSnapshot",
    "PortfolioRiskApprovalError",
    "PortfolioRiskApprovalEvidence",
    "PortfolioRiskApprovalGate",
    "PortfolioRiskApprovalRequest",
    "PortfolioRiskInstrumentSnapshot",
    "PortfolioRiskPolicy",
    "PortfolioRiskPosition",
    "PortfolioRiskReview",
    "PortfolioRiskReviewRequest",
    "PortfolioRiskReviewStatus",
    "PortfolioStressCheck",
    "PortfolioStressPolicy",
    "RiskApprovalAttestation",
    "StressScenarioLimit",
]


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_TEXT_LENGTH = 512
_LIMIT_IDS = (
    "per_contract",
    "per_commodity",
    "per_sector",
    "per_exchange",
    "per_strategy",
    "per_account",
    "gross_leverage",
    "net_leverage",
    "margin_utilization",
)


class PortfolioRiskApprovalError(ValueError):
    """Canonical portfolio-risk evidence cannot be derived or replayed safely."""


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value.strip()) is None:
        raise PortfolioRiskApprovalError(f"{field_name} must be a non-empty identifier")
    return value.strip()


def _text(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\x00" in value
        or "\r" in value
        or "\n" in value
        or len(value) > _MAX_TEXT_LENGTH
    ):
        raise PortfolioRiskApprovalError(
            f"{field_name} must be non-empty single-line text of at most {_MAX_TEXT_LENGTH} characters"
        )
    return value.strip()


def _hash(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise PortfolioRiskApprovalError(f"{field_name} must be a lowercase SHA-256")
    return value


def _time(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PortfolioRiskApprovalError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _number(
    value: object,
    field_name: str,
    *,
    non_negative: bool = False,
    positive: bool = False,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PortfolioRiskApprovalError(f"{field_name} must be a finite number")
    parsed = float(value)
    if non_negative and parsed < 0:
        raise PortfolioRiskApprovalError(f"{field_name} must be non-negative")
    if positive and parsed <= 0:
        raise PortfolioRiskApprovalError(f"{field_name} must be positive")
    if maximum is not None and parsed > maximum:
        raise PortfolioRiskApprovalError(f"{field_name} cannot exceed {maximum}")
    return parsed


def _optional_number(
    value: object,
    field_name: str,
    *,
    non_negative: bool = False,
) -> float | None:
    if value is None:
        return None
    return _number(value, field_name, non_negative=non_negative)


def _window(
    *,
    observed_at: datetime,
    available_at: datetime,
    expires_at: datetime,
    field_prefix: str,
) -> tuple[datetime, datetime, datetime]:
    observed_at = _time(observed_at, f"{field_prefix}.observed_at")
    available_at = _time(available_at, f"{field_prefix}.available_at")
    expires_at = _time(expires_at, f"{field_prefix}.expires_at")
    if not observed_at <= available_at < expires_at:
        raise PortfolioRiskApprovalError(
            f"{field_prefix} requires observed_at <= available_at < expires_at"
        )
    return observed_at, available_at, expires_at


def _limit_mapping(limits: RiskLimitSet) -> dict[str, float]:
    if type(limits) is not RiskLimitSet:
        raise PortfolioRiskApprovalError("limits must be an exact RiskLimitSet")
    return {name: float(getattr(limits, name)) for name in _LIMIT_IDS}


def _measurement_mapping(measurements: RiskMeasurements) -> dict[str, float | None]:
    if type(measurements) is not RiskMeasurements:
        raise PortfolioRiskApprovalError("measurements must be an exact RiskMeasurements")
    return {
        "contract": measurements.contract,
        "commodity": measurements.commodity,
        "sector": measurements.sector,
        "exchange": measurements.exchange,
        "strategy": measurements.strategy,
        "account": measurements.account,
        "gross_leverage": measurements.gross_leverage,
        "net_leverage": measurements.net_leverage,
        "margin_utilization": measurements.margin_utilization,
    }


def _check_mapping(check: LimitCheck) -> dict[str, object]:
    if type(check) is not LimitCheck:
        raise PortfolioRiskApprovalError("limit checks must be exact LimitCheck values")
    return {
        "limit_id": check.limit_id,
        "status": check.status.value,
        "observed": check.observed,
        "threshold": check.threshold,
        "evidence_hash": check.evidence_hash,
    }


@dataclass(frozen=True, slots=True)
class PortfolioRiskInstrumentSnapshot:
    """One classified instrument and margin input visible to the P3 risk review."""

    instrument_id: str
    commodity_id: str
    sector_id: str
    exchange_id: str
    correlation_cluster_id: str
    margin_fraction: float
    observed_at: datetime
    available_at: datetime
    expires_at: datetime
    snapshot_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "instrument_id",
            "commodity_id",
            "sector_id",
            "exchange_id",
            "correlation_cluster_id",
        ):
            object.__setattr__(self, name, _identifier(getattr(self, name), name))
        margin_fraction = _number(
            self.margin_fraction,
            "margin_fraction",
            positive=True,
            maximum=1,
        )
        observed_at, available_at, expires_at = _window(
            observed_at=self.observed_at,
            available_at=self.available_at,
            expires_at=self.expires_at,
            field_prefix="instrument snapshot",
        )
        object.__setattr__(self, "margin_fraction", margin_fraction)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "snapshot_hash", _canonical_json_sha256(self.as_mapping(False)))

    def as_mapping(self, include_hash: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "format": "northstar.portfolio-risk-instrument-snapshot.v1",
            "instrument_id": self.instrument_id,
            "commodity_id": self.commodity_id,
            "sector_id": self.sector_id,
            "exchange_id": self.exchange_id,
            "correlation_cluster_id": self.correlation_cluster_id,
            "margin_fraction": self.margin_fraction,
            "observed_at": self.observed_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }
        if include_hash:
            result["snapshot_hash"] = self.snapshot_hash
        return result


@dataclass(frozen=True, slots=True)
class PortfolioRiskAccountSnapshot:
    """Offline account capacity evidence; ``None`` deliberately represents UNKNOWN."""

    account_id: str
    equity: float | None
    margin_capacity: float | None
    observed_at: datetime
    available_at: datetime
    expires_at: datetime
    snapshot_hash: str = field(init=False)

    def __post_init__(self) -> None:
        account_id = _identifier(self.account_id, "account_id")
        equity = _optional_number(self.equity, "equity", non_negative=True)
        margin_capacity = _optional_number(
            self.margin_capacity,
            "margin_capacity",
            non_negative=True,
        )
        observed_at, available_at, expires_at = _window(
            observed_at=self.observed_at,
            available_at=self.available_at,
            expires_at=self.expires_at,
            field_prefix="account snapshot",
        )
        object.__setattr__(self, "account_id", account_id)
        object.__setattr__(self, "equity", equity)
        object.__setattr__(self, "margin_capacity", margin_capacity)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "snapshot_hash", _canonical_json_sha256(self.as_mapping(False)))

    def as_mapping(self, include_hash: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "format": "northstar.portfolio-risk-account-snapshot.v1",
            "account_id": self.account_id,
            "equity": self.equity,
            "margin_capacity": self.margin_capacity,
            "observed_at": self.observed_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }
        if include_hash:
            result["snapshot_hash"] = self.snapshot_hash
        return result


@dataclass(frozen=True, slots=True)
class AccountScopedRiskStateEvidence:
    """A state-machine observation scoped to one account; ``None`` is explicit UNKNOWN."""

    account_id: str
    state_snapshot: RiskStateSnapshot | None
    observed_at: datetime
    available_at: datetime
    expires_at: datetime
    evidence_hash: str = field(init=False)

    def __post_init__(self) -> None:
        account_id = _identifier(self.account_id, "risk state account_id")
        if self.state_snapshot is not None and type(self.state_snapshot) is not RiskStateSnapshot:
            raise PortfolioRiskApprovalError(
                "state_snapshot must be an exact RiskStateSnapshot or None for UNKNOWN"
            )
        observed_at, available_at, expires_at = _window(
            observed_at=self.observed_at,
            available_at=self.available_at,
            expires_at=self.expires_at,
            field_prefix="risk state evidence",
        )
        if self.state_snapshot is not None and self.state_snapshot.occurred_at > observed_at:
            raise PortfolioRiskApprovalError("risk state cannot be observed before it occurred")
        object.__setattr__(self, "account_id", account_id)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(self, "evidence_hash", _canonical_json_sha256(self.as_mapping(False)))

    def as_mapping(self, include_hash: bool = True) -> dict[str, object]:
        snapshot = self.state_snapshot
        result: dict[str, object] = {
            "format": "northstar.account-scoped-risk-state-evidence.v1",
            "account_id": self.account_id,
            "state_snapshot": (
                None
                if snapshot is None
                else {
                    "state": snapshot.state.value,
                    "occurred_at": snapshot.occurred_at.astimezone(UTC).isoformat(),
                    "reason": snapshot.reason,
                    "predecessor_hash": snapshot.predecessor_hash,
                    "recovery_approver_id": snapshot.recovery_approver_id,
                    "state_hash": snapshot.state_hash,
                }
            ),
            "observed_at": self.observed_at.isoformat(),
            "available_at": self.available_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }
        if include_hash:
            result["evidence_hash"] = self.evidence_hash
        return result


@dataclass(frozen=True, slots=True)
class StressScenarioLimit:
    """One required scenario plus its loss and stressed-margin limits."""

    scenario: StressScenario
    max_loss_fraction: float
    max_margin_utilization: float
    scenario_limit_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.scenario) is not StressScenario:
            raise PortfolioRiskApprovalError("scenario must be an exact StressScenario")
        try:
            scenario = StressScenario(
                scenario_id=self.scenario.scenario_id,
                kind=self.scenario.kind,
                shock_fraction=self.scenario.shock_fraction,
            )
        except ScenarioError as exc:
            raise PortfolioRiskApprovalError("stress scenario cannot be replayed") from exc
        if scenario != self.scenario:
            raise PortfolioRiskApprovalError("stress scenario replay mismatch")
        max_loss_fraction = _number(
            self.max_loss_fraction,
            "max_loss_fraction",
            non_negative=True,
        )
        max_margin_utilization = _number(
            self.max_margin_utilization,
            "max_margin_utilization",
            non_negative=True,
        )
        object.__setattr__(self, "scenario", scenario)
        object.__setattr__(self, "max_loss_fraction", max_loss_fraction)
        object.__setattr__(self, "max_margin_utilization", max_margin_utilization)
        object.__setattr__(self, "scenario_limit_hash", _canonical_json_sha256(self.as_mapping(False)))

    def as_mapping(self, include_hash: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "format": "northstar.portfolio-stress-scenario-limit.v1",
            "scenario_id": self.scenario.scenario_id,
            "kind": self.scenario.kind.value,
            "shock_fraction": self.scenario.shock_fraction,
            "max_loss_fraction": self.max_loss_fraction,
            "max_margin_utilization": self.max_margin_utilization,
        }
        if include_hash:
            result["scenario_limit_hash"] = self.scenario_limit_hash
        return result


@dataclass(frozen=True, slots=True)
class PortfolioStressPolicy:
    """The exact seven deterministic stress scenarios required for approval."""

    scenario_limits: tuple[StressScenarioLimit, ...]
    policy_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.scenario_limits, tuple)
            or len(self.scenario_limits) != len(ScenarioKind)
            or not all(type(item) is StressScenarioLimit for item in self.scenario_limits)
        ):
            raise PortfolioRiskApprovalError(
                "scenario_limits must contain exactly one exact StressScenarioLimit for every ScenarioKind"
            )
        limits = tuple(sorted(self.scenario_limits, key=lambda item: item.scenario.kind.value))
        kinds = tuple(item.scenario.kind for item in limits)
        scenario_ids = tuple(item.scenario.scenario_id for item in limits)
        if set(kinds) != set(ScenarioKind) or len(set(kinds)) != len(kinds):
            raise PortfolioRiskApprovalError("stress policy must cover every ScenarioKind exactly once")
        if len(set(scenario_ids)) != len(scenario_ids):
            raise PortfolioRiskApprovalError("stress policy cannot duplicate scenario_id")
        object.__setattr__(self, "scenario_limits", limits)
        object.__setattr__(self, "policy_hash", _canonical_json_sha256(self.as_mapping(False)))

    def as_mapping(self, include_hash: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "format": "northstar.portfolio-stress-policy.v1",
            "scenario_limits": [item.as_mapping() for item in self.scenario_limits],
        }
        if include_hash:
            result["policy_hash"] = self.policy_hash
        return result


@dataclass(frozen=True, slots=True)
class PortfolioRiskPolicy:
    """Typed limits and freshness policy used for one portfolio review."""

    policy_id: str
    policy_version: str
    authority_id: str
    limits: RiskLimitSet
    stress_policy: PortfolioStressPolicy
    max_input_age_seconds: int
    policy_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_id", _identifier(self.policy_id, "policy_id"))
        object.__setattr__(
            self,
            "policy_version",
            _identifier(self.policy_version, "policy_version"),
        )
        object.__setattr__(
            self,
            "authority_id",
            _identifier(self.authority_id, "authority_id"),
        )
        _limit_mapping(self.limits)
        if type(self.stress_policy) is not PortfolioStressPolicy:
            raise PortfolioRiskApprovalError("stress_policy must be an exact PortfolioStressPolicy")
        if (
            isinstance(self.max_input_age_seconds, bool)
            or not isinstance(self.max_input_age_seconds, int)
            or self.max_input_age_seconds <= 0
        ):
            raise PortfolioRiskApprovalError("max_input_age_seconds must be a positive integer")
        object.__setattr__(self, "policy_hash", _canonical_json_sha256(self.as_mapping(False)))

    def as_mapping(self, include_hash: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "format": "northstar.portfolio-risk-policy.v1",
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "authority_id": self.authority_id,
            "limits": _limit_mapping(self.limits),
            "stress_policy": self.stress_policy.as_mapping(),
            "max_input_age_seconds": self.max_input_age_seconds,
        }
        if include_hash:
            result["policy_hash"] = self.policy_hash
        return result


def _replay_composition(value: PortfolioCompositionEvidence) -> PortfolioCompositionEvidence:
    if type(value) is not PortfolioCompositionEvidence:
        raise PortfolioRiskApprovalError("composition must be an exact PortfolioCompositionEvidence")
    try:
        replay = PortfolioCompositionEvidence(
            request=PortfolioCompositionRequest(
                target_id=value.request.target_id,
                generated_at=value.request.generated_at,
                effective_at=value.request.effective_at,
                expires_at=value.request.expires_at,
                allocation_policy=value.request.allocation_policy,
                allocation_inputs=value.request.allocation_inputs,
            ),
            portfolio_target=PortfolioTarget(
                target_id=value.portfolio_target.target_id,
                generated_at=value.portfolio_target.generated_at,
                effective_at=value.portfolio_target.effective_at,
                expires_at=value.portfolio_target.expires_at,
                source_strategy_target_hashes=value.portfolio_target.source_strategy_target_hashes,
                positions=value.portfolio_target.positions,
                composition_hash=value.portfolio_target.composition_hash,
            ),
            allocation_result=value.allocation_result,
            contributions=tuple(
                StrategyTargetContribution(
                    strategy_target_hash=item.strategy_target_hash,
                    allocation=item.allocation,
                    volatility_scale=item.volatility_scale,
                    weighted_positions=item.weighted_positions,
                )
                for item in value.contributions
            ),
            composition_hash=value.composition_hash,
        )
    except (PortfolioCompositionError, PortfolioTargetError) as exc:
        raise PortfolioRiskApprovalError("composition evidence cannot be replayed") from exc
    if replay != value:
        raise PortfolioRiskApprovalError("composition evidence replay mismatch")
    return replay


@dataclass(frozen=True, slots=True)
class PortfolioRiskReviewRequest:
    """All controlled P3 inputs necessary to derive a portfolio risk review."""

    composition: PortfolioCompositionEvidence
    account_snapshot: PortfolioRiskAccountSnapshot
    instrument_snapshots: tuple[PortfolioRiskInstrumentSnapshot, ...]
    risk_state: AccountScopedRiskStateEvidence
    policy: PortfolioRiskPolicy
    evaluated_at: datetime

    def __post_init__(self) -> None:
        if type(self.composition) is not PortfolioCompositionEvidence:
            raise PortfolioRiskApprovalError("composition must be an exact PortfolioCompositionEvidence")
        if type(self.account_snapshot) is not PortfolioRiskAccountSnapshot:
            raise PortfolioRiskApprovalError("account_snapshot must be an exact PortfolioRiskAccountSnapshot")
        if type(self.risk_state) is not AccountScopedRiskStateEvidence:
            raise PortfolioRiskApprovalError("risk_state must be an exact AccountScopedRiskStateEvidence")
        if type(self.policy) is not PortfolioRiskPolicy:
            raise PortfolioRiskApprovalError("policy must be an exact PortfolioRiskPolicy")
        if (
            not isinstance(self.instrument_snapshots, tuple)
            or not self.instrument_snapshots
            or not all(type(item) is PortfolioRiskInstrumentSnapshot for item in self.instrument_snapshots)
        ):
            raise PortfolioRiskApprovalError(
                "instrument_snapshots must be a non-empty exact PortfolioRiskInstrumentSnapshot tuple"
            )
        snapshots = tuple(sorted(self.instrument_snapshots, key=lambda item: item.instrument_id))
        instrument_ids = tuple(item.instrument_id for item in snapshots)
        if len(set(instrument_ids)) != len(instrument_ids):
            raise PortfolioRiskApprovalError("instrument_snapshots cannot duplicate instrument_id")
        target_ids = tuple(position.instrument_id for position in self.composition.portfolio_target.positions)
        if set(instrument_ids) != set(target_ids):
            raise PortfolioRiskApprovalError(
                "instrument_snapshots must cover canonical portfolio positions exactly"
            )
        if self.risk_state.account_id != self.account_snapshot.account_id:
            raise PortfolioRiskApprovalError("risk state and account snapshot must share account_id")
        evaluated_at = _time(self.evaluated_at, "evaluated_at")
        object.__setattr__(self, "instrument_snapshots", snapshots)
        object.__setattr__(self, "evaluated_at", evaluated_at)

    def as_mapping(self) -> dict[str, object]:
        return {
            "format": "northstar.portfolio-risk-review-request.v1",
            "composition": self.composition.as_mapping(),
            "account_snapshot": self.account_snapshot.as_mapping(),
            "instrument_snapshots": [item.as_mapping() for item in self.instrument_snapshots],
            "risk_state": self.risk_state.as_mapping(),
            "policy": self.policy.as_mapping(),
            "evaluated_at": self.evaluated_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class PortfolioRiskPosition:
    """Per-target-position risk amount derived by the gate, including flat positions."""

    instrument_id: str
    commodity_id: str
    sector_id: str
    exchange_id: str
    correlation_cluster_id: str
    target_weight: float
    notional: float
    margin_required: float
    position_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "instrument_id",
            "commodity_id",
            "sector_id",
            "exchange_id",
            "correlation_cluster_id",
        ):
            object.__setattr__(self, name, _identifier(getattr(self, name), name))
        target_weight = _number(self.target_weight, "target_weight")
        notional = _number(self.notional, "notional", non_negative=True)
        margin_required = _number(self.margin_required, "margin_required", non_negative=True)
        object.__setattr__(self, "target_weight", target_weight)
        object.__setattr__(self, "notional", notional)
        object.__setattr__(self, "margin_required", margin_required)
        object.__setattr__(self, "position_hash", _canonical_json_sha256(self.as_mapping(False)))

    def as_mapping(self, include_hash: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "format": "northstar.portfolio-risk-position.v1",
            "instrument_id": self.instrument_id,
            "commodity_id": self.commodity_id,
            "sector_id": self.sector_id,
            "exchange_id": self.exchange_id,
            "correlation_cluster_id": self.correlation_cluster_id,
            "target_weight": self.target_weight,
            "notional": self.notional,
            "margin_required": self.margin_required,
        }
        if include_hash:
            result["position_hash"] = self.position_hash
        return result


def _flat_exposure_snapshot() -> ExposureSnapshot:
    return ExposureSnapshot(
        gross=0.0,
        net=0.0,
        by_commodity=(),
        by_sector=(),
        by_exchange=(),
        by_direction=(),
        by_correlation_cluster=(),
        margin_required=0.0,
        concentration=0.0,
    )


def _exposure_snapshot(positions: tuple[PortfolioRiskPosition, ...]) -> ExposureSnapshot:
    non_flat = tuple(item for item in positions if item.target_weight != 0)
    if not non_flat:
        return _flat_exposure_snapshot()
    return calculate_exposure(
        positions=tuple(
            ExposurePosition(
                instrument_id=item.instrument_id,
                commodity_id=item.commodity_id,
                sector_id=item.sector_id,
                exchange_id=item.exchange_id,
                correlation_cluster_id=item.correlation_cluster_id,
                direction=Direction.LONG if item.target_weight > 0 else Direction.SHORT,
                notional=item.notional,
                margin_required=item.margin_required,
            )
            for item in non_flat
        )
    )


def _grouped_gross_fraction(
    positions: tuple[PortfolioRiskPosition, ...],
    *,
    attribute: str,
    denominator: float,
) -> float:
    grouped: dict[str, list[float]] = {}
    for position in positions:
        grouped.setdefault(str(getattr(position, attribute)), []).append(position.notional)
    return max((math.fsum(values) / denominator for values in grouped.values()), default=0.0)


def _strategy_fraction(composition: PortfolioCompositionEvidence) -> float:
    return max(
        (
            math.fsum(abs(position.target_weight) for position in contribution.weighted_positions)
            for contribution in composition.contributions
        ),
        default=0.0,
    )


def _measurements(
    *,
    composition: PortfolioCompositionEvidence,
    positions: tuple[PortfolioRiskPosition, ...],
    exposure: ExposureSnapshot,
    account: PortfolioRiskAccountSnapshot,
) -> RiskMeasurements:
    equity = account.equity
    capacity = account.margin_capacity
    if equity is None or equity <= 0:
        return RiskMeasurements(None, None, None, None, None, None, None, None, None)
    contract = max((abs(item.target_weight) for item in positions), default=0.0)
    gross_leverage = exposure.gross / equity
    net_leverage = abs(exposure.net) / equity
    margin_utilization = (
        None if capacity is None or capacity <= 0 else exposure.margin_required / capacity
    )
    account_fraction = None if capacity is None or capacity <= 0 else exposure.gross / capacity
    return RiskMeasurements(
        contract=contract,
        commodity=_grouped_gross_fraction(positions, attribute="commodity_id", denominator=equity),
        sector=_grouped_gross_fraction(positions, attribute="sector_id", denominator=equity),
        exchange=_grouped_gross_fraction(positions, attribute="exchange_id", denominator=equity),
        strategy=_strategy_fraction(composition),
        account=account_fraction,
        gross_leverage=gross_leverage,
        net_leverage=net_leverage,
        margin_utilization=margin_utilization,
    )


@dataclass(frozen=True, slots=True)
class PortfolioStressCheck:
    """One derived stress result with explicit loss and margin approval status."""

    scenario_id: str
    kind: ScenarioKind
    stressed_loss: float
    stressed_margin: float
    loss_fraction: float | None
    margin_utilization: float | None
    max_loss_fraction: float
    max_margin_utilization: float
    loss_status: LimitStatus
    margin_status: LimitStatus
    status: LimitStatus
    evidence_hash: str = field(init=False)

    def __post_init__(self) -> None:
        scenario_id = _identifier(self.scenario_id, "scenario_id")
        if type(self.kind) is not ScenarioKind:
            raise PortfolioRiskApprovalError("stress kind must be an exact ScenarioKind")
        stressed_loss = _number(self.stressed_loss, "stressed_loss", non_negative=True)
        stressed_margin = _number(self.stressed_margin, "stressed_margin", non_negative=True)
        loss_fraction = _optional_number(self.loss_fraction, "loss_fraction", non_negative=True)
        margin_utilization = _optional_number(
            self.margin_utilization,
            "stressed margin_utilization",
            non_negative=True,
        )
        max_loss_fraction = _number(
            self.max_loss_fraction,
            "max_loss_fraction",
            non_negative=True,
        )
        max_margin_utilization = _number(
            self.max_margin_utilization,
            "max_margin_utilization",
            non_negative=True,
        )
        if not all(type(item) is LimitStatus for item in (self.loss_status, self.margin_status, self.status)):
            raise PortfolioRiskApprovalError("stress statuses must be exact LimitStatus values")
        expected_status = _combined_status(self.loss_status, self.margin_status)
        if self.status is not expected_status:
            raise PortfolioRiskApprovalError("stress status must combine loss and margin statuses")
        object.__setattr__(self, "scenario_id", scenario_id)
        object.__setattr__(self, "stressed_loss", stressed_loss)
        object.__setattr__(self, "stressed_margin", stressed_margin)
        object.__setattr__(self, "loss_fraction", loss_fraction)
        object.__setattr__(self, "margin_utilization", margin_utilization)
        object.__setattr__(self, "max_loss_fraction", max_loss_fraction)
        object.__setattr__(self, "max_margin_utilization", max_margin_utilization)
        object.__setattr__(self, "evidence_hash", _canonical_json_sha256(self.as_mapping(False)))

    def as_mapping(self, include_hash: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "format": "northstar.portfolio-stress-check.v1",
            "scenario_id": self.scenario_id,
            "kind": self.kind.value,
            "stressed_loss": self.stressed_loss,
            "stressed_margin": self.stressed_margin,
            "loss_fraction": self.loss_fraction,
            "margin_utilization": self.margin_utilization,
            "max_loss_fraction": self.max_loss_fraction,
            "max_margin_utilization": self.max_margin_utilization,
            "loss_status": self.loss_status.value,
            "margin_status": self.margin_status.value,
            "status": self.status.value,
        }
        if include_hash:
            result["evidence_hash"] = self.evidence_hash
        return result


def _combined_status(*statuses: LimitStatus) -> LimitStatus:
    if any(status is LimitStatus.BLOCK for status in statuses):
        return LimitStatus.BLOCK
    if any(status is LimitStatus.WARN for status in statuses):
        return LimitStatus.WARN
    return LimitStatus.PASS


def _stress_checks(
    *,
    policy: PortfolioStressPolicy,
    exposure: ExposureSnapshot,
    account: PortfolioRiskAccountSnapshot,
) -> tuple[PortfolioStressCheck, ...]:
    equity = account.equity
    capacity = account.margin_capacity
    usable_equity = equity is not None and equity > 0
    result_by_id: dict[str, StressResult] = {}
    if usable_equity:
        try:
            results = evaluate_scenarios(
                gross_notional=exposure.gross,
                margin_required=exposure.margin_required,
                scenarios=tuple(item.scenario for item in policy.scenario_limits),
            )
        except ScenarioError as exc:
            raise PortfolioRiskApprovalError("stress scenarios cannot be evaluated") from exc
        result_by_id = {result.scenario_id: result for result in results}
    checks: list[PortfolioStressCheck] = []
    for item in policy.scenario_limits:
        result = result_by_id.get(item.scenario.scenario_id)
        stressed_loss = 0.0 if result is None else result.stressed_loss
        stressed_margin = 0.0 if result is None else result.stressed_margin
        loss_fraction = (
            None if equity is None or equity <= 0 else stressed_loss / equity
        )
        margin_utilization = (
            None if capacity is None or capacity <= 0 else stressed_margin / capacity
        )
        loss_check = evaluate_limit(
            limit_id=f"{item.scenario.scenario_id}.loss",
            observed=loss_fraction,
            threshold=item.max_loss_fraction,
        )
        margin_check = evaluate_limit(
            limit_id=f"{item.scenario.scenario_id}.margin",
            observed=margin_utilization,
            threshold=item.max_margin_utilization,
        )
        checks.append(
            PortfolioStressCheck(
                scenario_id=item.scenario.scenario_id,
                kind=item.scenario.kind,
                stressed_loss=stressed_loss,
                stressed_margin=stressed_margin,
                loss_fraction=loss_fraction,
                margin_utilization=margin_utilization,
                max_loss_fraction=item.max_loss_fraction,
                max_margin_utilization=item.max_margin_utilization,
                loss_status=loss_check.status,
                margin_status=margin_check.status,
                status=_combined_status(loss_check.status, margin_check.status),
            )
        )
    return tuple(sorted(checks, key=lambda item: item.kind.value))


class PortfolioRiskReviewStatus(str, Enum):
    """Portfolio-wide result before the separate human attestation stage."""

    PASS = "PASS"
    WARN = "WARN"
    BLOCK = "BLOCK"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class PortfolioRiskReview:
    """Hash-bound derived P3 review; it never carries execution authority."""

    request: PortfolioRiskReviewRequest
    positions: tuple[PortfolioRiskPosition, ...]
    exposure: ExposureSnapshot
    measurements: RiskMeasurements
    limit_checks: tuple[LimitCheck, ...]
    stress_checks: tuple[PortfolioStressCheck, ...]
    observed_risk_state: RiskState | None
    reason_codes: tuple[str, ...]
    status: PortfolioRiskReviewStatus
    approval_valid_until: datetime
    review_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.request) is not PortfolioRiskReviewRequest:
            raise PortfolioRiskApprovalError("review request must be exact")
        expected = _derive_review(self.request)
        if not _review_components_match(self, expected):
            raise PortfolioRiskApprovalError("review must exactly replay its controlled request")
        object.__setattr__(self, "request", expected.request)
        object.__setattr__(self, "positions", expected.positions)
        object.__setattr__(self, "exposure", expected.exposure)
        object.__setattr__(self, "measurements", expected.measurements)
        object.__setattr__(self, "limit_checks", expected.limit_checks)
        object.__setattr__(self, "stress_checks", expected.stress_checks)
        object.__setattr__(self, "observed_risk_state", expected.observed_risk_state)
        object.__setattr__(self, "reason_codes", expected.reason_codes)
        object.__setattr__(self, "status", expected.status)
        object.__setattr__(self, "approval_valid_until", expected.approval_valid_until)
        object.__setattr__(self, "review_hash", _canonical_json_sha256(self.as_mapping(False)))

    @property
    def composition(self) -> PortfolioCompositionEvidence:
        return self.request.composition

    @property
    def composition_evidence_hash(self) -> str:
        return self.composition.evidence_hash

    @property
    def portfolio_target(self) -> PortfolioTarget:
        return self.composition.portfolio_target

    @property
    def evaluated_at(self) -> datetime:
        return self.request.evaluated_at

    @property
    def eligible_for_approval(self) -> bool:
        return (
            self.status is PortfolioRiskReviewStatus.PASS
            and self.observed_risk_state is RiskState.NORMAL
            and not self.reason_codes
            and all(item.status is LimitStatus.PASS for item in self.limit_checks)
            and all(item.status is LimitStatus.PASS for item in self.stress_checks)
        )

    @property
    def eligible_for_execution(self) -> bool:
        return False

    @property
    def eligible_for_broker_order(self) -> bool:
        return False

    def as_mapping(self, include_hash: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "format": "northstar.portfolio-risk-review.v1",
            "request": self.request.as_mapping(),
            "positions": [item.as_mapping() for item in self.positions],
            "exposure": self.exposure.as_mapping(),
            "measurements": _measurement_mapping(self.measurements),
            "limit_checks": [_check_mapping(item) for item in self.limit_checks],
            "stress_checks": [item.as_mapping() for item in self.stress_checks],
            "observed_risk_state": (
                None if self.observed_risk_state is None else self.observed_risk_state.value
            ),
            "reason_codes": list(self.reason_codes),
            "status": self.status.value,
            "approval_valid_until": self.approval_valid_until.isoformat(),
            "eligible_for_approval": self.eligible_for_approval,
            "eligible_for_execution": False,
            "eligible_for_broker_order": False,
        }
        if include_hash:
            result["review_hash"] = self.review_hash
        return result


@dataclass(frozen=True, slots=True)
class RiskApprovalAttestation:
    """Named human approval tied to a reviewed, approval-eligible hash."""

    approval_id: str
    review_hash: str
    approver_id: str
    approved_at: datetime
    rationale: str
    attestation_hash: str = field(init=False)

    def __post_init__(self) -> None:
        approval_id = _identifier(self.approval_id, "approval_id")
        review_hash = _hash(self.review_hash, "review_hash")
        approver_id = _identifier(self.approver_id, "approver_id")
        approved_at = _time(self.approved_at, "approved_at")
        rationale = _text(self.rationale, "rationale")
        object.__setattr__(self, "approval_id", approval_id)
        object.__setattr__(self, "review_hash", review_hash)
        object.__setattr__(self, "approver_id", approver_id)
        object.__setattr__(self, "approved_at", approved_at)
        object.__setattr__(self, "rationale", rationale)
        object.__setattr__(self, "attestation_hash", _canonical_json_sha256(self.as_mapping(False)))

    def as_mapping(self, include_hash: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "format": "northstar.risk-approval-attestation.v1",
            "approval_id": self.approval_id,
            "review_hash": self.review_hash,
            "approver_id": self.approver_id,
            "approved_at": self.approved_at.isoformat(),
            "rationale": self.rationale,
        }
        if include_hash:
            result["attestation_hash"] = self.attestation_hash
        return result


@dataclass(frozen=True, slots=True)
class ApprovedPortfolioTarget:
    """A gate-replayed, account-scoped human approval for one portfolio target.

    This deliberately replaces the former hash-only P3 constructor.  Callers
    cannot supply a bare target and arbitrary ``risk_evidence_hash``: the
    review and attestation are replayed against the canonical P10-WP04
    composition before this object exists.
    """

    review: PortfolioRiskReview
    attestation: RiskApprovalAttestation
    approval_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.review) is not PortfolioRiskReview:
            raise PortfolioRiskApprovalError("review must be an exact PortfolioRiskReview")
        if type(self.attestation) is not RiskApprovalAttestation:
            raise PortfolioRiskApprovalError("attestation must be an exact RiskApprovalAttestation")
        expected = _derive_review(self.review.request)
        if not _review_components_match(self.review, expected):
            raise PortfolioRiskApprovalError("approved target review cannot be replayed")
        if not self.review.eligible_for_approval:
            raise PortfolioRiskApprovalError("only an approval-eligible review can produce a target")
        if self.attestation.review_hash != self.review.review_hash:
            raise PortfolioRiskApprovalError("attestation must bind the exact review hash")
        if self.attestation.approved_at < self.review.evaluated_at:
            raise PortfolioRiskApprovalError("attestation cannot precede review evaluation")
        if self.attestation.approved_at >= self.review.approval_valid_until:
            raise PortfolioRiskApprovalError("attestation is outside the reviewed input validity window")
        object.__setattr__(self, "approval_hash", _canonical_json_sha256(self.as_mapping(False)))

    @property
    def approval_id(self) -> str:
        return self.attestation.approval_id

    @property
    def portfolio_target(self) -> PortfolioTarget:
        return self.review.portfolio_target

    @property
    def approved_at(self) -> datetime:
        return self.attestation.approved_at

    @property
    def approver_id(self) -> str:
        return self.attestation.approver_id

    @property
    def account_id(self) -> str:
        return self.review.request.account_snapshot.account_id

    @property
    def risk_evidence_hash(self) -> str:
        return self.review.review_hash

    @property
    def attestation_hash(self) -> str:
        return self.attestation.attestation_hash

    @property
    def eligible_for_broker_order(self) -> bool:
        return False

    @property
    def eligible_for_execution(self) -> bool:
        return False

    def as_mapping(self, include_hash: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "format": "northstar.approved-portfolio-target.v2",
            "approval_id": self.approval_id,
            "portfolio_target_hash": self.portfolio_target.target_hash,
            "composition_evidence_hash": self.review.composition_evidence_hash,
            "account_id": self.account_id,
            "approved_at": self.approved_at.isoformat(),
            "approver_id": self.approver_id,
            "risk_evidence_hash": self.risk_evidence_hash,
            "attestation_hash": self.attestation_hash,
            "eligible_for_execution": False,
            "eligible_for_broker_order": False,
        }
        if include_hash:
            result["approval_hash"] = self.approval_hash
        return result


@dataclass(frozen=True, slots=True)
class PortfolioRiskApprovalRequest:
    """Review inputs plus a named human attestation; never a broker command."""

    review_request: PortfolioRiskReviewRequest
    attestation: RiskApprovalAttestation

    def __post_init__(self) -> None:
        if type(self.review_request) is not PortfolioRiskReviewRequest:
            raise PortfolioRiskApprovalError("review_request must be exact")
        if type(self.attestation) is not RiskApprovalAttestation:
            raise PortfolioRiskApprovalError("attestation must be exact")

    def as_mapping(self) -> dict[str, object]:
        return {
            "format": "northstar.portfolio-risk-approval-request.v1",
            "review_request": self.review_request.as_mapping(),
            "attestation": self.attestation.as_mapping(),
        }


@dataclass(frozen=True, slots=True)
class PortfolioRiskApprovalEvidence:
    """Replayable P3 review and optional approved target.

    ``approved_target`` is ``None`` for every unknown, warning, block, state,
    or human-attestation failure.  Consumers must replay this evidence through
    :class:`PortfolioRiskApprovalGate`; this record alone is not authority.
    """

    approval_request: PortfolioRiskApprovalRequest
    review: PortfolioRiskReview
    approved_target: ApprovedPortfolioTarget | None
    rejection_reasons: tuple[str, ...]
    evidence_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.approval_request) is not PortfolioRiskApprovalRequest:
            raise PortfolioRiskApprovalError("approval_request must be exact")
        if type(self.review) is not PortfolioRiskReview:
            raise PortfolioRiskApprovalError("review must be exact")
        if self.review.request != self.approval_request.review_request:
            raise PortfolioRiskApprovalError("approval review must bind exact review_request")
        if self.approved_target is not None:
            if type(self.approved_target) is not ApprovedPortfolioTarget:
                raise PortfolioRiskApprovalError("approved_target must be exact or None")
            attestation = self.approval_request.attestation
            if not self.review.eligible_for_approval:
                raise PortfolioRiskApprovalError("ineligible review cannot carry an approved target")
            if self.approved_target.review != self.review or self.approved_target.attestation != attestation:
                raise PortfolioRiskApprovalError("approved target must exactly bind attestation and review")
        if (
            not isinstance(self.rejection_reasons, tuple)
            or not all(isinstance(item, str) and item for item in self.rejection_reasons)
        ):
            raise PortfolioRiskApprovalError("rejection_reasons must be a tuple of non-empty strings")
        reasons = tuple(sorted(set(self.rejection_reasons)))
        if self.approved_target is not None and reasons:
            raise PortfolioRiskApprovalError("approved evidence cannot contain rejection reasons")
        if self.approved_target is None and not reasons:
            raise PortfolioRiskApprovalError("unapproved evidence requires explicit rejection reasons")
        object.__setattr__(self, "rejection_reasons", reasons)
        object.__setattr__(self, "evidence_hash", _canonical_json_sha256(self.as_mapping(False)))

    @property
    def composition_evidence_hash(self) -> str:
        return self.review.composition_evidence_hash

    @property
    def portfolio_target(self) -> PortfolioTarget:
        return self.review.portfolio_target

    @property
    def eligible_for_execution(self) -> bool:
        return False

    @property
    def eligible_for_broker_order(self) -> bool:
        return False

    def as_mapping(self, include_hash: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "format": "northstar.portfolio-risk-approval-evidence.v1",
            "approval_request": self.approval_request.as_mapping(),
            "review": self.review.as_mapping(),
            "approved_target": (
                None if self.approved_target is None else self.approved_target.as_mapping()
            ),
            "rejection_reasons": list(self.rejection_reasons),
            "eligible_for_execution": False,
            "eligible_for_broker_order": False,
        }
        if include_hash:
            result["evidence_hash"] = self.evidence_hash
        return result


def _input_time_reasons(
    *,
    request: PortfolioRiskReviewRequest,
) -> tuple[str, ...]:
    evaluated_at = request.evaluated_at
    max_age = timedelta(seconds=request.policy.max_input_age_seconds)
    reasons: set[str] = set()
    target = request.composition.portfolio_target
    if evaluated_at < target.generated_at:
        reasons.add("PORTFOLIO_TARGET_FUTURE")
    if evaluated_at < target.effective_at:
        reasons.add("PORTFOLIO_TARGET_NOT_EFFECTIVE")
    if evaluated_at >= target.expires_at:
        reasons.add("PORTFOLIO_TARGET_EXPIRED")

    def observe(prefix: str, available_at: datetime, expires_at: datetime) -> None:
        if available_at > evaluated_at:
            reasons.add(f"{prefix}_FUTURE")
        elif evaluated_at >= expires_at:
            reasons.add(f"{prefix}_EXPIRED")
        elif evaluated_at - available_at > max_age:
            reasons.add(f"{prefix}_STALE")

    observe(
        "ACCOUNT_SNAPSHOT",
        request.account_snapshot.available_at,
        request.account_snapshot.expires_at,
    )
    observe("RISK_STATE_EVIDENCE", request.risk_state.available_at, request.risk_state.expires_at)
    for snapshot in request.instrument_snapshots:
        observe(
            f"INSTRUMENT_SNAPSHOT_{snapshot.instrument_id}",
            snapshot.available_at,
            snapshot.expires_at,
        )
    if request.account_snapshot.equity is None:
        reasons.add("ACCOUNT_EQUITY_UNKNOWN")
    elif request.account_snapshot.equity <= 0:
        reasons.add("ACCOUNT_EQUITY_INVALID")
    if request.account_snapshot.margin_capacity is None:
        reasons.add("ACCOUNT_MARGIN_CAPACITY_UNKNOWN")
    elif request.account_snapshot.margin_capacity <= 0:
        reasons.add("ACCOUNT_MARGIN_CAPACITY_INVALID")
    if request.risk_state.state_snapshot is None:
        reasons.add("RISK_STATE_UNKNOWN")
    elif request.risk_state.state_snapshot.state is not RiskState.NORMAL:
        reasons.add(f"RISK_STATE_{request.risk_state.state_snapshot.state.value}")
    return tuple(sorted(reasons))


def _review_status(
    *,
    reasons: tuple[str, ...],
    limit_checks: tuple[LimitCheck, ...],
    stress_checks: tuple[PortfolioStressCheck, ...],
) -> PortfolioRiskReviewStatus:
    unknown_markers = (
        "_UNKNOWN",
        "_FUTURE",
        "_EXPIRED",
        "_STALE",
        "_INVALID",
        "_NOT_EFFECTIVE",
    )
    if any(reason.endswith(unknown_markers) for reason in reasons):
        return PortfolioRiskReviewStatus.UNKNOWN
    if any(check.status is LimitStatus.BLOCK for check in limit_checks) or any(
        check.status is LimitStatus.BLOCK for check in stress_checks
    ):
        return PortfolioRiskReviewStatus.BLOCK
    if reasons:
        return PortfolioRiskReviewStatus.BLOCK
    if any(check.status is LimitStatus.WARN for check in limit_checks) or any(
        check.status is LimitStatus.WARN for check in stress_checks
    ):
        return PortfolioRiskReviewStatus.WARN
    return PortfolioRiskReviewStatus.PASS


@dataclass(frozen=True, slots=True)
class _DerivedPortfolioRiskReview:
    """Private raw replay result used to validate public immutable records."""

    request: PortfolioRiskReviewRequest
    positions: tuple[PortfolioRiskPosition, ...]
    exposure: ExposureSnapshot
    measurements: RiskMeasurements
    limit_checks: tuple[LimitCheck, ...]
    stress_checks: tuple[PortfolioStressCheck, ...]
    observed_risk_state: RiskState | None
    reason_codes: tuple[str, ...]
    status: PortfolioRiskReviewStatus
    approval_valid_until: datetime


def _approval_valid_until(request: PortfolioRiskReviewRequest) -> datetime:
    """Return the exclusive last instant at which the reviewed inputs remain usable."""

    max_age = timedelta(seconds=request.policy.max_input_age_seconds)

    def valid_until(available_at: datetime, expires_at: datetime) -> datetime:
        return min(available_at + max_age, expires_at)

    candidates = [request.composition.portfolio_target.expires_at]
    candidates.append(
        valid_until(
            request.account_snapshot.available_at,
            request.account_snapshot.expires_at,
        )
    )
    candidates.append(
        valid_until(request.risk_state.available_at, request.risk_state.expires_at)
    )
    candidates.extend(
        valid_until(snapshot.available_at, snapshot.expires_at)
        for snapshot in request.instrument_snapshots
    )
    return min(candidates)


def _derive_review(request: PortfolioRiskReviewRequest) -> _DerivedPortfolioRiskReview:
    """Replay all P3 inputs without constructing a public review recursively."""

    composition = _replay_composition(request.composition)
    replayed_request = PortfolioRiskReviewRequest(
        composition=composition,
        account_snapshot=request.account_snapshot,
        instrument_snapshots=request.instrument_snapshots,
        risk_state=request.risk_state,
        policy=request.policy,
        evaluated_at=request.evaluated_at,
    )
    account = replayed_request.account_snapshot
    equity = account.equity
    snapshot_by_instrument = {
        snapshot.instrument_id: snapshot for snapshot in replayed_request.instrument_snapshots
    }
    positions = tuple(
        PortfolioRiskPosition(
            instrument_id=target_position.instrument_id,
            commodity_id=snapshot_by_instrument[target_position.instrument_id].commodity_id,
            sector_id=snapshot_by_instrument[target_position.instrument_id].sector_id,
            exchange_id=snapshot_by_instrument[target_position.instrument_id].exchange_id,
            correlation_cluster_id=snapshot_by_instrument[
                target_position.instrument_id
            ].correlation_cluster_id,
            target_weight=target_position.target_weight,
            notional=(
                0.0
                if equity is None or equity <= 0
                else abs(target_position.target_weight) * equity
            ),
            margin_required=(
                0.0
                if equity is None or equity <= 0
                else abs(target_position.target_weight)
                * equity
                * snapshot_by_instrument[target_position.instrument_id].margin_fraction
            ),
        )
        for target_position in composition.portfolio_target.positions
    )
    exposure = _exposure_snapshot(positions)
    measurements = _measurements(
        composition=composition,
        positions=positions,
        exposure=exposure,
        account=account,
    )
    limit_checks = evaluate_limits(limits=replayed_request.policy.limits, measurements=measurements)
    stress_checks = _stress_checks(
        policy=replayed_request.policy.stress_policy,
        exposure=exposure,
        account=account,
    )
    state_snapshot = replayed_request.risk_state.state_snapshot
    observed_risk_state = None if state_snapshot is None else state_snapshot.state
    reasons = _input_time_reasons(request=replayed_request)
    return _DerivedPortfolioRiskReview(
        request=replayed_request,
        positions=positions,
        exposure=exposure,
        measurements=measurements,
        limit_checks=limit_checks,
        stress_checks=stress_checks,
        observed_risk_state=observed_risk_state,
        reason_codes=reasons,
        status=_review_status(
            reasons=reasons,
            limit_checks=limit_checks,
            stress_checks=stress_checks,
        ),
        approval_valid_until=_approval_valid_until(replayed_request),
    )


def _review_components_match(
    review: PortfolioRiskReview,
    expected: _DerivedPortfolioRiskReview,
) -> bool:
    return (
        review.request == expected.request
        and review.positions == expected.positions
        and review.exposure == expected.exposure
        and review.measurements == expected.measurements
        and review.limit_checks == expected.limit_checks
        and review.stress_checks == expected.stress_checks
        and review.observed_risk_state is expected.observed_risk_state
        and review.reason_codes == expected.reason_codes
        and review.status is expected.status
        and review.approval_valid_until == expected.approval_valid_until
    )


class PortfolioRiskApprovalGate:
    """Derive and replay portfolio risk evidence without execution capability."""

    __slots__ = ()

    def review(self, request: PortfolioRiskReviewRequest) -> PortfolioRiskReview:
        if type(request) is not PortfolioRiskReviewRequest:
            raise PortfolioRiskApprovalError("request must be an exact PortfolioRiskReviewRequest")
        derived = _derive_review(request)
        return PortfolioRiskReview(
            request=derived.request,
            positions=derived.positions,
            exposure=derived.exposure,
            measurements=derived.measurements,
            limit_checks=derived.limit_checks,
            stress_checks=derived.stress_checks,
            observed_risk_state=derived.observed_risk_state,
            reason_codes=derived.reason_codes,
            status=derived.status,
            approval_valid_until=derived.approval_valid_until,
        )

    def evaluate(self, request: PortfolioRiskApprovalRequest) -> PortfolioRiskApprovalEvidence:
        """Replay a review and construct a target only after valid human attestation."""

        if type(request) is not PortfolioRiskApprovalRequest:
            raise PortfolioRiskApprovalError("request must be an exact PortfolioRiskApprovalRequest")
        review = self.review(request.review_request)
        attestation = request.attestation
        rejection_reasons: list[str] = []
        if not review.eligible_for_approval:
            rejection_reasons.append(f"REVIEW_{review.status.value}")
        if attestation.review_hash != review.review_hash:
            rejection_reasons.append("ATTESTATION_REVIEW_HASH_MISMATCH")
        if attestation.approved_at < review.evaluated_at:
            rejection_reasons.append("ATTESTATION_BEFORE_REVIEW")
        if attestation.approved_at >= review.approval_valid_until:
            rejection_reasons.append("ATTESTATION_AFTER_INPUT_VALIDITY")
        approved_target: ApprovedPortfolioTarget | None = None
        if not rejection_reasons:
            approved_target = ApprovedPortfolioTarget(review=review, attestation=attestation)
        return PortfolioRiskApprovalEvidence(
            approval_request=request,
            review=review,
            approved_target=approved_target,
            rejection_reasons=tuple(sorted(set(rejection_reasons))),
        )

"""Trusted P8 authority derivation for portfolio-risk approvals.

P3 remains a pure, replayable risk domain.  This application boundary is the
only place where a P3 review is reconstructed from the active trading profile,
the exact CTP-sim broker snapshot, and a persisted reconciliation safety
observation.  P8 treats caller-provided P3 review/approval objects as claims
and requires them to match this result before it invokes the P3 gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import math
import re
from typing import Any, NoReturn, cast

from northstar_quant.data.artifacts.fingerprints import canonical_json_sha256
from northstar_quant.foundation.config.trading_profile import (
    ProfilePortfolioRiskApprovalConfig,
    TradingProfile,
)
from northstar_quant.portfolio_risk.limits import RiskLimitSet
from northstar_quant.portfolio_risk.portfolio import (
    AccountScopedRiskStateEvidence,
    PortfolioCompositionEvidence,
    PortfolioRiskAccountSnapshot,
    PortfolioRiskInstrumentSnapshot,
    PortfolioRiskPolicy,
    PortfolioRiskReviewRequest,
    PortfolioStressPolicy,
    StressScenarioLimit,
)
from northstar_quant.portfolio_risk.risk import RiskState, RiskStateSnapshot, ScenarioKind, StressScenario
from northstar_quant.trading_execution.broker.ctp_contract_mapping import (
    CtpContractMappingError,
    CtpContractRegistry,
)
from northstar_quant.trading_execution.execution.models import BrokerStateSnapshot


__all__ = [
    "ContractAuthorityView",
    "PortfolioRiskApprovalAuthority",
    "PortfolioRiskAuthorityError",
    "PortfolioRiskAuthorityExecutionRule",
    "PortfolioRiskAuthorityResolver",
    "ReconciliationSafetyStateEvidence",
    "broker_state_hash",
]


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class PortfolioRiskAuthorityError(ValueError):
    """Raised when trusted P8 authority inputs cannot be reconstructed."""


def _refuse(code: str) -> NoReturn:
    raise PortfolioRiskAuthorityError(code)


def _identifier(value: object, *, code: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value.strip()) is None:
        _refuse(code)
    return value.strip()


def _hash(value: object, *, code: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        _refuse(code)
    return value


def _time(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _refuse(code)
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ContractAuthorityView:
    """Database-free replay view consumed by P3 and P8 evidence gates.

    The PostgreSQL resolver remains in the application composition root. This
    narrow value verifies the publication binding and decision-scoped hash
    before passing only typed, immutable replay facts into non-submitting P3/P8
    code, so those gates cannot reach database capability through imports.
    """

    authority_id: str
    decision_at: datetime
    authority_hash: str
    master_publication_hash: str
    registry_publication_hash: str
    registry: CtpContractRegistry
    master: Any

    def __post_init__(self) -> None:
        authority_id = _identifier(
            self.authority_id,
            code="PORTFOLIO_RISK_AUTHORITY_CONTRACT_AUTHORITY_REQUIRED",
        )
        decision_at = _time(
            self.decision_at,
            code="PORTFOLIO_RISK_AUTHORITY_CONTRACT_AUTHORITY_REQUIRED",
        )
        master_publication_hash = _hash(
            self.master_publication_hash,
            code="PORTFOLIO_RISK_AUTHORITY_CONTRACT_AUTHORITY_REQUIRED",
        )
        registry_publication_hash = _hash(
            self.registry_publication_hash,
            code="PORTFOLIO_RISK_AUTHORITY_CONTRACT_AUTHORITY_REQUIRED",
        )
        if type(self.registry) is not CtpContractRegistry:
            _refuse("PORTFOLIO_RISK_AUTHORITY_CONTRACT_AUTHORITY_REQUIRED")
        if not callable(getattr(self.master, "resolve_for_execution", None)) or not callable(
            getattr(self.master, "require_execution_contract", None)
        ):
            _refuse("PORTFOLIO_RISK_AUTHORITY_CONTRACT_AUTHORITY_REQUIRED")
        expected_hash = canonical_json_sha256(
            {
                "decision_at": decision_at.isoformat(),
                "format": "northstar.futures-contract-authority.v1",
                "master_publication_hash": master_publication_hash,
                "registry_publication_hash": registry_publication_hash,
            }
        )
        if self.authority_hash != expected_hash:
            _refuse("PORTFOLIO_RISK_AUTHORITY_CONTRACT_AUTHORITY_TAMPERED")
        object.__setattr__(self, "authority_id", authority_id)
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "authority_hash", expected_hash)
        object.__setattr__(self, "master_publication_hash", master_publication_hash)
        object.__setattr__(self, "registry_publication_hash", registry_publication_hash)

    @classmethod
    def from_replayed_authority(cls, value: object) -> "ContractAuthorityView":
        """Narrow a resolved authority object without importing its DB adapter."""

        if type(value) is cls:
            return value
        master_publication = getattr(value, "master_publication", None)
        registry_publication = getattr(value, "registry_publication", None)
        master = getattr(value, "master", None)
        registry = getattr(value, "registry", None)
        if (
            master_publication is None
            or registry_publication is None
            or master is None
            or registry is None
        ):
            _refuse("PORTFOLIO_RISK_AUTHORITY_CONTRACT_AUTHORITY_REQUIRED")
        authority_id = getattr(value, "authority_id", None)
        decision_at = getattr(value, "decision_at", None)
        authority_hash = getattr(value, "authority_hash", None)
        master_publication_hash = getattr(master_publication, "publication_hash", None)
        registry_publication_hash = getattr(registry_publication, "publication_hash", None)
        if (
            getattr(master_publication, "authority_id", None) != authority_id
            or getattr(registry_publication, "authority_id", None) != authority_id
            or getattr(registry_publication, "master_publication_hash", None)
            != master_publication_hash
            or getattr(master_publication, "master", None) is not master
            or getattr(registry_publication, "registry", None) is not registry
        ):
            _refuse("PORTFOLIO_RISK_AUTHORITY_CONTRACT_AUTHORITY_TAMPERED")
        normalized_decision_at = _time(
            decision_at,
            code="PORTFOLIO_RISK_AUTHORITY_CONTRACT_AUTHORITY_TAMPERED",
        )
        master_available_at = _time(
            getattr(master_publication, "available_at", None),
            code="PORTFOLIO_RISK_AUTHORITY_CONTRACT_AUTHORITY_TAMPERED",
        )
        registry_available_at = _time(
            getattr(registry_publication, "available_at", None),
            code="PORTFOLIO_RISK_AUTHORITY_CONTRACT_AUTHORITY_TAMPERED",
        )
        registry_effective_from = _time(
            getattr(registry_publication, "effective_from", None),
            code="PORTFOLIO_RISK_AUTHORITY_CONTRACT_AUTHORITY_TAMPERED",
        )
        registry_effective_until = getattr(registry_publication, "effective_until", None)
        if registry_effective_until is not None:
            registry_effective_until = _time(
                registry_effective_until,
                code="PORTFOLIO_RISK_AUTHORITY_CONTRACT_AUTHORITY_TAMPERED",
            )
        if (
            master_available_at > normalized_decision_at
            or registry_available_at > normalized_decision_at
            or registry_effective_from > normalized_decision_at
            or (
                registry_effective_until is not None
                and registry_effective_until <= normalized_decision_at
            )
        ):
            _refuse("PORTFOLIO_RISK_AUTHORITY_CONTRACT_AUTHORITY_TAMPERED")
        return cls(
            authority_id=cast(str, authority_id),
            decision_at=normalized_decision_at,
            authority_hash=cast(str, authority_hash),
            master_publication_hash=cast(str, master_publication_hash),
            registry_publication_hash=cast(str, registry_publication_hash),
            registry=cast(CtpContractRegistry, registry),
            master=master,
        )


def _canonical_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            _refuse("PORTFOLIO_RISK_AUTHORITY_BROKER_STATE_NONFINITE")
        return value
    if isinstance(value, datetime):
        return _time(value, code="PORTFOLIO_RISK_AUTHORITY_BROKER_STATE_TIME_INVALID").isoformat()
    if isinstance(value, tuple | list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            _refuse("PORTFOLIO_RISK_AUTHORITY_BROKER_STATE_KEY_INVALID")
        return {key: _canonical_value(item) for key, item in sorted(value.items())}
    _refuse("PORTFOLIO_RISK_AUTHORITY_BROKER_STATE_VALUE_INVALID")


def _canonical_sort(values: tuple[object, ...]) -> list[object]:
    return sorted(values, key=canonical_json_sha256)


def broker_state_hash(snapshot: BrokerStateSnapshot) -> str:
    """Hash every P5 broker-state field that can change authority semantics."""

    if type(snapshot) is not BrokerStateSnapshot:
        _refuse("PORTFOLIO_RISK_AUTHORITY_BROKER_STATE_REQUIRED")
    positions = tuple(
        {
            "symbol": item.symbol,
            "qty": item.qty,
            "avg_cost": item.avg_cost,
            "market_price": item.market_price,
            "market_value": item.market_value,
            "sellable_qty": item.sellable_qty,
            "account": item.account,
            "instrument_id": item.instrument_id,
            "exchange_id": item.exchange_id,
            "long_today_qty": item.long_today_qty,
            "long_yesterday_qty": item.long_yesterday_qty,
            "short_today_qty": item.short_today_qty,
            "short_yesterday_qty": item.short_yesterday_qty,
            "long_frozen_qty": item.long_frozen_qty,
            "short_frozen_qty": item.short_frozen_qty,
            "long_closable_qty": item.long_closable_qty,
            "short_closable_qty": item.short_closable_qty,
            "margin": item.margin,
            "realized_pnl": item.realized_pnl,
            "unrealized_pnl": item.unrealized_pnl,
            "asof": item.asof,
            "snapshot_batch_id": item.snapshot_batch_id,
        }
        for item in snapshot.positions
    )
    fills = tuple(
        {
            "broker_order_id": item.broker_order_id,
            "symbol": item.symbol,
            "qty": item.qty,
            "price": item.price,
            "side": item.side,
            "filled_at": item.filled_at,
            "account": item.account,
            "exec_id": item.exec_id,
            "order_ref": item.order_ref,
            "perm_id": item.perm_id,
            "client_id": item.client_id,
            "instrument_id": item.instrument_id,
            "exchange_id": item.exchange_id,
            "ctp_offset": item.ctp_offset,
        }
        for item in snapshot.fills
    )
    return canonical_json_sha256(
        _canonical_value(
            {
                "format": "northstar.execution-account-snapshot.v1",
                "positions": _canonical_sort(tuple(_canonical_value(item) for item in positions)),
                "open_orders": _canonical_sort(
                    tuple(_canonical_value(item) for item in snapshot.open_orders)
                ),
                "completed_orders": _canonical_sort(
                    tuple(_canonical_value(item) for item in snapshot.completed_orders)
                ),
                "fills": _canonical_sort(tuple(_canonical_value(item) for item in fills)),
                "account_values": _canonical_value(snapshot.account_values),
                "account": snapshot.account,
                "state_complete": snapshot.state_complete,
                "state_errors": list(snapshot.state_errors),
                "asof": snapshot.asof,
            }
        )
    )


@dataclass(frozen=True, slots=True)
class ReconciliationSafetyStateEvidence:
    """Verified persisted reconciliation safety state, scoped to one account.

    The constructor verifies the state-machine hash.  Candidate composition
    obtains this record through :meth:`from_persisted_record` immediately
    after a successful reconciliation; P8 then binds its immutable hash.
    """

    profile_id: str
    broker: str
    account_id: str
    state_snapshot: RiskStateSnapshot
    reconciliation_state_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "profile_id",
            _identifier(self.profile_id, code="PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_SCOPE_INVALID"),
        )
        broker = _identifier(
            self.broker,
            code="PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_SCOPE_INVALID",
        ).lower()
        object.__setattr__(self, "broker", broker)
        object.__setattr__(
            self,
            "account_id",
            _identifier(self.account_id, code="PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_SCOPE_INVALID"),
        )
        if type(self.state_snapshot) is not RiskStateSnapshot:
            _refuse("PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_STATE_INVALID")
        persisted_hash = _hash(
            self.reconciliation_state_hash,
            code="PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_HASH_INVALID",
        )
        if self.state_snapshot.state_hash != persisted_hash:
            _refuse("PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_TAMPERED")
        object.__setattr__(self, "reconciliation_state_hash", persisted_hash)

    @classmethod
    def from_persisted_record(cls, record: object) -> "ReconciliationSafetyStateEvidence":
        """Reconstruct and hash-verify a repository record without DB imports."""

        if record is None:
            _refuse("PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_MISSING")
        try:
            snapshot = RiskStateSnapshot(
                state=RiskState(str(getattr(record, "state"))),
                occurred_at=_time(
                    getattr(record, "occurred_at"),
                    code="PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_STATE_INVALID",
                ),
                reason=str(getattr(record, "reason")),
                predecessor_hash=getattr(record, "predecessor_hash"),
                recovery_approver_id=getattr(record, "recovery_approver_id"),
            )
        except (TypeError, ValueError) as exc:
            raise PortfolioRiskAuthorityError(
                "PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_STATE_INVALID"
            ) from exc
        return cls(
            profile_id=getattr(record, "profile_id"),
            broker=getattr(record, "broker"),
            account_id=getattr(record, "account"),
            state_snapshot=snapshot,
            reconciliation_state_hash=getattr(record, "state_hash"),
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "format": "northstar.reconciliation-safety-state-evidence.v1",
            "profile_id": self.profile_id,
            "broker": self.broker,
            "account_id": self.account_id,
            "state": self.state_snapshot.state.value,
            "occurred_at": self.state_snapshot.occurred_at.astimezone(UTC).isoformat(),
            "reason": self.state_snapshot.reason,
            "predecessor_hash": self.state_snapshot.predecessor_hash,
            "recovery_approver_id": self.state_snapshot.recovery_approver_id,
            "reconciliation_state_hash": self.reconciliation_state_hash,
        }


@dataclass(frozen=True, slots=True)
class PortfolioRiskAuthorityExecutionRule:
    """One profile-owned CTP-sim contract rule included in authority replay."""

    symbol: str
    instrument_id: str
    product_id: str
    exchange_id: str
    volume_multiple: int
    margin_rate: float
    max_position_lots: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "symbol",
            _identifier(self.symbol, code="PORTFOLIO_RISK_AUTHORITY_CONTRACT_INVALID").upper(),
        )
        object.__setattr__(
            self,
            "instrument_id",
            _identifier(self.instrument_id, code="PORTFOLIO_RISK_AUTHORITY_CONTRACT_INVALID").lower(),
        )
        object.__setattr__(
            self,
            "product_id",
            _identifier(self.product_id, code="PORTFOLIO_RISK_AUTHORITY_CONTRACT_INVALID").lower(),
        )
        object.__setattr__(
            self,
            "exchange_id",
            _identifier(self.exchange_id, code="PORTFOLIO_RISK_AUTHORITY_CONTRACT_INVALID").upper(),
        )
        if (
            isinstance(self.volume_multiple, bool)
            or not isinstance(self.volume_multiple, int)
            or self.volume_multiple <= 0
        ):
            _refuse("PORTFOLIO_RISK_AUTHORITY_CONTRACT_INVALID")
        if (
            isinstance(self.margin_rate, bool)
            or not isinstance(self.margin_rate, (int, float))
            or not math.isfinite(self.margin_rate)
            or self.margin_rate <= 0
            or self.margin_rate > 1
        ):
            _refuse("PORTFOLIO_RISK_AUTHORITY_CONTRACT_INVALID")
        if (
            isinstance(self.max_position_lots, bool)
            or not isinstance(self.max_position_lots, int)
            or self.max_position_lots <= 0
        ):
            _refuse("PORTFOLIO_RISK_AUTHORITY_CONTRACT_INVALID")
        object.__setattr__(self, "margin_rate", float(self.margin_rate))

    def as_mapping(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "instrument_id": self.instrument_id,
            "product_id": self.product_id,
            "exchange_id": self.exchange_id,
            "volume_multiple": self.volume_multiple,
            "margin_rate": self.margin_rate,
            "max_position_lots": self.max_position_lots,
        }


@dataclass(frozen=True, slots=True)
class PortfolioRiskApprovalAuthority:
    """All trusted source facts reconstructed before P3 approval replay."""

    profile_id: str
    policy_id: str
    policy_version: str
    authority_id: str
    config_hash: str
    policy_hash: str
    contract_authority_hash: str
    broker_state_hash: str
    reconciliation_state_hash: str
    review_request: PortfolioRiskReviewRequest
    execution_rules: tuple[PortfolioRiskAuthorityExecutionRule, ...]
    authority_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ("profile_id", "policy_id", "policy_version", "authority_id"):
            object.__setattr__(
                self,
                name,
                _identifier(
                    getattr(self, name),
                    code="PORTFOLIO_RISK_AUTHORITY_IDENTITY_INVALID",
                ),
            )
        for name in (
            "config_hash",
            "policy_hash",
            "contract_authority_hash",
            "broker_state_hash",
            "reconciliation_state_hash",
        ):
            object.__setattr__(
                self,
                name,
                _hash(
                    getattr(self, name),
                    code="PORTFOLIO_RISK_AUTHORITY_HASH_INVALID",
                ),
            )
        if type(self.review_request) is not PortfolioRiskReviewRequest:
            _refuse("PORTFOLIO_RISK_AUTHORITY_REVIEW_INVALID")
        policy = self.review_request.policy
        if (
            policy.policy_id != self.policy_id
            or policy.policy_version != self.policy_version
            or policy.authority_id != self.authority_id
            or policy.policy_hash != self.policy_hash
        ):
            _refuse("PORTFOLIO_RISK_AUTHORITY_POLICY_MISMATCH")
        if (
            not isinstance(self.execution_rules, tuple)
            or not self.execution_rules
            or not all(type(item) is PortfolioRiskAuthorityExecutionRule for item in self.execution_rules)
        ):
            _refuse("PORTFOLIO_RISK_AUTHORITY_CONTRACT_RULES_INVALID")
        rules = tuple(sorted(self.execution_rules, key=lambda item: item.symbol))
        if len({item.symbol for item in rules}) != len(rules):
            _refuse("PORTFOLIO_RISK_AUTHORITY_CONTRACT_RULES_DUPLICATED")
        expected_symbols = {
            position.instrument_id.strip().upper()
            for position in self.review_request.composition.portfolio_target.positions
        }
        if {item.symbol for item in rules} != expected_symbols:
            _refuse("PORTFOLIO_RISK_AUTHORITY_CONTRACT_COVERAGE_MISMATCH")
        object.__setattr__(self, "execution_rules", rules)
        object.__setattr__(
            self,
            "authority_hash",
            canonical_json_sha256(self.as_mapping(include_hash=False)),
        )

    def as_mapping(self, *, include_hash: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "format": "northstar.portfolio-risk-approval-authority.v1",
            "profile_id": self.profile_id,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "authority_id": self.authority_id,
            "config_hash": self.config_hash,
            "policy_hash": self.policy_hash,
            "contract_authority_hash": self.contract_authority_hash,
            "broker_state_hash": self.broker_state_hash,
            "reconciliation_state_hash": self.reconciliation_state_hash,
            "review_request": self.review_request.as_mapping(),
            "execution_rules": [item.as_mapping() for item in self.execution_rules],
        }
        if include_hash:
            result["authority_hash"] = self.authority_hash
        return result


def _authoritative_account_number(
    snapshot: BrokerStateSnapshot,
    *,
    field_name: str,
    positive: bool,
) -> float:
    value = snapshot.account_values.get(field_name)
    if value is None:
        _refuse(f"PORTFOLIO_RISK_AUTHORITY_{field_name.upper()}_MISSING")
    if isinstance(value, bool):
        _refuse(f"PORTFOLIO_RISK_AUTHORITY_{field_name.upper()}_INVALID")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise PortfolioRiskAuthorityError(
            f"PORTFOLIO_RISK_AUTHORITY_{field_name.upper()}_INVALID"
        ) from exc
    if not math.isfinite(parsed) or (parsed <= 0 if positive else parsed < 0):
        _refuse(f"PORTFOLIO_RISK_AUTHORITY_{field_name.upper()}_INVALID")
    return parsed


def _policy_from_config(
    *,
    config: ProfilePortfolioRiskApprovalConfig,
    authority_id: str,
) -> PortfolioRiskPolicy:
    try:
        limits = RiskLimitSet(
            per_contract=config.limits.per_contract,
            per_commodity=config.limits.per_commodity,
            per_sector=config.limits.per_sector,
            per_exchange=config.limits.per_exchange,
            per_strategy=config.limits.per_strategy,
            per_account=config.limits.per_account,
            gross_leverage=config.limits.gross_leverage,
            net_leverage=config.limits.net_leverage,
            margin_utilization=config.limits.margin_utilization,
        )
        scenario_limits = tuple(
            StressScenarioLimit(
                scenario=StressScenario(
                    scenario_id=item.scenario_id,
                    kind=ScenarioKind(item.kind),
                    shock_fraction=item.shock_fraction,
                ),
                max_loss_fraction=item.max_loss_fraction,
                max_margin_utilization=item.max_margin_utilization,
            )
            for item in config.scenarios
        )
        stress_policy = PortfolioStressPolicy(scenario_limits)
        return PortfolioRiskPolicy(
            policy_id=config.policy_id,
            policy_version=config.policy_version,
            authority_id=authority_id,
            limits=limits,
            stress_policy=stress_policy,
            max_input_age_seconds=config.max_input_age_seconds,
        )
    except (TypeError, ValueError) as exc:
        raise PortfolioRiskAuthorityError(
            "PORTFOLIO_RISK_AUTHORITY_POLICY_CONFIG_INVALID"
        ) from exc


class PortfolioRiskAuthorityResolver:
    """Derive a trusted P3 review request from profile + P5 source evidence."""

    __slots__ = ()

    def resolve(
        self,
        *,
        profile: TradingProfile,
        broker_state: BrokerStateSnapshot,
        reconciliation_safety_state: ReconciliationSafetyStateEvidence,
        composition: PortfolioCompositionEvidence,
        evaluated_at: datetime,
        contract_authority: object,
    ) -> PortfolioRiskApprovalAuthority:
        if type(profile) is not TradingProfile:
            _refuse("PORTFOLIO_RISK_AUTHORITY_PROFILE_REQUIRED")
        if type(broker_state) is not BrokerStateSnapshot:
            _refuse("PORTFOLIO_RISK_AUTHORITY_BROKER_STATE_REQUIRED")
        if type(reconciliation_safety_state) is not ReconciliationSafetyStateEvidence:
            _refuse("PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_REQUIRED")
        if type(composition) is not PortfolioCompositionEvidence:
            _refuse("PORTFOLIO_RISK_AUTHORITY_COMPOSITION_REQUIRED")
        contract_authority = ContractAuthorityView.from_replayed_authority(
            contract_authority
        )
        if contract_authority.registry.broker != "ctp_sim":
            _refuse("PORTFOLIO_RISK_AUTHORITY_CONTRACT_AUTHORITY_BROKER_MISMATCH")
        evaluated_at = _time(
            evaluated_at,
            code="PORTFOLIO_RISK_AUTHORITY_EVALUATED_AT_INVALID",
        )
        config = profile.portfolio_risk_approval
        if config is None:
            _refuse("PORTFOLIO_RISK_AUTHORITY_CONFIG_MISSING")
        if type(config) is not ProfilePortfolioRiskApprovalConfig:
            _refuse("PORTFOLIO_RISK_AUTHORITY_CONFIG_INVALID")
        assert type(config) is ProfilePortfolioRiskApprovalConfig
        if config.policy_version != profile.versions.risk_policy:
            _refuse("PORTFOLIO_RISK_AUTHORITY_POLICY_VERSION_MISMATCH")

        snapshot_account = _identifier(
            broker_state.account,
            code="PORTFOLIO_RISK_AUTHORITY_BROKER_ACCOUNT_INVALID",
        )
        if broker_state.state_complete is not True or broker_state.state_errors:
            _refuse("PORTFOLIO_RISK_AUTHORITY_BROKER_STATE_INCOMPLETE")
        snapshot_at = _time(
            broker_state.asof,
            code="PORTFOLIO_RISK_AUTHORITY_BROKER_STATE_TIME_INVALID",
        )
        futures = profile.futures
        if (
            futures is None
            or futures.contract_authority_id != contract_authority.authority_id
            or contract_authority.decision_at != snapshot_at
        ):
            _refuse("PORTFOLIO_RISK_AUTHORITY_CONTRACT_AUTHORITY_MISMATCH")
        maximum_age = timedelta(seconds=config.max_input_age_seconds)
        if snapshot_at > evaluated_at:
            _refuse("PORTFOLIO_RISK_AUTHORITY_BROKER_STATE_FUTURE")
        if evaluated_at - snapshot_at > maximum_age:
            _refuse("PORTFOLIO_RISK_AUTHORITY_BROKER_STATE_STALE")
        equity = _authoritative_account_number(
            broker_state,
            field_name="NetLiquidation",
            positive=True,
        )
        margin_capacity = _authoritative_account_number(
            broker_state,
            field_name="AvailableFunds",
            positive=False,
        )

        safety = reconciliation_safety_state
        if (
            safety.profile_id != profile.profile_id
            or safety.broker != "ctp_sim"
            or safety.account_id != snapshot_account
        ):
            _refuse("PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_SCOPE_MISMATCH")
        if safety.state_snapshot.state is not RiskState.NORMAL:
            _refuse("PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_NOT_NORMAL")
        safety_at = _time(
            safety.state_snapshot.occurred_at,
            code="PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_STATE_INVALID",
        )
        if safety_at > snapshot_at or safety_at > evaluated_at:
            _refuse("PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_FUTURE")

        # ``occurred_at`` is the immutable risk-state transition time, not a
        # recurring heartbeat.  P5 deliberately writes one initial NORMAL
        # transition and the state machine forbids NORMAL -> NORMAL.  A fresh,
        # successfully reconciled broker snapshot is therefore the only safe
        # availability/freshness anchor for a long-lived NORMAL state.  The
        # immutable transition must still precede that exact snapshot, and the
        # snapshot itself is bounded above by ``maximum_age``.
        reconciliation_observed_at = snapshot_at

        authority_id = f"p8:{profile.profile_id}:{config.config_hash}"
        policy = _policy_from_config(config=config, authority_id=authority_id)
        registry = contract_authority.registry
        source_expires_at = snapshot_at + maximum_age
        instruments: list[PortfolioRiskInstrumentSnapshot] = []
        execution_rules: list[PortfolioRiskAuthorityExecutionRule] = []
        try:
            target_positions = composition.portfolio_target.positions
            for position in target_positions:
                symbol = _identifier(
                    position.instrument_id,
                    code="PORTFOLIO_RISK_AUTHORITY_CONTRACT_INVALID",
                ).upper()
                mapping = registry.resolve_data_symbol(symbol)
                taxonomy = config.taxonomy_for(mapping.product_id)
                position_limit = config.ctp_sim_execution_rule_for(mapping.product_id)
                resolution = contract_authority.master.resolve_for_execution(
                    symbol,
                    decision_at=snapshot_at,
                )
                _contract, authority_rule = contract_authority.master.require_execution_contract(
                    resolution
                )
                rule_expires_at = (
                    min(source_expires_at, authority_rule.effective_until)
                    if authority_rule.effective_until is not None
                    else source_expires_at
                )
                instruments.append(
                    PortfolioRiskInstrumentSnapshot(
                        instrument_id=position.instrument_id,
                        commodity_id=taxonomy.commodity_id,
                        sector_id=taxonomy.sector_id,
                        exchange_id=mapping.exchange_id,
                        correlation_cluster_id=taxonomy.correlation_cluster_id,
                        margin_fraction=authority_rule.initial_margin_rate,
                        observed_at=authority_rule.observed_at,
                        available_at=authority_rule.available_at,
                        expires_at=rule_expires_at,
                    )
                )
                execution_rules.append(
                    PortfolioRiskAuthorityExecutionRule(
                        symbol=symbol,
                        instrument_id=mapping.instrument_id,
                        product_id=mapping.product_id,
                        exchange_id=mapping.exchange_id,
                        volume_multiple=mapping.volume_multiple,
                        margin_rate=authority_rule.initial_margin_rate,
                        max_position_lots=position_limit.max_position_lots,
                    )
                )
        except (
            CtpContractMappingError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise PortfolioRiskAuthorityError(
                "PORTFOLIO_RISK_AUTHORITY_CONTRACT_CONFIG_MISMATCH"
            ) from exc

        try:
            review_request = PortfolioRiskReviewRequest(
                composition=composition,
                account_snapshot=PortfolioRiskAccountSnapshot(
                    account_id=snapshot_account,
                    equity=equity,
                    margin_capacity=margin_capacity,
                    observed_at=snapshot_at,
                    available_at=snapshot_at,
                    expires_at=source_expires_at,
                ),
                instrument_snapshots=tuple(instruments),
                risk_state=AccountScopedRiskStateEvidence(
                    account_id=snapshot_account,
                    state_snapshot=safety.state_snapshot,
                    observed_at=reconciliation_observed_at,
                    available_at=reconciliation_observed_at,
                    expires_at=reconciliation_observed_at + maximum_age,
                ),
                policy=policy,
                evaluated_at=evaluated_at,
            )
        except ValueError as exc:
            raise PortfolioRiskAuthorityError(
                "PORTFOLIO_RISK_AUTHORITY_REVIEW_INPUT_INVALID"
            ) from exc
        return PortfolioRiskApprovalAuthority(
            profile_id=profile.profile_id,
            policy_id=config.policy_id,
            policy_version=config.policy_version,
            authority_id=authority_id,
            config_hash=config.config_hash,
            policy_hash=policy.policy_hash,
            contract_authority_hash=contract_authority.authority_hash,
            broker_state_hash=broker_state_hash(broker_state),
            reconciliation_state_hash=safety.reconciliation_state_hash,
            review_request=review_request,
            execution_rules=tuple(execution_rules),
        )

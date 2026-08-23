"""P8-WP04 non-submitting execution provenance preflight.

This application-owned composition boundary replays the P2 candidate-to-P3
activation evidence, binds it to a P3 risk approval, and then constructs the
P5 execution plan and preflight *itself*.  Callers therefore cannot satisfy
this boundary with a handwritten activation hash, synthetic target,
``ExecutionPlan``, or ``PreflightResult``.

The resulting receipt is evidence only.  It deliberately carries no broker,
router, submit, cancel, recovery, database, or live-trading capability.  A
future final CTP-sim submission gate must revalidate its short-lived contents
immediately before any broker action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
import math
import re
from typing import Literal

import polars as pl

from northstar_quant.application.research_strategy_activation import (
    ResearchStrategyActivationReceipt,
    ResearchStrategyActivationRequest,
    ResearchStrategyTargetActivator,
)
from northstar_quant.application.portfolio_risk_authority import (
    PortfolioRiskApprovalAuthority,
    PortfolioRiskAuthorityError,
    PortfolioRiskAuthorityResolver,
    ReconciliationSafetyStateEvidence,
    broker_state_hash as trusted_broker_state_hash,
)
from northstar_quant.data_platform.artifacts.fingerprints import canonical_json_sha256
from northstar_quant.platform.config.settings import Settings
from northstar_quant.platform.config.trading_profile import TradingProfile
from northstar_quant.portfolio_risk.portfolio import (
    ApprovedPortfolioTarget,
    PortfolioTarget,
)
from northstar_quant.portfolio_risk.portfolio.approval import (
    PortfolioRiskApprovalError,
    PortfolioRiskApprovalEvidence,
    PortfolioRiskApprovalGate,
    PortfolioRiskApprovalRequest,
)
from northstar_quant.trading_execution.broker.ctp_contract_mapping import (
    CtpContractRegistry,
    load_ctp_contract_registry,
)
from northstar_quant.trading_execution.execution import (
    ExecutionPlan,
    build_approved_execution_plan,
)
from northstar_quant.trading_execution.execution.models import (
    BrokerStateSnapshot,
    FuturesExecutionRule,
    MarketQuoteSnapshot,
    RebalanceOrderPlan,
)
from northstar_quant.trading_execution.execution.pricing import (
    execution_reference_price_from_quote,
)
from northstar_quant.trading_execution.live.preflight import (
    PreflightResult,
    build_preflight_result,
)
from northstar_quant.trading_execution.live.runtime_risk import (
    RuntimeRiskAssessment,
    assess_runtime_risk,
)


__all__ = [
    "AccountAttributionAlert",
    "AccountAttributionEvidence",
    "ExecutionContractRuleEvidence",
    "ExecutionDataEvidence",
    "ExecutionOrderCommitment",
    "ExecutionProvenanceEnvironment",
    "ExecutionProvenancePreflight",
    "ExecutionProvenancePreflightError",
    "ExecutionProvenancePreflightReceipt",
    "ExecutionProvenanceRequest",
]


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_TEXT_LENGTH = 512


class ExecutionProvenancePreflightError(ValueError):
    """Raised when evidence cannot safely reach the P5 preflight boundary."""


class ExecutionProvenanceEnvironment(str, Enum):
    """The only execution evidence environment accepted in this work package."""

    CTP_SIM = "ctp_sim"


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value.strip()) is None:
        raise ExecutionProvenancePreflightError(f"{field_name} must be a non-empty identifier")
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
        raise ExecutionProvenancePreflightError(
            f"{field_name} must be non-empty, single-line text of at most {_MAX_TEXT_LENGTH} characters"
        )
    return value.strip()


def _hash(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ExecutionProvenancePreflightError(f"{field_name} must be a lowercase SHA-256")
    return value


def _time(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionProvenancePreflightError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _finite(value: object, field_name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ExecutionProvenancePreflightError(f"{field_name} must be a finite number")
    parsed = float(value)
    if positive and parsed <= 0:
        raise ExecutionProvenancePreflightError(f"{field_name} must be positive")
    return parsed


def _account_number(snapshot: BrokerStateSnapshot, *keys: str) -> float | None:
    for key in keys:
        value = snapshot.account_values.get(key)
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed):
            return parsed
    return None


def _canonical_value(value: object) -> object:
    """Return JSON-safe evidence without silently accepting non-finite inputs."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ExecutionProvenancePreflightError("evidence cannot contain non-finite floating values")
        return value
    if isinstance(value, datetime):
        return _time(value, "evidence timestamp").isoformat()
    if isinstance(value, tuple | list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ExecutionProvenancePreflightError("evidence mapping keys must be strings")
        return {key: _canonical_value(item) for key, item in sorted(value.items())}
    raise ExecutionProvenancePreflightError(
        f"evidence contains unsupported value type {type(value).__name__}"
    )


def _canonical_sort(values: tuple[object, ...]) -> list[object]:
    return sorted(values, key=canonical_json_sha256)


@dataclass(frozen=True, slots=True)
class ExecutionDataEvidence:
    """PIT metadata for the runtime market/signal/output inputs.

    This captures identity and timing only; it does not turn P2's static
    reproducibility evidence into a live-data authorization.
    """

    profile_id: str
    dataset_id: str
    data_source: str
    content_sha256: str
    raw_market_as_of: datetime
    signal_market_as_of: datetime
    target_output_as_of: datetime
    manifest_version: Literal["data_manifest_v3"] = "data_manifest_v3"

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _identifier(self.profile_id, "profile_id"))
        object.__setattr__(self, "dataset_id", _identifier(self.dataset_id, "dataset_id"))
        object.__setattr__(self, "data_source", _identifier(self.data_source, "data_source"))
        object.__setattr__(self, "content_sha256", _hash(self.content_sha256, "content_sha256"))
        raw_market_as_of = _time(self.raw_market_as_of, "raw_market_as_of")
        signal_market_as_of = _time(self.signal_market_as_of, "signal_market_as_of")
        target_output_as_of = _time(self.target_output_as_of, "target_output_as_of")
        if not raw_market_as_of <= signal_market_as_of <= target_output_as_of:
            raise ExecutionProvenancePreflightError(
                "runtime market, signal, and target-output evidence must be time ordered"
            )
        if self.manifest_version != "data_manifest_v3":
            raise ExecutionProvenancePreflightError("manifest_version must be data_manifest_v3")
        object.__setattr__(self, "raw_market_as_of", raw_market_as_of)
        object.__setattr__(self, "signal_market_as_of", signal_market_as_of)
        object.__setattr__(self, "target_output_as_of", target_output_as_of)

    @property
    def evidence_hash(self) -> str:
        return canonical_json_sha256(self.as_mapping())

    def as_mapping(self) -> dict[str, object]:
        return {
            "format": "northstar.execution-data-evidence.v1",
            "profile_id": self.profile_id,
            "dataset_id": self.dataset_id,
            "data_source": self.data_source,
            "content_sha256": self.content_sha256,
            "manifest_version": self.manifest_version,
            "raw_market_as_of": self.raw_market_as_of.isoformat(),
            "signal_market_as_of": self.signal_market_as_of.isoformat(),
            "target_output_as_of": self.target_output_as_of.isoformat(),
        }

    def as_preflight_manifest(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "dataset_id": self.dataset_id,
            "data_source": self.data_source,
            "content_sha256": self.content_sha256,
            "manifest_version": self.manifest_version,
            "live_trading_eligible": False,
        }


@dataclass(frozen=True, slots=True)
class AccountAttributionAlert:
    """One account-attribution alert supplied as hash-bound evidence only."""

    tag: str
    message: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "tag", _text(self.tag, "tag"))
        object.__setattr__(self, "message", _text(self.message, "message"))

    def as_mapping(self) -> dict[str, str]:
        return {"tag": self.tag, "message": self.message}


@dataclass(frozen=True, slots=True)
class AccountAttributionEvidence:
    """Account-health evidence needed by the existing P5 preflight."""

    account: str
    observed_at: datetime
    alerts: tuple[AccountAttributionAlert, ...] = ()

    def __post_init__(self) -> None:
        account = _identifier(self.account, "account")
        observed_at = _time(self.observed_at, "account attribution observed_at")
        if not isinstance(self.alerts, tuple) or not all(
            type(alert) is AccountAttributionAlert for alert in self.alerts
        ):
            raise ExecutionProvenancePreflightError(
                "alerts must be an AccountAttributionAlert tuple"
            )
        alerts = tuple(sorted(self.alerts, key=lambda alert: (alert.tag, alert.message)))
        if len({(alert.tag, alert.message) for alert in alerts}) != len(alerts):
            raise ExecutionProvenancePreflightError("account attribution alerts cannot be duplicated")
        object.__setattr__(self, "account", account)
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "alerts", alerts)

    @property
    def evidence_hash(self) -> str:
        return canonical_json_sha256(self.as_mapping())

    def as_mapping(self) -> dict[str, object]:
        return {
            "format": "northstar.account-attribution-evidence.v1",
            "account": self.account,
            "observed_at": self.observed_at.isoformat(),
            "alerts": [alert.as_mapping() for alert in self.alerts],
        }

    def as_preflight_mapping(self) -> dict[str, object]:
        return {"alert_items": [alert.as_mapping() for alert in self.alerts]}


@dataclass(frozen=True, slots=True)
class ExecutionContractRuleEvidence:
    """A time-bounded, concrete futures-contract rule snapshot."""

    symbol: str
    instrument_id: str
    exchange_id: str
    volume_multiple: int
    rule: FuturesExecutionRule
    available_at: datetime
    effective_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        symbol = _identifier(self.symbol, "contract rule symbol").upper()
        instrument_id = _identifier(self.instrument_id, "contract rule instrument_id").lower()
        exchange_id = _identifier(self.exchange_id, "contract rule exchange_id").upper()
        if isinstance(self.volume_multiple, bool) or not isinstance(self.volume_multiple, int) or self.volume_multiple <= 0:
            raise ExecutionProvenancePreflightError("volume_multiple must be a positive integer")
        if type(self.rule) is not FuturesExecutionRule:
            raise ExecutionProvenancePreflightError("rule must be a FuturesExecutionRule")
        margin_rate = _finite(self.rule.margin_rate, "contract rule margin_rate", positive=True)
        if margin_rate > 1:
            raise ExecutionProvenancePreflightError("contract rule margin_rate cannot exceed one")
        if self.rule.max_position_lots is not None and (
            isinstance(self.rule.max_position_lots, bool)
            or not isinstance(self.rule.max_position_lots, int)
            or self.rule.max_position_lots <= 0
        ):
            raise ExecutionProvenancePreflightError(
                "contract rule max_position_lots must be a positive integer or None"
            )
        available_at = _time(self.available_at, "contract rule available_at")
        effective_at = _time(self.effective_at, "contract rule effective_at")
        expires_at = _time(self.expires_at, "contract rule expires_at")
        if not available_at <= effective_at < expires_at:
            raise ExecutionProvenancePreflightError(
                "contract rule availability and validity window are invalid"
            )
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "instrument_id", instrument_id)
        object.__setattr__(self, "exchange_id", exchange_id)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "effective_at", effective_at)
        object.__setattr__(self, "expires_at", expires_at)

    @property
    def evidence_hash(self) -> str:
        return canonical_json_sha256(self.as_mapping())

    def as_mapping(self) -> dict[str, object]:
        return {
            "format": "northstar.execution-contract-rule-evidence.v1",
            "symbol": self.symbol,
            "instrument_id": self.instrument_id,
            "exchange_id": self.exchange_id,
            "volume_multiple": self.volume_multiple,
            "margin_rate": float(self.rule.margin_rate),
            "max_position_lots": self.rule.max_position_lots,
            "available_at": self.available_at.isoformat(),
            "effective_at": self.effective_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ExecutionProvenanceRequest:
    """All evidence the non-submitting preflight must replay from source contracts."""

    preflight_id: str
    environment: ExecutionProvenanceEnvironment
    profile: TradingProfile
    settings: Settings
    activation_requests: tuple[ResearchStrategyActivationRequest, ...]
    activation_receipts: tuple[ResearchStrategyActivationReceipt, ...]
    portfolio_risk_approval_request: PortfolioRiskApprovalRequest
    portfolio_risk_approval_evidence: PortfolioRiskApprovalEvidence
    portfolio_risk_authority: PortfolioRiskApprovalAuthority
    reconciliation_safety_state: ReconciliationSafetyStateEvidence
    data_evidence: ExecutionDataEvidence
    account_snapshot: BrokerStateSnapshot
    account_attribution: AccountAttributionEvidence
    quotes: tuple[MarketQuoteSnapshot, ...]
    contract_rules: tuple[ExecutionContractRuleEvidence, ...]
    plan_id: str
    market_snapshot_at: datetime
    plan_created_at: datetime
    checked_at: datetime

    def __post_init__(self) -> None:
        preflight_id = _identifier(self.preflight_id, "preflight_id")
        if type(self.environment) is not ExecutionProvenanceEnvironment:
            raise ExecutionProvenancePreflightError("environment must be an ExecutionProvenanceEnvironment")
        if type(self.profile) is not TradingProfile:
            raise ExecutionProvenancePreflightError("profile must be a TradingProfile")
        if type(self.settings) is not Settings:
            raise ExecutionProvenancePreflightError("settings must be a Settings instance")
        if not isinstance(self.activation_requests, tuple) or not self.activation_requests or not all(
            type(item) is ResearchStrategyActivationRequest for item in self.activation_requests
        ):
            raise ExecutionProvenancePreflightError(
                "activation_requests must be a non-empty ResearchStrategyActivationRequest tuple"
            )
        if not isinstance(self.activation_receipts, tuple) or not self.activation_receipts or not all(
            type(item) is ResearchStrategyActivationReceipt for item in self.activation_receipts
        ):
            raise ExecutionProvenancePreflightError(
                "activation_receipts must be a non-empty ResearchStrategyActivationReceipt tuple"
            )
        if len(self.activation_requests) != len(self.activation_receipts):
            raise ExecutionProvenancePreflightError(
                "activation requests and claimed receipts must have equal cardinality"
            )
        if type(self.portfolio_risk_approval_request) is not PortfolioRiskApprovalRequest:
            raise ExecutionProvenancePreflightError(
                "portfolio_risk_approval_request must be a PortfolioRiskApprovalRequest"
            )
        if type(self.portfolio_risk_approval_evidence) is not PortfolioRiskApprovalEvidence:
            raise ExecutionProvenancePreflightError(
                "portfolio_risk_approval_evidence must be a PortfolioRiskApprovalEvidence"
            )
        if type(self.portfolio_risk_authority) is not PortfolioRiskApprovalAuthority:
            raise ExecutionProvenancePreflightError(
                "portfolio_risk_authority must be a PortfolioRiskApprovalAuthority"
            )
        if type(self.reconciliation_safety_state) is not ReconciliationSafetyStateEvidence:
            raise ExecutionProvenancePreflightError(
                "reconciliation_safety_state must be a ReconciliationSafetyStateEvidence"
            )
        if type(self.data_evidence) is not ExecutionDataEvidence:
            raise ExecutionProvenancePreflightError("data_evidence must be an ExecutionDataEvidence")
        if type(self.account_snapshot) is not BrokerStateSnapshot:
            raise ExecutionProvenancePreflightError("account_snapshot must be a BrokerStateSnapshot")
        if type(self.account_attribution) is not AccountAttributionEvidence:
            raise ExecutionProvenancePreflightError(
                "account_attribution must be an AccountAttributionEvidence"
            )
        if not isinstance(self.quotes, tuple) or not self.quotes or not all(
            type(item) is MarketQuoteSnapshot for item in self.quotes
        ):
            raise ExecutionProvenancePreflightError(
                "quotes must be a non-empty MarketQuoteSnapshot tuple"
            )
        if not isinstance(self.contract_rules, tuple) or not self.contract_rules or not all(
            type(item) is ExecutionContractRuleEvidence for item in self.contract_rules
        ):
            raise ExecutionProvenancePreflightError(
                "contract_rules must be a non-empty ExecutionContractRuleEvidence tuple"
            )
        plan_id = _identifier(self.plan_id, "plan_id")
        market_snapshot_at = _time(self.market_snapshot_at, "market_snapshot_at")
        plan_created_at = _time(self.plan_created_at, "plan_created_at")
        checked_at = _time(self.checked_at, "checked_at")
        if not market_snapshot_at <= plan_created_at <= checked_at:
            raise ExecutionProvenancePreflightError(
                "market snapshot, plan creation, and preflight check must be time ordered"
            )
        object.__setattr__(self, "preflight_id", preflight_id)
        object.__setattr__(self, "plan_id", plan_id)
        object.__setattr__(self, "market_snapshot_at", market_snapshot_at)
        object.__setattr__(self, "plan_created_at", plan_created_at)
        object.__setattr__(self, "checked_at", checked_at)


@dataclass(frozen=True, slots=True)
class ExecutionOrderCommitment:
    """Hash-bound, non-submit representation of one replayed execution-plan row."""

    symbol: str
    side: str
    qty: float
    reference_price: float
    instrument_id: str
    exchange_id: str
    ctp_offset: str
    volume_multiple: int
    margin_rate: float
    required_margin: float
    order_hash: str = field(init=False)

    def __post_init__(self) -> None:
        symbol = _identifier(self.symbol, "order symbol").upper()
        side = _identifier(self.side, "order side").upper()
        if side not in {"BUY", "SELL"}:
            raise ExecutionProvenancePreflightError("order side must be BUY or SELL")
        qty = _finite(self.qty, "order qty", positive=True)
        reference_price = _finite(self.reference_price, "order reference_price", positive=True)
        instrument_id = _identifier(self.instrument_id, "order instrument_id").lower()
        exchange_id = _identifier(self.exchange_id, "order exchange_id").upper()
        ctp_offset = _identifier(self.ctp_offset, "order ctp_offset").lower()
        if ctp_offset not in {"open", "close", "close_today", "close_yesterday"}:
            raise ExecutionProvenancePreflightError("order ctp_offset is invalid")
        if isinstance(self.volume_multiple, bool) or not isinstance(self.volume_multiple, int) or self.volume_multiple <= 0:
            raise ExecutionProvenancePreflightError("order volume_multiple must be a positive integer")
        margin_rate = _finite(self.margin_rate, "order margin_rate", positive=True)
        required_margin = _finite(self.required_margin, "order required_margin")
        if required_margin < 0:
            raise ExecutionProvenancePreflightError("order required_margin cannot be negative")
        payload = {
            "format": "northstar.execution-order-commitment.v1",
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "reference_price": reference_price,
            "instrument_id": instrument_id,
            "exchange_id": exchange_id,
            "ctp_offset": ctp_offset,
            "volume_multiple": self.volume_multiple,
            "margin_rate": margin_rate,
            "required_margin": required_margin,
        }
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "qty", qty)
        object.__setattr__(self, "reference_price", reference_price)
        object.__setattr__(self, "instrument_id", instrument_id)
        object.__setattr__(self, "exchange_id", exchange_id)
        object.__setattr__(self, "ctp_offset", ctp_offset)
        object.__setattr__(self, "margin_rate", margin_rate)
        object.__setattr__(self, "required_margin", required_margin)
        object.__setattr__(self, "order_hash", canonical_json_sha256(payload))

    def as_mapping(self) -> dict[str, object]:
        return {
            "format": "northstar.execution-order-commitment.v1",
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "reference_price": self.reference_price,
            "instrument_id": self.instrument_id,
            "exchange_id": self.exchange_id,
            "ctp_offset": self.ctp_offset,
            "volume_multiple": self.volume_multiple,
            "margin_rate": self.margin_rate,
            "required_margin": self.required_margin,
            "order_hash": self.order_hash,
        }


@dataclass(frozen=True, slots=True)
class ExecutionProvenancePreflightReceipt:
    """Immutable evidence result; intentionally not a trading authorization."""

    preflight_id: str
    environment: ExecutionProvenanceEnvironment
    profile_id: str
    plan_id: str
    checked_at: datetime
    valid_until: datetime
    activation_hashes: tuple[str, ...]
    portfolio_target_hash: str
    approved_target_hash: str
    composition_evidence_hash: str
    portfolio_risk_approval_evidence_hash: str
    risk_evidence_hash: str
    data_evidence_hash: str
    account_snapshot_hash: str
    portfolio_risk_authority_hash: str
    portfolio_risk_policy_hash: str
    broker_state_hash: str
    reconciliation_state_hash: str
    account_attribution_hash: str
    quote_evidence_hash: str
    contract_rule_evidence_hash: str
    runtime_risk_hash: str
    preflight_hash: str
    plan_hash: str
    order_commitments: tuple[ExecutionOrderCommitment, ...]
    receipt_hash: str = field(init=False)
    eligible_for_ctp_sim: Literal[False] = field(default=False, init=False)
    eligible_for_trading: Literal[False] = field(default=False, init=False)
    eligible_for_live: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        preflight_id = _identifier(self.preflight_id, "preflight_id")
        if type(self.environment) is not ExecutionProvenanceEnvironment:
            raise ExecutionProvenancePreflightError("environment must be an ExecutionProvenanceEnvironment")
        profile_id = _identifier(self.profile_id, "profile_id")
        plan_id = _identifier(self.plan_id, "plan_id")
        checked_at = _time(self.checked_at, "checked_at")
        valid_until = _time(self.valid_until, "valid_until")
        if valid_until <= checked_at:
            raise ExecutionProvenancePreflightError("valid_until must be later than checked_at")
        activation_hashes = tuple(sorted(_hash(value, "activation_hash") for value in self.activation_hashes))
        if not activation_hashes or len(set(activation_hashes)) != len(activation_hashes):
            raise ExecutionProvenancePreflightError("activation_hashes must be non-empty and unique")
        for name in (
            "portfolio_target_hash",
            "approved_target_hash",
            "composition_evidence_hash",
            "portfolio_risk_approval_evidence_hash",
            "risk_evidence_hash",
            "data_evidence_hash",
            "account_snapshot_hash",
            "portfolio_risk_authority_hash",
            "portfolio_risk_policy_hash",
            "broker_state_hash",
            "reconciliation_state_hash",
            "account_attribution_hash",
            "quote_evidence_hash",
            "contract_rule_evidence_hash",
            "runtime_risk_hash",
            "preflight_hash",
            "plan_hash",
        ):
            object.__setattr__(self, name, _hash(getattr(self, name), name))
        if not isinstance(self.order_commitments, tuple) or not self.order_commitments or not all(
            type(item) is ExecutionOrderCommitment for item in self.order_commitments
        ):
            raise ExecutionProvenancePreflightError(
                "order_commitments must be a non-empty ExecutionOrderCommitment tuple"
            )
        commitments = tuple(sorted(self.order_commitments, key=lambda item: item.order_hash))
        if len({item.order_hash for item in commitments}) != len(commitments):
            raise ExecutionProvenancePreflightError("order commitments cannot be duplicated")
        payload = {
            "format": "northstar.execution-provenance-preflight-receipt.v1",
            "preflight_id": preflight_id,
            "environment": self.environment.value,
            "profile_id": profile_id,
            "plan_id": plan_id,
            "checked_at": checked_at.isoformat(),
            "valid_until": valid_until.isoformat(),
            "activation_hashes": list(activation_hashes),
            "portfolio_target_hash": self.portfolio_target_hash,
            "approved_target_hash": self.approved_target_hash,
            "composition_evidence_hash": self.composition_evidence_hash,
            "portfolio_risk_approval_evidence_hash": self.portfolio_risk_approval_evidence_hash,
            "risk_evidence_hash": self.risk_evidence_hash,
            "data_evidence_hash": self.data_evidence_hash,
            "account_snapshot_hash": self.account_snapshot_hash,
            "portfolio_risk_authority_hash": self.portfolio_risk_authority_hash,
            "portfolio_risk_policy_hash": self.portfolio_risk_policy_hash,
            "broker_state_hash": self.broker_state_hash,
            "reconciliation_state_hash": self.reconciliation_state_hash,
            "account_attribution_hash": self.account_attribution_hash,
            "quote_evidence_hash": self.quote_evidence_hash,
            "contract_rule_evidence_hash": self.contract_rule_evidence_hash,
            "runtime_risk_hash": self.runtime_risk_hash,
            "preflight_hash": self.preflight_hash,
            "plan_hash": self.plan_hash,
            "order_hashes": [item.order_hash for item in commitments],
            "eligible_for_ctp_sim": False,
            "eligible_for_trading": False,
            "eligible_for_live": False,
        }
        object.__setattr__(self, "preflight_id", preflight_id)
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "plan_id", plan_id)
        object.__setattr__(self, "checked_at", checked_at)
        object.__setattr__(self, "valid_until", valid_until)
        object.__setattr__(self, "activation_hashes", activation_hashes)
        object.__setattr__(self, "order_commitments", commitments)
        object.__setattr__(self, "receipt_hash", canonical_json_sha256(payload))

    def as_mapping(self) -> dict[str, object]:
        return {
            "format": "northstar.execution-provenance-preflight-receipt.v1",
            "preflight_id": self.preflight_id,
            "environment": self.environment.value,
            "profile_id": self.profile_id,
            "plan_id": self.plan_id,
            "checked_at": self.checked_at.isoformat(),
            "valid_until": self.valid_until.isoformat(),
            "activation_hashes": list(self.activation_hashes),
            "portfolio_target_hash": self.portfolio_target_hash,
            "approved_target_hash": self.approved_target_hash,
            "composition_evidence_hash": self.composition_evidence_hash,
            "portfolio_risk_approval_evidence_hash": self.portfolio_risk_approval_evidence_hash,
            "risk_evidence_hash": self.risk_evidence_hash,
            "data_evidence_hash": self.data_evidence_hash,
            "account_snapshot_hash": self.account_snapshot_hash,
            "portfolio_risk_authority_hash": self.portfolio_risk_authority_hash,
            "portfolio_risk_policy_hash": self.portfolio_risk_policy_hash,
            "broker_state_hash": self.broker_state_hash,
            "reconciliation_state_hash": self.reconciliation_state_hash,
            "account_attribution_hash": self.account_attribution_hash,
            "quote_evidence_hash": self.quote_evidence_hash,
            "contract_rule_evidence_hash": self.contract_rule_evidence_hash,
            "runtime_risk_hash": self.runtime_risk_hash,
            "preflight_hash": self.preflight_hash,
            "plan_hash": self.plan_hash,
            "order_commitments": [item.as_mapping() for item in self.order_commitments],
            "eligible_for_ctp_sim": False,
            "eligible_for_trading": False,
            "eligible_for_live": False,
            "receipt_hash": self.receipt_hash,
        }


@dataclass(frozen=True, slots=True)
class _ExecutionProvenanceEvaluation:
    """Private replay result for the final candidate execution composition.

    The public WP04 contract remains a non-submitting evidence receipt.  WP05
    can use this internal result only while it replays the same source request
    itself and derives its own exact CTP-sim payloads.
    """

    receipt: ExecutionProvenancePreflightReceipt
    execution_plan: ExecutionPlan
    preflight: PreflightResult
    runtime_risk: RuntimeRiskAssessment


def _snapshot_hash(snapshot: BrokerStateSnapshot) -> str:
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


def _quote_hash(quotes: tuple[MarketQuoteSnapshot, ...]) -> str:
    payload = tuple(
        {
            "symbol": str(quote.symbol).strip().upper(),
            "bid": quote.bid,
            "ask": quote.ask,
            "last": quote.last,
            "close": quote.close,
            "market_price": quote.market_price,
            "market_data_type": quote.market_data_type,
            "asof": quote.asof,
            "source": quote.source,
        }
        for quote in quotes
    )
    return canonical_json_sha256(
        {
            "format": "northstar.execution-quote-evidence.v1",
            "quotes": _canonical_sort(tuple(_canonical_value(item) for item in payload)),
        }
    )


def _rule_hash(rules: tuple[ExecutionContractRuleEvidence, ...]) -> str:
    return canonical_json_sha256(
        {
            "format": "northstar.execution-contract-rule-set.v1",
            "rules": [item.as_mapping() for item in sorted(rules, key=lambda item: item.symbol)],
        }
    )


def _validate_environment(request: ExecutionProvenanceRequest) -> None:
    if request.environment is not ExecutionProvenanceEnvironment.CTP_SIM:
        raise ExecutionProvenancePreflightError("ENVIRONMENT_REFUSED: only ctp_sim evidence is allowed")
    if request.settings.broker != "ctp_sim":
        raise ExecutionProvenancePreflightError("BROKER_REFUSED: provenance preflight requires ctp_sim")
    if request.settings.live_trading_enabled:
        raise ExecutionProvenancePreflightError("LIVE_TRADING_REFUSED: live trading must remain disabled")
    if request.settings.kill_switch_enabled:
        raise ExecutionProvenancePreflightError("KILL_SWITCH_ENABLED: no new risk is permitted")
    if request.profile.lifecycle.role != "simulated":
        raise ExecutionProvenancePreflightError("PROFILE_REFUSED: profile must have simulated lifecycle role")
    futures = request.profile.futures
    if futures is None or futures.symbols_are_continuous or not futures.execution_allowed:
        raise ExecutionProvenancePreflightError(
            "PROFILE_REFUSED: profile must use executable, non-continuous futures contracts"
        )
    if request.profile.data.live_trading_eligible:
        raise ExecutionProvenancePreflightError(
            "PROFILE_REFUSED: ctp_sim provenance cannot consume live-trading-eligible data"
        )


def _replay_activations(
    request: ExecutionProvenanceRequest,
) -> tuple[ResearchStrategyActivationReceipt, ...]:
    claimed_by_id: dict[str, ResearchStrategyActivationReceipt] = {}
    for receipt in request.activation_receipts:
        activation_id = receipt.activation_approval.activation_id
        if activation_id in claimed_by_id:
            raise ExecutionProvenancePreflightError("ACTIVATION_RECEIPT_DUPLICATED")
        claimed_by_id[activation_id] = receipt
    activator = ResearchStrategyTargetActivator()
    replayed: list[ResearchStrategyActivationReceipt] = []
    for activation_request in request.activation_requests:
        activation_id = activation_request.activation_approval.activation_id
        claimed = claimed_by_id.get(activation_id)
        if claimed is None:
            raise ExecutionProvenancePreflightError("ACTIVATION_RECEIPT_MISSING")
        replay = activator.activate(activation_request)
        if replay != claimed:
            raise ExecutionProvenancePreflightError(
                "ACTIVATION_RECEIPT_REPLAY_MISMATCH: claimed receipt is not the exact replay result"
            )
        if replay.strategy_target.activation.activation_hash != replay.activation_hash:
            raise ExecutionProvenancePreflightError("ACTIVATION_TARGET_BINDING_MISMATCH")
        if replay.decision_time_safe is not False or replay.eligible_for_trading is not False:
            raise ExecutionProvenancePreflightError("RESEARCH_PIT_SEMANTICS_INVALID")
        replayed.append(replay)
    if len(replayed) != len(claimed_by_id):
        raise ExecutionProvenancePreflightError("ACTIVATION_RECEIPT_UNMATCHED")
    return tuple(replayed)


@dataclass(frozen=True, slots=True)
class _ReplayedPortfolioRiskApproval:
    """P3 outputs derived only by replaying the structured approval request."""

    authority: PortfolioRiskApprovalAuthority
    evidence: PortfolioRiskApprovalEvidence
    portfolio_target: PortfolioTarget
    approved_target: ApprovedPortfolioTarget


def _replay_portfolio_risk_approval(
    request: ExecutionProvenanceRequest,
    receipts: tuple[ResearchStrategyActivationReceipt, ...],
) -> _ReplayedPortfolioRiskApproval:
    """Replay the canonical composition and portfolio-wide P3 approval.

    P8 deliberately receives neither a raw ``PortfolioTarget`` nor a raw
    ``ApprovedPortfolioTarget``.  Its only P3 input is the review request and
    its claimed replay result, so a caller cannot hand-bind a target,
    allocation, limit check, or approval hash around the canonical gate.
    """

    claimed_request = request.portfolio_risk_approval_request
    try:
        authority = PortfolioRiskAuthorityResolver().resolve(
            profile=request.profile,
            broker_state=request.account_snapshot,
            reconciliation_safety_state=request.reconciliation_safety_state,
            composition=claimed_request.review_request.composition,
            evaluated_at=claimed_request.review_request.evaluated_at,
        )
    except PortfolioRiskAuthorityError as exc:
        raise ExecutionProvenancePreflightError(str(exc)) from exc
    if authority != request.portfolio_risk_authority:
        raise ExecutionProvenancePreflightError("PORTFOLIO_RISK_AUTHORITY_REPLAY_MISMATCH")
    if claimed_request.review_request != authority.review_request:
        raise ExecutionProvenancePreflightError("PORTFOLIO_RISK_AUTHORITY_CLAIM_MISMATCH")
    if authority.broker_state_hash != trusted_broker_state_hash(request.account_snapshot):
        raise ExecutionProvenancePreflightError(
            "PORTFOLIO_RISK_AUTHORITY_BROKER_STATE_MISMATCH"
        )
    if (
        authority.reconciliation_state_hash
        != request.reconciliation_safety_state.reconciliation_state_hash
    ):
        raise ExecutionProvenancePreflightError(
            "PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_MISMATCH"
        )
    try:
        replayed_evidence = PortfolioRiskApprovalGate().evaluate(
            claimed_request
        )
    except PortfolioRiskApprovalError as exc:
        raise ExecutionProvenancePreflightError(
            "PORTFOLIO_RISK_APPROVAL_REPLAY_REFUSED"
        ) from exc
    if replayed_evidence != request.portfolio_risk_approval_evidence:
        raise ExecutionProvenancePreflightError(
            "PORTFOLIO_RISK_APPROVAL_REPLAY_MISMATCH"
        )
    approved = replayed_evidence.approved_target
    if type(approved) is not ApprovedPortfolioTarget:
        raise ExecutionProvenancePreflightError("PORTFOLIO_RISK_APPROVAL_REFUSED")
    review = replayed_evidence.review
    target = review.portfolio_target
    if type(target) is not PortfolioTarget:
        raise ExecutionProvenancePreflightError("PORTFOLIO_RISK_TARGET_INVALID")
    composition = review.composition
    if composition.portfolio_target != target:
        raise ExecutionProvenancePreflightError("PORTFOLIO_COMPOSITION_TARGET_MISMATCH")
    if replayed_evidence.composition_evidence_hash != composition.evidence_hash:
        raise ExecutionProvenancePreflightError("PORTFOLIO_COMPOSITION_EVIDENCE_MISMATCH")
    if approved.portfolio_target != target:
        raise ExecutionProvenancePreflightError("APPROVED_TARGET_PORTFOLIO_MISMATCH")
    if approved.risk_evidence_hash != review.review_hash:
        raise ExecutionProvenancePreflightError("RISK_EVIDENCE_MISMATCH")

    source_hashes = tuple(sorted(receipt.strategy_target.target_hash for receipt in receipts))
    if target.source_strategy_target_hashes != source_hashes:
        raise ExecutionProvenancePreflightError(
            "STRATEGY_TARGET_SOURCE_MISMATCH: portfolio target must contain exactly replayed sources"
        )
    composition_sources = tuple(
        item.strategy_target for item in composition.request.allocation_inputs
    )
    if len(composition_sources) != len(receipts):
        raise ExecutionProvenancePreflightError("COMPOSITION_SOURCE_CARDINALITY_MISMATCH")
    replayed_sources = {receipt.strategy_target.target_hash: receipt.strategy_target for receipt in receipts}
    if tuple(sorted(source.target_hash for source in composition_sources)) != source_hashes:
        raise ExecutionProvenancePreflightError("COMPOSITION_SOURCE_HASH_MISMATCH")
    for source in composition_sources:
        if replayed_sources.get(source.target_hash) != source:
            raise ExecutionProvenancePreflightError("COMPOSITION_SOURCE_REPLAY_MISMATCH")

    review_at = _time(review.request.evaluated_at, "portfolio risk evaluated_at")
    approval_valid_until = _time(
        review.approval_valid_until,
        "portfolio risk approval_valid_until",
    )
    review_account = _identifier(
        review.request.account_snapshot.account_id,
        "portfolio risk account_id",
    )
    snapshot_account = _identifier(request.account_snapshot.account, "broker snapshot account")
    if review_account != snapshot_account:
        raise ExecutionProvenancePreflightError("PORTFOLIO_RISK_ACCOUNT_MISMATCH")
    maximum_age = timedelta(seconds=review.request.policy.max_input_age_seconds)
    if review_at > request.plan_created_at or request.checked_at - review_at > maximum_age:
        raise ExecutionProvenancePreflightError("PORTFOLIO_RISK_EVIDENCE_STALE_OR_FUTURE")
    if (
        request.plan_created_at >= approval_valid_until
        or request.checked_at >= approval_valid_until
    ):
        raise ExecutionProvenancePreflightError("PORTFOLIO_RISK_APPROVAL_VALIDITY_EXPIRED")
    for receipt in receipts:
        strategy_target = receipt.strategy_target
        if not (
            strategy_target.effective_at
            <= target.generated_at
            <= target.effective_at
            <= review_at
            <= approved.approved_at
            <= request.plan_created_at
            <= request.checked_at
            < target.expires_at
            <= strategy_target.expires_at
        ):
            raise ExecutionProvenancePreflightError("PROVENANCE_CHRONOLOGY_INVALID")
    return _ReplayedPortfolioRiskApproval(
        authority=authority,
        evidence=replayed_evidence,
        portfolio_target=target,
        approved_target=approved,
    )


def _validate_runtime_data(
    request: ExecutionProvenanceRequest,
    registry: CtpContractRegistry,
    *,
    portfolio_target: PortfolioTarget,
) -> tuple[dict[str, MarketQuoteSnapshot], dict[str, ExecutionContractRuleEvidence]]:
    data = request.data_evidence
    if data.profile_id != request.profile.profile_id:
        raise ExecutionProvenancePreflightError("DATA_PROFILE_MISMATCH")
    if data.dataset_id != request.profile.data.dataset_id:
        raise ExecutionProvenancePreflightError("DATASET_MISMATCH")
    if data.target_output_as_of > request.plan_created_at:
        raise ExecutionProvenancePreflightError("TARGET_OUTPUT_AFTER_PLAN")
    if data.raw_market_as_of > request.checked_at:
        raise ExecutionProvenancePreflightError("MARKET_DATA_IN_FUTURE")
    quote_by_symbol: dict[str, MarketQuoteSnapshot] = {}
    for quote in request.quotes:
        symbol = _identifier(quote.symbol, "quote symbol").upper()
        if symbol in quote_by_symbol:
            raise ExecutionProvenancePreflightError("QUOTE_SYMBOL_DUPLICATED")
        if quote.source != "ctp_sim_market_data":
            raise ExecutionProvenancePreflightError("QUOTE_SOURCE_REFUSED")
        quote_asof = _time(quote.asof, f"quote {symbol} asof")
        if quote_asof > request.checked_at:
            raise ExecutionProvenancePreflightError("QUOTE_IN_FUTURE")
        if request.checked_at - quote_asof > timedelta(
            seconds=request.settings.runtime_risk_max_quote_age_seconds
        ):
            raise ExecutionProvenancePreflightError("QUOTE_STALE")
        if execution_reference_price_from_quote(quote) is None:
            raise ExecutionProvenancePreflightError("QUOTE_REFERENCE_PRICE_MISSING")
        quote_by_symbol[symbol] = quote
    rule_by_symbol: dict[str, ExecutionContractRuleEvidence] = {}
    for rule in request.contract_rules:
        if rule.symbol in rule_by_symbol:
            raise ExecutionProvenancePreflightError("CONTRACT_RULE_DUPLICATED")
        if not (
            rule.available_at
            <= request.plan_created_at
            < rule.expires_at
            and rule.effective_at <= request.plan_created_at
        ):
            raise ExecutionProvenancePreflightError("CONTRACT_RULE_NOT_EFFECTIVE")
        mapping = registry.resolve_data_symbol(rule.symbol)
        if (
            mapping.instrument_id != rule.instrument_id
            or mapping.exchange_id != rule.exchange_id
            or mapping.volume_multiple != rule.volume_multiple
        ):
            raise ExecutionProvenancePreflightError("CONTRACT_MAPPING_MISMATCH")
        rule_by_symbol[rule.symbol] = rule
    expected_symbols = tuple(
        sorted(position.instrument_id.upper() for position in portfolio_target.positions)
    )
    if set(quote_by_symbol) != set(expected_symbols):
        raise ExecutionProvenancePreflightError("QUOTE_COVERAGE_MISMATCH")
    if set(rule_by_symbol) != set(expected_symbols):
        raise ExecutionProvenancePreflightError("CONTRACT_RULE_COVERAGE_MISMATCH")
    return quote_by_symbol, rule_by_symbol


def _validate_portfolio_risk_execution_rules(
    *,
    portfolio_risk: _ReplayedPortfolioRiskApproval,
    rule_by_symbol: dict[str, ExecutionContractRuleEvidence],
) -> None:
    """Bind P3 classified margin evidence to the exact P5 planning rules.

    A canonical review is not reusable for a different executable contract or
    margin rule.  The independently time-bounded P5 rule set is the final
    executable identity source, so any drift from P3's reviewed instrument
    snapshot fails before a plan, receipt, durable intent, or broker action.
    """

    snapshots = {
        snapshot.instrument_id.strip().upper(): snapshot
        for snapshot in portfolio_risk.evidence.review.request.instrument_snapshots
    }
    if set(snapshots) != set(rule_by_symbol):
        raise ExecutionProvenancePreflightError("PORTFOLIO_RISK_CONTRACT_COVERAGE_MISMATCH")
    for symbol, rule in rule_by_symbol.items():
        snapshot = snapshots[symbol]
        if snapshot.instrument_id.casefold() != rule.instrument_id.casefold():
            raise ExecutionProvenancePreflightError(
                "PORTFOLIO_RISK_CONTRACT_IDENTITY_MISMATCH"
            )
        if snapshot.exchange_id.upper() != rule.exchange_id.upper():
            raise ExecutionProvenancePreflightError(
                "PORTFOLIO_RISK_EXCHANGE_MISMATCH"
            )
        if not math.isclose(
            snapshot.margin_fraction,
            float(rule.rule.margin_rate),
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise ExecutionProvenancePreflightError(
                "PORTFOLIO_RISK_MARGIN_RULE_MISMATCH"
            )


def _validate_authority_execution_rules(
    *,
    authority: PortfolioRiskApprovalAuthority,
    rule_by_symbol: dict[str, ExecutionContractRuleEvidence],
) -> None:
    """Require P5 rule evidence to exactly match trusted profile authority.

    P3 receives its classification and margin inputs from the profile-owned
    authority.  Planning must therefore also use that authority's concrete
    registry identity, margin rate, and maximum lot cap; otherwise a valid P3
    review could be replayed against a looser P5 execution rule.
    """

    authority_rules = {item.symbol: item for item in authority.execution_rules}
    if set(authority_rules) != set(rule_by_symbol):
        raise ExecutionProvenancePreflightError(
            "PORTFOLIO_RISK_AUTHORITY_CONTRACT_COVERAGE_MISMATCH"
        )
    for symbol, rule in rule_by_symbol.items():
        expected = authority_rules[symbol]
        if (
            expected.instrument_id.casefold() != rule.instrument_id.casefold()
            or expected.exchange_id.upper() != rule.exchange_id.upper()
            or expected.volume_multiple != rule.volume_multiple
        ):
            raise ExecutionProvenancePreflightError(
                "PORTFOLIO_RISK_AUTHORITY_CONTRACT_IDENTITY_MISMATCH"
            )
        if not math.isclose(
            expected.margin_rate,
            float(rule.rule.margin_rate),
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise ExecutionProvenancePreflightError(
                "PORTFOLIO_RISK_AUTHORITY_MARGIN_RULE_MISMATCH"
            )
        if rule.rule.max_position_lots != expected.max_position_lots:
            raise ExecutionProvenancePreflightError(
                "PORTFOLIO_RISK_AUTHORITY_MAX_LOT_RULE_MISMATCH"
            )


def _validate_account(request: ExecutionProvenanceRequest) -> tuple[float, float]:
    snapshot = request.account_snapshot
    account = _identifier(snapshot.account, "broker snapshot account")
    if account != request.account_attribution.account:
        raise ExecutionProvenancePreflightError("ACCOUNT_ATTRIBUTION_MISMATCH")
    snapshot_asof = _time(snapshot.asof, "broker snapshot asof")
    if snapshot_asof > request.checked_at:
        raise ExecutionProvenancePreflightError("BROKER_STATE_IN_FUTURE")
    if request.checked_at - snapshot_asof > timedelta(
        seconds=request.settings.runtime_risk_max_state_age_seconds
    ):
        raise ExecutionProvenancePreflightError("BROKER_STATE_STALE")
    if snapshot.state_complete is not True or snapshot.state_errors:
        raise ExecutionProvenancePreflightError("BROKER_STATE_INCOMPLETE")
    attribution_age = request.checked_at - request.account_attribution.observed_at
    if attribution_age < timedelta(0) or attribution_age > timedelta(
        seconds=request.settings.runtime_risk_gate_max_age_seconds
    ):
        raise ExecutionProvenancePreflightError("ACCOUNT_ATTRIBUTION_STALE_OR_FUTURE")
    equity = _account_number(snapshot, "NetLiquidation")
    available_cash = _account_number(snapshot, "AvailableFunds")
    if equity is None or equity <= 0:
        raise ExecutionProvenancePreflightError("ACCOUNT_EQUITY_INVALID")
    if available_cash is None or available_cash < 0:
        raise ExecutionProvenancePreflightError("ACCOUNT_AVAILABLE_CASH_INVALID")
    return equity, available_cash


def _commit_order(
    order: RebalanceOrderPlan,
    *,
    rule: ExecutionContractRuleEvidence,
    quote: MarketQuoteSnapshot,
    registry: CtpContractRegistry,
) -> ExecutionOrderCommitment:
    symbol = _identifier(order.symbol, "execution order symbol").upper()
    if symbol != rule.symbol:
        raise ExecutionProvenancePreflightError("ORDER_CONTRACT_RULE_MISMATCH")
    mapping = registry.resolve_data_symbol(symbol)
    if (
        order.instrument_id != mapping.instrument_id
        or order.exchange_id != mapping.exchange_id
        or order.volume_multiple != mapping.volume_multiple
    ):
        raise ExecutionProvenancePreflightError("ORDER_CONTRACT_IDENTITY_MISMATCH")
    reference_price = execution_reference_price_from_quote(quote)
    if reference_price is None or order.execution_reference_price is None:
        raise ExecutionProvenancePreflightError("ORDER_REFERENCE_PRICE_MISSING")
    if not math.isclose(float(order.execution_reference_price), reference_price, rel_tol=0, abs_tol=1e-9):
        raise ExecutionProvenancePreflightError("ORDER_REFERENCE_PRICE_MISMATCH")
    if order.margin_rate is None or not math.isclose(
        float(order.margin_rate), float(rule.rule.margin_rate), rel_tol=0, abs_tol=1e-12
    ):
        raise ExecutionProvenancePreflightError("ORDER_MARGIN_RATE_MISMATCH")
    if order.qty <= 0 or not math.isfinite(float(order.qty)):
        raise ExecutionProvenancePreflightError("ORDER_QTY_INVALID")
    expected_notional = float(order.qty) * reference_price * mapping.volume_multiple
    if order.estimated_trade_value is None or not math.isclose(
        float(order.estimated_trade_value), expected_notional, rel_tol=0, abs_tol=1e-6
    ):
        raise ExecutionProvenancePreflightError("ORDER_NOTIONAL_MISMATCH")
    expected_required_margin = (
        expected_notional * float(rule.rule.margin_rate)
        if str(order.ctp_offset).lower() == "open"
        else 0.0
    )
    if order.required_margin is None or not math.isclose(
        float(order.required_margin), expected_required_margin, rel_tol=0, abs_tol=1e-6
    ):
        raise ExecutionProvenancePreflightError("ORDER_REQUIRED_MARGIN_MISMATCH")
    if order.instrument_id is None or order.exchange_id is None or order.ctp_offset is None:
        raise ExecutionProvenancePreflightError("ORDER_CONTRACT_IDENTITY_MISSING")
    return ExecutionOrderCommitment(
        symbol=symbol,
        side=order.side,
        qty=float(order.qty),
        reference_price=reference_price,
        instrument_id=order.instrument_id,
        exchange_id=order.exchange_id,
        ctp_offset=order.ctp_offset,
        volume_multiple=mapping.volume_multiple,
        margin_rate=float(rule.rule.margin_rate),
        required_margin=expected_required_margin,
    )


def _plan_hash(
    *,
    request: ExecutionProvenanceRequest,
    approved_target: ApprovedPortfolioTarget,
    composition_evidence_hash: str,
    portfolio_risk_approval_evidence_hash: str,
    account_snapshot_hash: str,
    portfolio_risk_authority_hash: str,
    portfolio_risk_policy_hash: str,
    broker_state_hash: str,
    reconciliation_state_hash: str,
    contract_rule_evidence_hash: str,
    commitments: tuple[ExecutionOrderCommitment, ...],
) -> str:
    return canonical_json_sha256(
        {
            "format": "northstar.execution-provenance-plan.v1",
            "plan_id": request.plan_id,
            "approved_target_hash": approved_target.approval_hash,
            "composition_evidence_hash": composition_evidence_hash,
            "portfolio_risk_approval_evidence_hash": portfolio_risk_approval_evidence_hash,
            "account_snapshot_hash": account_snapshot_hash,
            "portfolio_risk_authority_hash": portfolio_risk_authority_hash,
            "portfolio_risk_policy_hash": portfolio_risk_policy_hash,
            "broker_state_hash": broker_state_hash,
            "reconciliation_state_hash": reconciliation_state_hash,
            "market_snapshot_at": request.market_snapshot_at.isoformat(),
            "contract_rule_evidence_hash": contract_rule_evidence_hash,
            "created_at": request.plan_created_at.isoformat(),
            "order_hashes": [item.order_hash for item in commitments],
        }
    )


class ExecutionProvenancePreflight:
    """Replay a candidate provenance chain without a broker-side effect."""

    __slots__ = ()

    def verify(
        self,
        request: ExecutionProvenanceRequest,
    ) -> ExecutionProvenancePreflightReceipt:
        """Build a non-tradable evidence receipt from exact source contracts."""

        return self._evaluate(request).receipt

    def _evaluate(
        self,
        request: ExecutionProvenanceRequest,
    ) -> _ExecutionProvenanceEvaluation:
        """Replay evidence and retain private typed intermediates for WP05."""

        if type(request) is not ExecutionProvenanceRequest:
            raise ExecutionProvenancePreflightError("request must be an ExecutionProvenanceRequest")
        _validate_environment(request)
        equity, available_cash = _validate_account(request)
        receipts = _replay_activations(request)
        portfolio_risk = _replay_portfolio_risk_approval(request, receipts)
        portfolio_target = portfolio_risk.portfolio_target
        approved_target = portfolio_risk.approved_target
        futures = request.profile.futures
        if futures is None:  # Kept explicit for type narrowing and fail-closed behavior.
            raise ExecutionProvenancePreflightError("PROFILE_REFUSED")
        registry = load_ctp_contract_registry(
            futures.ctp_contract_mapping_path,
            expected_broker=ExecutionProvenanceEnvironment.CTP_SIM.value,
        )
        quote_by_symbol, rule_by_symbol = _validate_runtime_data(
            request,
            registry,
            portfolio_target=portfolio_target,
        )
        _validate_portfolio_risk_execution_rules(
            portfolio_risk=portfolio_risk,
            rule_by_symbol=rule_by_symbol,
        )
        _validate_authority_execution_rules(
            authority=portfolio_risk.authority,
            rule_by_symbol=rule_by_symbol,
        )
        futures_rules = {symbol: evidence.rule for symbol, evidence in rule_by_symbol.items()}
        latest_prices = {
            symbol: execution_reference_price_from_quote(quote)
            for symbol, quote in quote_by_symbol.items()
        }
        if any(price is None for price in latest_prices.values()):
            raise ExecutionProvenancePreflightError("QUOTE_REFERENCE_PRICE_MISSING")
        execution_plan = build_approved_execution_plan(
            plan_id=request.plan_id,
            approved_target=approved_target,
            profile=request.profile,
            account_snapshot=request.account_snapshot,
            latest_prices={symbol: float(price) for symbol, price in latest_prices.items() if price is not None},
            market_snapshot_at=request.market_snapshot_at,
            created_at=request.plan_created_at,
            broker_name=ExecutionProvenanceEnvironment.CTP_SIM.value,
            futures_rules=futures_rules,
            equity=equity,
        )
        runtime_risk = assess_runtime_risk(
            profile_id=request.profile.profile_id,
            broker=ExecutionProvenanceEnvironment.CTP_SIM.value,
            account=request.account_snapshot.account,
            state=request.account_snapshot,
            quotes=list(request.quotes),
            required_symbols=[order.symbol for order in execution_plan.orders],
            settings=request.settings,
            checked_at=request.checked_at,
        )
        if not runtime_risk.can_submit:
            raise ExecutionProvenancePreflightError("RUNTIME_RISK_BLOCKED")
        output_frame = pl.DataFrame(
            {
                "asof": [request.data_evidence.target_output_as_of] * len(portfolio_target.positions),
                "symbol": [position.instrument_id.upper() for position in portfolio_target.positions],
                "target_weight": [position.target_weight for position in portfolio_target.positions],
            }
        )
        raw_market_frame = pl.DataFrame({"asof": [request.data_evidence.raw_market_as_of]})
        signal_market_frame = pl.DataFrame({"asof": [request.data_evidence.signal_market_as_of]})
        preflight = build_preflight_result(
            profile=request.profile,
            settings=request.settings,
            raw_market_df=raw_market_frame,
            signal_market_df=signal_market_frame,
            output_frame=output_frame,
            output_time_column="asof",
            broker_state=request.account_snapshot,
            execution_symbols=[order.symbol for order in execution_plan.orders],
            execution_reference_prices={
                symbol: float(price) for symbol, price in latest_prices.items() if price is not None
            },
            execution_price_sources={symbol: quote.source for symbol, quote in quote_by_symbol.items()},
            equity=equity,
            available_cash=available_cash,
            live_account_attribution=request.account_attribution.as_preflight_mapping(),
            broker_name=ExecutionProvenanceEnvironment.CTP_SIM.value,
            expected_account=request.account_snapshot.account,
            data_manifest=request.data_evidence.as_preflight_manifest(),
            runtime_risk_assessment=runtime_risk.to_dict(),
            checked_at=request.checked_at,
        )
        if not preflight.can_trade:
            raise ExecutionProvenancePreflightError("P5_PREFLIGHT_BLOCKED")
        commitments = tuple(
            _commit_order(
                order,
                rule=rule_by_symbol[_identifier(order.symbol, "execution order symbol").upper()],
                quote=quote_by_symbol[_identifier(order.symbol, "execution order symbol").upper()],
                registry=registry,
            )
            for order in execution_plan.orders
        )
        if not commitments:
            raise ExecutionProvenancePreflightError("EXECUTION_PLAN_EMPTY")
        account_snapshot_hash = _snapshot_hash(request.account_snapshot)
        broker_state_hash = trusted_broker_state_hash(request.account_snapshot)
        if broker_state_hash != portfolio_risk.authority.broker_state_hash:
            raise ExecutionProvenancePreflightError(
                "PORTFOLIO_RISK_AUTHORITY_BROKER_STATE_MISMATCH"
            )
        if (
            request.reconciliation_safety_state.reconciliation_state_hash
            != portfolio_risk.authority.reconciliation_state_hash
        ):
            raise ExecutionProvenancePreflightError(
                "PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_MISMATCH"
            )
        contract_rule_evidence_hash = _rule_hash(request.contract_rules)
        valid_until = min(
            request.checked_at
            + timedelta(seconds=request.settings.runtime_risk_gate_max_age_seconds),
            portfolio_target.expires_at,
            portfolio_risk.evidence.review.approval_valid_until,
            portfolio_risk.authority.review_request.account_snapshot.expires_at,
            portfolio_risk.authority.review_request.risk_state.expires_at,
            *(
                snapshot.expires_at
                for snapshot in portfolio_risk.authority.review_request.instrument_snapshots
            ),
            *(receipt.strategy_target.expires_at for receipt in receipts),
            *(rule.expires_at for rule in request.contract_rules),
        )
        if valid_until <= request.checked_at:
            raise ExecutionProvenancePreflightError("PREFLIGHT_RECEIPT_EXPIRED")
        receipt = ExecutionProvenancePreflightReceipt(
            preflight_id=request.preflight_id,
            environment=request.environment,
            profile_id=request.profile.profile_id,
            plan_id=request.plan_id,
            checked_at=request.checked_at,
            valid_until=valid_until,
            activation_hashes=tuple(receipt.activation_hash for receipt in receipts),
            portfolio_target_hash=portfolio_target.target_hash,
            approved_target_hash=approved_target.approval_hash,
            composition_evidence_hash=portfolio_risk.evidence.composition_evidence_hash,
            portfolio_risk_approval_evidence_hash=portfolio_risk.evidence.evidence_hash,
            risk_evidence_hash=approved_target.risk_evidence_hash,
            data_evidence_hash=request.data_evidence.evidence_hash,
            account_snapshot_hash=account_snapshot_hash,
            portfolio_risk_authority_hash=portfolio_risk.authority.authority_hash,
            portfolio_risk_policy_hash=portfolio_risk.authority.policy_hash,
            broker_state_hash=broker_state_hash,
            reconciliation_state_hash=portfolio_risk.authority.reconciliation_state_hash,
            account_attribution_hash=request.account_attribution.evidence_hash,
            quote_evidence_hash=_quote_hash(request.quotes),
            contract_rule_evidence_hash=contract_rule_evidence_hash,
            runtime_risk_hash=canonical_json_sha256(runtime_risk.to_dict()),
            preflight_hash=canonical_json_sha256(preflight.to_dict()),
            plan_hash=_plan_hash(
                request=request,
                approved_target=approved_target,
                composition_evidence_hash=portfolio_risk.evidence.composition_evidence_hash,
                portfolio_risk_approval_evidence_hash=portfolio_risk.evidence.evidence_hash,
                account_snapshot_hash=account_snapshot_hash,
                portfolio_risk_authority_hash=portfolio_risk.authority.authority_hash,
                portfolio_risk_policy_hash=portfolio_risk.authority.policy_hash,
                broker_state_hash=broker_state_hash,
                reconciliation_state_hash=portfolio_risk.authority.reconciliation_state_hash,
                contract_rule_evidence_hash=contract_rule_evidence_hash,
                commitments=commitments,
            ),
            order_commitments=commitments,
        )
        return _ExecutionProvenanceEvaluation(
            receipt=receipt,
            execution_plan=execution_plan,
            preflight=preflight,
            runtime_risk=runtime_risk,
        )

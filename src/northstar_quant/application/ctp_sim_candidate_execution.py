"""P8-WP05 guarded CTP-sim candidate execution composition.

This is the only application path that can submit a candidate-derived order to
the isolated CTP simulator.  It never accepts a caller-supplied order, plan,
preflight result, or receipt as authorization.  Instead it replays the full
P2 → P3 → P5 provenance request, derives exact canonical payloads, and
persists each one-time commitment consumption atomically with its durable
broker intent.

The module is intentionally CTP-sim-only.  It does not support a live CTP
profile, credentials, or a route for re-enabling live trading.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
import math
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from northstar_quant.application.execution_provenance_preflight import (
    ExecutionOrderCommitment,
    ExecutionProvenanceEnvironment,
    ExecutionProvenancePreflight,
    ExecutionProvenancePreflightError,
    ExecutionProvenancePreflightReceipt,
    ExecutionProvenanceRequest,
)
from northstar_quant.application.portfolio_risk_manual_approval import (
    require_persisted_portfolio_risk_approval,
)
from northstar_quant.application.portfolio_risk_authority import (
    PortfolioRiskAuthorityError,
    ReconciliationSafetyStateEvidence,
)
from northstar_quant.data_platform.artifacts.fingerprints import canonical_json_sha256
from northstar_quant.platform.common.order_identity import build_order_ref
from northstar_quant.platform.common.time import ensure_utc, utc_now
from northstar_quant.platform.config.settings import (
    Settings,
    load_settings,
    normalize_local_state_account,
)
from northstar_quant.platform.config.trading_profile import (
    TradingProfile,
    load_trading_profile_uncached,
)
from northstar_quant.platform.db.repositories import (
    acquire_reconciliation_safety_fence,
    find_execution_provenance_consumption,
    latest_reconciliation_safety_state,
    list_execution_recovery_blockers,
    record_execution_provenance_consumption,
    release_execution_lease,
    save_execution_plan_records,
    try_acquire_execution_lease,
)
from northstar_quant.portfolio_risk.limits.models import OrderRiskContext, RiskLimits
from northstar_quant.portfolio_risk.risk import RiskState
from northstar_quant.trading_execution.broker.ctp_sim_broker import (
    CtpSimBrokerAdapter,
    CtpSimPreSyncCheckRejected,
    CtpSimPreSyncGuardRefusal,
)
from northstar_quant.trading_execution.broker.ctp_contract_mapping import (
    CtpContractMappingError,
    load_ctp_contract_registry,
)
from northstar_quant.trading_execution.execution.models import (
    BrokerStateSnapshot,
    MarketQuoteSnapshot,
    OrderRequest,
    OrderResult,
    RebalanceOrderPlan,
)
from northstar_quant.trading_execution.execution.router import OrderRouter
from northstar_quant.trading_execution.orders.durable_submission import (
    DurableBrokerAdapter,
    SubmissionLease,
)
from northstar_quant.trading_execution.orders.ctp_sim_submission_guard import (
    _issue_ctp_sim_submission_authority,
)
from northstar_quant.trading_execution.reconciliation.reconciliation import (
    halt_for_reconciliation,
    reconcile_broker_state,
)


__all__ = [
    "CtpSimCandidateExecutionBundle",
    "CtpSimCandidateExecutionError",
    "CtpSimCandidateExecutionEvidence",
    "CtpSimCandidateExecutor",
]


class CtpSimCandidateExecutionError(
    ExecutionProvenancePreflightError,
    PermissionError,
):
    """Raised when the candidate-only CTP-sim submission boundary refuses risk."""


def _load_active_profile(
    *,
    settings: Settings,
    claimed_profile: TradingProfile,
) -> TradingProfile:
    """Load the uncached profile that is authoritative for this exact order."""

    try:
        active_profile = load_trading_profile_uncached(
            claimed_profile.profile_id,
            settings.profile_config_dir,
        )
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise CtpSimCandidateExecutionError(
            "CTP_SIM_CANDIDATE_PROFILE_LOAD_FAILED"
        ) from exc
    if active_profile != claimed_profile:
        raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_PROFILE_MISMATCH")
    return active_profile


def _require_safe_ctp_sim_settings(settings: Settings) -> None:
    if type(settings) is not Settings:
        raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_SETTINGS_INVALID")
    if settings.broker != "ctp_sim":
        raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_BROKER_REQUIRED")
    if settings.live_trading_enabled:
        raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_LIVE_TRADING_REFUSED")
    if settings.kill_switch_enabled:
        raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_KILL_SWITCH_ENABLED")


def _assert_broker_matches_settings(
    *,
    broker: CtpSimBrokerAdapter,
    settings: Settings,
    profile: TradingProfile,
) -> None:
    """Bind a simulator instance to the current trusted execution settings."""

    expected_account = normalize_local_state_account(settings.ctp_sim_account)
    expected_state_path = Path(settings.ctp_sim_state_path).resolve()
    expected_mapping_path = Path(settings.ctp_sim_contract_mapping_path).resolve()
    if (
        broker.get_name() != "ctp_sim"
        or broker.get_account() != expected_account
        or broker.state_path != expected_state_path
        or broker.mapping_path != expected_mapping_path
        or broker.default_cash != float(settings.default_cash)
    ):
        raise CtpSimCandidateExecutionError(
            "CTP_SIM_CANDIDATE_BROKER_BINDING_MISMATCH"
        )
    futures = profile.futures
    if futures is None or not futures.ctp_contract_mapping_path:
        raise CtpSimCandidateExecutionError(
            "CTP_SIM_CANDIDATE_CONTRACT_MAPPING_MISMATCH"
        )
    profile_mapping_path = Path(futures.ctp_contract_mapping_path)
    if not profile_mapping_path.is_absolute():
        profile_mapping_path = settings.project_root / profile_mapping_path
    if profile_mapping_path.resolve() != expected_mapping_path:
        raise CtpSimCandidateExecutionError(
            "CTP_SIM_CANDIDATE_CONTRACT_MAPPING_MISMATCH"
        )
    try:
        expected_registry = load_ctp_contract_registry(
            expected_mapping_path,
            expected_broker="ctp_sim",
        )
    except (CtpContractMappingError, OSError, ValueError) as exc:
        raise CtpSimCandidateExecutionError(
            "CTP_SIM_CANDIDATE_CONTRACT_MAPPING_MISMATCH"
        ) from exc
    if broker.registry != expected_registry:
        raise CtpSimCandidateExecutionError(
            "CTP_SIM_CANDIDATE_CONTRACT_MAPPING_MISMATCH"
        )


def _clock_now(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_CLOCK_INVALID")
    return ensure_utc(value)


def _durable_plan_id(*, plan_hash: str, order_hash: str) -> str:
    """Derive a distinct durable identity for each immutable commitment."""

    digest = sha256(f"{plan_hash}:{order_hash}".encode("utf-8")).hexdigest()
    return f"p8p-{digest[:48]}"


def _snapshot_semantics(snapshot: BrokerStateSnapshot) -> str:
    """Hash state content while allowing an otherwise identical fresh as-of time."""

    return canonical_json_sha256(
        {
            "account": snapshot.account,
            "account_values": dict(sorted(snapshot.account_values.items())),
            "state_complete": snapshot.state_complete,
            "state_errors": sorted(str(item) for item in snapshot.state_errors),
            "positions": sorted(
                (
                    item.symbol,
                    item.qty,
                    item.avg_cost,
                    item.market_price,
                    item.market_value,
                    item.sellable_qty,
                    item.account,
                    item.instrument_id,
                    item.exchange_id,
                    item.long_today_qty,
                    item.long_yesterday_qty,
                    item.short_today_qty,
                    item.short_yesterday_qty,
                    item.long_frozen_qty,
                    item.short_frozen_qty,
                    item.long_closable_qty,
                    item.short_closable_qty,
                    item.margin,
                    item.realized_pnl,
                    item.unrealized_pnl,
                )
                for item in snapshot.positions
            ),
            "open_orders": sorted(
                (dict(sorted(item.items())) for item in snapshot.open_orders),
                key=canonical_json_sha256,
            ),
            "completed_orders": sorted(
                (dict(sorted(item.items())) for item in snapshot.completed_orders),
                key=canonical_json_sha256,
            ),
            "fills": sorted(
                (
                    item.broker_order_id,
                    item.symbol,
                    item.qty,
                    item.price,
                    item.side,
                    item.account,
                    item.exec_id,
                    item.order_ref,
                    item.perm_id,
                    item.client_id,
                    item.instrument_id,
                    item.exchange_id,
                    item.ctp_offset,
                )
                for item in snapshot.fills
            ),
        }
    )


def _quote_semantics(quotes: tuple[MarketQuoteSnapshot, ...]) -> str:
    return canonical_json_sha256(
        {
            "quotes": sorted(
                (
                    item.symbol.strip().upper(),
                    item.bid,
                    item.ask,
                    item.last,
                    item.close,
                    item.market_price,
                    item.market_data_type,
                    item.source,
                )
                for item in quotes
            )
        }
    )


def _order_binding(order: OrderRequest) -> str:
    """Hash every field that may change a final broker-side order meaning."""

    return canonical_json_sha256(
        {
            "strategy_id": order.strategy_id,
            "symbol": order.symbol.strip().upper(),
            "side": order.side.strip().upper(),
            "qty": float(order.qty),
            "profile_id": order.profile_id,
            "target_weight": order.target_weight,
            "order_type": order.order_type.strip().upper(),
            "limit_price": order.limit_price,
            "order_semantic": order.order_semantic,
            "account": order.account,
            "reason": order.reason,
            "reference_price": order.reference_price,
            "reference_price_source": order.reference_price_source,
            "planned_trade_value": order.planned_trade_value,
            "run_id": order.run_id,
            "batch_id": order.batch_id,
            "plan_id": order.plan_id,
            "attempt_no": int(order.attempt_no),
            "execution_policy_fingerprint": order.execution_policy_fingerprint,
            "execution_planner_id": order.execution_planner_id,
            "instrument_id": order.instrument_id,
            "exchange_id": order.exchange_id,
            "ctp_offset": order.ctp_offset,
            "volume_multiple": order.volume_multiple,
            "margin_rate": order.margin_rate,
            "required_margin": order.required_margin,
            "order_ref": order.order_ref,
            "currency": order.currency,
        }
    )


def _commitment_for_plan(plan: RebalanceOrderPlan) -> ExecutionOrderCommitment:
    if (
        plan.execution_reference_price is None
        or plan.instrument_id is None
        or plan.exchange_id is None
        or plan.ctp_offset is None
        or plan.volume_multiple is None
        or plan.margin_rate is None
        or plan.required_margin is None
    ):
        raise CtpSimCandidateExecutionError(
            "CTP_SIM_CANDIDATE_PLAN_CONTRACT_INCOMPLETE"
        )
    return ExecutionOrderCommitment(
        symbol=plan.symbol,
        side=plan.side,
        qty=plan.qty,
        reference_price=plan.execution_reference_price,
        instrument_id=plan.instrument_id,
        exchange_id=plan.exchange_id,
        ctp_offset=plan.ctp_offset,
        volume_multiple=plan.volume_multiple,
        margin_rate=plan.margin_rate,
        required_margin=plan.required_margin,
    )


def _available_cash(snapshot: BrokerStateSnapshot) -> float:
    value = snapshot.account_values.get("AvailableFunds")
    if value is not None and not isinstance(value, bool):
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = float("nan")
        if math.isfinite(parsed) and parsed >= 0:
            return parsed
    raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_AVAILABLE_CASH_UNKNOWN")


def _require_persisted_normal_reconciliation_safety_state(
    session: Session,
    *,
    profile_id: str,
    broker: str,
    account: str,
) -> ReconciliationSafetyStateEvidence:
    """Read and hash-verify the one persisted scoped reconciliation state."""

    normalized_account = str(account or "").strip()
    if not normalized_account:
        raise CtpSimCandidateExecutionError(
            "CTP_SIM_CANDIDATE_RECONCILIATION_ACCOUNT_UNKNOWN"
        )
    normalized_broker = str(broker).strip().lower()
    row = latest_reconciliation_safety_state(
        session,
        profile_id=profile_id,
        broker=normalized_broker,
        account=normalized_account,
    )
    if row is None:
        raise CtpSimCandidateExecutionError(
            "CTP_SIM_CANDIDATE_RECONCILIATION_SAFETY_MISSING"
        )
    try:
        evidence = ReconciliationSafetyStateEvidence.from_persisted_record(row)
    except PortfolioRiskAuthorityError as exc:
        raise CtpSimCandidateExecutionError(str(exc)) from exc
    if (
        evidence.profile_id != profile_id
        or evidence.broker != normalized_broker
        or evidence.account_id != normalized_account
    ):
        raise CtpSimCandidateExecutionError(
            "CTP_SIM_CANDIDATE_RECONCILIATION_SAFETY_SCOPE_MISMATCH"
        )
    if evidence.state_snapshot.state is not RiskState.NORMAL:
        raise CtpSimCandidateExecutionError(
            "CTP_SIM_CANDIDATE_RECONCILIATION_STATE_BLOCKED: "
            f"{evidence.state_snapshot.state.value}"
        )
    return evidence


def _require_persisted_manual_risk_approval(
    session: Session,
    *,
    request: ExecutionProvenanceRequest,
    broker: CtpSimBrokerAdapter,
    checked_at: datetime,
) -> object:
    """Require the durable human-approved grant for the exact P3 claim.

    ``RiskApprovalAttestation`` is deliberately only a pure P3 value.  The
    candidate path must never treat it as an authority credential by itself:
    this application boundary replays it against the trusted authority and
    requires its immutable PostgreSQL grant before any P8 receipt, plan,
    durable intent, or simulator action can exist.
    """

    try:
        return require_persisted_portfolio_risk_approval(
            session,
            profile=request.profile,
            broker="ctp_sim",
            account=broker.get_account(),
            authority=request.portfolio_risk_authority,
            approval_request=request.portfolio_risk_approval_request,
            approval_evidence=request.portfolio_risk_approval_evidence,
            checked_at=checked_at,
        )
    except ValueError as exc:
        raise CtpSimCandidateExecutionError(str(exc)) from exc


def _manual_risk_approval_hashes(manual_approval: object) -> tuple[str, str]:
    """Read only the opaque persistent-binding identifiers the gate retains."""

    binding = getattr(manual_approval, "binding", None)
    binding_hash = getattr(binding, "binding_hash", None)
    record_hash = getattr(manual_approval, "record_hash", None)
    if not (
        isinstance(binding_hash, str)
        and len(binding_hash) == 64
        and isinstance(record_hash, str)
        and len(record_hash) == 64
    ):
        raise CtpSimCandidateExecutionError(
            "CTP_SIM_CANDIDATE_MANUAL_RISK_APPROVAL_INVALID"
        )
    return binding_hash, record_hash


def _assert_same_commitment(
    expected: ExecutionOrderCommitment,
    actual: ExecutionOrderCommitment,
) -> None:
    if expected.order_hash != actual.order_hash:
        raise CtpSimCandidateExecutionError(
            "CTP_SIM_CANDIDATE_COMMITMENT_CHANGED: final provenance replay produced a different order"
        )


@dataclass(frozen=True, slots=True)
class _ExpectedCandidateOrder:
    commitment: ExecutionOrderCommitment
    durable_plan_id: str
    order_ref: str
    order_binding_hash: str


@dataclass(frozen=True, slots=True)
class CtpSimCandidateExecutionEvidence:
    """Hash-only evidence from a reconciled candidate CTP-sim execution."""

    receipt_hash: str
    plan_hash: str
    consumption_order_hashes: tuple[str, ...]
    observed_order_refs: tuple[str, ...]
    observed_fill_exec_ids: tuple[str, ...]
    reconciliation_hash: str
    evidence_hash: str


class _CtpSimCandidateSubmissionGate:
    """Private protocol implementation bound to one simulator instance and bundle."""

    __slots__ = (
        "_broker",
        "_clock",
        "_expected_by_ref",
        "_manual_approval_binding_hash",
        "_manual_approval_record_hash",
        "_prepared_quote_semantics",
        "_prepared_request",
        "_authorized_snapshot_semantics",
        "_owner_token",
        "_receipt",
        "_reserved_order_hashes",
        "_settings_provider",
        "_session",
        "_source_request",
        "_verified_pre_submit_reconciliation",
    )

    def __init__(
        self,
        *,
        settings_provider: Callable[[], Settings],
        clock: Callable[[], datetime],
        owner_token: object,
    ) -> None:
        self._settings_provider = settings_provider
        self._clock = clock
        self._owner_token = owner_token
        self._broker: CtpSimBrokerAdapter | None = None
        self._source_request: ExecutionProvenanceRequest | None = None
        self._prepared_request: ExecutionProvenanceRequest | None = None
        self._receipt: ExecutionProvenancePreflightReceipt | None = None
        self._expected_by_ref: dict[str, _ExpectedCandidateOrder] = {}
        self._manual_approval_binding_hash: str | None = None
        self._manual_approval_record_hash: str | None = None
        self._authorized_snapshot_semantics: str | None = None
        self._prepared_quote_semantics: str | None = None
        self._reserved_order_hashes: set[str] = set()
        self._session: Session | None = None
        self._verified_pre_submit_reconciliation = False

    def is_owned_by(self, owner_token: object) -> bool:
        """Return whether this private gate was issued by one executor instance."""

        return self._owner_token is owner_token

    def configure(
        self,
        *,
        broker: CtpSimBrokerAdapter,
        source_request: ExecutionProvenanceRequest,
        prepared_request: ExecutionProvenanceRequest,
        receipt: ExecutionProvenancePreflightReceipt,
        expected_orders: tuple[_ExpectedCandidateOrder, ...],
        manual_approval: object,
    ) -> None:
        if self._session is not None:
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_GATE_ALREADY_BOUND")
        if not expected_orders:
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_PLAN_EMPTY")
        refs = {item.order_ref for item in expected_orders}
        if len(refs) != len(expected_orders):
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_ORDER_REF_DUPLICATED")
        self._broker = broker
        self._source_request = source_request
        self._prepared_request = prepared_request
        self._receipt = receipt
        self._expected_by_ref = {item.order_ref: item for item in expected_orders}
        (
            self._manual_approval_binding_hash,
            self._manual_approval_record_hash,
        ) = _manual_risk_approval_hashes(manual_approval)
        self._authorized_snapshot_semantics = _snapshot_semantics(
            prepared_request.account_snapshot
        )
        self._prepared_quote_semantics = _quote_semantics(prepared_request.quotes)
        self._reserved_order_hashes = set()

    def bind_session(self, session: Session) -> None:
        if self._session is not None and self._session is not session:
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_GATE_SESSION_MISMATCH")
        self._session = session

    def mark_pre_submit_reconciled(self) -> None:
        self._verified_pre_submit_reconciliation = True

    def assert_reconciliation_baseline(self, snapshot: BrokerStateSnapshot) -> None:
        """Reject external simulator drift before its lifecycle processor runs.

        The CTP-sim adapter invokes this under its state-file lock, before it
        advances a known submitted order to a fill.  That makes a later
        reconciliation capable of accepting only the adapter's own transition,
        never an interleaved external state rewrite.
        """

        if type(snapshot) is not BrokerStateSnapshot:
            raise CtpSimPreSyncGuardRefusal(
                "CTP_SIM_CANDIDATE_RECONCILIATION_SNAPSHOT_REQUIRED"
            )
        if _snapshot_semantics(snapshot) != self._authorized_snapshot_semantics:
            raise CtpSimPreSyncGuardRefusal(
                "CTP_SIM_CANDIDATE_BROKER_STATE_CHANGED"
            )

    def accept_reconciled_snapshot(self, snapshot: BrokerStateSnapshot) -> None:
        """Advance the baseline after a fenced, provenance-verified own fill."""

        if type(snapshot) is not BrokerStateSnapshot:
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_RECONCILIATION_SNAPSHOT_REQUIRED"
            )
        self._authorized_snapshot_semantics = _snapshot_semantics(snapshot)

    def unbind_session(self) -> None:
        self._session = None
        self._verified_pre_submit_reconciliation = False

    def _settings(self) -> Settings:
        settings = self._settings_provider()
        _require_safe_ctp_sim_settings(settings)
        return settings

    def assert_active_profile_current(
        self,
        *,
        settings: Settings | None = None,
    ) -> Settings:
        """Refuse profile/config drift before any candidate-side mutation."""

        if self._prepared_request is None:
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_GATE_NOT_CONFIGURED")
        if settings is None:
            settings = self._settings()
        else:
            _require_safe_ctp_sim_settings(settings)
        if self._broker is None:
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_GATE_NOT_CONFIGURED")
        active_profile = _load_active_profile(
            settings=settings,
            claimed_profile=self._prepared_request.profile,
        )
        _assert_broker_matches_settings(
            broker=self._broker,
            settings=settings,
            profile=active_profile,
        )
        return settings

    def _require_ready(self, order: OrderRequest) -> tuple[_ExpectedCandidateOrder, ExecutionProvenancePreflightReceipt, Session]:
        self.assert_active_profile_current()
        if not self._verified_pre_submit_reconciliation:
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_RECONCILIATION_REQUIRED"
            )
        receipt = self._receipt
        session = self._session
        if receipt is None or session is None or self._broker is None:
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_GATE_NOT_CONFIGURED")
        authority = self._broker.submission_authority
        if authority is None or not authority.is_bound_to(self):
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_GUARD_BINDING_LOST")
        now = _clock_now(self._clock)
        if not receipt.checked_at <= now < receipt.valid_until:
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_RECEIPT_EXPIRED")
        order_ref = str(order.order_ref or "").strip()
        expected = self._expected_by_ref.get(order_ref)
        if expected is None or _order_binding(order) != expected.order_binding_hash:
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_ORDER_BINDING_MISMATCH"
            )
        return expected, receipt, session

    def _assert_reconciliation_safety(
        self,
        session: Session,
        receipt: ExecutionProvenancePreflightReceipt,
        *,
        ignore_in_flight_order_ref: str | None = None,
    ) -> ReconciliationSafetyStateEvidence:
        assert self._broker is not None
        blockers = list_execution_recovery_blockers(
            session,
            broker="ctp_sim",
            account=self._broker.get_account(),
            ignore_in_flight_order_ref=ignore_in_flight_order_ref,
        )
        if blockers:
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_RECOVERY_BLOCKED: " + " | ".join(blockers)
            )
        safety = _require_persisted_normal_reconciliation_safety_state(
            session,
            profile_id=receipt.profile_id,
            broker="ctp_sim",
            account=self._broker.get_account(),
        )
        if safety.reconciliation_state_hash != receipt.reconciliation_state_hash:
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_RECONCILIATION_STATE_CHANGED"
            )
        if self._prepared_request is None:
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_GATE_NOT_CONFIGURED")
        prepared_authority = self._prepared_request.portfolio_risk_authority
        if (
            receipt.portfolio_risk_authority_hash != prepared_authority.authority_hash
            or receipt.portfolio_risk_policy_hash != prepared_authority.policy_hash
            or receipt.broker_state_hash != prepared_authority.broker_state_hash
            or receipt.reconciliation_state_hash
            != prepared_authority.reconciliation_state_hash
        ):
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_AUTHORITY_RECEIPT_MISMATCH"
            )
        return safety

    def _assert_persisted_manual_risk_approval(self, session: Session) -> None:
        """Re-read the immutable manual grant at every candidate boundary."""

        if self._prepared_request is None or self._broker is None:
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_GATE_NOT_CONFIGURED")
        if (
            self._manual_approval_binding_hash is None
            or self._manual_approval_record_hash is None
        ):
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_MANUAL_RISK_APPROVAL_MISSING"
            )
        manual_approval = _require_persisted_manual_risk_approval(
            session,
            request=self._prepared_request,
            broker=self._broker,
            checked_at=_clock_now(self._clock),
        )
        binding_hash, record_hash = _manual_risk_approval_hashes(manual_approval)
        if (
            binding_hash != self._manual_approval_binding_hash
            or record_hash != self._manual_approval_record_hash
        ):
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_MANUAL_RISK_APPROVAL_CHANGED"
            )

    def _revalidate(
        self,
        expected: _ExpectedCandidateOrder,
    ) -> None:
        if (
            self._broker is None
            or self._source_request is None
            or self._prepared_request is None
            or self._receipt is None
        ):
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_GATE_NOT_CONFIGURED")
        live_snapshot = self._broker.read_state_snapshot()
        if _snapshot_semantics(live_snapshot) != self._authorized_snapshot_semantics:
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_BROKER_STATE_CHANGED"
            )
        settings = self._settings()
        self.assert_active_profile_current(settings=settings)
        live_quotes = _read_runtime_quotes(
            request=self._source_request,
            broker=self._broker,
            settings=settings,
            snapshot=live_snapshot,
        )
        _assert_runtime_freshness(
            request=self._source_request,
            snapshot=live_snapshot,
            quotes=live_quotes,
            settings=settings,
            checked_at=_clock_now(self._clock),
            final_adapter_lock=False,
        )
        if _quote_semantics(live_quotes) != self._prepared_quote_semantics:
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_MARKET_DATA_CHANGED"
            )

        # The live simulator already matches this gate's post-own-submission
        # baseline. Replay only the original reviewed account/authority facts;
        # a new P3 authority must never be synthesized from a changed account.
        try:
            evaluation = ExecutionProvenancePreflight()._evaluate(
                replace(
                    self._prepared_request,
                    settings=settings,
                    quotes=live_quotes,
                    checked_at=_clock_now(self._clock),
                )
            )
        except ExecutionProvenancePreflightError as exc:
            raise CtpSimCandidateExecutionError(str(exc)) from exc
        receipt = evaluation.receipt
        original = self._receipt
        if (
            receipt.environment is not ExecutionProvenanceEnvironment.CTP_SIM
            or receipt.profile_id != original.profile_id
            or receipt.plan_id != original.plan_id
            or receipt.activation_hashes != original.activation_hashes
            or receipt.portfolio_target_hash != original.portfolio_target_hash
            or receipt.approved_target_hash != original.approved_target_hash
            or receipt.composition_evidence_hash != original.composition_evidence_hash
            or (
                receipt.portfolio_risk_approval_evidence_hash
                != original.portfolio_risk_approval_evidence_hash
            )
            or receipt.risk_evidence_hash != original.risk_evidence_hash
            or receipt.data_evidence_hash != original.data_evidence_hash
            or (
                receipt.portfolio_risk_authority_hash
                != original.portfolio_risk_authority_hash
            )
            or receipt.portfolio_risk_policy_hash != original.portfolio_risk_policy_hash
            or receipt.broker_state_hash != original.broker_state_hash
            or (
                receipt.reconciliation_state_hash
                != original.reconciliation_state_hash
            )
            or receipt.contract_rule_evidence_hash != original.contract_rule_evidence_hash
        ):
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_PROVENANCE_REPLAY_CHANGED"
            )
        by_hash = {item.order_hash: item for item in receipt.order_commitments}
        replayed_plan_commitments: dict[str, ExecutionOrderCommitment] = {}
        for plan in evaluation.execution_plan.orders:
            commitment = _commitment_for_plan(plan)
            if commitment.order_hash in replayed_plan_commitments:
                raise CtpSimCandidateExecutionError(
                    "CTP_SIM_CANDIDATE_REPLAYED_COMMITMENT_DUPLICATED"
                )
            replayed_plan_commitments[commitment.order_hash] = commitment
        replayed = by_hash.get(expected.commitment.order_hash)
        plan_commitment = replayed_plan_commitments.get(expected.commitment.order_hash)
        if replayed is None or plan_commitment is None:
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_COMMITMENT_MISSING_AFTER_REPLAY"
            )
        _assert_same_commitment(expected.commitment, replayed)
        _assert_same_commitment(expected.commitment, plan_commitment)

    def _assert_authorized_runtime_unchanged(
        self,
        *,
        snapshot: BrokerStateSnapshot,
        quotes: tuple[MarketQuoteSnapshot, ...],
    ) -> None:
        """Close the durable-to-adapter TOCTOU window under the broker lock."""

        if self._source_request is None:
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_GATE_NOT_CONFIGURED")
        if _snapshot_semantics(snapshot) != self._authorized_snapshot_semantics:
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_BROKER_STATE_CHANGED"
            )
        required_symbols = {
            position.instrument_id.strip().upper()
            for position in self._source_request.portfolio_risk_approval_request.review_request.composition.portfolio_target.positions
        }
        scoped_quotes = tuple(
            item
            for item in quotes
            if item.symbol.strip().upper() in required_symbols
        )
        if _quote_semantics(scoped_quotes) != self._prepared_quote_semantics:
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_MARKET_DATA_CHANGED"
            )

    def _prevalidate(
        self,
        order: OrderRequest,
    ) -> tuple[
        _ExpectedCandidateOrder,
        ExecutionProvenancePreflightReceipt,
        Session,
    ]:
        expected, receipt, session = self._require_ready(order)
        self._settings()
        self._assert_reconciliation_safety(session, receipt)
        self._assert_persisted_manual_risk_approval(session)
        self._revalidate(expected)
        return expected, receipt, session

    def prevalidate(self, order: OrderRequest) -> None:
        """Check a future candidate leg without minting a consumption fact."""

        self._prevalidate(order)

    def reserve(self, order: OrderRequest) -> None:
        """Reserve one exact order within the durable-intent transaction."""

        expected, receipt, session = self._prevalidate(order)
        if expected.commitment.order_hash in self._reserved_order_hashes:
            self._assert_consumption_record(
                expected=expected,
                receipt=receipt,
                session=session,
                order=order,
            )
            return
        record_execution_provenance_consumption(
            session,
            preflight_id=receipt.preflight_id,
            receipt_hash=receipt.receipt_hash,
            plan_hash=receipt.plan_hash,
            order_hash=expected.commitment.order_hash,
            profile_id=receipt.profile_id,
            broker="ctp_sim",
            account=str(order.account or ""),
            order_ref=expected.order_ref,
            checked_at=receipt.checked_at,
            valid_until=receipt.valid_until,
            consumed_at=_clock_now(self._clock),
        )
        self._reserved_order_hashes.add(expected.commitment.order_hash)

    def _assert_consumption_record(
        self,
        *,
        expected: _ExpectedCandidateOrder,
        receipt: ExecutionProvenancePreflightReceipt,
        session: Session,
        order: OrderRequest,
    ) -> None:
        row = find_execution_provenance_consumption(
            session,
            broker="ctp_sim",
            account=str(order.account or ""),
            plan_hash=receipt.plan_hash,
            order_hash=expected.commitment.order_hash,
            order_ref=expected.order_ref,
        )
        if (
            row is None
            or row.receipt_hash != receipt.receipt_hash
            or row.preflight_id != receipt.preflight_id
            or row.profile_id != receipt.profile_id
        ):
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_RESERVATION_MISSING"
            )

    def _assert_snapshot_consumptions(
        self,
        *,
        session: Session,
        snapshot: BrokerStateSnapshot,
    ) -> set[str]:
        """Refuse to absorb a simulator state mutation without consumption."""

        account = str(snapshot.account or "").strip()
        if not account:
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_RECONCILIATION_ACCOUNT_UNKNOWN"
            )
        observed_refs = {
            str(row.get("order_ref") or "").strip()
            for row in [*snapshot.open_orders, *snapshot.completed_orders]
        } | {
            str(item.order_ref or "").strip() for item in snapshot.fills
        }
        if "" in observed_refs:
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_ORDER_REF_UNKNOWN")
        for order_ref in observed_refs:
            if (
                find_execution_provenance_consumption(
                    session,
                    broker="ctp_sim",
                    account=account,
                    order_ref=order_ref,
                )
                is None
            ):
                raise CtpSimCandidateExecutionError(
                    "CTP_SIM_CANDIDATE_PROVENANCE_MISSING"
                )
        return observed_refs

    def assert_reserved(
        self,
        order: OrderRequest,
        *,
        snapshot: BrokerStateSnapshot,
        quotes: tuple[MarketQuoteSnapshot, ...],
    ) -> None:
        """Close the adapter-level bypass immediately before CTP-sim mutation."""

        expected, receipt, session = self._require_ready(order)
        # This transaction-scoped PostgreSQL fence is intentionally acquired
        # while the simulator adapter holds its final file lock.  A concurrent
        # reconciliation HALT uses the same key and therefore cannot commit
        # between this persisted-NORMAL check and the simulator mutation.
        # It remains held until DurableBrokerAdapter commits the acknowledged
        # outcome or SubmissionUnknown result.
        acquire_reconciliation_safety_fence(
            session,
            profile_id=receipt.profile_id,
            broker="ctp_sim",
            account=self._broker.get_account() if self._broker is not None else None,
        )
        # Waiting for the account fence can cross the receipt horizon.  Repeat
        # the ready check after lock acquisition, before any locked state,
        # safety, or adapter work.  The initial check above only chooses the
        # scoped fence key; it is not an authorization that survives a wait.
        expected, receipt, locked_session = self._require_ready(order)
        if locked_session is not session:
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_GATE_SESSION_MISMATCH"
            )
        settings = self._settings()
        self.assert_active_profile_current(settings=settings)
        self._assert_reconciliation_safety(
            session,
            receipt,
            ignore_in_flight_order_ref=expected.order_ref,
        )
        self._assert_persisted_manual_risk_approval(session)
        if self._source_request is None:
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_GATE_NOT_CONFIGURED")
        _assert_runtime_freshness(
            request=self._source_request,
            snapshot=snapshot,
            quotes=quotes,
            settings=settings,
            checked_at=_clock_now(self._clock),
            final_adapter_lock=True,
        )
        self._assert_authorized_runtime_unchanged(
            snapshot=snapshot,
            quotes=quotes,
        )
        self._assert_consumption_record(
            expected=expected,
            receipt=receipt,
            session=session,
            order=order,
        )

    def mark_submitted(
        self,
        order: OrderRequest,
        *,
        snapshot: BrokerStateSnapshot,
    ) -> None:
        """Advance from CTP-sim's already-locked post-mutation snapshot.

        The simulator invokes this before releasing its file lock, while this
        gate still owns the PostgreSQL reconciliation fence established by
        ``assert_reserved``.  Re-reading via ``sync_state`` here would acquire
        file-after-database and could deadlock with another submit waiting on
        that same fence.
        """

        if type(snapshot) is not BrokerStateSnapshot:
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_SUBMISSION_SNAPSHOT_REQUIRED"
            )
        expected, receipt, session = self._require_ready(order)
        self._settings()
        self._assert_consumption_record(
            expected=expected,
            receipt=receipt,
            session=session,
            order=order,
        )
        observed_refs = self._assert_snapshot_consumptions(
            session=session,
            snapshot=snapshot,
        )
        if expected.order_ref not in observed_refs:
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_SUBMISSION_STATE_MISSING"
            )
        latest_semantics = _snapshot_semantics(snapshot)
        if latest_semantics == self._authorized_snapshot_semantics:
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_SUBMISSION_STATE_UNCHANGED"
            )
        self._authorized_snapshot_semantics = latest_semantics


def _refresh_request(
    *,
    request: ExecutionProvenanceRequest,
    broker: CtpSimBrokerAdapter,
    settings: Settings,
    clock: Callable[[], datetime],
    snapshot: BrokerStateSnapshot,
    reconciliation_safety_state: ReconciliationSafetyStateEvidence,
) -> ExecutionProvenanceRequest:
    """Confirm a fresh unchanged runtime against immutable signed P3 facts.

    Snapshot and quote observations are deliberately collected before sampling
    ``checked_at``.  A normal real clock advances between those reads; sampling
    it first would convert that harmless ordering into a false future-fact
    refusal.  A truly future source timestamp remains refused below.
    """

    quotes = _read_runtime_quotes(
        request=request,
        broker=broker,
        settings=settings,
        snapshot=snapshot,
    )
    checked_at = _clock_now(clock)
    _assert_runtime_freshness(
        request=request,
        snapshot=snapshot,
        quotes=quotes,
        settings=settings,
        checked_at=checked_at,
        final_adapter_lock=False,
    )
    if request.reconciliation_safety_state != reconciliation_safety_state:
        raise CtpSimCandidateExecutionError(
            "PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_MISMATCH"
        )
    if _snapshot_semantics(snapshot) != _snapshot_semantics(request.account_snapshot):
        raise CtpSimCandidateExecutionError(
            "CTP_SIM_CANDIDATE_SIGNED_BROKER_STATE_CHANGED"
        )

    # A signed review is bound to the original account snapshot and authority.
    # The fresh simulator observation merely proves that its state content has
    # not drifted; replacing either signed P3 object would invalidate the human
    # review and could silently turn a new account state into trading authority.
    return replace(
        request,
        settings=settings,
        quotes=quotes,
        checked_at=checked_at,
    )


def _read_runtime_quotes(
    *,
    request: ExecutionProvenanceRequest,
    broker: CtpSimBrokerAdapter,
    settings: Settings,
    snapshot: BrokerStateSnapshot,
) -> tuple[MarketQuoteSnapshot, ...]:
    """Read only the runtime facts needed to guard a prepared batch."""

    if broker.get_name() != "ctp_sim" or not broker.broker_status().permits_new_risk:
        raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_BROKER_UNAVAILABLE")
    expected_account = str(settings.ctp_sim_account).strip()
    if broker.get_account() != expected_account:
        raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_ACCOUNT_MISMATCH")
    if str(snapshot.account or "").strip() != expected_account:
        raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_STATE_ACCOUNT_MISMATCH")
    symbols = sorted(
        position.instrument_id.strip().upper()
        for position in request.portfolio_risk_approval_request.review_request.composition.portfolio_target.positions
    )
    return tuple(broker.get_market_quotes(symbols))


def _runtime_symbols(request: ExecutionProvenanceRequest) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                position.instrument_id.strip().upper()
                for position in request.portfolio_risk_approval_request.review_request.composition.portfolio_target.positions
            }
        )
    )


def _assert_runtime_freshness(
    *,
    request: ExecutionProvenanceRequest,
    snapshot: BrokerStateSnapshot,
    quotes: tuple[MarketQuoteSnapshot, ...],
    settings: Settings,
    checked_at: datetime,
    final_adapter_lock: bool,
) -> None:
    """Fail closed on stale/future state or quotes, including under adapter lock."""

    prefix = (
        "CTP_SIM_CANDIDATE_FINAL"
        if final_adapter_lock
        else "CTP_SIM_CANDIDATE_RUNTIME"
    )
    if snapshot.state_complete is not True or snapshot.state_errors:
        raise CtpSimCandidateExecutionError(f"{prefix}_BROKER_STATE_INCOMPLETE")
    try:
        snapshot_at = ensure_utc(snapshot.asof)
    except (TypeError, ValueError) as exc:
        raise CtpSimCandidateExecutionError(
            f"{prefix}_BROKER_STATE_TIME_INVALID"
        ) from exc
    if snapshot_at > checked_at:
        raise CtpSimCandidateExecutionError(f"{prefix}_BROKER_STATE_FUTURE")
    if checked_at - snapshot_at > timedelta(
        seconds=settings.runtime_risk_max_state_age_seconds
    ):
        raise CtpSimCandidateExecutionError(f"{prefix}_BROKER_STATE_STALE")

    scoped_quotes: dict[str, MarketQuoteSnapshot] = {}
    for quote in quotes:
        symbol = str(quote.symbol or "").strip().upper()
        if symbol not in _runtime_symbols(request):
            continue
        if symbol in scoped_quotes:
            raise CtpSimCandidateExecutionError(f"{prefix}_QUOTE_DUPLICATED")
        scoped_quotes[symbol] = quote
    required_symbols = _runtime_symbols(request)
    if set(scoped_quotes) != set(required_symbols):
        raise CtpSimCandidateExecutionError(f"{prefix}_QUOTE_MISSING")
    for quote in scoped_quotes.values():
        try:
            quote_at = ensure_utc(quote.asof)
        except (TypeError, ValueError) as exc:
            raise CtpSimCandidateExecutionError(
                f"{prefix}_QUOTE_TIME_INVALID"
            ) from exc
        if quote_at > checked_at:
            raise CtpSimCandidateExecutionError(f"{prefix}_QUOTE_FUTURE")
        if checked_at - quote_at > timedelta(
            seconds=settings.runtime_risk_max_quote_age_seconds
        ):
            raise CtpSimCandidateExecutionError(f"{prefix}_QUOTE_STALE")


@dataclass(slots=True)
class CtpSimCandidateExecutionBundle:
    """Prepared, internally-derived CTP-sim candidate batch.

    ``orders`` are visible for audit only.  Submission remains safe if a caller
    mutates one, because the private gate compares every canonical field with
    its retained provenance binding before it can reach durable persistence.
    """

    broker: CtpSimBrokerAdapter
    source_request: ExecutionProvenanceRequest
    prepared_request: ExecutionProvenanceRequest
    receipt: ExecutionProvenancePreflightReceipt
    plans: tuple[RebalanceOrderPlan, ...]
    orders: tuple[OrderRequest, ...]
    run_id: str
    batch_id: str
    _gate: _CtpSimCandidateSubmissionGate

    def _assert_snapshot_provenance(self, session: Session, snapshot: BrokerStateSnapshot) -> None:
        account = str(snapshot.account or self.broker.get_account() or "").strip()
        if not account:
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_RECONCILIATION_ACCOUNT_UNKNOWN")
        observed_order_rows = [*snapshot.open_orders, *snapshot.completed_orders]
        observed_refs: set[str] = set()
        for row in observed_order_rows:
            order_ref = str(row.get("order_ref") or "").strip()
            if not order_ref:
                self._halt_unexplained(
                    session,
                    account=account,
                    reason="CTP_SIM_CANDIDATE_ORDER_REF_UNKNOWN",
                    evidence={"row": dict(row)},
                )
            observed_refs.add(order_ref)
        for item in snapshot.fills:
            order_ref = str(item.order_ref or "").strip()
            if not order_ref:
                self._halt_unexplained(
                    session,
                    account=account,
                    reason="CTP_SIM_CANDIDATE_FILL_REF_UNKNOWN",
                    evidence={"exec_id": item.exec_id, "broker_order_id": item.broker_order_id},
                )
            observed_refs.add(order_ref)
        for order_ref in sorted(observed_refs):
            consumption = find_execution_provenance_consumption(
                session,
                broker="ctp_sim",
                account=account,
                order_ref=order_ref,
            )
            if consumption is None:
                self._halt_unexplained(
                    session,
                    account=account,
                    reason="CTP_SIM_CANDIDATE_PROVENANCE_MISSING",
                    evidence={"order_ref": order_ref},
                )

    def _halt_unexplained(
        self,
        session: Session,
        *,
        account: str,
        reason: str,
        evidence: dict[str, object],
    ) -> None:
        halt_for_reconciliation(
            session,
            profile_id=self.receipt.profile_id,
            broker="ctp_sim",
            account=account,
            reason=reason,
            evidence=evidence,
        )
        # This candidate boundary owns the read transaction that discovered an
        # unexplained simulator fact.  Persist the fail-closed transition now;
        # returning it to an arbitrary caller/session context could otherwise
        # roll the HALT back while leaving the external fact unexplained.
        session.commit()
        raise CtpSimCandidateExecutionError(reason)

    def _reconcile(
        self,
        session: Session,
    ) -> tuple[dict[str, object], CtpSimCandidateExecutionEvidence]:
        """Read the simulator directly and refuse unexplained orders or fills.

        A caller cannot inject a hand-built, partial, or stale
        ``BrokerStateSnapshot`` here: candidate evidence is valid only when it
        comes from the connected isolated CTP simulator at reconciliation time.
        """

        if session.in_transaction():
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_RECONCILIATION_SESSION_NOT_CLEAN"
            )
        try:
            resolved_snapshot = self.broker.sync_state_checked(
                self._gate.assert_reconciliation_baseline
            )
        except CtpSimPreSyncCheckRejected as exc:
            # The locked pre-sync comparison refused an externally changed
            # state before CTP-sim could advance it.  Reconcile the exact
            # captured state solely to discover/persist a lower-level
            # unexplained-order HALT; if it otherwise looks internally
            # explained, append a semantic-drift HALT ourselves.  Either way
            # this candidate cannot progress toward an intent or broker call.
            try:
                reconcile_broker_state(
                    session,
                    self.broker,
                    snapshot=exc.snapshot,
                    run_id=self.run_id,
                    profile_id=self.receipt.profile_id,
                )
            except RuntimeError as reconciliation_error:
                raise CtpSimCandidateExecutionError(
                    str(reconciliation_error)
                ) from reconciliation_error
            halt_for_reconciliation(
                session,
                profile_id=self.receipt.profile_id,
                broker="ctp_sim",
                account=self.broker.get_account(),
                reason="CTP_SIM_CANDIDATE_BROKER_STATE_CHANGED",
                evidence={"pre_sync_rejection": exc.reason},
            )
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_BROKER_STATE_CHANGED"
            ) from exc
        try:
            summary = reconcile_broker_state(
                session,
                self.broker,
                snapshot=resolved_snapshot,
                run_id=self.run_id,
                profile_id=self.receipt.profile_id,
            )
        except RuntimeError as exc:
            # Reconciliation has already persisted its fenced fail-closed
            # transition where possible.  Do not leak a lower-layer exception
            # across the candidate public boundary.
            raise CtpSimCandidateExecutionError(str(exc)) from exc
        # ``reconcile_broker_state`` owns and commits its fenced transaction.
        # Provenance reads must follow it so they cannot open a caller-owned
        # transaction before that routine establishes the clean boundary.
        self._assert_snapshot_provenance(session, resolved_snapshot)
        safety = _require_persisted_normal_reconciliation_safety_state(
            session,
            profile_id=self.receipt.profile_id,
            broker="ctp_sim",
            account=self.broker.get_account(),
        )
        if safety.reconciliation_state_hash != self.receipt.reconciliation_state_hash:
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_RECONCILIATION_STATE_CHANGED"
            )
        # ``sync_state_checked`` has already established that the pre-process
        # state matched the gate baseline under the simulator lock.  We may
        # advance that baseline only after the persisted reconciliation and
        # every observed order/fill provenance check above have succeeded.
        self._gate.accept_reconciled_snapshot(resolved_snapshot)
        account = str(resolved_snapshot.account or self.broker.get_account() or "").strip()
        refs = sorted(
            {
                str(row.get("order_ref") or "").strip()
                for row in [*resolved_snapshot.open_orders, *resolved_snapshot.completed_orders]
                if str(row.get("order_ref") or "").strip()
            }
            | {
                str(item.order_ref or "").strip()
                for item in resolved_snapshot.fills
                if str(item.order_ref or "").strip()
            }
        )
        consumptions = [
            find_execution_provenance_consumption(
                session,
                broker="ctp_sim",
                account=account,
                order_ref=order_ref,
            )
            for order_ref in refs
        ]
        if any(item is None for item in consumptions):  # Defensive: verification ran before write.
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_PROVENANCE_MISSING")
        consumption_hashes = tuple(
            sorted(str(item.order_hash) for item in consumptions if item is not None)
        )
        fill_exec_ids = tuple(
            sorted(
                str(item.exec_id)
                for item in resolved_snapshot.fills
                if item.exec_id is not None
            )
        )
        reconciliation_hash = canonical_json_sha256(
            {
                "format": "northstar.ctp-sim-candidate-reconciliation.v1",
                "receipt_hash": self.receipt.receipt_hash,
                "plan_hash": self.receipt.plan_hash,
                "consumption_order_hashes": list(consumption_hashes),
                "observed_order_refs": refs,
                "observed_fill_exec_ids": list(fill_exec_ids),
                "summary": summary,
            }
        )
        evidence_hash = canonical_json_sha256(
            {
                "format": "northstar.ctp-sim-candidate-execution-evidence.v1",
                "receipt_hash": self.receipt.receipt_hash,
                "plan_hash": self.receipt.plan_hash,
                "reconciliation_hash": reconciliation_hash,
            }
        )
        evidence = CtpSimCandidateExecutionEvidence(
            receipt_hash=self.receipt.receipt_hash,
            plan_hash=self.receipt.plan_hash,
            consumption_order_hashes=consumption_hashes,
            observed_order_refs=tuple(refs),
            observed_fill_exec_ids=fill_exec_ids,
            reconciliation_hash=reconciliation_hash,
            evidence_hash=evidence_hash,
        )
        # The remaining verification above is read-only.  End its autobegun
        # transaction so the next candidate leg/reconciliation has a fresh
        # explicitly owned transaction rather than inheriting this one.
        if session.in_transaction():
            session.rollback()
        return summary, evidence

    def reconcile(
        self,
        session: Session,
    ) -> tuple[dict[str, object], CtpSimCandidateExecutionEvidence]:
        """Reconcile while cleaning only a transaction this boundary opened."""

        if not isinstance(session, Session):
            return self._reconcile(session)
        caller_transaction_active = session.in_transaction()
        try:
            return self._reconcile(session)
        except Exception:
            # Do not alter a caller-owned transaction: public entry rejects it
            # before reconciliation.  If this boundary began a read-only
            # verification transaction after its committed reconcile, end it
            # so an immediate retry starts cleanly.
            if not caller_transaction_active and session.in_transaction():
                session.rollback()
            raise

    def submit(self, session: Session) -> tuple[OrderResult, ...]:
        """Persist and submit only the internally-derived candidate order batch."""

        self._gate.bind_session(session)
        try:
            self._gate.assert_active_profile_current()
            # A fresh reconciliation occurs before a lease, durable intent, or broker action.
            self.reconcile(session)
            self._gate.mark_pre_submit_reconciled()
            account = self.broker.get_account()
            owner_token = f"p8-ctp-sim-{uuid4().hex}"
            lease_resource = f"ctp_sim_candidate:{account}"
            fence = try_acquire_execution_lease(
                session,
                resource_key=lease_resource,
                owner_token=owner_token,
                ttl_seconds=self.prepared_request.settings.execution_lease_ttl_seconds,
            )
            if fence is None:
                raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_LEASE_UNAVAILABLE")
            lease = SubmissionLease(
                resource_key=lease_resource,
                owner_token=owner_token,
                fencing_token=fence,
                ttl_seconds=self.prepared_request.settings.execution_lease_ttl_seconds,
            )
            try:
                authority = self.broker.submission_authority
                if authority is None or not authority.is_bound_to(self._gate):
                    raise CtpSimCandidateExecutionError(
                        "CTP_SIM_CANDIDATE_AUTHORITY_BINDING_LOST"
                    )
                # Every leg is first checked without producing a consumption fact.
                # This proves the whole batch can enter routing before a plan,
                # durable intent, or one-time consumption becomes persistent.
                for order in self.orders:
                    self._gate.prevalidate(order)

                plans_by_id = {
                    str(plan.plan_id or "").strip(): plan for plan in self.plans
                }
                if (
                    not plans_by_id
                    or "" in plans_by_id
                    or len(plans_by_id) != len(self.plans)
                ):
                    raise CtpSimCandidateExecutionError(
                        "CTP_SIM_CANDIDATE_PLAN_IDENTITY_INVALID"
                    )
                staged_plan_ids: set[str] = set()

                def stage_plan_with_durable_intent(order: OrderRequest) -> None:
                    plan_id = str(order.plan_id or "").strip()
                    plan = plans_by_id.get(plan_id)
                    if plan is None:
                        raise CtpSimCandidateExecutionError(
                            "CTP_SIM_CANDIDATE_PLAN_ORDER_BINDING_MISMATCH"
                        )
                    if plan_id in staged_plan_ids:
                        raise CtpSimCandidateExecutionError(
                            "CTP_SIM_CANDIDATE_PLAN_ALREADY_STAGED"
                        )
                    save_execution_plan_records(
                        session,
                        [plan],
                        run_id=self.run_id,
                        batch_id=self.batch_id,
                        profile_id=self.receipt.profile_id,
                        execution_planner_id="p8_ctp_sim_candidate",
                        commit=False,
                    )
                    staged_plan_ids.add(plan_id)

                durable = DurableBrokerAdapter(
                    self.broker,
                    session,
                    lease=lease,
                    ctp_sim_submission_authority=authority,
                    before_durable_intent=stage_plan_with_durable_intent,
                )
                router = OrderRouter(
                    durable,
                    RiskLimits(max_order_notional=None, enforce_available_cash=True),
                    OrderRiskContext(
                        available_cash=_available_cash(
                            self.prepared_request.account_snapshot
                        )
                    ),
                )
                results: list[OrderResult] = []
                try:
                    for order in self.orders:
                        results.append(router.route(order))
                except Exception:
                    # A leg that has not reached ``prepare_order_submission``
                    # has no durable intent.  Roll back its staged plan and
                    # consumption before lease release can commit anything.
                    session.rollback()
                    raise
                return tuple(results)
            finally:
                release_execution_lease(
                    session,
                    resource_key=lease_resource,
                    owner_token=owner_token,
                    fencing_token=fence,
                )
        finally:
            self._gate.unbind_session()


class CtpSimCandidateExecutor:
    """Derive guarded CTP-sim submissions from a full provenance request only."""

    __slots__ = ("_clock", "_owner_token", "_settings_provider")

    def __init__(self) -> None:
        """Bind production-only runtime sources at construction time.

        Candidate execution may not accept caller-selected settings or clocks:
        those inputs could relax a kill switch, expiry, profile directory, or
        CTP-sim state binding.  Test composition has a separate helper which
        deliberately changes private slots after construction.
        """

        self._settings_provider = load_settings
        self._clock = utc_now
        self._owner_token = object()

    def create_broker(self) -> CtpSimBrokerAdapter:
        """Create an isolated simulator bound to a private final submission gate."""

        settings = self._settings_provider()
        _require_safe_ctp_sim_settings(settings)
        gate = _CtpSimCandidateSubmissionGate(
            settings_provider=self._settings_provider,
            clock=self._clock,
            owner_token=self._owner_token,
        )
        return CtpSimBrokerAdapter(
            state_path=settings.ctp_sim_state_path,
            mapping_path=settings.ctp_sim_contract_mapping_path,
            account=settings.ctp_sim_account,
            default_cash=settings.default_cash,
            submission_authority=_issue_ctp_sim_submission_authority(gate),
        )

    def _assert_owned_broker(
        self,
        *,
        broker: CtpSimBrokerAdapter,
        gate: _CtpSimCandidateSubmissionGate,
        settings: Settings,
        profile: TradingProfile,
    ) -> None:
        if not gate.is_owned_by(self._owner_token):
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_FOREIGN_BROKER")
        _assert_broker_matches_settings(
            broker=broker,
            settings=settings,
            profile=profile,
        )

    def _prepare(
        self,
        request: ExecutionProvenanceRequest,
        *,
        session: Session,
        broker: CtpSimBrokerAdapter,
        run_id: str,
        batch_id: str,
    ) -> CtpSimCandidateExecutionBundle:
        """Replay source evidence and derive canonical orders without submitting them."""

        if type(request) is not ExecutionProvenanceRequest:
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_REQUEST_REQUIRED")
        if not isinstance(session, Session):
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_SESSION_REQUIRED")
        if session.in_transaction():
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_RECONCILIATION_SESSION_NOT_CLEAN"
            )
        if type(broker) is not CtpSimBrokerAdapter:
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_ADAPTER_REQUIRED")
        authority = broker.submission_authority
        if authority is None:
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_GUARD_REQUIRED")
        gate = authority._guard_for_composition()
        if not isinstance(gate, _CtpSimCandidateSubmissionGate):
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_GUARD_REQUIRED")
        settings = self._settings_provider()
        _require_safe_ctp_sim_settings(settings)
        active_profile = _load_active_profile(
            settings=settings,
            claimed_profile=request.profile,
        )
        self._assert_owned_broker(
            broker=broker,
            gate=gate,
            settings=settings,
            profile=active_profile,
        )
        request = replace(request, profile=active_profile)
        snapshot = broker.read_state_snapshot()
        reconcile_broker_state(
            session,
            broker,
            snapshot=snapshot,
            run_id=str(run_id).strip(),
            profile_id=request.profile.profile_id,
        )
        reconciliation_safety_state = _require_persisted_normal_reconciliation_safety_state(
            session,
            profile_id=request.profile.profile_id,
            broker="ctp_sim",
            account=broker.get_account(),
        )
        prepared_request = _refresh_request(
            request=request,
            broker=broker,
            settings=settings,
            clock=self._clock,
            snapshot=snapshot,
            reconciliation_safety_state=reconciliation_safety_state,
        )
        manual_approval = _require_persisted_manual_risk_approval(
            session,
            request=prepared_request,
            broker=broker,
            checked_at=prepared_request.checked_at,
        )
        try:
            evaluation = ExecutionProvenancePreflight()._evaluate(prepared_request)
        except ExecutionProvenancePreflightError as exc:
            raise CtpSimCandidateExecutionError(str(exc)) from exc
        receipt = evaluation.receipt
        commitments = {item.order_hash: item for item in receipt.order_commitments}
        plans: list[RebalanceOrderPlan] = []
        orders: list[OrderRequest] = []
        expected: list[_ExpectedCandidateOrder] = []
        for raw_plan in evaluation.execution_plan.orders:
            commitment = _commitment_for_plan(raw_plan)
            receipt_commitment = commitments.get(commitment.order_hash)
            if receipt_commitment is None:
                raise CtpSimCandidateExecutionError(
                    "CTP_SIM_CANDIDATE_PLAN_RECEIPT_MISMATCH"
                )
            _assert_same_commitment(receipt_commitment, commitment)
            durable_plan_id = _durable_plan_id(
                plan_hash=receipt.plan_hash,
                order_hash=commitment.order_hash,
            )
            plan = replace(raw_plan, plan_id=durable_plan_id)
            unprepared_order = OrderRequest(
                strategy_id=plan.strategy_id,
                symbol=plan.symbol,
                side=plan.side,
                qty=plan.qty,
                profile_id=receipt.profile_id,
                target_weight=plan.target_weight,
                order_type=plan.order_type,
                limit_price=plan.limit_price,
                order_semantic=plan.order_semantic,
                account=broker.get_account(),
                reason=plan.reason,
                reference_price=plan.execution_reference_price,
                reference_price_source="ctp_sim_market_data",
                planned_trade_value=plan.estimated_trade_value,
                run_id=str(run_id).strip(),
                batch_id=str(batch_id).strip(),
                plan_id=durable_plan_id,
                attempt_no=1,
                execution_planner_id="p8_ctp_sim_candidate",
                instrument_id=plan.instrument_id,
                exchange_id=plan.exchange_id,
                ctp_offset=plan.ctp_offset,
                volume_multiple=plan.volume_multiple,
                margin_rate=plan.margin_rate,
                required_margin=plan.required_margin,
                currency=settings.trading_currency,
            )
            canonical_order = broker.prepare_order(unprepared_order)
            order_ref = str(canonical_order.order_ref or "").strip()
            if order_ref != build_order_ref(durable_plan_id, 1):
                raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_ORDER_REF_INVALID")
            expected.append(
                _ExpectedCandidateOrder(
                    commitment=receipt_commitment,
                    durable_plan_id=durable_plan_id,
                    order_ref=order_ref,
                    order_binding_hash=_order_binding(canonical_order),
                )
            )
            plans.append(plan)
            orders.append(canonical_order)
        if len(expected) != len(commitments):
            raise CtpSimCandidateExecutionError("CTP_SIM_CANDIDATE_COMMITMENT_COVERAGE_MISMATCH")
        gate.configure(
            broker=broker,
            source_request=request,
            prepared_request=prepared_request,
            receipt=receipt,
            expected_orders=tuple(expected),
            manual_approval=manual_approval,
        )
        # P8 evaluation and grant lookup are deliberately read-only after the
        # successful reconciliation commit.  Do not return an autobegun read
        # transaction to ``submit``: it must start its own reconciliation and
        # advisory-fence transaction.
        if session.in_transaction():
            session.rollback()
        return CtpSimCandidateExecutionBundle(
            broker=broker,
            source_request=request,
            prepared_request=prepared_request,
            receipt=receipt,
            plans=tuple(plans),
            orders=tuple(orders),
            run_id=str(run_id).strip(),
            batch_id=str(batch_id).strip(),
            _gate=gate,
        )

    def prepare(
        self,
        request: ExecutionProvenanceRequest,
        *,
        session: Session,
        broker: CtpSimBrokerAdapter,
        run_id: str,
        batch_id: str,
    ) -> CtpSimCandidateExecutionBundle:
        """Prepare a candidate batch without retaining P8 read transactions."""

        if not isinstance(session, Session):
            return self._prepare(
                request,
                session=session,
                broker=broker,
                run_id=run_id,
                batch_id=batch_id,
            )
        caller_transaction_active = session.in_transaction()
        try:
            return self._prepare(
                request,
                session=session,
                broker=broker,
                run_id=run_id,
                batch_id=batch_id,
            )
        except Exception:
            # The clean-session guard protects callers' own work.  Once entry
            # was clean, however, every later lookup is candidate-owned and
            # must be ended on a refusal so retry cannot inherit a stale read
            # transaction.
            if not caller_transaction_active and session.in_transaction():
                session.rollback()
            raise

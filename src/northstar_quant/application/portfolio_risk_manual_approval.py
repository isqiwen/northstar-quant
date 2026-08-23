"""Durable, verifier-backed authority for P3 portfolio-risk approvals.

P3 deliberately remains a pure replayable domain: its attestation value is a
claim, not a trusted credential.  The shipped production composition contains
only an unavailable verifier, so it cannot issue an approval grant.  The
successful issuance machinery is private and used solely by test composition;
in-process test substitutes do not prove human identity and are excluded from
the production package surface.

BLOCKED_EXTERNAL: a deployed human-approval service (or another privileged
external issuer) must authenticate the approver and write grants with a
dedicated database role.  The CTP-sim candidate database role must be
SELECT-only for ``portfolio_risk_approval_records``.  Until those external
credentials and roles are supplied, production issuance remains unavailable.

The module is CTP-sim-only and never creates an execution plan, durable broker
intent, or broker-side effect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import re
from typing import NoReturn, Protocol

from sqlalchemy.orm import Session

from northstar_quant.application.portfolio_risk_authority import PortfolioRiskApprovalAuthority
from northstar_quant.data.artifacts.fingerprints import canonical_json_sha256
from northstar_quant.foundation.config.trading_profile import (
    ProfilePortfolioRiskApprovalConfig,
    TradingProfile,
)
from northstar_quant.foundation.db.repositories import (
    find_portfolio_risk_approval,
    record_portfolio_risk_approval,
)
from northstar_quant.portfolio_risk.portfolio import (
    ApprovedPortfolioTarget,
    PortfolioCompositionEvidence,
    PortfolioRiskApprovalEvidence,
    PortfolioRiskApprovalError,
    PortfolioRiskApprovalGate,
    PortfolioRiskApprovalRequest,
    PortfolioRiskReview,
    RiskApprovalAttestation,
)


__all__ = [
    "ManualRiskApprovalBinding",
    "ManualRiskApprovalError",
    "PersistedPortfolioRiskApproval",
    "require_persisted_portfolio_risk_approval",
]


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_TEXT_LENGTH = 512
_VERIFIED_MANUAL_RISK_APPROVAL_CAPABILITY = object()
_TRUSTED_ISSUER_COMPOSITION_CAPABILITY = object()


class ManualRiskApprovalError(ValueError):
    """A manual approval could not be verified, persisted, or replayed safely."""


def _refuse(code: str) -> NoReturn:
    raise ManualRiskApprovalError(code)


def _identifier(value: object, *, code: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value.strip()) is None:
        _refuse(code)
    return value.strip()


def _hash(value: object, *, code: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        _refuse(code)
    return value


def _text(value: object, *, code: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\x00" in value
        or "\r" in value
        or "\n" in value
        or len(value) > _MAX_TEXT_LENGTH
    ):
        _refuse(code)
    return value.strip()


def _time(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _refuse(code)
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ManualRiskApprovalBinding:
    """Exact P3/P5 facts that an external manual verifier must approve."""

    approval_id: str
    profile_id: str
    broker: str
    account: str
    verifier_id: str
    review_hash: str
    portfolio_target_hash: str
    composition_hash: str
    composition_evidence_hash: str
    authority_hash: str
    policy_hash: str
    reconciliation_state_hash: str
    review_evaluated_at: datetime
    valid_until: datetime
    binding_hash: str = field(init=False)

    def __post_init__(self) -> None:
        for name in (
            "approval_id",
            "profile_id",
            "account",
            "verifier_id",
        ):
            object.__setattr__(
                self,
                name,
                _identifier(
                    getattr(self, name),
                    code="MANUAL_RISK_APPROVAL_BINDING_IDENTITY_INVALID",
                ),
            )
        broker = _identifier(
            self.broker,
            code="MANUAL_RISK_APPROVAL_BINDING_BROKER_INVALID",
        ).lower()
        if broker != "ctp_sim":
            _refuse("MANUAL_RISK_APPROVAL_BINDING_BROKER_REFUSED")
        object.__setattr__(self, "broker", broker)
        for name in (
            "review_hash",
            "portfolio_target_hash",
            "composition_hash",
            "composition_evidence_hash",
            "authority_hash",
            "policy_hash",
            "reconciliation_state_hash",
        ):
            object.__setattr__(
                self,
                name,
                _hash(
                    getattr(self, name),
                    code="MANUAL_RISK_APPROVAL_BINDING_HASH_INVALID",
                ),
            )
        review_evaluated_at = _time(
            self.review_evaluated_at,
            code="MANUAL_RISK_APPROVAL_BINDING_TIME_INVALID",
        )
        valid_until = _time(
            self.valid_until,
            code="MANUAL_RISK_APPROVAL_BINDING_TIME_INVALID",
        )
        if valid_until <= review_evaluated_at:
            _refuse("MANUAL_RISK_APPROVAL_BINDING_WINDOW_INVALID")
        object.__setattr__(self, "review_evaluated_at", review_evaluated_at)
        object.__setattr__(self, "valid_until", valid_until)
        object.__setattr__(
            self,
            "binding_hash",
            canonical_json_sha256(self.as_mapping(include_hash=False)),
        )

    def as_mapping(self, *, include_hash: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "format": "northstar.manual-portfolio-risk-approval-binding.v1",
            "approval_id": self.approval_id,
            "profile_id": self.profile_id,
            "broker": self.broker,
            "account": self.account,
            "verifier_id": self.verifier_id,
            "review_hash": self.review_hash,
            "portfolio_target_hash": self.portfolio_target_hash,
            "composition_hash": self.composition_hash,
            "composition_evidence_hash": self.composition_evidence_hash,
            "authority_hash": self.authority_hash,
            "policy_hash": self.policy_hash,
            "reconciliation_state_hash": self.reconciliation_state_hash,
            "review_evaluated_at": self.review_evaluated_at.isoformat(),
            "valid_until": self.valid_until.isoformat(),
        }
        if include_hash:
            result["binding_hash"] = self.binding_hash
        return result


@dataclass(frozen=True, slots=True)
class _VerifiedManualRiskApproval:
    """Ephemeral verifier result with only a receipt digest, never raw proof."""

    binding: ManualRiskApprovalBinding
    approver_id: str
    verifier_id: str
    verifier_receipt_hash: str
    approved_at: datetime
    verified_at: datetime
    rationale: str
    _verification_capability: object = field(repr=False, compare=False, kw_only=True)
    verification_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self._verification_capability is not _VERIFIED_MANUAL_RISK_APPROVAL_CAPABILITY:
            _refuse("MANUAL_RISK_APPROVAL_VERIFIED_RESULT_FACTORY_REQUIRED")
        if type(self.binding) is not ManualRiskApprovalBinding:
            _refuse("MANUAL_RISK_APPROVAL_VERIFICATION_BINDING_INVALID")
        approver_id = _identifier(
            self.approver_id,
            code="MANUAL_RISK_APPROVAL_VERIFICATION_APPROVER_INVALID",
        )
        verifier_id = _identifier(
            self.verifier_id,
            code="MANUAL_RISK_APPROVAL_VERIFICATION_VERIFIER_INVALID",
        )
        if verifier_id != self.binding.verifier_id:
            _refuse("MANUAL_RISK_APPROVAL_VERIFICATION_VERIFIER_MISMATCH")
        receipt_hash = _hash(
            self.verifier_receipt_hash,
            code="MANUAL_RISK_APPROVAL_VERIFICATION_RECEIPT_HASH_INVALID",
        )
        approved_at = _time(
            self.approved_at,
            code="MANUAL_RISK_APPROVAL_VERIFICATION_TIME_INVALID",
        )
        verified_at = _time(
            self.verified_at,
            code="MANUAL_RISK_APPROVAL_VERIFICATION_TIME_INVALID",
        )
        if not (
            self.binding.review_evaluated_at
            <= approved_at
            <= verified_at
            < self.binding.valid_until
        ):
            _refuse("MANUAL_RISK_APPROVAL_VERIFICATION_WINDOW_INVALID")
        object.__setattr__(self, "approver_id", approver_id)
        object.__setattr__(self, "verifier_id", verifier_id)
        object.__setattr__(self, "verifier_receipt_hash", receipt_hash)
        object.__setattr__(self, "approved_at", approved_at)
        object.__setattr__(self, "verified_at", verified_at)
        object.__setattr__(
            self,
            "rationale",
            _text(self.rationale, code="MANUAL_RISK_APPROVAL_VERIFICATION_RATIONALE_INVALID"),
        )
        object.__setattr__(
            self,
            "verification_hash",
            canonical_json_sha256(self.as_mapping(include_hash=False)),
        )

    def as_mapping(self, *, include_hash: bool = True) -> dict[str, object]:
        result: dict[str, object] = {
            "format": "northstar.verified-manual-portfolio-risk-approval.v1",
            "binding_hash": self.binding.binding_hash,
            "approver_id": self.approver_id,
            "verifier_id": self.verifier_id,
            "verifier_receipt_hash": self.verifier_receipt_hash,
            "approved_at": self.approved_at.isoformat(),
            "verified_at": self.verified_at.isoformat(),
            "rationale": self.rationale,
        }
        if include_hash:
            result["verification_hash"] = self.verification_hash
        return result


def _verified_manual_risk_approval_from_trusted_verifier(
    *,
    binding: ManualRiskApprovalBinding,
    approver_id: str,
    verifier_id: str,
    verifier_receipt_hash: str,
    approved_at: datetime,
    verified_at: datetime,
    rationale: str,
) -> _VerifiedManualRiskApproval:
    """Construct a verifier result only for a composition-owned verifier."""

    return _VerifiedManualRiskApproval(
        binding=binding,
        approver_id=approver_id,
        verifier_id=verifier_id,
        verifier_receipt_hash=verifier_receipt_hash,
        approved_at=approved_at,
        verified_at=verified_at,
        rationale=rationale,
        _verification_capability=_VERIFIED_MANUAL_RISK_APPROVAL_CAPABILITY,
    )


class _ManualRiskApprovalVerifier(Protocol):
    """External manual-approval verifier; it must not return raw proof data."""

    @property
    def verifier_id(self) -> str:
        """Stable identifier for the external verification authority."""

    def verify(
        self,
        *,
        binding: ManualRiskApprovalBinding,
    ) -> _VerifiedManualRiskApproval:
        """Verify a human approval for the exact immutable binding."""


@dataclass(frozen=True, slots=True)
class _UnavailableManualRiskApprovalVerifier:
    """Safe default verifier used until a real external verifier is configured."""

    verifier_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "verifier_id",
            _identifier(
                self.verifier_id,
                code="MANUAL_RISK_APPROVAL_UNAVAILABLE_VERIFIER_ID_INVALID",
            ),
        )

    def verify(
        self,
        *,
        binding: ManualRiskApprovalBinding,
    ) -> _VerifiedManualRiskApproval:
        del binding
        _refuse("MANUAL_RISK_APPROVAL_VERIFIER_UNAVAILABLE")


class _UnavailableManualRiskApprovalVerifierRegistry:
    """The sole default composition registry: every verifier is unavailable."""

    __slots__ = ()

    def resolve(self, verifier_id: str) -> _ManualRiskApprovalVerifier:
        return _UnavailableManualRiskApprovalVerifier(verifier_id)


_DEFAULT_MANUAL_RISK_APPROVAL_VERIFIER_REGISTRY = (
    _UnavailableManualRiskApprovalVerifierRegistry()
)


@dataclass(frozen=True, slots=True)
class _IssuedPortfolioRiskApproval:
    """P3 evidence plus the durable immutable grant that authorizes its use."""

    binding: ManualRiskApprovalBinding
    verified_approval: _VerifiedManualRiskApproval
    approval_request: PortfolioRiskApprovalRequest
    approval_evidence: PortfolioRiskApprovalEvidence
    record_hash: str

    def __post_init__(self) -> None:
        if type(self.binding) is not ManualRiskApprovalBinding:
            _refuse("ISSUED_PORTFOLIO_RISK_APPROVAL_BINDING_INVALID")
        if type(self.verified_approval) is not _VerifiedManualRiskApproval:
            _refuse("ISSUED_PORTFOLIO_RISK_APPROVAL_VERIFICATION_INVALID")
        if self.verified_approval.binding != self.binding:
            _refuse("ISSUED_PORTFOLIO_RISK_APPROVAL_BINDING_MISMATCH")
        if type(self.approval_request) is not PortfolioRiskApprovalRequest:
            _refuse("ISSUED_PORTFOLIO_RISK_APPROVAL_REQUEST_INVALID")
        if type(self.approval_evidence) is not PortfolioRiskApprovalEvidence:
            _refuse("ISSUED_PORTFOLIO_RISK_APPROVAL_EVIDENCE_INVALID")
        attestation = self.approval_request.attestation
        verified = self.verified_approval
        if (
            attestation.approval_id != self.binding.approval_id
            or attestation.review_hash != self.binding.review_hash
            or attestation.approver_id != verified.approver_id
            or attestation.approved_at != verified.approved_at
            or attestation.rationale != verified.rationale
        ):
            _refuse("ISSUED_PORTFOLIO_RISK_APPROVAL_ATTESTATION_MISMATCH")
        if self.approval_evidence.approval_request != self.approval_request:
            _refuse("ISSUED_PORTFOLIO_RISK_APPROVAL_EVIDENCE_REQUEST_MISMATCH")
        approved_target = self.approval_evidence.approved_target
        if type(approved_target) is not ApprovedPortfolioTarget:
            _refuse("ISSUED_PORTFOLIO_RISK_APPROVAL_P3_REFUSED")
        if (
            self.approval_evidence.review.review_hash != self.binding.review_hash
            or approved_target.portfolio_target.target_hash != self.binding.portfolio_target_hash
            or self.approval_evidence.composition_evidence_hash
            != self.binding.composition_evidence_hash
        ):
            _refuse("ISSUED_PORTFOLIO_RISK_APPROVAL_P3_BINDING_MISMATCH")
        object.__setattr__(
            self,
            "record_hash",
            _hash(
                self.record_hash,
                code="ISSUED_PORTFOLIO_RISK_APPROVAL_RECORD_HASH_INVALID",
            ),
        )

    @property
    def approval_id(self) -> str:
        return self.binding.approval_id

    @property
    def valid_until(self) -> datetime:
        return self.binding.valid_until


@dataclass(frozen=True, slots=True)
class PersistedPortfolioRiskApproval:
    """Typed exact binding returned to CTP-sim candidate composition."""

    binding: ManualRiskApprovalBinding
    record_hash: str

    def __post_init__(self) -> None:
        if type(self.binding) is not ManualRiskApprovalBinding:
            _refuse("PERSISTED_PORTFOLIO_RISK_APPROVAL_BINDING_INVALID")
        object.__setattr__(
            self,
            "record_hash",
            _hash(
                self.record_hash,
                code="PERSISTED_PORTFOLIO_RISK_APPROVAL_RECORD_HASH_INVALID",
            ),
        )

    @property
    def approval_id(self) -> str:
        return self.binding.approval_id

    @property
    def valid_until(self) -> datetime:
        return self.binding.valid_until


def _profile_config(profile: TradingProfile) -> ProfilePortfolioRiskApprovalConfig:
    if type(profile) is not TradingProfile:
        _refuse("MANUAL_RISK_APPROVAL_PROFILE_REQUIRED")
    config = profile.portfolio_risk_approval
    if type(config) is not ProfilePortfolioRiskApprovalConfig:
        _refuse("MANUAL_RISK_APPROVAL_PROFILE_CONFIG_MISSING")
    return config


def _replayed_review(
    *,
    profile: TradingProfile,
    broker: str,
    account: str,
    authority: PortfolioRiskApprovalAuthority,
    composition: PortfolioCompositionEvidence,
) -> tuple[ProfilePortfolioRiskApprovalConfig, PortfolioRiskReview]:
    config = _profile_config(profile)
    normalized_broker = _identifier(
        broker,
        code="MANUAL_RISK_APPROVAL_BROKER_INVALID",
    ).lower()
    if normalized_broker != "ctp_sim":
        _refuse("MANUAL_RISK_APPROVAL_BROKER_REFUSED")
    normalized_account = _identifier(
        account,
        code="MANUAL_RISK_APPROVAL_ACCOUNT_INVALID",
    )
    if type(authority) is not PortfolioRiskApprovalAuthority:
        _refuse("MANUAL_RISK_APPROVAL_AUTHORITY_REQUIRED")
    if type(composition) is not PortfolioCompositionEvidence:
        _refuse("MANUAL_RISK_APPROVAL_COMPOSITION_REQUIRED")
    request = authority.review_request
    state_snapshot = request.risk_state.state_snapshot
    if state_snapshot is None:
        _refuse("MANUAL_RISK_APPROVAL_AUTHORITY_RECONCILIATION_UNKNOWN")
    if (
        authority.profile_id != profile.profile_id
        or authority.policy_id != config.policy_id
        or authority.policy_version != config.policy_version
        or authority.config_hash != config.config_hash
        or authority.policy_hash != request.policy.policy_hash
        or authority.reconciliation_state_hash != state_snapshot.state_hash
    ):
        _refuse("MANUAL_RISK_APPROVAL_AUTHORITY_PROFILE_MISMATCH")
    if request.composition != composition:
        _refuse("MANUAL_RISK_APPROVAL_COMPOSITION_MISMATCH")
    if request.account_snapshot.account_id != normalized_account:
        _refuse("MANUAL_RISK_APPROVAL_ACCOUNT_MISMATCH")
    try:
        review = PortfolioRiskApprovalGate().review(request)
    except PortfolioRiskApprovalError as exc:
        raise ManualRiskApprovalError("MANUAL_RISK_APPROVAL_P3_REPLAY_REFUSED") from exc
    return config, review


def _binding(
    *,
    approval_id: str,
    profile: TradingProfile,
    broker: str,
    account: str,
    authority: PortfolioRiskApprovalAuthority,
    composition: PortfolioCompositionEvidence,
    checked_at: datetime,
) -> tuple[ProfilePortfolioRiskApprovalConfig, PortfolioRiskReview, ManualRiskApprovalBinding]:
    config, review = _replayed_review(
        profile=profile,
        broker=broker,
        account=account,
        authority=authority,
        composition=composition,
    )
    normalized_checked_at = _time(
        checked_at,
        code="MANUAL_RISK_APPROVAL_CHECKED_AT_INVALID",
    )
    if not review.eligible_for_approval:
        _refuse("MANUAL_RISK_APPROVAL_P3_REFUSED")
    if not review.evaluated_at <= normalized_checked_at < review.approval_valid_until:
        _refuse("MANUAL_RISK_APPROVAL_CHECKED_AT_OUTSIDE_VALIDITY")
    return (
        config,
        review,
        ManualRiskApprovalBinding(
            approval_id=approval_id,
            profile_id=profile.profile_id,
            broker=broker,
            account=account,
            verifier_id=config.manual_approval_verifier_id,
            review_hash=review.review_hash,
            portfolio_target_hash=review.portfolio_target.target_hash,
            composition_hash=composition.composition_hash,
            composition_evidence_hash=composition.evidence_hash,
            authority_hash=authority.authority_hash,
            policy_hash=authority.policy_hash,
            reconciliation_state_hash=authority.reconciliation_state_hash,
            review_evaluated_at=review.evaluated_at,
            valid_until=review.approval_valid_until,
        ),
    )


def _validate_verified_approval(
    *,
    config: ProfilePortfolioRiskApprovalConfig,
    binding: ManualRiskApprovalBinding,
    verified: _VerifiedManualRiskApproval,
    checked_at: datetime,
) -> None:
    if type(verified) is not _VerifiedManualRiskApproval:
        _refuse("MANUAL_RISK_APPROVAL_VERIFIER_RESULT_INVALID")
    if verified.binding != binding:
        _refuse("MANUAL_RISK_APPROVAL_VERIFIER_BINDING_MISMATCH")
    if verified.verifier_id != config.manual_approval_verifier_id:
        _refuse("MANUAL_RISK_APPROVAL_VERIFIER_ID_MISMATCH")
    if verified.approver_id not in config.authorized_approver_ids:
        _refuse("MANUAL_RISK_APPROVAL_APPROVER_UNAUTHORIZED")
    if verified.verified_at > checked_at:
        _refuse("MANUAL_RISK_APPROVAL_VERIFICATION_FROM_FUTURE")


class _PortfolioRiskApprovalIssuer:
    """Issue an immutable grant only after a configured verifier confirms P3 facts."""

    __slots__ = ("_verifier",)

    def __init__(self) -> None:
        """Build the safe default issuer; it always resolves to unavailable."""

        self._verifier: _ManualRiskApprovalVerifier | None = None

    @classmethod
    def _from_trusted_composition(
        cls,
        verifier: _ManualRiskApprovalVerifier,
        *,
        capability: object,
    ) -> "_PortfolioRiskApprovalIssuer":
        if capability is not _TRUSTED_ISSUER_COMPOSITION_CAPABILITY:
            _refuse("MANUAL_RISK_APPROVAL_ISSUER_FACTORY_REQUIRED")
        issuer = cls()
        issuer._verifier = verifier
        return issuer

    def _resolve_verifier(
        self,
        config: ProfilePortfolioRiskApprovalConfig,
    ) -> _ManualRiskApprovalVerifier:
        if self._verifier is not None:
            return self._verifier
        return _DEFAULT_MANUAL_RISK_APPROVAL_VERIFIER_REGISTRY.resolve(
            config.manual_approval_verifier_id
        )

    def issue(
        self,
        session: Session,
        *,
        profile: TradingProfile,
        broker: str,
        account: str,
        authority: PortfolioRiskApprovalAuthority,
        composition: PortfolioCompositionEvidence,
        approval_id: str,
        checked_at: datetime,
    ) -> _IssuedPortfolioRiskApproval:
        """Verify, replay, and persist a CTP-sim-only manual risk approval."""

        config, review, binding = _binding(
            approval_id=approval_id,
            profile=profile,
            broker=broker,
            account=account,
            authority=authority,
            composition=composition,
            checked_at=checked_at,
        )
        verifier = self._resolve_verifier(config)
        verifier_id = _identifier(
            getattr(verifier, "verifier_id", None),
            code="MANUAL_RISK_APPROVAL_VERIFIER_ID_INVALID",
        )
        if verifier_id != config.manual_approval_verifier_id:
            _refuse("MANUAL_RISK_APPROVAL_VERIFIER_ID_MISMATCH")
        verified = verifier.verify(binding=binding)
        _validate_verified_approval(
            config=config,
            binding=binding,
            verified=verified,
            checked_at=_time(checked_at, code="MANUAL_RISK_APPROVAL_CHECKED_AT_INVALID"),
        )
        attestation = RiskApprovalAttestation(
            approval_id=binding.approval_id,
            review_hash=binding.review_hash,
            approver_id=verified.approver_id,
            approved_at=verified.approved_at,
            rationale=verified.rationale,
        )
        approval_request = PortfolioRiskApprovalRequest(
            review_request=authority.review_request,
            attestation=attestation,
        )
        try:
            approval_evidence = PortfolioRiskApprovalGate().evaluate(approval_request)
        except PortfolioRiskApprovalError as exc:
            raise ManualRiskApprovalError("MANUAL_RISK_APPROVAL_P3_REPLAY_REFUSED") from exc
        approved_target = approval_evidence.approved_target
        if type(approved_target) is not ApprovedPortfolioTarget:
            _refuse("MANUAL_RISK_APPROVAL_P3_REFUSED")
        if approval_evidence.review != review:
            _refuse("MANUAL_RISK_APPROVAL_REVIEW_REPLAY_MISMATCH")
        row = record_portfolio_risk_approval(
            session,
            approval_id=binding.approval_id,
            profile_id=binding.profile_id,
            broker=binding.broker,
            account=binding.account,
            review_hash=review.review_hash,
            evidence_hash=approval_evidence.evidence_hash,
            portfolio_target_hash=review.portfolio_target.target_hash,
            approved_target_hash=approved_target.approval_hash,
            composition_hash=composition.composition_hash,
            composition_evidence_hash=composition.evidence_hash,
            authority_hash=authority.authority_hash,
            policy_hash=authority.policy_hash,
            reconciliation_state_hash=authority.reconciliation_state_hash,
            binding_hash=binding.binding_hash,
            attestation_hash=attestation.attestation_hash,
            approver_id=verified.approver_id,
            verifier_id=verified.verifier_id,
            verifier_receipt_hash=verified.verifier_receipt_hash,
            rationale=verified.rationale,
            review_evaluated_at=review.evaluated_at,
            approved_at=verified.approved_at,
            verified_at=verified.verified_at,
            valid_until=review.approval_valid_until,
            issued_at=_time(checked_at, code="MANUAL_RISK_APPROVAL_CHECKED_AT_INVALID"),
        )
        record_hash = row.record_hash
        # The repository verifies exact idempotency with reads and refreshes a
        # newly committed row, which leaves SQLAlchemy in an autobegun read
        # transaction.  Test-only issuance must not hand that transaction to
        # the candidate's independent, clean-session boundary.  The durable
        # row is already committed (or was pre-existing) before this point.
        if session.in_transaction():
            session.rollback()
        return _IssuedPortfolioRiskApproval(
            binding=binding,
            verified_approval=verified,
            approval_request=approval_request,
            approval_evidence=approval_evidence,
            record_hash=record_hash,
        )


def _create_portfolio_risk_approval_issuer_for_test(
    verifier: _ManualRiskApprovalVerifier,
) -> _PortfolioRiskApprovalIssuer:
    """Private test-composition hook; production has no verifier injection API."""

    return _PortfolioRiskApprovalIssuer._from_trusted_composition(
        verifier,
        capability=_TRUSTED_ISSUER_COMPOSITION_CAPABILITY,
    )


def require_persisted_portfolio_risk_approval(
    session: Session,
    *,
    profile: TradingProfile,
    broker: str,
    account: str,
    authority: PortfolioRiskApprovalAuthority,
    approval_request: PortfolioRiskApprovalRequest,
    approval_evidence: PortfolioRiskApprovalEvidence,
    checked_at: datetime,
) -> PersistedPortfolioRiskApproval:
    """Require an exact immutable grant for caller-supplied P3 approval claims.

    This function is intentionally the consumer-side boundary.  It first
    rebuilds the P3 output from the trusted authority and then requires every
    hash, identity, attestation field, validity bound, and durable record hash
    to match.  Missing, stale, forged, or cross-account approvals fail closed.
    """

    if type(approval_request) is not PortfolioRiskApprovalRequest:
        _refuse("PERSISTED_PORTFOLIO_RISK_APPROVAL_REQUEST_INVALID")
    if type(approval_evidence) is not PortfolioRiskApprovalEvidence:
        _refuse("PERSISTED_PORTFOLIO_RISK_APPROVAL_EVIDENCE_INVALID")
    composition = (
        authority.review_request.composition
        if type(authority) is PortfolioRiskApprovalAuthority
        else None
    )
    if type(composition) is not PortfolioCompositionEvidence:
        _refuse("PERSISTED_PORTFOLIO_RISK_APPROVAL_COMPOSITION_INVALID")
    if approval_request.review_request != authority.review_request:
        _refuse("PERSISTED_PORTFOLIO_RISK_APPROVAL_AUTHORITY_MISMATCH")
    config, review, binding = _binding(
        approval_id=approval_request.attestation.approval_id,
        profile=profile,
        broker=broker,
        account=account,
        authority=authority,
        composition=composition,
        checked_at=checked_at,
    )
    try:
        replayed_evidence = PortfolioRiskApprovalGate().evaluate(approval_request)
    except PortfolioRiskApprovalError as exc:
        raise ManualRiskApprovalError("PERSISTED_PORTFOLIO_RISK_APPROVAL_REPLAY_REFUSED") from exc
    if replayed_evidence != approval_evidence:
        _refuse("PERSISTED_PORTFOLIO_RISK_APPROVAL_EVIDENCE_MISMATCH")
    approved_target = replayed_evidence.approved_target
    if type(approved_target) is not ApprovedPortfolioTarget:
        _refuse("PERSISTED_PORTFOLIO_RISK_APPROVAL_P3_REFUSED")
    try:
        record = find_portfolio_risk_approval(
            session,
            approval_id=binding.approval_id,
            profile_id=binding.profile_id,
            broker=binding.broker,
            account=binding.account,
        )
    except (PermissionError, RuntimeError, ValueError) as exc:
        raise ManualRiskApprovalError(
            "PERSISTED_PORTFOLIO_RISK_APPROVAL_RECORD_INVALID"
        ) from exc
    if record is None:
        _refuse("PERSISTED_PORTFOLIO_RISK_APPROVAL_MISSING")
    attestation = approval_request.attestation
    exact = (
        record.review_hash == review.review_hash
        and record.evidence_hash == replayed_evidence.evidence_hash
        and record.portfolio_target_hash == review.portfolio_target.target_hash
        and record.approved_target_hash == approved_target.approval_hash
        and record.composition_hash == composition.composition_hash
        and record.composition_evidence_hash == composition.evidence_hash
        and record.authority_hash == authority.authority_hash
        and record.policy_hash == authority.policy_hash
        and record.reconciliation_state_hash == authority.reconciliation_state_hash
        and record.binding_hash == binding.binding_hash
        and record.attestation_hash == attestation.attestation_hash
        and record.approver_id == attestation.approver_id
        and record.verifier_id == config.manual_approval_verifier_id
        and record.approver_id in config.authorized_approver_ids
        and record.rationale == attestation.rationale
        and record.review_evaluated_at == review.evaluated_at
        and record.approved_at == attestation.approved_at
        and record.valid_until == review.approval_valid_until
    )
    if not exact:
        _refuse("PERSISTED_PORTFOLIO_RISK_APPROVAL_MISMATCH")
    normalized_checked_at = _time(
        checked_at,
        code="PERSISTED_PORTFOLIO_RISK_APPROVAL_CHECKED_AT_INVALID",
    )
    if not record.issued_at <= normalized_checked_at < record.valid_until:
        _refuse("PERSISTED_PORTFOLIO_RISK_APPROVAL_EXPIRED")
    return PersistedPortfolioRiskApproval(
        binding=binding,
        record_hash=record.record_hash,
    )

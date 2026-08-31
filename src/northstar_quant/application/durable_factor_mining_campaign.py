"""Durable application seam for one bounded local factor-mining campaign.

The factor-mining domain deliberately remains free of database, worker and
provider capabilities.  This module is the narrow composition seam that
surrounds a trusted local execution adapter with an append-only ledger.  It
does not implement PIT research itself: the concrete adapter must reuse the
sealed local-factor-research path and return hash-only evidence here.

An uncertain operation is never converted into a terminal outcome.  Once a
request reservation exists, provider/worker failure, cancellation, timeout,
crash, or ledger-write ambiguity leaves it unresolved.  A later run may only
proceed through the explicit replay-authorization seam.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
import resource
import re
import time
from typing import NoReturn, Protocol, TypeVar

from sqlalchemy.orm import Session

from northstar_quant.application.ai_factor_mining import FactorCandidateGenerator
from northstar_quant.application.factor_mining_worker_supervisor import (
    FactorMiningCampaignWorkerSupervisorPort,
    LinuxFactorMiningCampaignWorkerSupervisor,
)
from northstar_quant.application.local_factor_research import (
    LocalFactorMiningDiscoverySelectionPreparation,
    LocalFactorMiningResearchError,
    LocalFactorMiningResearchPreparation,
    LocalFactorMiningResearchService,
)
from northstar_quant.data.artifacts.fingerprints import (
    FingerprintError,
    canonical_json_sha256,
    require_sha256,
)
from northstar_quant.data.artifacts.immutable_store import ArtifactStore
from northstar_quant.foundation.common.time import utc_now
from northstar_quant.foundation.db.repositories import (
    FactorMiningCampaignFailureCode as FoundationFactorMiningCampaignFailureCode,
    FactorMiningCampaignRegistration as FoundationFactorMiningCampaignRegistration,
    _FactorMiningCampaignReplayAuthorizationInput as _FoundationFactorMiningCampaignReplayAuthorizationInput,
    FactorMiningCampaignRequestEventAppend as FoundationFactorMiningCampaignRequestEventAppend,
    FactorMiningCampaignRequestEventKind as FoundationFactorMiningCampaignRequestEventKind,
    FactorMiningCampaignRequestReservation as FoundationFactorMiningCampaignRequestReservation,
    FactorMiningCampaignResourceUsage as FoundationFactorMiningCampaignResourceUsage,
    factor_mining_campaign_append_event,
    _factor_mining_campaign_authorize_replay,
    factor_mining_campaign_register,
    factor_mining_campaign_read_request_ledger,
    factor_mining_campaign_reserve_request,
)
from northstar_quant.foundation.db.session import SessionLocal
from northstar_quant.research.factor_mining.artifact_bundle import (
    LocalFactorMiningArtifactBundleStore,
)
from northstar_quant.research.factor_mining.models import FactorMiningRunnerResourceBudget
from northstar_quant.research.factor_mining.models import (
    FactorCandidateGenerationReceipt,
    FactorCandidateGenerationRequest,
)
from northstar_quant.research.factor_mining.run_bundle import (
    LocalFactorMiningCampaignDeclaration,
    LocalFactorMiningRunBundle,
)


__all__ = [
    "DurableFactorMiningCampaignResult",
    "DurableFactorMiningCampaignRunner",
    "FactorMiningCampaignDurabilityError",
    "FactorMiningCampaignExecutionPort",
    "FactorMiningCampaignPreparedExecution",
    "FactorMiningCampaignPreparedSelection",
    "FactorMiningCampaignExecutionResult",
    "FactorMiningCampaignGeneration",
    "FactorMiningCampaignKnownFailure",
    "FactorMiningCampaignLedgerEvent",
    "FactorMiningCampaignLedgerEventKind",
    "FactorMiningCampaignLedgerEventReceipt",
    "FactorMiningCampaignLedgerPort",
    "FactorMiningCampaignPreparation",
    "FactorMiningCampaignRegistrationMetadata",
    "FactorMiningCampaignRegistration",
    "FactorMiningCampaignReplayAuthorization",
    "FactorMiningCampaignReplayAuthorizationIntent",
    "FactorMiningCampaignReservation",
    "FactorMiningCampaignRunRequest",
    "FactorMiningCampaignResourceUsage",
    "build_local_factor_mining_campaign_runner",
    "LocalFactorMiningCampaignExecutionAdapter",
    "PostgresFactorMiningCampaignLedger",
]


_RUN_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_ACTOR_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REASON_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_FORBIDDEN_SAFE_TOKENS = frozenset(
    {"__import__", "compile", "eval", "exec", "latest", "open", "select", "shell"}
)
_FOUNDATION_FAILURE_CODES = frozenset(
    item.value for item in FoundationFactorMiningCampaignFailureCode
)
_VERIFIED_REPLAY_AUTHORIZATION_CAPABILITY = object()
_TRUSTED_REPLAY_AUTHORIZATION_COMPOSITION_CAPABILITY = object()
_T = TypeVar("_T")


class FactorMiningCampaignDurabilityError(RuntimeError):
    """The durable campaign boundary cannot prove a safe next action."""


def _refuse(code: str) -> NoReturn:
    raise FactorMiningCampaignDurabilityError(code)


def _hash(value: object, *, field_name: str) -> str:
    try:
        return require_sha256(value, field_name=field_name)  # type: ignore[arg-type]
    except FingerprintError as exc:
        raise FactorMiningCampaignDurabilityError(
            f"FACTOR_MINING_CAMPAIGN_INVALID_{field_name.upper()}"
        ) from exc


def _optional_hash(value: object | None, *, field_name: str) -> str | None:
    return None if value is None else _hash(value, field_name=field_name)


def _run_id(value: object, *, field_name: str = "run_id") -> str:
    if (
        not isinstance(value, str)
        or _RUN_ID_RE.fullmatch(value) is None
        or value.casefold() == "latest"
    ):
        _refuse(f"FACTOR_MINING_CAMPAIGN_INVALID_{field_name.upper()}")
    return value


def _actor_id(value: object, *, field_name: str = "actor_id") -> str:
    if (
        not isinstance(value, str)
        or _ACTOR_ID_RE.fullmatch(value) is None
        or value.casefold() in _FORBIDDEN_SAFE_TOKENS
    ):
        _refuse(f"FACTOR_MINING_CAMPAIGN_INVALID_{field_name.upper()}")
    return value


def _reason_code(value: object, *, field_name: str = "reason_code") -> str:
    if not isinstance(value, str) or _REASON_CODE_RE.fullmatch(value) is None:
        _refuse(f"FACTOR_MINING_CAMPAIGN_INVALID_{field_name.upper()}")
    return value


def _failure_code(value: object, *, field_name: str = "failure_code") -> str:
    normalized = _reason_code(value, field_name=field_name)
    if normalized not in _FOUNDATION_FAILURE_CODES:
        _refuse(f"FACTOR_MINING_CAMPAIGN_INVALID_{field_name.upper()}")
    return normalized


def _nonnegative_int(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _refuse(f"FACTOR_MINING_CAMPAIGN_INVALID_{field_name.upper()}")
    return value


def _utc(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _refuse(f"FACTOR_MINING_CAMPAIGN_INVALID_{field_name.upper()}")
    return value.astimezone(UTC)


def _hashes(value: object, *, field_name: str, minimum: int = 1) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        _refuse(f"FACTOR_MINING_CAMPAIGN_INVALID_{field_name.upper()}")
    normalized = tuple(sorted(_hash(item, field_name=field_name) for item in value))
    if len(normalized) < minimum or len(set(normalized)) != len(normalized):
        _refuse(f"FACTOR_MINING_CAMPAIGN_INVALID_{field_name.upper()}")
    return normalized


class FactorMiningCampaignLedgerEventKind(str, Enum):
    """The finite append-only request lifecycle visible to the runner/reader.

    ``RESERVED`` and ``REPLAY_AUTHORIZED`` are repository-owned facts: the
    runner never appends them through :class:`FactorMiningCampaignLedgerEvent`,
    but a truthful inspect projection must not hide either state.
    """

    RESERVED = "RESERVED"
    RECEIPT_RECORDED = "RECEIPT_RECORDED"
    DISCOVERY_RECORDED = "DISCOVERY_RECORDED"
    SELECTION_COMMITTED = "SELECTION_COMMITTED"
    OOS_RESERVED = "OOS_RESERVED"
    OOS_RELEASED = "OOS_RELEASED"
    RESULT_RECORDED = "RESULT_RECORDED"
    FAILED = "FAILED"
    REPLAY_AUTHORIZED = "REPLAY_AUTHORIZED"


@dataclass(frozen=True, slots=True)
class FactorMiningCampaignRunRequest:
    """Hash-addressed request to execute one sealed campaign declaration.

    ``replay_authorization_hash`` is not an arbitrary retry flag.  It must be
    a separately durable, explicit human authorization that the ledger checks
    while reserving this new run identity.
    """

    run_id: str
    actor_id: str
    declaration_snapshot_hash: str
    replay_authorization_hash: str | None = None
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        run_id = _run_id(self.run_id)
        actor_id = _actor_id(self.actor_id)
        declaration_snapshot_hash = _hash(
            self.declaration_snapshot_hash,
            field_name="declaration_snapshot_hash",
        )
        replay_authorization_hash = _optional_hash(
            self.replay_authorization_hash,
            field_name="replay_authorization_hash",
        )
        request_hash = canonical_json_sha256(
            {
                "actor_id": actor_id,
                "declaration_snapshot_hash": declaration_snapshot_hash,
                "format": "northstar.durable-factor-mining-campaign-request.v1",
                "replay_authorization_hash": replay_authorization_hash,
                "run_id": run_id,
            }
        )
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "actor_id", actor_id)
        object.__setattr__(self, "declaration_snapshot_hash", declaration_snapshot_hash)
        object.__setattr__(self, "replay_authorization_hash", replay_authorization_hash)
        object.__setattr__(self, "request_hash", request_hash)


@dataclass(frozen=True, slots=True)
class FactorMiningCampaignReplayAuthorizationIntent:
    """Ask a trusted verifier to authorize replay of one unresolved request.

    This is deliberately an *intent*, not an approval claim. A CLI or caller
    can name the verifier-issued immutable approval reference and source
    request, but cannot self-attest an approver identity or approval evidence.
    The PostgreSQL adapter appends ``REPLAY_AUTHORIZED`` only after its private
    verifier seam returns a receipt bound to these exact fields.
    """

    authorization_id: str
    unresolved_request_hash: str

    def __post_init__(self) -> None:
        authorization_id = _run_id(self.authorization_id, field_name="authorization_id")
        unresolved_request_hash = _hash(
            self.unresolved_request_hash,
            field_name="unresolved_request_hash",
        )
        object.__setattr__(self, "authorization_id", authorization_id)
        object.__setattr__(self, "unresolved_request_hash", unresolved_request_hash)


@dataclass(frozen=True, slots=True)
class _VerifiedFactorMiningCampaignReplayAuthorization:
    """Ephemeral verifier result; no raw approval proof crosses this seam."""

    authorization_id: str
    unresolved_request_hash: str
    approver_id: str
    verifier_receipt_hash: str
    _verification_capability: object = field(repr=False, compare=False, kw_only=True)

    def __post_init__(self) -> None:
        if self._verification_capability is not _VERIFIED_REPLAY_AUTHORIZATION_CAPABILITY:
            _refuse("FACTOR_MINING_CAMPAIGN_REPLAY_VERIFIED_RESULT_FACTORY_REQUIRED")
        object.__setattr__(
            self,
            "authorization_id",
            _run_id(self.authorization_id, field_name="authorization_id"),
        )
        object.__setattr__(
            self,
            "unresolved_request_hash",
            _hash(self.unresolved_request_hash, field_name="unresolved_request_hash"),
        )
        object.__setattr__(
            self,
            "approver_id",
            _actor_id(self.approver_id, field_name="approver_id"),
        )
        object.__setattr__(
            self,
            "verifier_receipt_hash",
            _hash(self.verifier_receipt_hash, field_name="verifier_receipt_hash"),
        )


def _verified_factor_mining_campaign_replay_authorization_from_trusted_verifier(
    *,
    authorization_id: str,
    unresolved_request_hash: str,
    approver_id: str,
    verifier_receipt_hash: str,
) -> _VerifiedFactorMiningCampaignReplayAuthorization:
    """Construct a verifier result only for trusted composition or tests."""

    return _VerifiedFactorMiningCampaignReplayAuthorization(
        authorization_id=authorization_id,
        unresolved_request_hash=unresolved_request_hash,
        approver_id=approver_id,
        verifier_receipt_hash=verifier_receipt_hash,
        _verification_capability=_VERIFIED_REPLAY_AUTHORIZATION_CAPABILITY,
    )


class _FactorMiningCampaignReplayAuthorizationVerifier(Protocol):
    """Verify one immutable external human-approval reference and its binding."""

    def verify(
        self,
        *,
        intent: FactorMiningCampaignReplayAuthorizationIntent,
    ) -> _VerifiedFactorMiningCampaignReplayAuthorization:
        """Return only verified identity and receipt digest for this exact intent."""


@dataclass(frozen=True, slots=True)
class _UnavailableFactorMiningCampaignReplayAuthorizationVerifier:
    """Safe default until a privileged external human-approval verifier exists."""

    def verify(
        self,
        *,
        intent: FactorMiningCampaignReplayAuthorizationIntent,
    ) -> _VerifiedFactorMiningCampaignReplayAuthorization:
        del intent
        _refuse("FACTOR_MINING_CAMPAIGN_REPLAY_AUTHORIZATION_VERIFIER_UNAVAILABLE")


_DEFAULT_REPLAY_AUTHORIZATION_VERIFIER: _FactorMiningCampaignReplayAuthorizationVerifier = (
    _UnavailableFactorMiningCampaignReplayAuthorizationVerifier()
)


@dataclass(frozen=True, slots=True)
class FactorMiningCampaignRegistrationMetadata:
    """The immutable declaration fields required by the PostgreSQL campaign root.

    This is copied from a verified
    ``LocalFactorMiningCampaignDeclaration`` by the local execution adapter;
    it deliberately contains only IDs, hashes and fixed timestamps.
    """

    campaign_id: str
    decision_replay_plan_hash: str
    dataset_version_set_hash: str
    template_hash: str
    search_budget_hash: str
    selection_policy_hash: str
    generator_id: str
    generator_model_revision_hash: str
    prompt_template_hash: str
    source_authorization_hash: str
    code_revision_hash: str
    selection_at: datetime
    registered_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "campaign_id", _run_id(self.campaign_id, field_name="campaign_id"))
        for field_name, value in (
            ("decision_replay_plan_hash", self.decision_replay_plan_hash),
            ("dataset_version_set_hash", self.dataset_version_set_hash),
            ("template_hash", self.template_hash),
            ("search_budget_hash", self.search_budget_hash),
            ("selection_policy_hash", self.selection_policy_hash),
            ("generator_model_revision_hash", self.generator_model_revision_hash),
            ("prompt_template_hash", self.prompt_template_hash),
            ("source_authorization_hash", self.source_authorization_hash),
            ("code_revision_hash", self.code_revision_hash),
        ):
            object.__setattr__(
                self,
                field_name,
                _hash(value, field_name=field_name),
            )
        object.__setattr__(self, "generator_id", _actor_id(self.generator_id, field_name="generator_id"))
        object.__setattr__(self, "selection_at", _utc(self.selection_at, field_name="selection_at"))
        object.__setattr__(self, "registered_at", _utc(self.registered_at, field_name="registered_at"))

    @property
    def metadata_hash(self) -> str:
        return canonical_json_sha256(
            {
                "campaign_id": self.campaign_id,
                "code_revision_hash": self.code_revision_hash,
                "dataset_version_set_hash": self.dataset_version_set_hash,
                "decision_replay_plan_hash": self.decision_replay_plan_hash,
                "format": "northstar.factor-mining-campaign-registration-metadata.v1",
                "generator_id": self.generator_id,
                "generator_model_revision_hash": self.generator_model_revision_hash,
                "prompt_template_hash": self.prompt_template_hash,
                "registered_at": self.registered_at.isoformat(),
                "search_budget_hash": self.search_budget_hash,
                "selection_at": self.selection_at.isoformat(),
                "selection_policy_hash": self.selection_policy_hash,
                "source_authorization_hash": self.source_authorization_hash,
                "template_hash": self.template_hash,
            }
        )


@dataclass(frozen=True, slots=True)
class FactorMiningCampaignPreparation:
    """Hash-only preflight evidence returned before a request is reserved.

    The concrete execution adapter resolves the typed
    ``FactorMiningCampaignDeclaration`` and its
    ``FactorMiningRunnerResourceBudget``.  Keeping only their commitments here
    prevents this application seam from duplicating research-domain models.
    """

    declaration_snapshot_hash: str
    declaration_hash: str
    campaign_hash: str
    resource_budget_hash: str
    data_authorization_hashes: tuple[str, ...]
    registration_metadata: FactorMiningCampaignRegistrationMetadata | None = None
    runner_budget: FactorMiningRunnerResourceBudget | None = None
    verified_data_row_count: int | None = None
    preflight_hash: str = field(init=False)

    def __post_init__(self) -> None:
        declaration_snapshot_hash = _hash(
            self.declaration_snapshot_hash,
            field_name="declaration_snapshot_hash",
        )
        declaration_hash = _hash(self.declaration_hash, field_name="declaration_hash")
        campaign_hash = _hash(self.campaign_hash, field_name="campaign_hash")
        resource_budget_hash = _hash(
            self.resource_budget_hash,
            field_name="resource_budget_hash",
        )
        authorization_hashes = _hashes(
            self.data_authorization_hashes,
            field_name="data_authorization_hashes",
        )
        if (
            self.registration_metadata is not None
            and type(self.registration_metadata) is not FactorMiningCampaignRegistrationMetadata
        ):
            _refuse("FACTOR_MINING_CAMPAIGN_INVALID_REGISTRATION_METADATA")
        if self.runner_budget is not None:
            if type(self.runner_budget) is not FactorMiningRunnerResourceBudget:
                _refuse("FACTOR_MINING_CAMPAIGN_INVALID_RUNNER_BUDGET")
            if self.runner_budget.budget_hash != resource_budget_hash:
                _refuse("FACTOR_MINING_CAMPAIGN_RUNNER_BUDGET_HASH_MISMATCH")
        verified_data_row_count = (
            None
            if self.verified_data_row_count is None
            else _nonnegative_int(
                self.verified_data_row_count,
                field_name="verified_data_row_count",
            )
        )
        if (
            self.runner_budget is not None
            and verified_data_row_count is not None
            and verified_data_row_count > self.runner_budget.max_data_rows
        ):
            _refuse("FACTOR_MINING_CAMPAIGN_DATA_ROW_LIMIT_EXCEEDED")
        if (
            self.registration_metadata is not None
            and self.registration_metadata.source_authorization_hash not in authorization_hashes
        ):
            _refuse("FACTOR_MINING_CAMPAIGN_SOURCE_AUTHORIZATION_MISMATCH")
        preflight_hash = canonical_json_sha256(
            {
                "campaign_hash": campaign_hash,
                "data_authorization_hashes": list(authorization_hashes),
                "declaration_hash": declaration_hash,
                "declaration_snapshot_hash": declaration_snapshot_hash,
                "format": "northstar.factor-mining-campaign-preflight.v1",
                "registration_metadata_hash": (
                    None
                    if self.registration_metadata is None
                    else self.registration_metadata.metadata_hash
                ),
                "resource_budget_hash": resource_budget_hash,
                "verified_data_row_count": verified_data_row_count,
            }
        )
        object.__setattr__(self, "declaration_snapshot_hash", declaration_snapshot_hash)
        object.__setattr__(self, "declaration_hash", declaration_hash)
        object.__setattr__(self, "campaign_hash", campaign_hash)
        object.__setattr__(self, "resource_budget_hash", resource_budget_hash)
        object.__setattr__(self, "data_authorization_hashes", authorization_hashes)
        object.__setattr__(self, "verified_data_row_count", verified_data_row_count)
        object.__setattr__(self, "preflight_hash", preflight_hash)


@dataclass(frozen=True, slots=True)
class FactorMiningCampaignGeneration:
    """The sealed generation request and receipt, without provider text."""

    generation_request_hash: str
    generation_receipt_hash: str
    candidate_count: int
    generation_hash: str = field(init=False)

    def __post_init__(self) -> None:
        generation_request_hash = _hash(
            self.generation_request_hash,
            field_name="generation_request_hash",
        )
        generation_receipt_hash = _hash(
            self.generation_receipt_hash,
            field_name="generation_receipt_hash",
        )
        if (
            isinstance(self.candidate_count, bool)
            or not isinstance(self.candidate_count, int)
            or not 1 <= self.candidate_count <= 64
        ):
            _refuse("FACTOR_MINING_CAMPAIGN_INVALID_CANDIDATE_COUNT")
        generation_hash = canonical_json_sha256(
            {
                "candidate_count": self.candidate_count,
                "format": "northstar.factor-mining-campaign-generation.v1",
                "generation_receipt_hash": generation_receipt_hash,
                "generation_request_hash": generation_request_hash,
            }
        )
        object.__setattr__(self, "generation_request_hash", generation_request_hash)
        object.__setattr__(self, "generation_receipt_hash", generation_receipt_hash)
        object.__setattr__(self, "generation_hash", generation_hash)


@dataclass(frozen=True, slots=True)
class FactorMiningCampaignResourceUsage:
    """An attested complete measurement for one local worker invocation.

    A successful result can never use a guessed, missing, or hash-only budget
    claim.  The concrete worker must report every counter, and the runner
    checks the sealed :class:`FactorMiningRunnerResourceBudget` before it can
    append ``RESULT_RECORDED``.
    """

    max_concurrency_observed: int
    cpu_milliseconds: int
    peak_memory_bytes: int
    wall_clock_milliseconds: int
    data_row_count: int
    artifact_byte_count: int
    resource_usage_hash: str = field(init=False)

    def __post_init__(self) -> None:
        values = (
            ("max_concurrency_observed", self.max_concurrency_observed),
            ("cpu_milliseconds", self.cpu_milliseconds),
            ("peak_memory_bytes", self.peak_memory_bytes),
            ("wall_clock_milliseconds", self.wall_clock_milliseconds),
            ("data_row_count", self.data_row_count),
            ("artifact_byte_count", self.artifact_byte_count),
        )
        normalized: dict[str, int] = {}
        for field_name, value in values:
            normalized[field_name] = _nonnegative_int(value, field_name=field_name)
            object.__setattr__(self, field_name, normalized[field_name])
        resource_usage_hash = canonical_json_sha256(
            {
                "artifact_byte_count": normalized["artifact_byte_count"],
                "cpu_milliseconds": normalized["cpu_milliseconds"],
                "data_row_count": normalized["data_row_count"],
                "format": "northstar.factor-mining-campaign-resource-usage.v1",
                "max_concurrency_observed": normalized["max_concurrency_observed"],
                "peak_memory_bytes": normalized["peak_memory_bytes"],
                "wall_clock_milliseconds": normalized["wall_clock_milliseconds"],
            }
        )
        object.__setattr__(self, "resource_usage_hash", resource_usage_hash)

    def require_within(self, budget: FactorMiningRunnerResourceBudget) -> None:
        """Fail closed unless every actual metric lies within the sealed budget."""

        if type(budget) is not FactorMiningRunnerResourceBudget:
            _refuse("FACTOR_MINING_CAMPAIGN_INVALID_RUNNER_BUDGET")
        if (
            self.max_concurrency_observed > budget.max_concurrent_runs
            or self.cpu_milliseconds > budget.max_cpu_seconds * 1_000
            or self.peak_memory_bytes > budget.max_memory_bytes
            or self.wall_clock_milliseconds > budget.max_wall_clock_seconds * 1_000
            or self.data_row_count > budget.max_data_rows
            or self.artifact_byte_count > budget.max_artifact_bytes
        ):
            _refuse("FACTOR_MINING_CAMPAIGN_RESOURCE_LIMIT_EXCEEDED")


@dataclass(frozen=True, slots=True)
class FactorMiningCampaignPreparedSelection:
    """Proved discovery and selection facts before the durable OOS gate opens."""

    campaign_hash: str
    declaration_hash: str
    generation_receipt_hash: str
    bundle_snapshot_hash: str
    discovery_result_hash: str
    selection_commitment_hash: str
    selected_candidate_count: int
    resource_usage: FactorMiningCampaignResourceUsage
    prepared_selection_hash: str = field(init=False)

    def __post_init__(self) -> None:
        campaign_hash = _hash(self.campaign_hash, field_name="campaign_hash")
        declaration_hash = _hash(self.declaration_hash, field_name="declaration_hash")
        generation_receipt_hash = _hash(
            self.generation_receipt_hash,
            field_name="generation_receipt_hash",
        )
        bundle_snapshot_hash = _hash(self.bundle_snapshot_hash, field_name="bundle_snapshot_hash")
        discovery_result_hash = _hash(
            self.discovery_result_hash,
            field_name="discovery_result_hash",
        )
        selection_commitment_hash = _hash(
            self.selection_commitment_hash,
            field_name="selection_commitment_hash",
        )
        selected_candidate_count = _nonnegative_int(
            self.selected_candidate_count,
            field_name="selected_candidate_count",
        )
        if type(self.resource_usage) is not FactorMiningCampaignResourceUsage:
            _refuse("FACTOR_MINING_CAMPAIGN_INVALID_RESOURCE_USAGE")
        prepared_selection_hash = canonical_json_sha256(
            {
                "bundle_snapshot_hash": bundle_snapshot_hash,
                "campaign_hash": campaign_hash,
                "declaration_hash": declaration_hash,
                "discovery_result_hash": discovery_result_hash,
                "format": "northstar.factor-mining-campaign-prepared-selection.v1",
                "generation_receipt_hash": generation_receipt_hash,
                "resource_usage_hash": self.resource_usage.resource_usage_hash,
                "selected_candidate_count": selected_candidate_count,
                "selection_commitment_hash": selection_commitment_hash,
            }
        )
        object.__setattr__(self, "campaign_hash", campaign_hash)
        object.__setattr__(self, "declaration_hash", declaration_hash)
        object.__setattr__(self, "generation_receipt_hash", generation_receipt_hash)
        object.__setattr__(self, "bundle_snapshot_hash", bundle_snapshot_hash)
        object.__setattr__(self, "discovery_result_hash", discovery_result_hash)
        object.__setattr__(self, "selection_commitment_hash", selection_commitment_hash)
        object.__setattr__(self, "selected_candidate_count", selected_candidate_count)
        object.__setattr__(self, "prepared_selection_hash", prepared_selection_hash)


@dataclass(frozen=True, slots=True)
class FactorMiningCampaignPreparedExecution:
    """Verified stage identities before immutable research evidence is published.

    ``prepare`` may calculate in-memory OOS material, but this value contains
    no durable research result.  The runner records receipt/discovery/
    selection/OOS stage facts first; only then may the adapter publish its
    previously projected immutable evidence.
    """

    campaign_hash: str
    declaration_hash: str
    generation_receipt_hash: str
    bundle_snapshot_hash: str
    discovery_result_hash: str
    selection_commitment_hash: str
    oos_release_hash: str | None
    result_hash: str
    selected_candidate_count: int
    resource_usage: FactorMiningCampaignResourceUsage
    prepared_execution_hash: str = field(init=False)

    def __post_init__(self) -> None:
        campaign_hash = _hash(self.campaign_hash, field_name="campaign_hash")
        declaration_hash = _hash(self.declaration_hash, field_name="declaration_hash")
        generation_receipt_hash = _hash(
            self.generation_receipt_hash,
            field_name="generation_receipt_hash",
        )
        bundle_snapshot_hash = _hash(self.bundle_snapshot_hash, field_name="bundle_snapshot_hash")
        discovery_result_hash = _hash(
            self.discovery_result_hash,
            field_name="discovery_result_hash",
        )
        selection_commitment_hash = _hash(
            self.selection_commitment_hash,
            field_name="selection_commitment_hash",
        )
        oos_release_hash = _optional_hash(self.oos_release_hash, field_name="oos_release_hash")
        result_hash = _hash(self.result_hash, field_name="result_hash")
        selected_candidate_count = _nonnegative_int(
            self.selected_candidate_count,
            field_name="selected_candidate_count",
        )
        if type(self.resource_usage) is not FactorMiningCampaignResourceUsage:
            _refuse("FACTOR_MINING_CAMPAIGN_INVALID_RESOURCE_USAGE")
        prepared_execution_hash = canonical_json_sha256(
            {
                "bundle_snapshot_hash": bundle_snapshot_hash,
                "campaign_hash": campaign_hash,
                "declaration_hash": declaration_hash,
                "discovery_result_hash": discovery_result_hash,
                "format": "northstar.factor-mining-campaign-prepared-execution.v1",
                "generation_receipt_hash": generation_receipt_hash,
                "oos_release_hash": oos_release_hash,
                "resource_usage_hash": self.resource_usage.resource_usage_hash,
                "result_hash": result_hash,
                "selected_candidate_count": selected_candidate_count,
                "selection_commitment_hash": selection_commitment_hash,
            }
        )
        object.__setattr__(self, "campaign_hash", campaign_hash)
        object.__setattr__(self, "declaration_hash", declaration_hash)
        object.__setattr__(self, "generation_receipt_hash", generation_receipt_hash)
        object.__setattr__(self, "bundle_snapshot_hash", bundle_snapshot_hash)
        object.__setattr__(self, "discovery_result_hash", discovery_result_hash)
        object.__setattr__(self, "selection_commitment_hash", selection_commitment_hash)
        object.__setattr__(self, "oos_release_hash", oos_release_hash)
        object.__setattr__(self, "result_hash", result_hash)
        object.__setattr__(self, "selected_candidate_count", selected_candidate_count)
        object.__setattr__(self, "prepared_execution_hash", prepared_execution_hash)


@dataclass(frozen=True, slots=True)
class FactorMiningCampaignExecutionResult:
    """Hash-only research result plus mandatory measured resource evidence."""

    campaign_hash: str
    declaration_hash: str
    generation_receipt_hash: str
    bundle_snapshot_hash: str
    discovery_result_hash: str
    selection_commitment_hash: str
    oos_release_hash: str | None
    manifest_snapshot_hash: str
    result_hash: str
    selected_candidate_count: int
    resource_usage: FactorMiningCampaignResourceUsage
    execution_hash: str = field(init=False)

    def __post_init__(self) -> None:
        campaign_hash = _hash(self.campaign_hash, field_name="campaign_hash")
        declaration_hash = _hash(self.declaration_hash, field_name="declaration_hash")
        generation_receipt_hash = _hash(
            self.generation_receipt_hash,
            field_name="generation_receipt_hash",
        )
        bundle_snapshot_hash = _hash(self.bundle_snapshot_hash, field_name="bundle_snapshot_hash")
        discovery_result_hash = _hash(
            self.discovery_result_hash,
            field_name="discovery_result_hash",
        )
        selection_commitment_hash = _hash(
            self.selection_commitment_hash,
            field_name="selection_commitment_hash",
        )
        oos_release_hash = _optional_hash(self.oos_release_hash, field_name="oos_release_hash")
        manifest_snapshot_hash = _hash(
            self.manifest_snapshot_hash,
            field_name="manifest_snapshot_hash",
        )
        result_hash = _hash(self.result_hash, field_name="result_hash")
        selected_candidate_count = _nonnegative_int(
            self.selected_candidate_count,
            field_name="selected_candidate_count",
        )
        if type(self.resource_usage) is not FactorMiningCampaignResourceUsage:
            _refuse("FACTOR_MINING_CAMPAIGN_INVALID_RESOURCE_USAGE")
        execution_hash = canonical_json_sha256(
            {
                "bundle_snapshot_hash": bundle_snapshot_hash,
                "campaign_hash": campaign_hash,
                "declaration_hash": declaration_hash,
                "discovery_result_hash": discovery_result_hash,
                "format": "northstar.factor-mining-campaign-execution-result.v1",
                "generation_receipt_hash": generation_receipt_hash,
                "manifest_snapshot_hash": manifest_snapshot_hash,
                "oos_release_hash": oos_release_hash,
                "resource_usage_hash": self.resource_usage.resource_usage_hash,
                "result_hash": result_hash,
                "selected_candidate_count": selected_candidate_count,
                "selection_commitment_hash": selection_commitment_hash,
            }
        )
        object.__setattr__(self, "campaign_hash", campaign_hash)
        object.__setattr__(self, "declaration_hash", declaration_hash)
        object.__setattr__(self, "generation_receipt_hash", generation_receipt_hash)
        object.__setattr__(self, "bundle_snapshot_hash", bundle_snapshot_hash)
        object.__setattr__(self, "discovery_result_hash", discovery_result_hash)
        object.__setattr__(self, "selection_commitment_hash", selection_commitment_hash)
        object.__setattr__(self, "oos_release_hash", oos_release_hash)
        object.__setattr__(self, "manifest_snapshot_hash", manifest_snapshot_hash)
        object.__setattr__(self, "result_hash", result_hash)
        object.__setattr__(self, "selected_candidate_count", selected_candidate_count)
        object.__setattr__(self, "execution_hash", execution_hash)


@dataclass(frozen=True, slots=True)
class FactorMiningCampaignRegistration:
    """Idempotent durable campaign registration evidence."""

    campaign_hash: str
    declaration_hash: str
    resource_budget_hash: str
    registration_record_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "campaign_hash", _hash(self.campaign_hash, field_name="campaign_hash"))
        object.__setattr__(self, "declaration_hash", _hash(self.declaration_hash, field_name="declaration_hash"))
        object.__setattr__(
            self,
            "resource_budget_hash",
            _hash(self.resource_budget_hash, field_name="resource_budget_hash"),
        )
        object.__setattr__(
            self,
            "registration_record_hash",
            _hash(self.registration_record_hash, field_name="registration_record_hash"),
        )


@dataclass(frozen=True, slots=True)
class FactorMiningCampaignReservation:
    """Committed request reservation returned before any generator call."""

    request_hash: str
    campaign_hash: str
    declaration_hash: str
    reservation_record_hash: str
    max_concurrency_observed: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_hash", _hash(self.request_hash, field_name="request_hash"))
        object.__setattr__(self, "campaign_hash", _hash(self.campaign_hash, field_name="campaign_hash"))
        object.__setattr__(self, "declaration_hash", _hash(self.declaration_hash, field_name="declaration_hash"))
        object.__setattr__(
            self,
            "reservation_record_hash",
            _hash(self.reservation_record_hash, field_name="reservation_record_hash"),
        )
        if self.max_concurrency_observed is not None:
            object.__setattr__(
                self,
                "max_concurrency_observed",
                _nonnegative_int(
                    self.max_concurrency_observed,
                    field_name="max_concurrency_observed",
                ),
            )


@dataclass(frozen=True, slots=True)
class FactorMiningCampaignLedgerEvent:
    """One typed, hash-only transition to append after a reservation.

    The PostgreSQL repository owns inherited fields and validates the full
    state machine.  This application value carries only the new fact for the
    next transition plus the previous record identity that the runner saw.
    """

    kind: FactorMiningCampaignLedgerEventKind
    request_id: str
    request_hash: str
    campaign_hash: str
    declaration_hash: str
    resource_budget_hash: str
    predecessor_record_hash: str
    generation_receipt_hash: str | None = None
    discovery_result_hash: str | None = None
    selection_commitment_hash: str | None = None
    oos_release_hash: str | None = None
    bundle_snapshot_hash: str | None = None
    manifest_snapshot_hash: str | None = None
    result_hash: str | None = None
    candidate_count: int | None = None
    selected_candidate_count: int | None = None
    failure_code: str | None = None
    resource_usage: FactorMiningCampaignResourceUsage | None = None
    event_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.kind) is not FactorMiningCampaignLedgerEventKind:
            _refuse("FACTOR_MINING_CAMPAIGN_INVALID_EVENT_KIND")
        if self.kind in {
            FactorMiningCampaignLedgerEventKind.RESERVED,
            FactorMiningCampaignLedgerEventKind.REPLAY_AUTHORIZED,
        }:
            _refuse("FACTOR_MINING_CAMPAIGN_REPOSITORY_OWNED_EVENT_KIND")
        request_id = _run_id(self.request_id, field_name="request_id")
        request_hash = _hash(self.request_hash, field_name="request_hash")
        campaign_hash = _hash(self.campaign_hash, field_name="campaign_hash")
        declaration_hash = _hash(self.declaration_hash, field_name="declaration_hash")
        resource_budget_hash = _hash(
            self.resource_budget_hash,
            field_name="resource_budget_hash",
        )
        predecessor_record_hash = _hash(
            self.predecessor_record_hash,
            field_name="predecessor_record_hash",
        )
        generation_receipt_hash = _optional_hash(
            self.generation_receipt_hash,
            field_name="generation_receipt_hash",
        )
        discovery_result_hash = _optional_hash(
            self.discovery_result_hash,
            field_name="discovery_result_hash",
        )
        selection_commitment_hash = _optional_hash(
            self.selection_commitment_hash,
            field_name="selection_commitment_hash",
        )
        oos_release_hash = _optional_hash(self.oos_release_hash, field_name="oos_release_hash")
        bundle_snapshot_hash = _optional_hash(
            self.bundle_snapshot_hash,
            field_name="bundle_snapshot_hash",
        )
        manifest_snapshot_hash = _optional_hash(
            self.manifest_snapshot_hash,
            field_name="manifest_snapshot_hash",
        )
        result_hash = _optional_hash(self.result_hash, field_name="result_hash")
        candidate_count = (
            None
            if self.candidate_count is None
            else _nonnegative_int(self.candidate_count, field_name="candidate_count")
        )
        selected_candidate_count = (
            None
            if self.selected_candidate_count is None
            else _nonnegative_int(
                self.selected_candidate_count,
                field_name="selected_candidate_count",
            )
        )
        failure_code = (
            None
            if self.failure_code is None
            else _failure_code(self.failure_code, field_name="failure_code")
        )
        if self.resource_usage is not None and type(self.resource_usage) is not FactorMiningCampaignResourceUsage:
            _refuse("FACTOR_MINING_CAMPAIGN_INVALID_RESOURCE_USAGE")
        self._validate_shape(
            generation_receipt_hash=generation_receipt_hash,
            discovery_result_hash=discovery_result_hash,
            selection_commitment_hash=selection_commitment_hash,
            oos_release_hash=oos_release_hash,
            bundle_snapshot_hash=bundle_snapshot_hash,
            manifest_snapshot_hash=manifest_snapshot_hash,
            result_hash=result_hash,
            candidate_count=candidate_count,
            selected_candidate_count=selected_candidate_count,
            failure_code=failure_code,
        )
        event_hash = canonical_json_sha256(
            {
                "campaign_hash": campaign_hash,
                "declaration_hash": declaration_hash,
                "discovery_result_hash": discovery_result_hash,
                "failure_code": failure_code,
                "format": "northstar.factor-mining-campaign-ledger-event.v1",
                "generation_receipt_hash": generation_receipt_hash,
                "kind": self.kind.value,
                "manifest_snapshot_hash": manifest_snapshot_hash,
                "oos_release_hash": oos_release_hash,
                "predecessor_record_hash": predecessor_record_hash,
                "request_id": request_id,
                "request_hash": request_hash,
                "resource_budget_hash": resource_budget_hash,
                "resource_usage_hash": (
                    None if self.resource_usage is None else self.resource_usage.resource_usage_hash
                ),
                "result_hash": result_hash,
                "selected_candidate_count": selected_candidate_count,
                "selection_commitment_hash": selection_commitment_hash,
                "bundle_snapshot_hash": bundle_snapshot_hash,
                "candidate_count": candidate_count,
            }
        )
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "request_hash", request_hash)
        object.__setattr__(self, "campaign_hash", campaign_hash)
        object.__setattr__(self, "declaration_hash", declaration_hash)
        object.__setattr__(self, "resource_budget_hash", resource_budget_hash)
        object.__setattr__(self, "predecessor_record_hash", predecessor_record_hash)
        object.__setattr__(self, "generation_receipt_hash", generation_receipt_hash)
        object.__setattr__(self, "discovery_result_hash", discovery_result_hash)
        object.__setattr__(self, "selection_commitment_hash", selection_commitment_hash)
        object.__setattr__(self, "oos_release_hash", oos_release_hash)
        object.__setattr__(self, "bundle_snapshot_hash", bundle_snapshot_hash)
        object.__setattr__(self, "manifest_snapshot_hash", manifest_snapshot_hash)
        object.__setattr__(self, "result_hash", result_hash)
        object.__setattr__(self, "candidate_count", candidate_count)
        object.__setattr__(self, "selected_candidate_count", selected_candidate_count)
        object.__setattr__(self, "failure_code", failure_code)
        object.__setattr__(self, "event_hash", event_hash)

    def _validate_shape(
        self,
        *,
        generation_receipt_hash: str | None,
        discovery_result_hash: str | None,
        selection_commitment_hash: str | None,
        oos_release_hash: str | None,
        bundle_snapshot_hash: str | None,
        manifest_snapshot_hash: str | None,
        result_hash: str | None,
        candidate_count: int | None,
        selected_candidate_count: int | None,
        failure_code: str | None,
    ) -> None:
        """Reject an event whose new facts cannot be safely interpreted."""

        no_result = (
            bundle_snapshot_hash is None
            and manifest_snapshot_hash is None
            and result_hash is None
        )
        if self.kind is FactorMiningCampaignLedgerEventKind.RECEIPT_RECORDED:
            valid = (
                generation_receipt_hash is not None
                and candidate_count is not None
                and candidate_count > 0
                and discovery_result_hash is None
                and selection_commitment_hash is None
                and oos_release_hash is None
                and no_result
                and selected_candidate_count is None
                and failure_code is None
                and self.resource_usage is None
            )
        elif self.kind is FactorMiningCampaignLedgerEventKind.DISCOVERY_RECORDED:
            valid = (
                generation_receipt_hash is None
                and discovery_result_hash is not None
                and selection_commitment_hash is None
                and oos_release_hash is None
                and no_result
                and candidate_count is None
                and selected_candidate_count is None
                and failure_code is None
                and self.resource_usage is None
            )
        elif self.kind is FactorMiningCampaignLedgerEventKind.SELECTION_COMMITTED:
            valid = (
                generation_receipt_hash is None
                and discovery_result_hash is None
                and selection_commitment_hash is not None
                and oos_release_hash is None
                and no_result
                and candidate_count is None
                and selected_candidate_count is not None
                and failure_code is None
                and self.resource_usage is None
            )
        elif self.kind is FactorMiningCampaignLedgerEventKind.OOS_RESERVED:
            valid = (
                generation_receipt_hash is None
                and discovery_result_hash is None
                and selection_commitment_hash is None
                and oos_release_hash is None
                and no_result
                and candidate_count is None
                and selected_candidate_count is None
                and failure_code is None
                and self.resource_usage is None
            )
        elif self.kind is FactorMiningCampaignLedgerEventKind.OOS_RELEASED:
            valid = (
                generation_receipt_hash is None
                and discovery_result_hash is None
                and selection_commitment_hash is None
                and oos_release_hash is not None
                and no_result
                and candidate_count is None
                and selected_candidate_count is None
                and failure_code is None
                and self.resource_usage is None
            )
        elif self.kind is FactorMiningCampaignLedgerEventKind.RESULT_RECORDED:
            valid = (
                generation_receipt_hash is None
                and discovery_result_hash is None
                and selection_commitment_hash is None
                and oos_release_hash is None
                and not no_result
                and bundle_snapshot_hash is not None
                and manifest_snapshot_hash is not None
                and result_hash is not None
                and candidate_count is None
                and selected_candidate_count is None
                and failure_code is None
                and self.resource_usage is not None
            )
        else:
            valid = (
                generation_receipt_hash is None
                and discovery_result_hash is None
                and selection_commitment_hash is None
                and oos_release_hash is None
                and no_result
                and candidate_count is None
                and selected_candidate_count is None
                and failure_code is not None
            )
        if not valid:
            _refuse("FACTOR_MINING_CAMPAIGN_INVALID_LEDGER_EVENT_SHAPE")


@dataclass(frozen=True, slots=True)
class FactorMiningCampaignLedgerEventReceipt:
    """The immutable record identity returned after appending one event."""

    request_hash: str
    kind: FactorMiningCampaignLedgerEventKind
    record_hash: str

    def __post_init__(self) -> None:
        if type(self.kind) is not FactorMiningCampaignLedgerEventKind:
            _refuse("FACTOR_MINING_CAMPAIGN_INVALID_EVENT_KIND")
        object.__setattr__(self, "request_hash", _hash(self.request_hash, field_name="request_hash"))
        object.__setattr__(self, "record_hash", _hash(self.record_hash, field_name="record_hash"))


@dataclass(frozen=True, slots=True)
class FactorMiningCampaignReplayAuthorization:
    """Durable proof that a human approved a new replay attempt."""

    authorization_hash: str
    unresolved_request_hash: str
    authorization_record_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "authorization_hash",
            _hash(self.authorization_hash, field_name="authorization_hash"),
        )
        object.__setattr__(
            self,
            "unresolved_request_hash",
            _hash(self.unresolved_request_hash, field_name="unresolved_request_hash"),
        )
        object.__setattr__(
            self,
            "authorization_record_hash",
            _hash(self.authorization_record_hash, field_name="authorization_record_hash"),
        )


class FactorMiningCampaignKnownFailure(RuntimeError):
    """A worker proved a non-retryable failure without an ambiguous outcome.

    Adapters may raise this only when they can establish that the result is
    invalid or a budget was rejected before it could be published as a valid
    campaign result.  Timeout, cancellation, crash, transport loss and any
    unknown provider/worker result must instead escape as ordinary exceptions,
    which the runner deliberately leaves unresolved.
    """

    def __init__(
        self,
        reason_code: str,
        *,
        resource_usage: FactorMiningCampaignResourceUsage | None = None,
    ) -> None:
        self.reason_code = _failure_code(reason_code)
        if resource_usage is not None and type(resource_usage) is not FactorMiningCampaignResourceUsage:
            _refuse("FACTOR_MINING_CAMPAIGN_INVALID_RESOURCE_USAGE")
        self.resource_usage = resource_usage
        super().__init__(self.reason_code)


class FactorMiningCampaignExecutionPort(Protocol):
    """Internal seam for artifact preflight, generation and bounded compute.

    A concrete adapter owns the typed campaign declaration, resource budget,
    worker isolation and ``LocalFactorMiningRunBundle.from_campaign_declaration``
    composition.  It must never receive a database session or a broker/risk/
    execution capability.
    """

    def preflight(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
    ) -> FactorMiningCampaignPreparation: ...

    def generate(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
        reservation: FactorMiningCampaignReservation,
    ) -> FactorMiningCampaignGeneration: ...

    def execute(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
        reservation: FactorMiningCampaignReservation,
        generation: FactorMiningCampaignGeneration,
    ) -> FactorMiningCampaignPreparedSelection: ...

    def prepare_release(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
        reservation: FactorMiningCampaignReservation,
        generation: FactorMiningCampaignGeneration,
        prepared_selection: FactorMiningCampaignPreparedSelection,
    ) -> FactorMiningCampaignPreparedExecution: ...

    def publish(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
        reservation: FactorMiningCampaignReservation,
        generation: FactorMiningCampaignGeneration,
        prepared_execution: FactorMiningCampaignPreparedExecution,
    ) -> FactorMiningCampaignExecutionResult: ...


class FactorMiningCampaignLedgerPort(Protocol):
    """Application-facing adapter over the PostgreSQL append-only ledger."""

    def register_campaign(
        self,
        *,
        preparation: FactorMiningCampaignPreparation,
    ) -> FactorMiningCampaignRegistration: ...

    def reserve_request(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
        registration: FactorMiningCampaignRegistration,
    ) -> FactorMiningCampaignReservation: ...

    def append_event(
        self,
        *,
        event: FactorMiningCampaignLedgerEvent,
    ) -> FactorMiningCampaignLedgerEventReceipt: ...

    def authorize_replay(
        self,
        *,
        request: FactorMiningCampaignReplayAuthorizationIntent,
    ) -> FactorMiningCampaignReplayAuthorization: ...

    def read_request_events(
        self,
        *,
        request_id: str,
    ) -> Sequence[FactorMiningCampaignLedgerEventReceipt]: ...


class PostgresFactorMiningCampaignLedger:
    """Concrete application adapter for the Foundation PostgreSQL ledger.

    The only database capability in the durable factor-mining closure lives
    here.  It translates validated hash-only application records into the
    Foundation repository inputs in a new short-lived session.  Neither the
    AI generator nor the local research service ever receives this adapter or
    a database session.
    """

    __slots__ = ("_replay_authorization_verifier", "_session_factory")

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        if not callable(session_factory):
            _refuse("FACTOR_MINING_CAMPAIGN_INVALID_SESSION_FACTORY")
        self._session_factory = session_factory
        self._replay_authorization_verifier = _DEFAULT_REPLAY_AUTHORIZATION_VERIFIER

    @classmethod
    def _from_trusted_composition(
        cls,
        *,
        session_factory: Callable[[], Session],
        replay_authorization_verifier: _FactorMiningCampaignReplayAuthorizationVerifier,
        capability: object,
    ) -> "PostgresFactorMiningCampaignLedger":
        """Inject a verifier only through a private reviewed composition seam."""

        if capability is not _TRUSTED_REPLAY_AUTHORIZATION_COMPOSITION_CAPABILITY:
            _refuse("FACTOR_MINING_CAMPAIGN_REPLAY_AUTHORIZATION_FACTORY_REQUIRED")
        ledger = cls(session_factory=session_factory)
        ledger._replay_authorization_verifier = replay_authorization_verifier
        return ledger

    def register_campaign(
        self,
        *,
        preparation: FactorMiningCampaignPreparation,
    ) -> FactorMiningCampaignRegistration:
        metadata = self._require_registration_metadata(preparation)
        budget = self._require_runner_budget(preparation)
        registration = FoundationFactorMiningCampaignRegistration(
            campaign_id=metadata.campaign_id,
            campaign_hash=preparation.campaign_hash,
            declaration_hash=preparation.declaration_hash,
            declaration_snapshot_hash=preparation.declaration_snapshot_hash,
            decision_replay_plan_hash=metadata.decision_replay_plan_hash,
            dataset_version_set_hash=metadata.dataset_version_set_hash,
            template_hash=metadata.template_hash,
            search_budget_hash=metadata.search_budget_hash,
            selection_policy_hash=metadata.selection_policy_hash,
            generator_id=metadata.generator_id,
            generator_model_revision_hash=metadata.generator_model_revision_hash,
            prompt_template_hash=metadata.prompt_template_hash,
            source_authorization_hash=metadata.source_authorization_hash,
            runner_resource_budget_hash=preparation.resource_budget_hash,
            max_concurrent_runs=budget.max_concurrent_runs,
            code_revision_hash=metadata.code_revision_hash,
            selection_at=metadata.selection_at,
            registered_at=metadata.registered_at,
        )
        with self._session_factory() as session:
            receipt = factor_mining_campaign_register(session, registration=registration)
            record_hash = _hash(receipt.record_hash, field_name="registration_record_hash")
        return FactorMiningCampaignRegistration(
            campaign_hash=preparation.campaign_hash,
            declaration_hash=preparation.declaration_hash,
            resource_budget_hash=preparation.resource_budget_hash,
            registration_record_hash=record_hash,
        )

    def reserve_request(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
        registration: FactorMiningCampaignRegistration,
    ) -> FactorMiningCampaignReservation:
        metadata = self._require_registration_metadata(preparation)
        self._require_runner_budget(preparation)
        if registration.campaign_hash != preparation.campaign_hash:
            _refuse("FACTOR_MINING_CAMPAIGN_REGISTRATION_BINDING_MISMATCH")
        reservation = FoundationFactorMiningCampaignRequestReservation(
            campaign_id=metadata.campaign_id,
            campaign_hash=preparation.campaign_hash,
            request_id=request.run_id,
            request_hash=request.request_hash,
            request_actor_id=request.actor_id,
            source_authorization_hash=metadata.source_authorization_hash,
            resource_budget_hash=preparation.resource_budget_hash,
            reserved_at=utc_now(),
            replay_authorization_hash=request.replay_authorization_hash,
        )
        with self._session_factory() as session:
            event = factor_mining_campaign_reserve_request(session, reservation=reservation)
            record_hash = _hash(event.record_hash, field_name="reservation_record_hash")
            observed = event.active_concurrency_observed
        return FactorMiningCampaignReservation(
            request_hash=request.request_hash,
            campaign_hash=preparation.campaign_hash,
            declaration_hash=preparation.declaration_hash,
            reservation_record_hash=record_hash,
            max_concurrency_observed=observed,
        )

    def append_event(
        self,
        *,
        event: FactorMiningCampaignLedgerEvent,
    ) -> FactorMiningCampaignLedgerEventReceipt:
        try:
            foundation_kind = FoundationFactorMiningCampaignRequestEventKind(event.kind.value)
        except ValueError as exc:
            # Reservation and replay authorization are atomic repository
            # operations with additional fields; the app runner must never
            # fabricate them through a generic append.
            raise FactorMiningCampaignDurabilityError(
                "FACTOR_MINING_CAMPAIGN_REPOSITORY_OWNED_EVENT_KIND"
            ) from exc
        foundation_failure = self._foundation_failure_code(event.failure_code)
        foundation_usage = (
            None
            if event.resource_usage is None
            else FoundationFactorMiningCampaignResourceUsage(
                resource_usage_hash=event.resource_usage.resource_usage_hash,
                max_concurrency_observed=event.resource_usage.max_concurrency_observed,
                cpu_milliseconds=event.resource_usage.cpu_milliseconds,
                peak_memory_bytes=event.resource_usage.peak_memory_bytes,
                wall_clock_milliseconds=event.resource_usage.wall_clock_milliseconds,
                data_row_count=event.resource_usage.data_row_count,
                artifact_byte_count=event.resource_usage.artifact_byte_count,
            )
        )
        append = FoundationFactorMiningCampaignRequestEventAppend(
            request_id=event.request_id,
            event_kind=foundation_kind,
            occurred_at=utc_now(),
            generation_receipt_hash=event.generation_receipt_hash,
            discovery_result_hash=event.discovery_result_hash,
            selection_commitment_hash=event.selection_commitment_hash,
            oos_release_hash=event.oos_release_hash,
            bundle_snapshot_hash=event.bundle_snapshot_hash,
            manifest_snapshot_hash=event.manifest_snapshot_hash,
            result_hash=event.result_hash,
            candidate_count=event.candidate_count,
            selected_candidate_count=event.selected_candidate_count,
            failure_code=foundation_failure,
            resource_usage=foundation_usage,
        )
        with self._session_factory() as session:
            receipt = factor_mining_campaign_append_event(session, append=append)
            record_hash = _hash(receipt.record_hash, field_name="event_record_hash")
        return FactorMiningCampaignLedgerEventReceipt(
            request_hash=event.request_hash,
            kind=event.kind,
            record_hash=record_hash,
        )

    def authorize_replay(
        self,
        *,
        request: FactorMiningCampaignReplayAuthorizationIntent,
    ) -> FactorMiningCampaignReplayAuthorization:
        if type(request) is not FactorMiningCampaignReplayAuthorizationIntent:
            _refuse("FACTOR_MINING_CAMPAIGN_REPLAY_AUTHORIZATION_INTENT_INVALID")
        try:
            verified = self._replay_authorization_verifier.verify(intent=request)
        except FactorMiningCampaignDurabilityError:
            raise
        except Exception as exc:
            # Verification itself has no ledger side effect. Its failure must
            # not become a self-attested approval or a database write.
            raise FactorMiningCampaignDurabilityError(
                "FACTOR_MINING_CAMPAIGN_REPLAY_AUTHORIZATION_VERIFICATION_UNAVAILABLE"
            ) from exc
        if type(verified) is not _VerifiedFactorMiningCampaignReplayAuthorization:
            _refuse("FACTOR_MINING_CAMPAIGN_REPLAY_AUTHORIZATION_VERIFIER_RESULT_INVALID")
        if (
            verified.authorization_id != request.authorization_id
            or verified.unresolved_request_hash != request.unresolved_request_hash
        ):
            _refuse("FACTOR_MINING_CAMPAIGN_REPLAY_AUTHORIZATION_VERIFIER_BINDING_MISMATCH")
        authorization = _FoundationFactorMiningCampaignReplayAuthorizationInput(
            authorization_id=request.authorization_id,
            actor_id=verified.approver_id,
            unresolved_request_hash=request.unresolved_request_hash,
            authorization_evidence_hash=verified.verifier_receipt_hash,
            authorized_at=utc_now(),
        )
        with self._session_factory() as session:
            receipt = _factor_mining_campaign_authorize_replay(
                session,
                authorization=authorization,
            )
            record_hash = _hash(
                receipt.authorization_record_hash,
                field_name="authorization_record_hash",
            )
        return FactorMiningCampaignReplayAuthorization(
            authorization_hash=receipt.authorization_hash,
            unresolved_request_hash=receipt.unresolved_request_hash,
            authorization_record_hash=record_hash,
        )

    def read_request_events(
        self,
        *,
        request_id: str,
    ) -> Sequence[FactorMiningCampaignLedgerEventReceipt]:
        normalized_request_id = _run_id(request_id, field_name="request_id")
        with self._session_factory() as session:
            ledger = factor_mining_campaign_read_request_ledger(
                session,
                request_id=normalized_request_id,
            )
            if ledger is None:
                _refuse("FACTOR_MINING_CAMPAIGN_REQUEST_NOT_FOUND")
            receipts: list[FactorMiningCampaignLedgerEventReceipt] = []
            for event in ledger.events:
                if event.event_kind in {item.value for item in FactorMiningCampaignLedgerEventKind}:
                    receipts.append(
                        FactorMiningCampaignLedgerEventReceipt(
                            request_hash=event.request_hash,
                            kind=FactorMiningCampaignLedgerEventKind(event.event_kind),
                            record_hash=event.record_hash,
                        )
                    )
        return tuple(receipts)

    @staticmethod
    def _require_registration_metadata(
        preparation: FactorMiningCampaignPreparation,
    ) -> FactorMiningCampaignRegistrationMetadata:
        metadata = preparation.registration_metadata
        if type(metadata) is not FactorMiningCampaignRegistrationMetadata:
            _refuse("FACTOR_MINING_CAMPAIGN_REGISTRATION_METADATA_UNAVAILABLE")
        return metadata

    @staticmethod
    def _require_runner_budget(
        preparation: FactorMiningCampaignPreparation,
    ) -> FactorMiningRunnerResourceBudget:
        budget = preparation.runner_budget
        if type(budget) is not FactorMiningRunnerResourceBudget:
            _refuse("FACTOR_MINING_CAMPAIGN_RUNNER_BUDGET_UNAVAILABLE")
        return budget

    @staticmethod
    def _foundation_failure_code(
        value: str | None,
    ) -> FoundationFactorMiningCampaignFailureCode | None:
        if value is None:
            return None
        try:
            return FoundationFactorMiningCampaignFailureCode(value)
        except ValueError as exc:
            raise FactorMiningCampaignDurabilityError(
                "FACTOR_MINING_CAMPAIGN_INVALID_FAILURE_CODE"
            ) from exc


def _create_postgres_factor_mining_campaign_ledger_for_test(
    *,
    replay_authorization_verifier: _FactorMiningCampaignReplayAuthorizationVerifier,
    session_factory: Callable[[], Session] = SessionLocal,
) -> PostgresFactorMiningCampaignLedger:
    """Private test composition hook; production has no verifier injection API."""

    return PostgresFactorMiningCampaignLedger._from_trusted_composition(
        session_factory=session_factory,
        replay_authorization_verifier=replay_authorization_verifier,
        capability=_TRUSTED_REPLAY_AUTHORIZATION_COMPOSITION_CAPABILITY,
    )


@dataclass(slots=True)
class _LocalFactorMiningCampaignExecutionState:
    """Verified local inputs retained only for one in-process request attempt."""

    declaration: LocalFactorMiningCampaignDeclaration
    source_authorization_hash: str
    verified_data_row_count: int
    started_wall_clock_ns: int | None = None
    started_cpu_ns: int | None = None
    generation_receipt: FactorCandidateGenerationReceipt | None = None
    definition_artifact_byte_count: int | None = None
    prepared_discovery_selection: LocalFactorMiningDiscoverySelectionPreparation | None = None
    prepared_selection: FactorMiningCampaignPreparedSelection | None = None
    prepared_research: LocalFactorMiningResearchPreparation | None = None
    prepared_execution: FactorMiningCampaignPreparedExecution | None = None


class LocalFactorMiningCampaignExecutionAdapter:
    """Concrete DB-free worker adapter around the sealed local research service.

    It reloads the declaration artifact and PIT snapshots itself, so callers
    cannot provide arbitrary row counts or authorization hashes.  It records
    actual process CPU, elapsed wall time, Linux peak RSS and projected output
    bytes.  A pre-publication limit breach is a proven terminal failure; a
    breach or uncertainty after immutable publication is left unresolved.
    """

    __slots__ = (
        "_artifact_store",
        "_bundle_store",
        "_generator",
        "_research_service",
        "_states",
        "_supervisor",
    )

    def __init__(
        self,
        *,
        artifact_store: ArtifactStore,
        generator: FactorCandidateGenerator,
        research_service: LocalFactorMiningResearchService | None = None,
        supervisor: FactorMiningCampaignWorkerSupervisorPort | None = None,
    ) -> None:
        if type(artifact_store) is not ArtifactStore:
            _refuse("FACTOR_MINING_CAMPAIGN_INVALID_ARTIFACT_STORE")
        if generator is None:
            _refuse("FACTOR_MINING_CAMPAIGN_GENERATOR_UNAVAILABLE")
        if research_service is not None and type(research_service) is not LocalFactorMiningResearchService:
            _refuse("FACTOR_MINING_CAMPAIGN_INVALID_RESEARCH_SERVICE")
        self._artifact_store = artifact_store
        self._bundle_store = LocalFactorMiningArtifactBundleStore(
            artifact_store=artifact_store
        )
        self._generator = generator
        self._research_service = (
            LocalFactorMiningResearchService(artifact_store=artifact_store)
            if research_service is None
            else research_service
        )
        self._supervisor = (
            LinuxFactorMiningCampaignWorkerSupervisor()
            if supervisor is None
            else supervisor
        )
        self._states: dict[str, _LocalFactorMiningCampaignExecutionState] = {}

    def preflight(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
    ) -> FactorMiningCampaignPreparation:
        """Reload declaration/PIT facts and derive the only admissible counters."""

        if request.request_hash in self._states:
            _refuse("FACTOR_MINING_CAMPAIGN_PREFLIGHT_ALREADY_PREPARED")
        loaded = self._bundle_store.load_campaign_declaration(
            request.declaration_snapshot_hash
        )
        if loaded.stored.snapshot.snapshot_hash != request.declaration_snapshot_hash:
            _refuse("FACTOR_MINING_CAMPAIGN_DECLARATION_SNAPSHOT_MISMATCH")
        declaration = loaded.declaration
        started_wall_clock_ns = time.monotonic_ns()
        started_cpu_ns = time.process_time_ns()
        market_evidence = self._supervisor.run(
            budget=declaration.runner_budget,
            started_wall_clock_ns=started_wall_clock_ns,
            started_cpu_ns=started_cpu_ns,
            operation=lambda: declaration.plan.replay_market_data(self._artifact_store),
        )
        authorization_hashes = tuple(
            sorted(
                {
                    item.market_snapshot.publication_authorization_hash
                    for item in market_evidence
                }
            )
        )
        if not authorization_hashes:
            _refuse("FACTOR_MINING_CAMPAIGN_DATA_AUTHORIZATION_UNAVAILABLE")
        verified_data_row_count = sum(
            len(item.market_snapshot.revisions) for item in market_evidence
        )
        source_authorization_hash = canonical_json_sha256(
            {
                "authorization_hashes": list(authorization_hashes),
                "format": "northstar.factor-mining-campaign-source-authorization-set.v1",
            }
        )
        data_authorization_hashes = tuple(
            sorted((*authorization_hashes, source_authorization_hash))
        )
        campaign = declaration.campaign
        registration_metadata = FactorMiningCampaignRegistrationMetadata(
            campaign_id=campaign.campaign_id,
            decision_replay_plan_hash=campaign.decision_replay_plan_hash,
            dataset_version_set_hash=canonical_json_sha256(
                {
                    "dataset_version_hashes": list(declaration.dataset_version_hashes),
                    "format": "northstar.factor-mining-campaign-dataset-version-set.v1",
                }
            ),
            template_hash=campaign.template.template_hash,
            search_budget_hash=campaign.budget.budget_hash,
            selection_policy_hash=campaign.selection_policy.policy_hash,
            generator_id=campaign.generator_id,
            generator_model_revision_hash=campaign.generator_model_revision_hash,
            prompt_template_hash=campaign.prompt_template_hash,
            source_authorization_hash=source_authorization_hash,
            code_revision_hash=declaration.config.code_revision_hash,
            selection_at=campaign.selection_at,
            # The immutable declaration's availability time is part of the
            # campaign root.  A wall-clock timestamp here would make replay
            # conflict with an otherwise identical registered declaration.
            registered_at=loaded.stored.snapshot.available_at,
        )
        preparation = FactorMiningCampaignPreparation(
            declaration_snapshot_hash=request.declaration_snapshot_hash,
            declaration_hash=declaration.declaration_hash,
            campaign_hash=campaign.campaign_hash,
            resource_budget_hash=declaration.runner_budget.budget_hash,
            data_authorization_hashes=data_authorization_hashes,
            registration_metadata=registration_metadata,
            runner_budget=declaration.runner_budget,
            verified_data_row_count=verified_data_row_count,
        )
        self._states[request.request_hash] = _LocalFactorMiningCampaignExecutionState(
            declaration=declaration,
            source_authorization_hash=source_authorization_hash,
            verified_data_row_count=verified_data_row_count,
            started_wall_clock_ns=started_wall_clock_ns,
            started_cpu_ns=started_cpu_ns,
        )
        return preparation

    def generate(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
        reservation: FactorMiningCampaignReservation,
    ) -> FactorMiningCampaignGeneration:
        """Make exactly one redacted generator call after durable reservation."""

        state = self._state(request=request, preparation=preparation)
        if state.generation_receipt is not None:
            _refuse("FACTOR_MINING_CAMPAIGN_GENERATION_ALREADY_ATTEMPTED")
        receipt = self._run_bounded(
            state=state,
            operation=lambda: self._generator.generate(
                FactorCandidateGenerationRequest(campaign=state.declaration.campaign)
            ),
        )
        if type(receipt) is not FactorCandidateGenerationReceipt:
            raise FactorMiningCampaignKnownFailure(
                "FACTOR_MINING_CAMPAIGN_RESULT_INVALID"
            )
        try:
            receipt.require_campaign(state.declaration.campaign)
        except ValueError as exc:
            raise FactorMiningCampaignKnownFailure(
                "FACTOR_MINING_CAMPAIGN_RESULT_INVALID"
            ) from exc
        state.generation_receipt = receipt
        generation = FactorMiningCampaignGeneration(
            generation_request_hash=FactorCandidateGenerationRequest(
                campaign=state.declaration.campaign
            ).request_hash,
            generation_receipt_hash=receipt.receipt_hash,
            candidate_count=len(receipt.proposals),
        )
        usage = self._measured_usage(
            state=state,
            reservation=reservation,
            artifact_byte_count=0,
        )
        try:
            usage.require_within(state.declaration.runner_budget)
        except FactorMiningCampaignDurabilityError as exc:
            raise FactorMiningCampaignKnownFailure(
                "FACTOR_MINING_CAMPAIGN_RESOURCE_LIMIT_EXCEEDED",
                resource_usage=usage,
            ) from exc
        return generation

    def execute(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
        reservation: FactorMiningCampaignReservation,
        generation: FactorMiningCampaignGeneration,
    ) -> FactorMiningCampaignPreparedSelection:
        """Project discovery/selection only; it must not invoke ``release_oos``."""

        state = self._state(request=request, preparation=preparation)
        receipt = state.generation_receipt
        if type(receipt) is not FactorCandidateGenerationReceipt:
            raise FactorMiningCampaignKnownFailure(
                "FACTOR_MINING_CAMPAIGN_INPUT_INVALID"
            )
        if receipt.receipt_hash != generation.generation_receipt_hash:
            raise FactorMiningCampaignKnownFailure(
                "FACTOR_MINING_CAMPAIGN_RESULT_INVALID"
            )
        if reservation.max_concurrency_observed is None:
            raise FactorMiningCampaignKnownFailure(
                "FACTOR_MINING_CAMPAIGN_INPUT_INVALID"
            )
        if state.prepared_selection is not None:
            _refuse("FACTOR_MINING_CAMPAIGN_EXECUTION_ALREADY_PREPARED")
        bundle, definition_artifact_byte_count = self._run_bounded(
            state=state,
            operation=lambda: self._bundle_and_definition_artifact_byte_count(
                declaration=state.declaration,
                receipt=receipt,
            ),
        )
        # ``publish_definition`` persists this receipt-bound bundle before
        # discovery.  Its canonical payload is the exact store payload, so it
        # must be part of the resource accounting rather than being hidden
        # behind the later research-evidence count.
        definition_artifact_byte_count = len(bundle.to_bytes())
        usage_before_definition = self._measured_usage(
            state=state,
            reservation=reservation,
            artifact_byte_count=definition_artifact_byte_count,
        )
        try:
            usage_before_definition.require_within(state.declaration.runner_budget)
        except FactorMiningCampaignDurabilityError as exc:
            raise FactorMiningCampaignKnownFailure(
                "FACTOR_MINING_CAMPAIGN_RESOURCE_LIMIT_EXCEEDED",
                resource_usage=usage_before_definition,
            ) from exc
        (
            bundle_snapshot_hash,
            observed_definition_artifact_byte_count,
            prepared_discovery_selection,
        ) = self._run_bounded(
            state=state,
            operation=lambda: self._publish_definition_and_prepare_discovery_selection(
                bundle=bundle,
            ),
        )
        if observed_definition_artifact_byte_count != definition_artifact_byte_count:
            _refuse("FACTOR_MINING_CAMPAIGN_DEFINITION_ARTIFACT_MEASUREMENT_INVALID")
        state.definition_artifact_byte_count = definition_artifact_byte_count
        usage_before_oos = self._measured_usage(
            state=state,
            reservation=reservation,
            artifact_byte_count=definition_artifact_byte_count,
        )
        try:
            usage_before_oos.require_within(state.declaration.runner_budget)
        except FactorMiningCampaignDurabilityError as exc:
            raise FactorMiningCampaignKnownFailure(
                "FACTOR_MINING_CAMPAIGN_RESOURCE_LIMIT_EXCEEDED",
                resource_usage=usage_before_oos,
            ) from exc
        prepared_selection = FactorMiningCampaignPreparedSelection(
            campaign_hash=preparation.campaign_hash,
            declaration_hash=preparation.declaration_hash,
            generation_receipt_hash=generation.generation_receipt_hash,
            bundle_snapshot_hash=bundle_snapshot_hash,
            discovery_result_hash=prepared_discovery_selection.discovery_result_hash,
            selection_commitment_hash=prepared_discovery_selection.selection_commitment_hash,
            selected_candidate_count=prepared_discovery_selection.selected_candidate_count,
            resource_usage=usage_before_oos,
        )
        state.prepared_discovery_selection = prepared_discovery_selection
        state.prepared_selection = prepared_selection
        return prepared_selection

    def prepare_release(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
        reservation: FactorMiningCampaignReservation,
        generation: FactorMiningCampaignGeneration,
        prepared_selection: FactorMiningCampaignPreparedSelection,
    ) -> FactorMiningCampaignPreparedExecution:
        """Call ``release_oos`` only after the outer durable OOS reservation."""

        state = self._state(request=request, preparation=preparation)
        discovery_selection = state.prepared_discovery_selection
        if (
            type(discovery_selection) is not LocalFactorMiningDiscoverySelectionPreparation
            or state.prepared_selection != prepared_selection
        ):
            _refuse("FACTOR_MINING_CAMPAIGN_PREPARED_SELECTION_UNAVAILABLE")
        if (
            prepared_selection.generation_receipt_hash != generation.generation_receipt_hash
            or prepared_selection.selected_candidate_count
            != discovery_selection.selected_candidate_count
        ):
            _refuse("FACTOR_MINING_CAMPAIGN_PREPARED_SELECTION_BINDING_MISMATCH")
        prepared = self._run_bounded(
            state=state,
            operation=lambda: self._research_service.prepare_release(
                preparation=discovery_selection
            ),
        )
        definition_artifact_byte_count = self._definition_artifact_byte_count(state=state)
        usage_before_publication = self._measured_usage(
            state=state,
            reservation=reservation,
            artifact_byte_count=(
                definition_artifact_byte_count + prepared.artifact_byte_count
            ),
        )
        try:
            usage_before_publication.require_within(state.declaration.runner_budget)
        except FactorMiningCampaignDurabilityError as exc:
            if prepared_selection.selected_candidate_count > 0:
                # ``prepare_release`` has already invoked the retained
                # runner's OOS method.  Even though no artifact was
                # published, a terminal FAILED fact would overstate what is
                # known about that OOS action; force explicit inspection.
                raise LocalFactorMiningResearchError(
                    "local factor-mining OOS release exceeded its attested resource budget"
                ) from exc
            raise FactorMiningCampaignKnownFailure(
                "FACTOR_MINING_CAMPAIGN_RESOURCE_LIMIT_EXCEEDED",
                resource_usage=usage_before_publication,
            ) from exc
        prepared_execution = FactorMiningCampaignPreparedExecution(
            campaign_hash=preparation.campaign_hash,
            declaration_hash=preparation.declaration_hash,
            generation_receipt_hash=generation.generation_receipt_hash,
            bundle_snapshot_hash=prepared.bundle_snapshot_hash,
            discovery_result_hash=prepared.discovery_result_hash,
            selection_commitment_hash=prepared.selection_commitment_hash,
            oos_release_hash=prepared.oos_release_hash,
            result_hash=prepared.result_hash,
            selected_candidate_count=prepared.selected_candidate_count,
            resource_usage=usage_before_publication,
        )
        state.prepared_research = prepared
        state.prepared_execution = prepared_execution
        return prepared_execution

    def publish(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
        reservation: FactorMiningCampaignReservation,
        generation: FactorMiningCampaignGeneration,
        prepared_execution: FactorMiningCampaignPreparedExecution,
    ) -> FactorMiningCampaignExecutionResult:
        """Publish only after the outer runner durably records all stage facts."""

        state = self._state(request=request, preparation=preparation)
        prepared = state.prepared_research
        if (
            type(prepared) is not LocalFactorMiningResearchPreparation
            or state.prepared_execution != prepared_execution
        ):
            _refuse("FACTOR_MINING_CAMPAIGN_PREPARED_EXECUTION_UNAVAILABLE")
        if prepared_execution.generation_receipt_hash != generation.generation_receipt_hash:
            _refuse("FACTOR_MINING_CAMPAIGN_PREPARED_EXECUTION_BINDING_MISMATCH")
        published = self._run_bounded(
            state=state,
            operation=lambda: self._research_service.publish(preparation=prepared),
        )
        definition_artifact_byte_count = self._definition_artifact_byte_count(state=state)
        usage = self._measured_usage(
            state=state,
            reservation=reservation,
            artifact_byte_count=(
                definition_artifact_byte_count + prepared.artifact_byte_count
            ),
        )
        try:
            usage.require_within(state.declaration.runner_budget)
        except FactorMiningCampaignDurabilityError as exc:
            # Evidence may already exist.  A terminal FAILED fact would claim
            # an unambiguous outcome despite post-publication uncertainty, so
            # leave the reservation unresolved for explicit human review.
            raise LocalFactorMiningResearchError(
                "local factor-mining publication exceeded its attested resource budget"
            ) from exc
        if (
            published.bundle_snapshot_hash != prepared_execution.bundle_snapshot_hash
            or published.manifest.manifest_hash != prepared.manifest_hash
            or published.manifest.result_hash != prepared.result_hash
            or published.discovery.discovery_result_hash
            != prepared.discovery_result_hash
            or published.commitment.commitment_hash
            != prepared.selection_commitment_hash
            or (
                published.release.release_hash
                if published.release is not None
                else None
            )
            != prepared.oos_release_hash
        ):
            raise LocalFactorMiningResearchError(
                "published local factor-mining evidence no longer matches its prepared identities"
            )
        return FactorMiningCampaignExecutionResult(
            campaign_hash=preparation.campaign_hash,
            declaration_hash=preparation.declaration_hash,
            generation_receipt_hash=generation.generation_receipt_hash,
            bundle_snapshot_hash=prepared_execution.bundle_snapshot_hash,
            discovery_result_hash=prepared.discovery_result_hash,
            selection_commitment_hash=prepared.selection_commitment_hash,
            oos_release_hash=prepared.oos_release_hash,
            manifest_snapshot_hash=published.manifest_snapshot_hash,
            result_hash=prepared.result_hash,
            selected_candidate_count=len(published.commitment.selected_records),
            resource_usage=usage,
        )

    def _state(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
    ) -> _LocalFactorMiningCampaignExecutionState:
        state = self._states.get(request.request_hash)
        if state is None:
            _refuse("FACTOR_MINING_CAMPAIGN_PREFLIGHT_STATE_UNAVAILABLE")
        if (
            state.declaration.declaration_hash != preparation.declaration_hash
            or state.declaration.campaign.campaign_hash != preparation.campaign_hash
            or state.declaration.runner_budget.budget_hash
            != preparation.resource_budget_hash
            or state.verified_data_row_count != preparation.verified_data_row_count
        ):
            _refuse("FACTOR_MINING_CAMPAIGN_PREFLIGHT_BINDING_MISMATCH")
        return state

    def _run_bounded(
        self,
        *,
        state: _LocalFactorMiningCampaignExecutionState,
        operation: Callable[[], _T],
    ) -> _T:
        """Run one DB-free stage against the attempt's non-resetting limits."""

        started_wall_clock_ns = state.started_wall_clock_ns
        started_cpu_ns = state.started_cpu_ns
        if started_wall_clock_ns is None or started_cpu_ns is None:
            _refuse("FACTOR_MINING_CAMPAIGN_RESOURCE_MEASUREMENT_UNAVAILABLE")
        return self._supervisor.run(
            budget=state.declaration.runner_budget,
            started_wall_clock_ns=started_wall_clock_ns,
            started_cpu_ns=started_cpu_ns,
            operation=operation,
        )

    @staticmethod
    def _bundle_and_definition_artifact_byte_count(
        *,
        declaration: LocalFactorMiningCampaignDeclaration,
        receipt: FactorCandidateGenerationReceipt,
    ) -> tuple[LocalFactorMiningRunBundle, int]:
        bundle = LocalFactorMiningRunBundle.from_campaign_declaration(
            declaration=declaration,
            generation=receipt,
        )
        return (bundle, len(bundle.to_bytes()))

    def _publish_definition_and_prepare_discovery_selection(
        self,
        *,
        bundle: LocalFactorMiningRunBundle,
    ) -> tuple[str, int, LocalFactorMiningDiscoverySelectionPreparation]:
        """Persist/verify the receipt-bound definition before discovery work."""

        bundle_snapshot_hash = self._research_service.publish_definition(bundle=bundle)
        verified_definition = self._bundle_store.load_definition(bundle_snapshot_hash)
        preparation = self._research_service.prepare_discovery_selection(
            bundle_snapshot_hash=bundle_snapshot_hash
        )
        return (
            bundle_snapshot_hash,
            verified_definition.stored.byte_length,
            preparation,
        )

    @staticmethod
    def _definition_artifact_byte_count(
        *,
        state: _LocalFactorMiningCampaignExecutionState,
    ) -> int:
        byte_count = state.definition_artifact_byte_count
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 1:
            _refuse("FACTOR_MINING_CAMPAIGN_DEFINITION_ARTIFACT_MEASUREMENT_UNAVAILABLE")
        return byte_count

    @staticmethod
    def _measured_usage(
        *,
        state: _LocalFactorMiningCampaignExecutionState,
        reservation: FactorMiningCampaignReservation | None,
        artifact_byte_count: int,
    ) -> FactorMiningCampaignResourceUsage:
        if state.started_wall_clock_ns is None or state.started_cpu_ns is None:
            _refuse("FACTOR_MINING_CAMPAIGN_RESOURCE_MEASUREMENT_UNAVAILABLE")
        elapsed_wall_ns = time.monotonic_ns() - state.started_wall_clock_ns
        elapsed_cpu_ns = time.process_time_ns() - state.started_cpu_ns
        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if elapsed_wall_ns < 0 or elapsed_cpu_ns < 0 or peak_rss < 0:
            _refuse("FACTOR_MINING_CAMPAIGN_RESOURCE_MEASUREMENT_UNAVAILABLE")
        return FactorMiningCampaignResourceUsage(
            max_concurrency_observed=(
                0
                if reservation is None or reservation.max_concurrency_observed is None
                else reservation.max_concurrency_observed
            ),
            cpu_milliseconds=elapsed_cpu_ns // 1_000_000,
            peak_memory_bytes=peak_rss * 1_024,
            wall_clock_milliseconds=elapsed_wall_ns // 1_000_000,
            data_row_count=state.verified_data_row_count,
            artifact_byte_count=_nonnegative_int(
                artifact_byte_count,
                field_name="artifact_byte_count",
            ),
        )


@dataclass(frozen=True, slots=True)
class DurableFactorMiningCampaignResult:
    """A successful in-memory result paired with its durable audit identities."""

    request: FactorMiningCampaignRunRequest
    preparation: FactorMiningCampaignPreparation
    generation: FactorMiningCampaignGeneration
    execution: FactorMiningCampaignExecutionResult
    registration_record_hash: str
    reservation_record_hash: str
    receipt_record_hash: str
    completion_record_hash: str

    def __post_init__(self) -> None:
        if type(self.request) is not FactorMiningCampaignRunRequest:
            _refuse("FACTOR_MINING_CAMPAIGN_RESULT_REQUEST_INVALID")
        if type(self.preparation) is not FactorMiningCampaignPreparation:
            _refuse("FACTOR_MINING_CAMPAIGN_RESULT_PREFLIGHT_INVALID")
        if type(self.generation) is not FactorMiningCampaignGeneration:
            _refuse("FACTOR_MINING_CAMPAIGN_RESULT_GENERATION_INVALID")
        if type(self.execution) is not FactorMiningCampaignExecutionResult:
            _refuse("FACTOR_MINING_CAMPAIGN_RESULT_EXECUTION_INVALID")
        if self.preparation.declaration_snapshot_hash != self.request.declaration_snapshot_hash:
            _refuse("FACTOR_MINING_CAMPAIGN_RESULT_DECLARATION_MISMATCH")
        if (
            self.execution.campaign_hash != self.preparation.campaign_hash
            or self.execution.declaration_hash != self.preparation.declaration_hash
            or self.execution.generation_receipt_hash
            != self.generation.generation_receipt_hash
        ):
            _refuse("FACTOR_MINING_CAMPAIGN_RESULT_BINDING_MISMATCH")
        object.__setattr__(
            self,
            "registration_record_hash",
            _hash(self.registration_record_hash, field_name="registration_record_hash"),
        )
        object.__setattr__(
            self,
            "reservation_record_hash",
            _hash(self.reservation_record_hash, field_name="reservation_record_hash"),
        )
        object.__setattr__(
            self,
            "receipt_record_hash",
            _hash(self.receipt_record_hash, field_name="receipt_record_hash"),
        )
        object.__setattr__(
            self,
            "completion_record_hash",
            _hash(self.completion_record_hash, field_name="completion_record_hash"),
        )

    def as_mapping(self) -> dict[str, object]:
        """Return the bounded CLI/audit projection; no provider text is present."""

        return {
            "bundle_snapshot_hash": self.execution.bundle_snapshot_hash,
            "campaign_hash": self.preparation.campaign_hash,
            "completion_record_hash": self.completion_record_hash,
            "declaration_hash": self.preparation.declaration_hash,
            "declaration_snapshot_hash": self.request.declaration_snapshot_hash,
            "discovery_result_hash": self.execution.discovery_result_hash,
            "generation_receipt_hash": self.generation.generation_receipt_hash,
            "manifest_snapshot_hash": self.execution.manifest_snapshot_hash,
            "oos_release_hash": self.execution.oos_release_hash,
            "receipt_record_hash": self.receipt_record_hash,
            "registration_record_hash": self.registration_record_hash,
            "request_hash": self.request.request_hash,
            "reservation_record_hash": self.reservation_record_hash,
            "resource_budget_hash": self.preparation.resource_budget_hash,
            "resource_usage_hash": self.execution.resource_usage.resource_usage_hash,
            "result_hash": self.execution.result_hash,
            "research_only": True,
            "run_id": self.request.run_id,
            "selected_candidate_count": self.execution.selected_candidate_count,
            "selection_commitment_hash": self.execution.selection_commitment_hash,
        }


class DurableFactorMiningCampaignRunner:
    """Reserve → generate once → execute once → append one terminal outcome.

    The external interface intentionally has two methods only: :meth:`run`
    and :meth:`authorize_replay`.  Queueing, resource enforcement and local
    factor replay stay behind the injected execution seam; PostgreSQL details
    stay behind the injected ledger seam.
    """

    __slots__ = ("_execution", "_ledger")

    def __init__(
        self,
        *,
        ledger: FactorMiningCampaignLedgerPort,
        execution: FactorMiningCampaignExecutionPort,
    ) -> None:
        self._ledger = ledger
        self._execution = execution

    def authorize_replay(
        self,
        request: FactorMiningCampaignReplayAuthorizationIntent,
    ) -> FactorMiningCampaignReplayAuthorization:
        """Verify and append one human replay authorization, without running work."""

        if type(request) is not FactorMiningCampaignReplayAuthorizationIntent:
            _refuse("FACTOR_MINING_CAMPAIGN_REPLAY_AUTHORIZATION_INTENT_INVALID")
        try:
            authorization = self._ledger.authorize_replay(request=request)
        except Exception as exc:
            # If PostgreSQL committed but the response was lost, retrying this
            # authorization could create a conflicting audit fact.  The caller
            # must inspect the ledger manually rather than retrying blindly.
            raise FactorMiningCampaignDurabilityError(
                "FACTOR_MINING_CAMPAIGN_REPLAY_AUTHORIZATION_UNRESOLVED"
            ) from exc
        if type(authorization) is not FactorMiningCampaignReplayAuthorization:
            _refuse("FACTOR_MINING_CAMPAIGN_REPLAY_AUTHORIZATION_INVALID")
        if authorization.unresolved_request_hash != request.unresolved_request_hash:
            _refuse("FACTOR_MINING_CAMPAIGN_REPLAY_AUTHORIZATION_BINDING_MISMATCH")
        return authorization

    def run(self, request: FactorMiningCampaignRunRequest) -> DurableFactorMiningCampaignResult:
        """Reserve, execute once, and append every proved research transition.

        Preflight is read-only.  The reservation is committed before generation
        or factor work, and an ambiguous error after that point remains an
        unresolved request.  A valid execution is recorded in the same
        receipt/discovery/selection/OOS/result vocabulary as the Foundation
        PostgreSQL ledger; no generic completion fact exists.
        """

        if type(request) is not FactorMiningCampaignRunRequest:
            _refuse("FACTOR_MINING_CAMPAIGN_RUN_REQUEST_INVALID")
        preparation = self._preflight(request)
        self._require_concrete_preflight_measurements(preparation=preparation)
        registration = self._register(preparation)
        reservation = self._reserve(
            request=request,
            preparation=preparation,
            registration=registration,
        )
        self._require_concrete_reservation_measurement(reservation=reservation)

        try:
            generation = self._execution.generate(
                request=request,
                preparation=preparation,
                reservation=reservation,
            )
        except FactorMiningCampaignKnownFailure as exc:
            self._record_known_failure(
                request=request,
                preparation=preparation,
                predecessor_record_hash=reservation.reservation_record_hash,
                reason_code=exc.reason_code,
                resource_usage=exc.resource_usage,
            )
            _refuse(f"FACTOR_MINING_CAMPAIGN_GENERATION_{exc.reason_code}")
        except Exception as exc:
            # A provider can perform an action and lose its response.  The
            # committed reservation must remain unresolved and block replay.
            raise FactorMiningCampaignDurabilityError(
                "FACTOR_MINING_CAMPAIGN_GENERATION_UNRESOLVED"
            ) from exc
        if type(generation) is not FactorMiningCampaignGeneration:
            self._record_known_failure(
                request=request,
                preparation=preparation,
                predecessor_record_hash=reservation.reservation_record_hash,
                reason_code="FACTOR_MINING_CAMPAIGN_RESULT_INVALID",
            )
            _refuse("FACTOR_MINING_CAMPAIGN_GENERATION_RESULT_INVALID")
        if (
            preparation.runner_budget is not None
            and generation.candidate_count > preparation.runner_budget.max_candidates
        ):
            self._record_known_failure(
                request=request,
                preparation=preparation,
                predecessor_record_hash=reservation.reservation_record_hash,
                reason_code="FACTOR_MINING_CAMPAIGN_RESOURCE_LIMIT_EXCEEDED",
            )
            _refuse("FACTOR_MINING_CAMPAIGN_GENERATION_RESOURCE_LIMIT_EXCEEDED")

        receipt_record_hash = self._append(
            event=FactorMiningCampaignLedgerEvent(
                kind=FactorMiningCampaignLedgerEventKind.RECEIPT_RECORDED,
                request_id=request.run_id,
                request_hash=request.request_hash,
                campaign_hash=preparation.campaign_hash,
                declaration_hash=preparation.declaration_hash,
                resource_budget_hash=preparation.resource_budget_hash,
                predecessor_record_hash=reservation.reservation_record_hash,
                generation_receipt_hash=generation.generation_receipt_hash,
                candidate_count=generation.candidate_count,
            ),
            unresolved_code="FACTOR_MINING_CAMPAIGN_RECEIPT_UNRESOLVED",
        )

        try:
            prepared_selection = self._execution.execute(
                request=request,
                preparation=preparation,
                reservation=reservation,
                generation=generation,
            )
        except FactorMiningCampaignKnownFailure as exc:
            self._record_known_failure(
                request=request,
                preparation=preparation,
                predecessor_record_hash=receipt_record_hash,
                reason_code=exc.reason_code,
                resource_usage=exc.resource_usage,
            )
            _refuse(f"FACTOR_MINING_CAMPAIGN_EXECUTION_{exc.reason_code}")
        except Exception as exc:
            # Worker cancellation/timeout/crash and any indeterminate
            # discovery/selection preparation remain unresolved, not FAILED.
            raise FactorMiningCampaignDurabilityError(
                "FACTOR_MINING_CAMPAIGN_EXECUTION_UNRESOLVED"
            ) from exc
        if type(prepared_selection) is not FactorMiningCampaignPreparedSelection:
            self._record_known_failure(
                request=request,
                preparation=preparation,
                predecessor_record_hash=receipt_record_hash,
                reason_code="FACTOR_MINING_CAMPAIGN_RESULT_INVALID",
            )
            _refuse("FACTOR_MINING_CAMPAIGN_PREPARED_SELECTION_INVALID")
        if not self._prepared_selection_binds(
            preparation=preparation,
            generation=generation,
            prepared_selection=prepared_selection,
        ):
            self._record_known_failure(
                request=request,
                preparation=preparation,
                predecessor_record_hash=receipt_record_hash,
                reason_code="FACTOR_MINING_CAMPAIGN_RESULT_INVALID",
                resource_usage=prepared_selection.resource_usage,
            )
            _refuse("FACTOR_MINING_CAMPAIGN_PREPARED_SELECTION_BINDING_INVALID")
        self._require_prepared_selection_evidence(
            request=request,
            preparation=preparation,
            reservation=reservation,
            generation=generation,
            prepared_selection=prepared_selection,
            predecessor_record_hash=receipt_record_hash,
        )

        discovery_record_hash = self._append(
            event=FactorMiningCampaignLedgerEvent(
                kind=FactorMiningCampaignLedgerEventKind.DISCOVERY_RECORDED,
                request_id=request.run_id,
                request_hash=request.request_hash,
                campaign_hash=preparation.campaign_hash,
                declaration_hash=preparation.declaration_hash,
                resource_budget_hash=preparation.resource_budget_hash,
                predecessor_record_hash=receipt_record_hash,
                discovery_result_hash=prepared_selection.discovery_result_hash,
            ),
            unresolved_code="FACTOR_MINING_CAMPAIGN_DISCOVERY_UNRESOLVED",
        )
        selection_record_hash = self._append(
            event=FactorMiningCampaignLedgerEvent(
                kind=FactorMiningCampaignLedgerEventKind.SELECTION_COMMITTED,
                request_id=request.run_id,
                request_hash=request.request_hash,
                campaign_hash=preparation.campaign_hash,
                declaration_hash=preparation.declaration_hash,
                resource_budget_hash=preparation.resource_budget_hash,
                predecessor_record_hash=discovery_record_hash,
                selection_commitment_hash=prepared_selection.selection_commitment_hash,
                selected_candidate_count=prepared_selection.selected_candidate_count,
            ),
            unresolved_code="FACTOR_MINING_CAMPAIGN_SELECTION_UNRESOLVED",
        )
        result_predecessor_hash = selection_record_hash
        if prepared_selection.selected_candidate_count > 0:
            oos_reservation_record_hash = self._append(
                event=FactorMiningCampaignLedgerEvent(
                    kind=FactorMiningCampaignLedgerEventKind.OOS_RESERVED,
                    request_id=request.run_id,
                    request_hash=request.request_hash,
                    campaign_hash=preparation.campaign_hash,
                    declaration_hash=preparation.declaration_hash,
                    resource_budget_hash=preparation.resource_budget_hash,
                    predecessor_record_hash=selection_record_hash,
                ),
                unresolved_code="FACTOR_MINING_CAMPAIGN_OOS_RESERVATION_UNRESOLVED",
            )
            result_predecessor_hash = oos_reservation_record_hash

        try:
            prepared_execution = self._execution.prepare_release(
                request=request,
                preparation=preparation,
                reservation=reservation,
                generation=generation,
                prepared_selection=prepared_selection,
            )
        except FactorMiningCampaignKnownFailure as exc:
            if prepared_selection.selected_candidate_count > 0:
                # This call is the first operation permitted to invoke
                # ``release_oos``.  Even a worker-reported known failure
                # cannot prove whether it released any OOS material before
                # returning, so its reservation remains unresolved.
                raise FactorMiningCampaignDurabilityError(
                    "FACTOR_MINING_CAMPAIGN_OOS_UNRESOLVED"
                ) from exc
            self._record_known_failure(
                request=request,
                preparation=preparation,
                predecessor_record_hash=result_predecessor_hash,
                reason_code=exc.reason_code,
                resource_usage=exc.resource_usage,
            )
            _refuse(f"FACTOR_MINING_CAMPAIGN_OOS_{exc.reason_code}")
        except Exception as exc:
            # OOS may have started but not proven a releasable result; do not
            # append an OOS release or terminal failure automatically.
            raise FactorMiningCampaignDurabilityError(
                "FACTOR_MINING_CAMPAIGN_OOS_UNRESOLVED"
            ) from exc
        if type(prepared_execution) is not FactorMiningCampaignPreparedExecution:
            _refuse("FACTOR_MINING_CAMPAIGN_OOS_UNRESOLVED")
        if not self._prepared_execution_binds(
            prepared_selection=prepared_selection,
            prepared_execution=prepared_execution,
        ):
            _refuse("FACTOR_MINING_CAMPAIGN_OOS_UNRESOLVED")
        self._require_prepared_execution_evidence(
            preparation=preparation,
            reservation=reservation,
            prepared_selection=prepared_selection,
            prepared_execution=prepared_execution,
            predecessor_record_hash=result_predecessor_hash,
            request=request,
        )
        if prepared_selection.selected_candidate_count > 0:
            result_predecessor_hash = self._append(
                event=FactorMiningCampaignLedgerEvent(
                    kind=FactorMiningCampaignLedgerEventKind.OOS_RELEASED,
                    request_id=request.run_id,
                    request_hash=request.request_hash,
                    campaign_hash=preparation.campaign_hash,
                    declaration_hash=preparation.declaration_hash,
                    resource_budget_hash=preparation.resource_budget_hash,
                    predecessor_record_hash=result_predecessor_hash,
                    oos_release_hash=prepared_execution.oos_release_hash,
                ),
                unresolved_code="FACTOR_MINING_CAMPAIGN_OOS_RELEASE_UNRESOLVED",
            )

        try:
            execution = self._execution.publish(
                request=request,
                preparation=preparation,
                reservation=reservation,
                generation=generation,
                prepared_execution=prepared_execution,
            )
        except Exception as exc:
            # The stage chain is durable, but publication might have written
            # some immutable evidence before an error.  It must remain
            # unresolved rather than inventing a terminal failure or retry.
            raise FactorMiningCampaignDurabilityError(
                "FACTOR_MINING_CAMPAIGN_PUBLICATION_UNRESOLVED"
            ) from exc
        if type(execution) is not FactorMiningCampaignExecutionResult:
            _refuse("FACTOR_MINING_CAMPAIGN_PUBLICATION_UNRESOLVED")
        if not self._published_execution_binds(
            prepared_execution=prepared_execution,
            execution=execution,
        ):
            _refuse("FACTOR_MINING_CAMPAIGN_PUBLICATION_UNRESOLVED")
        self._require_published_execution_measurements(
            preparation=preparation,
            reservation=reservation,
            execution=execution,
        )

        completion_record_hash = self._append(
            event=FactorMiningCampaignLedgerEvent(
                kind=FactorMiningCampaignLedgerEventKind.RESULT_RECORDED,
                request_id=request.run_id,
                request_hash=request.request_hash,
                campaign_hash=preparation.campaign_hash,
                declaration_hash=preparation.declaration_hash,
                resource_budget_hash=preparation.resource_budget_hash,
                predecessor_record_hash=result_predecessor_hash,
                bundle_snapshot_hash=execution.bundle_snapshot_hash,
                manifest_snapshot_hash=execution.manifest_snapshot_hash,
                result_hash=execution.result_hash,
                resource_usage=execution.resource_usage,
            ),
            unresolved_code="FACTOR_MINING_CAMPAIGN_RESULT_UNRESOLVED",
        )
        return DurableFactorMiningCampaignResult(
            request=request,
            preparation=preparation,
            generation=generation,
            execution=execution,
            registration_record_hash=registration.registration_record_hash,
            reservation_record_hash=reservation.reservation_record_hash,
            receipt_record_hash=receipt_record_hash,
            completion_record_hash=completion_record_hash,
        )

    def _require_prepared_selection_evidence(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
        reservation: FactorMiningCampaignReservation,
        generation: FactorMiningCampaignGeneration,
        prepared_selection: FactorMiningCampaignPreparedSelection,
        predecessor_record_hash: str,
    ) -> None:
        """Prove discovery/selection facts before the durable OOS reservation."""

        if prepared_selection.selected_candidate_count > generation.candidate_count:
            self._record_known_failure(
                request=request,
                preparation=preparation,
                predecessor_record_hash=predecessor_record_hash,
                reason_code="FACTOR_MINING_CAMPAIGN_RESULT_INVALID",
                resource_usage=prepared_selection.resource_usage,
            )
            _refuse("FACTOR_MINING_CAMPAIGN_SELECTED_CANDIDATE_COUNT_INVALID")
        self._require_prepublication_measurements(
            request=request,
            preparation=preparation,
            reservation=reservation,
            resource_usage=prepared_selection.resource_usage,
            predecessor_record_hash=predecessor_record_hash,
        )

    def _require_prepared_execution_evidence(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
        reservation: FactorMiningCampaignReservation,
        prepared_selection: FactorMiningCampaignPreparedSelection,
        prepared_execution: FactorMiningCampaignPreparedExecution,
        predecessor_record_hash: str,
    ) -> None:
        """Prove OOS/research projection identities before immutable publication."""

        if (
            prepared_execution.selected_candidate_count == 0
            and prepared_execution.oos_release_hash is not None
        ) or (
            prepared_execution.selected_candidate_count > 0
            and prepared_execution.oos_release_hash is None
        ):
            if prepared_selection.selected_candidate_count > 0:
                _refuse("FACTOR_MINING_CAMPAIGN_OOS_UNRESOLVED")
            self._record_known_failure(
                request=request,
                preparation=preparation,
                predecessor_record_hash=predecessor_record_hash,
                reason_code="FACTOR_MINING_CAMPAIGN_RESULT_INVALID",
                resource_usage=prepared_execution.resource_usage,
            )
            _refuse("FACTOR_MINING_CAMPAIGN_OOS_RESULT_BINDING_INVALID")
        if (
            prepared_execution.selected_candidate_count
            != prepared_selection.selected_candidate_count
        ):
            if prepared_selection.selected_candidate_count > 0:
                _refuse("FACTOR_MINING_CAMPAIGN_OOS_UNRESOLVED")
            self._record_known_failure(
                request=request,
                preparation=preparation,
                predecessor_record_hash=predecessor_record_hash,
                reason_code="FACTOR_MINING_CAMPAIGN_RESULT_INVALID",
                resource_usage=prepared_execution.resource_usage,
            )
            _refuse("FACTOR_MINING_CAMPAIGN_OOS_SELECTION_COUNT_MISMATCH")
        self._require_prepublication_measurements(
            request=request,
            preparation=preparation,
            reservation=reservation,
            resource_usage=prepared_execution.resource_usage,
            predecessor_record_hash=predecessor_record_hash,
            oos_may_have_been_released=(
                prepared_selection.selected_candidate_count > 0
            ),
        )

    def _require_prepublication_measurements(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
        reservation: FactorMiningCampaignReservation,
        resource_usage: FactorMiningCampaignResourceUsage,
        predecessor_record_hash: str,
        oos_may_have_been_released: bool = False,
    ) -> None:
        """Record a terminal failure only while no OOS action may have occurred."""

        if (
            preparation.verified_data_row_count is not None
            and resource_usage.data_row_count != preparation.verified_data_row_count
        ):
            if oos_may_have_been_released:
                _refuse("FACTOR_MINING_CAMPAIGN_OOS_UNRESOLVED")
            self._record_known_failure(
                request=request,
                preparation=preparation,
                predecessor_record_hash=predecessor_record_hash,
                reason_code="FACTOR_MINING_CAMPAIGN_RESULT_INVALID",
                resource_usage=resource_usage,
            )
            _refuse("FACTOR_MINING_CAMPAIGN_DATA_ROW_COUNT_MISMATCH")
        if (
            reservation.max_concurrency_observed is not None
            and resource_usage.max_concurrency_observed
            != reservation.max_concurrency_observed
        ):
            if oos_may_have_been_released:
                _refuse("FACTOR_MINING_CAMPAIGN_OOS_UNRESOLVED")
            self._record_known_failure(
                request=request,
                preparation=preparation,
                predecessor_record_hash=predecessor_record_hash,
                reason_code="FACTOR_MINING_CAMPAIGN_RESULT_INVALID",
                resource_usage=resource_usage,
            )
            _refuse("FACTOR_MINING_CAMPAIGN_CONCURRENCY_MEASUREMENT_MISMATCH")
        if preparation.runner_budget is not None:
            try:
                resource_usage.require_within(preparation.runner_budget)
            except FactorMiningCampaignDurabilityError as exc:
                if oos_may_have_been_released:
                    raise FactorMiningCampaignDurabilityError(
                        "FACTOR_MINING_CAMPAIGN_OOS_UNRESOLVED"
                    ) from exc
                self._record_known_failure(
                    request=request,
                    preparation=preparation,
                    predecessor_record_hash=predecessor_record_hash,
                    reason_code="FACTOR_MINING_CAMPAIGN_RESOURCE_LIMIT_EXCEEDED",
                    resource_usage=resource_usage,
                )
                raise exc

    @staticmethod
    def _prepared_selection_binds(
        *,
        preparation: FactorMiningCampaignPreparation,
        generation: FactorMiningCampaignGeneration,
        prepared_selection: FactorMiningCampaignPreparedSelection,
    ) -> bool:
        return (
            prepared_selection.campaign_hash == preparation.campaign_hash
            and prepared_selection.declaration_hash == preparation.declaration_hash
            and prepared_selection.generation_receipt_hash
            == generation.generation_receipt_hash
        )

    @staticmethod
    def _prepared_execution_binds(
        *,
        prepared_selection: FactorMiningCampaignPreparedSelection,
        prepared_execution: FactorMiningCampaignPreparedExecution,
    ) -> bool:
        return (
            prepared_execution.campaign_hash == prepared_selection.campaign_hash
            and prepared_execution.declaration_hash == prepared_selection.declaration_hash
            and prepared_execution.generation_receipt_hash
            == prepared_selection.generation_receipt_hash
            and prepared_execution.bundle_snapshot_hash
            == prepared_selection.bundle_snapshot_hash
            and prepared_execution.discovery_result_hash
            == prepared_selection.discovery_result_hash
            and prepared_execution.selection_commitment_hash
            == prepared_selection.selection_commitment_hash
            and prepared_execution.selected_candidate_count
            == prepared_selection.selected_candidate_count
        )

    @staticmethod
    def _published_execution_binds(
        *,
        prepared_execution: FactorMiningCampaignPreparedExecution,
        execution: FactorMiningCampaignExecutionResult,
    ) -> bool:
        return (
            execution.campaign_hash == prepared_execution.campaign_hash
            and execution.declaration_hash == prepared_execution.declaration_hash
            and execution.generation_receipt_hash
            == prepared_execution.generation_receipt_hash
            and execution.bundle_snapshot_hash == prepared_execution.bundle_snapshot_hash
            and execution.discovery_result_hash
            == prepared_execution.discovery_result_hash
            and execution.selection_commitment_hash
            == prepared_execution.selection_commitment_hash
            and execution.oos_release_hash == prepared_execution.oos_release_hash
            and execution.result_hash == prepared_execution.result_hash
            and execution.selected_candidate_count
            == prepared_execution.selected_candidate_count
        )

    @staticmethod
    def _require_published_execution_measurements(
        *,
        preparation: FactorMiningCampaignPreparation,
        reservation: FactorMiningCampaignReservation,
        execution: FactorMiningCampaignExecutionResult,
    ) -> None:
        """Post-publication discrepancies are ambiguous and must stay unresolved."""

        if (
            preparation.verified_data_row_count is not None
            and execution.resource_usage.data_row_count
            != preparation.verified_data_row_count
        ) or (
            reservation.max_concurrency_observed is not None
            and execution.resource_usage.max_concurrency_observed
            != reservation.max_concurrency_observed
        ):
            _refuse("FACTOR_MINING_CAMPAIGN_PUBLICATION_UNRESOLVED")
        if preparation.runner_budget is not None:
            try:
                execution.resource_usage.require_within(preparation.runner_budget)
            except FactorMiningCampaignDurabilityError as exc:
                raise FactorMiningCampaignDurabilityError(
                    "FACTOR_MINING_CAMPAIGN_PUBLICATION_UNRESOLVED"
                ) from exc

    def _preflight(
        self,
        request: FactorMiningCampaignRunRequest,
    ) -> FactorMiningCampaignPreparation:
        try:
            preparation = self._execution.preflight(request=request)
        except Exception as exc:
            raise FactorMiningCampaignDurabilityError(
                "FACTOR_MINING_CAMPAIGN_PREFLIGHT_REFUSED"
            ) from exc
        if type(preparation) is not FactorMiningCampaignPreparation:
            _refuse("FACTOR_MINING_CAMPAIGN_PREFLIGHT_INVALID")
        if preparation.declaration_snapshot_hash != request.declaration_snapshot_hash:
            _refuse("FACTOR_MINING_CAMPAIGN_PREFLIGHT_DECLARATION_MISMATCH")
        return preparation

    def _require_concrete_preflight_measurements(
        self,
        *,
        preparation: FactorMiningCampaignPreparation,
    ) -> None:
        """Reject the PostgreSQL path before reservation/generation if facts are absent."""

        if not isinstance(self._ledger, PostgresFactorMiningCampaignLedger):
            return
        if (
            preparation.registration_metadata is None
            or preparation.runner_budget is None
            or preparation.verified_data_row_count is None
        ):
            _refuse("FACTOR_MINING_CAMPAIGN_RESOURCE_MEASUREMENT_UNAVAILABLE")

    def _require_concrete_reservation_measurement(
        self,
        *,
        reservation: FactorMiningCampaignReservation,
    ) -> None:
        """Require the repository-attested active count before any generator call."""

        if (
            isinstance(self._ledger, PostgresFactorMiningCampaignLedger)
            and reservation.max_concurrency_observed is None
        ):
            _refuse("FACTOR_MINING_CAMPAIGN_RESOURCE_MEASUREMENT_UNAVAILABLE")

    def _register(
        self,
        preparation: FactorMiningCampaignPreparation,
    ) -> FactorMiningCampaignRegistration:
        try:
            registration = self._ledger.register_campaign(preparation=preparation)
        except Exception as exc:
            raise FactorMiningCampaignDurabilityError(
                "FACTOR_MINING_CAMPAIGN_REGISTRATION_REFUSED"
            ) from exc
        if type(registration) is not FactorMiningCampaignRegistration:
            _refuse("FACTOR_MINING_CAMPAIGN_REGISTRATION_INVALID")
        if (
            registration.campaign_hash != preparation.campaign_hash
            or registration.declaration_hash != preparation.declaration_hash
            or registration.resource_budget_hash != preparation.resource_budget_hash
        ):
            _refuse("FACTOR_MINING_CAMPAIGN_REGISTRATION_BINDING_MISMATCH")
        return registration

    def _reserve(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
        registration: FactorMiningCampaignRegistration,
    ) -> FactorMiningCampaignReservation:
        try:
            reservation = self._ledger.reserve_request(
                request=request,
                preparation=preparation,
                registration=registration,
            )
        except Exception as exc:
            # This includes duplicate/concurrent reservations.  No generator
            # call has happened yet, therefore admission is safely refused.
            raise FactorMiningCampaignDurabilityError(
                "FACTOR_MINING_CAMPAIGN_RESERVATION_REFUSED"
            ) from exc
        if type(reservation) is not FactorMiningCampaignReservation:
            _refuse("FACTOR_MINING_CAMPAIGN_RESERVATION_INVALID")
        if (
            reservation.request_hash != request.request_hash
            or reservation.campaign_hash != preparation.campaign_hash
            or reservation.declaration_hash != preparation.declaration_hash
        ):
            _refuse("FACTOR_MINING_CAMPAIGN_RESERVATION_BINDING_MISMATCH")
        return reservation

    def _append(
        self,
        *,
        event: FactorMiningCampaignLedgerEvent,
        unresolved_code: str,
    ) -> str:
        try:
            receipt = self._ledger.append_event(event=event)
        except Exception as exc:
            # The database may have committed the event before a transport
            # failure.  Never append another event or rerun work in response.
            raise FactorMiningCampaignDurabilityError(unresolved_code) from exc
        if type(receipt) is not FactorMiningCampaignLedgerEventReceipt:
            _refuse(unresolved_code)
        if receipt.request_hash != event.request_hash or receipt.kind is not event.kind:
            _refuse(unresolved_code)
        return receipt.record_hash

    def _record_known_failure(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
        predecessor_record_hash: str,
        reason_code: str,
        resource_usage: FactorMiningCampaignResourceUsage | None = None,
    ) -> str:
        normalized_reason_code = _failure_code(reason_code)
        return self._append(
            event=FactorMiningCampaignLedgerEvent(
                kind=FactorMiningCampaignLedgerEventKind.FAILED,
                request_id=request.run_id,
                request_hash=request.request_hash,
                campaign_hash=preparation.campaign_hash,
                declaration_hash=preparation.declaration_hash,
                resource_budget_hash=preparation.resource_budget_hash,
                predecessor_record_hash=predecessor_record_hash,
                failure_code=normalized_reason_code,
                resource_usage=resource_usage,
            ),
            unresolved_code="FACTOR_MINING_CAMPAIGN_FAILURE_UNRESOLVED",
        )

    @staticmethod
    def _execution_binds(
        *,
        preparation: FactorMiningCampaignPreparation,
        generation: FactorMiningCampaignGeneration,
        execution: FactorMiningCampaignExecutionResult,
    ) -> bool:
        return (
            execution.campaign_hash == preparation.campaign_hash
            and execution.declaration_hash == preparation.declaration_hash
            and execution.generation_receipt_hash == generation.generation_receipt_hash
        )


def build_local_factor_mining_campaign_runner(
    *,
    artifact_store: ArtifactStore,
    generator: FactorCandidateGenerator,
    session_factory: Callable[[], Session] = SessionLocal,
) -> DurableFactorMiningCampaignRunner:
    """Compose the only concrete PostgreSQL + DB-free local worker path.

    The provider adapter remains a trusted injected capability.  It is never
    discovered from command-line input, an environment string, or a dynamic
    import; the worker receives no database session and the ledger receives no
    provider or research capability.
    """

    return DurableFactorMiningCampaignRunner(
        ledger=PostgresFactorMiningCampaignLedger(session_factory=session_factory),
        execution=LocalFactorMiningCampaignExecutionAdapter(
            artifact_store=artifact_store,
            generator=generator,
        ),
    )

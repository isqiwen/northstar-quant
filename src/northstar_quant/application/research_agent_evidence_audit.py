"""Durable, hash-only PostgreSQL evidence composition for ``ResearchAgent``.

This module is deliberately outside :mod:`research_agent`: the constrained
agent keeps its single typed-tool capability and never receives a database
session.  The wrapper commits a short-lived reservation before invoking the
agent, then appends a sanitized terminal fact and ordered hash trace in a new
transaction.  A process interruption therefore leaves an unresolved
reservation which prevents an automatic replay of the same run identity.

Only opaque identifiers, timestamps, stable SHA-256 commitments, lifecycle,
and ordered tool-trace hashes cross the persistence boundary.  Request and
result text remain in memory only and are never serialized here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
import json
import re
from typing import Literal, NoReturn

from sqlalchemy.orm import Session, sessionmaker

from northstar_quant.application.research_agent import (
    ResearchAgent,
    ResearchAgentRequest,
    ResearchAgentResult,
    ResearchAgentTraceEntry,
    research_agent_request_hash,
)
from northstar_quant.foundation.common.time import utc_now
from northstar_quant.foundation.db.repositories import (
    ResearchAgentRunTraceInput,
    admit_research_agent_run,
    complete_research_agent_run,
    fail_research_agent_run,
)


__all__ = [
    "DurableResearchAgentAuditReceipt",
    "DurableResearchAgentResult",
    "DurableResearchAgentRunner",
    "ResearchAgentEvidenceAuditError",
]


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class ResearchAgentEvidenceAuditError(RuntimeError):
    """The durable evidence boundary cannot establish an auditable outcome."""


def _refuse(code: str) -> NoReturn:
    raise ResearchAgentEvidenceAuditError(code)


def _identifier(value: object, *, code: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        _refuse(code)
    return value


def _hash(value: object, *, code: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        _refuse(code)
    return value


def _time(value: object, *, code: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _refuse(code)
    return value.astimezone(UTC)


def _text_hash(value: str) -> str:
    """Commit locally to bounded in-memory text without retaining the text itself."""

    return sha256(value.encode("utf-8")).hexdigest()


def _fingerprint(payload: dict[str, object]) -> str:
    """Return a deterministic local commitment without persisting its projection."""

    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ResearchAgentEvidenceAuditError(
            "RESEARCH_AGENT_AUDIT_RESULT_COMMITMENT_INVALID"
        ) from exc
    return sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class DurableResearchAgentAuditReceipt:
    """Hash-only receipt for one completed, non-tradable research-agent run."""

    run_id: str
    request_hash: str
    result_hash: str
    trace_tip_hash: str
    trace_count: int
    admission_record_hash: str
    terminal_record_hash: str
    lifecycle: Literal["RESEARCH_ONLY"]
    eligible_for_trading: Literal[False] = field(default=False, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "run_id",
            _identifier(self.run_id, code="RESEARCH_AGENT_AUDIT_RECEIPT_RUN_ID_INVALID"),
        )
        object.__setattr__(
            self,
            "request_hash",
            _hash(self.request_hash, code="RESEARCH_AGENT_AUDIT_RECEIPT_HASH_INVALID"),
        )
        object.__setattr__(
            self,
            "result_hash",
            _hash(self.result_hash, code="RESEARCH_AGENT_AUDIT_RECEIPT_HASH_INVALID"),
        )
        object.__setattr__(
            self,
            "trace_tip_hash",
            _hash(self.trace_tip_hash, code="RESEARCH_AGENT_AUDIT_RECEIPT_HASH_INVALID"),
        )
        object.__setattr__(
            self,
            "admission_record_hash",
            _hash(
                self.admission_record_hash,
                code="RESEARCH_AGENT_AUDIT_RECEIPT_HASH_INVALID",
            ),
        )
        object.__setattr__(
            self,
            "terminal_record_hash",
            _hash(
                self.terminal_record_hash,
                code="RESEARCH_AGENT_AUDIT_RECEIPT_HASH_INVALID",
            ),
        )
        if isinstance(self.trace_count, bool) or not isinstance(self.trace_count, int):
            _refuse("RESEARCH_AGENT_AUDIT_RECEIPT_TRACE_COUNT_INVALID")
        if self.trace_count < 1:
            _refuse("RESEARCH_AGENT_AUDIT_RECEIPT_TRACE_COUNT_INVALID")
        if self.lifecycle != "RESEARCH_ONLY" or self.eligible_for_trading is not False:
            _refuse("RESEARCH_AGENT_AUDIT_RECEIPT_LIFECYCLE_INVALID")


@dataclass(frozen=True, slots=True)
class DurableResearchAgentResult:
    """An in-memory research result paired with its durable hash-only receipt."""

    result: ResearchAgentResult
    receipt: DurableResearchAgentAuditReceipt

    def __post_init__(self) -> None:
        if type(self.result) is not ResearchAgentResult:
            _refuse("RESEARCH_AGENT_AUDIT_RESULT_TYPE_INVALID")
        if type(self.receipt) is not DurableResearchAgentAuditReceipt:
            _refuse("RESEARCH_AGENT_AUDIT_RECEIPT_TYPE_INVALID")
        if (
            self.result.run_id != self.receipt.run_id
            or self.result.lifecycle != "RESEARCH_ONLY"
            or self.result.eligible_for_trading is not False
        ):
            _refuse("RESEARCH_AGENT_AUDIT_RESULT_RECEIPT_MISMATCH")


class DurableResearchAgentRunner:
    """Append durable evidence around exactly one existing ``ResearchAgent`` run.

    It has no Tool API, broker, risk, scheduler, CLI, or live-trading surface.
    The database session factory belongs solely to this independent composition
    boundary and is not passed into the constrained agent.
    """

    __slots__ = ("_agent", "_session_factory")

    def __init__(
        self,
        agent: ResearchAgent,
        session_factory: sessionmaker[Session],
    ) -> None:
        if type(agent) is not ResearchAgent:
            _refuse("RESEARCH_AGENT_AUDIT_AGENT_INVALID")
        if type(session_factory) is not sessionmaker:
            _refuse("RESEARCH_AGENT_AUDIT_SESSION_FACTORY_INVALID")
        self._agent = agent
        self._session_factory = session_factory

    def run(self, request: ResearchAgentRequest) -> DurableResearchAgentResult:
        """Reserve, execute once, and append a terminal hash-only audit outcome.

        ``ADMITTED`` is committed before any typed-tool call.  A duplicate or a
        crash after admission is intentionally not replayed: the caller must use
        a new explicit run identity after human investigation.
        """

        if type(request) is not ResearchAgentRequest:
            _refuse("RESEARCH_AGENT_AUDIT_REQUEST_INVALID")
        request_hash = research_agent_request_hash(request)
        admission_record_hash = self._admit(
            run_id=request.run_id,
            request_hash=request_hash,
            as_of=request.as_of,
        )

        try:
            result = self._agent.run(request)
        except Exception as exc:
            # A typed tool can complete a side effect and lose its response (or
            # fail after an unknown partial action).  The wrapper cannot prove
            # a terminal failure from a generic agent exception, so it must not
            # append a false FAILED fact.  Preserve the committed reservation as
            # unresolved and block automatic replay of this run identity.
            raise ResearchAgentEvidenceAuditError(
                "RESEARCH_AGENT_AUDIT_AGENT_OUTCOME_UNRESOLVED"
            ) from exc

        try:
            result_hash, trace_inputs, trace_tip_hash = self._validated_result_commitment(
                request=request,
                request_hash=request_hash,
                result=result,
            )
        except ResearchAgentEvidenceAuditError:
            self._record_failure(
                run_id=request.run_id,
                request_hash=request_hash,
                failure_code="RESEARCH_AGENT_RESULT_INVALID",
            )
            raise

        try:
            terminal_record_hash = self._complete(
                run_id=request.run_id,
                request_hash=request_hash,
                result_hash=result_hash,
                trace_entries=trace_inputs,
            )
        except Exception as exc:
            # Completion may have committed before a connection-level error was
            # observed.  Do not append a contradictory terminal event or replay.
            raise ResearchAgentEvidenceAuditError(
                "RESEARCH_AGENT_AUDIT_COMPLETION_UNRESOLVED"
            ) from exc

        return DurableResearchAgentResult(
            result=result,
            receipt=DurableResearchAgentAuditReceipt(
                run_id=request.run_id,
                request_hash=request_hash,
                result_hash=result_hash,
                trace_tip_hash=trace_tip_hash,
                trace_count=len(trace_inputs),
                admission_record_hash=admission_record_hash,
                terminal_record_hash=terminal_record_hash,
                lifecycle="RESEARCH_ONLY",
            ),
        )

    def _admit(
        self,
        *,
        run_id: str,
        request_hash: str,
        as_of: datetime,
    ) -> str:
        try:
            with self._session_factory() as session:
                record = admit_research_agent_run(
                    session,
                    run_id=run_id,
                    request_hash=request_hash,
                    as_of=as_of,
                    admitted_at=utc_now(),
                )
                # Repository writes commit their own isolated transaction.  Read
                # the scalar while this session remains open so a valid default
                # ``expire_on_commit=True`` sessionmaker cannot detach the row
                # before the receipt is constructed.
                record_hash = _hash(
                    record.record_hash,
                    code="RESEARCH_AGENT_AUDIT_ADMISSION_RECORD_INVALID",
                )
        except Exception as exc:
            raise ResearchAgentEvidenceAuditError("RESEARCH_AGENT_AUDIT_ADMISSION_REFUSED") from exc
        return record_hash

    def _record_failure(
        self,
        *,
        run_id: str,
        request_hash: str,
        failure_code: Literal["RESEARCH_AGENT_RESULT_INVALID"],
    ) -> None:
        try:
            with self._session_factory() as session:
                fail_research_agent_run(
                    session,
                    run_id=run_id,
                    request_hash=request_hash,
                    failure_code=failure_code,
                    failed_at=utc_now(),
                )
        except Exception as exc:
            # A terminal outcome cannot be proven, so preserve the reservation
            # as unresolved and refuse this run rather than retrying it.
            raise ResearchAgentEvidenceAuditError(
                "RESEARCH_AGENT_AUDIT_FAILURE_UNRESOLVED"
            ) from exc

    def _complete(
        self,
        *,
        run_id: str,
        request_hash: str,
        result_hash: str,
        trace_entries: tuple[ResearchAgentRunTraceInput, ...],
    ) -> str:
        with self._session_factory() as session:
            record = complete_research_agent_run(
                session,
                run_id=run_id,
                request_hash=request_hash,
                result_hash=result_hash,
                trace_entries=trace_entries,
                completed_at=utc_now(),
            )
            # See ``_admit``: this must happen before the default session closes.
            return _hash(
                record.record_hash,
                code="RESEARCH_AGENT_AUDIT_TERMINAL_RECORD_INVALID",
            )

    @staticmethod
    def _validated_result_commitment(
        *,
        request: ResearchAgentRequest,
        request_hash: str,
        result: ResearchAgentResult,
    ) -> tuple[str, tuple[ResearchAgentRunTraceInput, ...], str]:
        if type(result) is not ResearchAgentResult:
            _refuse("RESEARCH_AGENT_AUDIT_RESULT_TYPE_INVALID")
        if (
            result.run_id != request.run_id
            or result.as_of != request.as_of
            or result.hypothesis != request.hypothesis
            or result.feature_proposals != request.feature_proposals
        ):
            _refuse("RESEARCH_AGENT_AUDIT_RESULT_REQUEST_MISMATCH")
        if result.lifecycle != "RESEARCH_ONLY" or result.eligible_for_trading is not False:
            _refuse("RESEARCH_AGENT_AUDIT_RESULT_LIFECYCLE_INVALID")
        if (
            result.matched_event.event_id != request.hypothesis.event_id
            or result.matched_event.event_hash != request.hypothesis.event_hash
            or result.matched_dataset.dataset_version_hash
            != request.experiment_request.dataset_version_hash
            or tuple(feature.reference for feature in result.features)
            != tuple(item.feature for item in request.feature_proposals)
            or result.experiment.experiment_id != request.experiment_request.experiment_id
            or result.backtest.experiment_id != request.experiment_request.experiment_id
            or result.backtest.backtest_request_id != request.backtest_request.backtest_request_id
            or result.validation.experiment_id != request.validation_request.experiment_id
            or result.validation.backtest_run_id != request.validation_request.backtest_run_id
            or result.validation.validation_request_id != request.validation_request.validation_request_id
            or result.research_card.research_card_request_id
            != request.research_card_request.research_card_request_id
            or result.research_card.research_decision_id
            != request.research_card_request.research_decision_id
        ):
            _refuse("RESEARCH_AGENT_AUDIT_RESULT_BINDING_INVALID")

        trace_entries = tuple(
            ResearchAgentRunTraceInput(
                sequence=entry.sequence,
                tool_name=entry.tool_name.value,
                request_hash=entry.request_hash,
                response_hash=entry.response_hash,
                predecessor_trace_hash=entry.predecessor_trace_hash,
                trace_hash=entry.trace_hash,
            )
            for entry in result.trace
            if type(entry) is ResearchAgentTraceEntry
        )
        if len(trace_entries) != len(result.trace) or not trace_entries:
            _refuse("RESEARCH_AGENT_AUDIT_TRACE_INVALID")
        trace_tip_hash = _hash(
            trace_entries[-1].trace_hash,
            code="RESEARCH_AGENT_AUDIT_TRACE_INVALID",
        )

        return (
            _fingerprint(
                DurableResearchAgentRunner._result_hash_projection(
                    request_hash=request_hash,
                    result=result,
                    trace_tip_hash=trace_tip_hash,
                )
            ),
            trace_entries,
            trace_tip_hash,
        )

    @staticmethod
    def _result_hash_projection(
        *,
        request_hash: str,
        result: ResearchAgentResult,
        trace_tip_hash: str,
    ) -> dict[str, object]:
        """Return a secret-free commitment projection; it is never persisted as JSON."""

        return {
            "format": "northstar.durable-research-agent-result.v1",
            "request_hash": request_hash,
            "run_id": result.run_id,
            "as_of": result.as_of.isoformat(),
            "lifecycle": result.lifecycle,
            "eligible_for_trading": result.eligible_for_trading,
            "hypothesis": {
                "hypothesis_id": result.hypothesis.hypothesis_id,
                "event_id": result.hypothesis.event_id,
                "event_hash": result.hypothesis.event_hash,
                "evidence_hashes": list(result.hypothesis.evidence_hashes),
                "statement_hash": _text_hash(result.hypothesis.statement),
            },
            "feature_proposals": [
                {
                    "proposal_id": proposal.proposal_id,
                    "hypothesis_id": proposal.hypothesis_id,
                    "feature_id": proposal.feature.feature_id,
                    "feature_version_hash": proposal.feature.feature_version_hash,
                    "rationale_hash": _text_hash(proposal.rationale),
                }
                for proposal in result.feature_proposals
            ],
            "matched_event": {
                "event_id": result.matched_event.event_id,
                "event_hash": result.matched_event.event_hash,
                "available_at": result.matched_event.available_at.isoformat(),
            },
            "matched_dataset": {
                "dataset_id": result.matched_dataset.dataset_id,
                "dataset_version_hash": result.matched_dataset.dataset_version_hash,
                "schema_hash": result.matched_dataset.schema_hash,
                "lineage_hash": result.matched_dataset.lineage_hash,
                "available_at": result.matched_dataset.available_at.isoformat(),
            },
            "features": [
                {
                    "feature_id": feature.reference.feature_id,
                    "feature_version_hash": feature.reference.feature_version_hash,
                    "lineage_hash": feature.lineage_hash,
                    "dataset_version_hashes": list(feature.dataset_version_hashes),
                    "selection_mode": feature.selection_mode.value,
                    "decision_time_safe": feature.decision_time_safe,
                    "available_at": feature.available_at.isoformat(),
                }
                for feature in result.features
            ],
            "experiment": {
                "experiment_id": result.experiment.experiment_id,
                "experiment_spec_hash": result.experiment.experiment_spec_hash,
                "dataset_version_hash": result.experiment.dataset_version_hash,
                "feature_version_hashes": list(result.experiment.feature_version_hashes),
                "available_at": result.experiment.available_at.isoformat(),
            },
            "backtest": {
                "experiment_id": result.backtest.experiment_id,
                "backtest_request_id": result.backtest.backtest_request_id,
                "backtest_run_id": result.backtest.backtest_run_id,
                "run_manifest_hash": result.backtest.run_manifest_hash,
                "evidence_hash": result.backtest.evidence_hash,
                "available_at": result.backtest.available_at.isoformat(),
            },
            "validation": {
                "experiment_id": result.validation.experiment_id,
                "backtest_run_id": result.validation.backtest_run_id,
                "validation_request_id": result.validation.validation_request_id,
                "validation_report_id": result.validation.validation_report_id,
                "validation_report_hash": result.validation.validation_report_hash,
                "evidence_hash": result.validation.evidence_hash,
                "available_at": result.validation.available_at.isoformat(),
            },
            "research_card": {
                "research_card_request_id": result.research_card.research_card_request_id,
                "research_card_id": result.research_card.research_card_id,
                "research_card_hash": result.research_card.research_card_hash,
                "research_decision_id": result.research_card.research_decision_id,
                "available_at": result.research_card.available_at.isoformat(),
            },
            "trace_hashes": [entry.trace_hash for entry in result.trace],
            "trace_tip_hash": trace_tip_hash,
        }

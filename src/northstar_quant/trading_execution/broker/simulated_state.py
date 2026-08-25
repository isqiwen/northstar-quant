"""PostgreSQL-backed, auditable state repository for simulated brokers.

Paper and CTP-sim deliberately keep their matching semantics inside their own
adapters.  What this repository owns is the durable boundary: one PostgreSQL
transaction and account-scoped advisory lock for each state transition, a
current snapshot, and an immutable hash-chained transition ledger.  It is not
a Local-tools cache and never falls back to files or SQLite.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import re
from typing import Any, TypeAlias, cast

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.session import SessionTransaction

from northstar_quant.foundation.common.time import ensure_utc, utc_now
from northstar_quant.foundation.db.models import (
    SimulatedBrokerStateRecord,
    SimulatedBrokerStateTransitionRecord,
)


SessionFactory: TypeAlias = Callable[[], Session]
StateFactory: TypeAlias = Callable[[], dict[str, Any]]
StateValidator: TypeAlias = Callable[[dict[str, Any]], None]

_ACTION_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_STATE_FORMAT = "northstar.simulated-broker-state.v1"
_TRANSITION_FORMAT = "northstar.simulated-broker-state-transition.v1"


def default_simulated_broker_session() -> Session:
    """Resolve the core PostgreSQL session lazily at a production call site.

    This keeps importing adapter types independent from a user's active `.env`;
    actual broker-state access still resolves and validates the core runtime
    PostgreSQL configuration, with no SQLite or file fallback.
    """

    from northstar_quant.foundation.db.session import SessionLocal

    return SessionLocal()


class SimulatedBrokerStateIntegrityError(RuntimeError):
    """PostgreSQL simulator state is absent, malformed, or tampered with."""


@dataclass(frozen=True, slots=True)
class SimulatedBrokerStateEvidence:
    """Verified, non-payload metadata for diagnostics and safe test assertions."""

    revision: int
    state_hash: str
    last_transition_hash: str


@dataclass(slots=True)
class LockedSimulatedBrokerState:
    """The only mutable state object exposed during one locked transition."""

    state: dict[str, Any]
    _action: str | None = None

    @property
    def changed(self) -> bool:
        return self._action is not None

    @property
    def action(self) -> str:
        if self._action is None:
            raise RuntimeError("SIMULATED_BROKER_STATE_ACTION_REQUIRED")
        return self._action

    def persist(self, *, action: str) -> None:
        """Declare this transaction's one explicit state transition."""

        normalized = str(action).strip().lower()
        if _ACTION_PATTERN.fullmatch(normalized) is None:
            raise ValueError("SIMULATED_BROKER_STATE_ACTION_INVALID")
        if self._action is not None and self._action != normalized:
            raise RuntimeError("SIMULATED_BROKER_STATE_MULTIPLE_ACTIONS")
        self._action = normalized


class PostgresSimulatedBrokerStateRepository:
    """Account-scoped simulator state repository with a verified audit chain."""

    def __init__(
        self,
        *,
        broker: str,
        account: str,
        schema_version: int,
        state_factory: StateFactory,
        state_validator: StateValidator,
        session_factory: SessionFactory | None = None,
    ) -> None:
        normalized_broker = str(broker).strip().lower()
        normalized_account = str(account).strip()
        if normalized_broker not in {"paper", "ctp_sim"}:
            raise ValueError("SIMULATED_BROKER_STATE_BROKER_INVALID")
        if not normalized_account:
            raise ValueError("SIMULATED_BROKER_STATE_ACCOUNT_REQUIRED")
        if not isinstance(schema_version, int) or schema_version <= 0:
            raise ValueError("SIMULATED_BROKER_STATE_SCHEMA_VERSION_INVALID")
        if not callable(state_factory) or not callable(state_validator):
            raise TypeError("SIMULATED_BROKER_STATE_CONTRACT_REQUIRED")

        self.broker = normalized_broker
        self.account = normalized_account
        self.schema_version = schema_version
        self._state_factory = state_factory
        self._state_validator = state_validator
        self._session_factory = session_factory or default_simulated_broker_session

    @contextmanager
    def locked_state(
        self,
        *,
        session: Session | None = None,
    ) -> Iterator[LockedSimulatedBrokerState]:
        """Open one PostgreSQL transaction locked to this broker/account scope.

        The advisory lock serializes initialization as well as later row-level
        changes.  A failing validator, callback, or database operation rolls
        the entire transition back; the adapter must never continue with an
        uncommitted in-memory mutation.
        """

        owned_session = session is None
        active_session = self._session_factory() if session is None else session
        savepoint: SessionTransaction | None = None
        try:
            if not owned_session:
                # A broker callback can fail after it tentatively changes the
                # simulator. Isolate that change from the durable intent so a
                # caller can persist SubmissionUnknown without fabricating an
                # acknowledged simulator order.
                savepoint = active_session.begin_nested()
            self._acquire_scope_fence(active_session)
            record, state = self._load_or_initialize(active_session)
            locked = LockedSimulatedBrokerState(state=state)
            yield locked
            if locked.changed:
                self._save_transition(
                    active_session,
                    record=record,
                    state=locked.state,
                    action=locked.action,
                )
            if owned_session:
                active_session.commit()
            elif savepoint is not None:
                savepoint.commit()
        except Exception:
            if owned_session:
                active_session.rollback()
            elif savepoint is not None and savepoint.is_active:
                savepoint.rollback()
            raise
        finally:
            if owned_session:
                active_session.close()

    def read_state(self) -> dict[str, Any]:
        """Read a verified snapshot, initializing one explicitly if absent."""

        with self.locked_state() as locked:
            return _clone_state(locked.state)

    def read_state_in_session(self, session: Session) -> dict[str, Any]:
        """Read state under a caller-owned transaction without committing it."""

        with self.locked_state(session=session) as locked:
            return _clone_state(locked.state)

    def current_revision(self, *, session: Session | None = None) -> int:
        """Return the verified revision for diagnostics and test assertions."""

        return self.current_evidence(session=session).revision

    def current_evidence(
        self,
        *,
        session: Session | None = None,
    ) -> SimulatedBrokerStateEvidence:
        """Return the verified current audit tail without exposing its payload."""

        owned_session = session is None
        active_session = self._session_factory() if session is None else session
        try:
            self._acquire_scope_fence(active_session)
            record, _state = self._load_or_initialize(active_session)
            evidence = SimulatedBrokerStateEvidence(
                revision=int(record.revision),
                state_hash=str(record.state_hash),
                last_transition_hash=str(record.last_transition_hash),
            )
            if owned_session:
                active_session.commit()
            return evidence
        except Exception:
            if owned_session:
                active_session.rollback()
            raise
        finally:
            if owned_session:
                active_session.close()

    def _acquire_scope_fence(self, session: Session) -> None:
        digest = sha256(
            f"northstar:simulated-broker-state:{self.broker}:{self.account}".encode(
                "utf-8"
            )
        ).digest()
        advisory_key = int.from_bytes(digest[:8], byteorder="big", signed=True)
        session.execute(
            text("SELECT pg_advisory_xact_lock(:advisory_key)"),
            {"advisory_key": advisory_key},
        )

    def _load_or_initialize(
        self,
        session: Session,
    ) -> tuple[SimulatedBrokerStateRecord, dict[str, Any]]:
        record = session.execute(
            select(SimulatedBrokerStateRecord)
            .where(
                SimulatedBrokerStateRecord.broker == self.broker,
                SimulatedBrokerStateRecord.account == self.account,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if record is None:
            state = _clone_state(self._state_factory())
            self._validate_state(state)
            state_json = _encode_state(state)
            occurred_at = utc_now()
            state_hash = self._state_hash(state_json)
            transition_hash = self._transition_hash(
                action="initialize",
                revision=0,
                state_hash=state_hash,
                predecessor_transition_hash=None,
                occurred_at=occurred_at,
            )
            record = SimulatedBrokerStateRecord(
                broker=self.broker,
                account=self.account,
                schema_version=self.schema_version,
                revision=0,
                state_json=state_json,
                state_hash=state_hash,
                last_transition_hash=transition_hash,
                initialized_at=occurred_at,
                updated_at=occurred_at,
            )
            session.add(record)
            session.add(
                SimulatedBrokerStateTransitionRecord(
                    broker=self.broker,
                    account=self.account,
                    schema_version=self.schema_version,
                    revision=0,
                    action="initialize",
                    state_json=state_json,
                    state_hash=state_hash,
                    predecessor_transition_hash=None,
                    transition_hash=transition_hash,
                    occurred_at=occurred_at,
                )
            )
            session.flush()
            return record, state

        if record.schema_version != self.schema_version:
            raise SimulatedBrokerStateIntegrityError(
                "SIMULATED_BROKER_STATE_SCHEMA_VERSION_UNSUPPORTED"
            )
        state = _decode_state(record.state_json)
        self._validate_state(state)
        expected_state_hash = self._state_hash(record.state_json)
        if record.state_hash != expected_state_hash:
            raise SimulatedBrokerStateIntegrityError("SIMULATED_BROKER_STATE_HASH_MISMATCH")
        self._verify_transition_chain(session, record=record)
        return record, state

    def _save_transition(
        self,
        session: Session,
        *,
        record: SimulatedBrokerStateRecord,
        state: dict[str, Any],
        action: str,
    ) -> None:
        self._validate_state(state)
        state_json = _encode_state(state)
        state_hash = self._state_hash(state_json)
        # Lifecycle polling is allowed to request persistence even when no
        # order, fill, quote, position, cash or trading-day fact changed. Do
        # not manufacture a transition/revision for that no-op: a rejected
        # candidate path must leave exactly the same simulator evidence.
        if state_hash == record.state_hash:
            return
        occurred_at = utc_now()
        revision = int(record.revision) + 1
        predecessor = record.last_transition_hash
        transition_hash = self._transition_hash(
            action=action,
            revision=revision,
            state_hash=state_hash,
            predecessor_transition_hash=predecessor,
            occurred_at=occurred_at,
        )
        record.revision = revision
        record.state_json = state_json
        record.state_hash = state_hash
        record.last_transition_hash = transition_hash
        record.updated_at = occurred_at
        session.add(
            SimulatedBrokerStateTransitionRecord(
                broker=self.broker,
                account=self.account,
                schema_version=self.schema_version,
                revision=revision,
                action=action,
                state_json=state_json,
                state_hash=state_hash,
                predecessor_transition_hash=predecessor,
                transition_hash=transition_hash,
                occurred_at=occurred_at,
            )
        )

    def _verify_transition_chain(
        self,
        session: Session,
        *,
        record: SimulatedBrokerStateRecord,
    ) -> None:
        rows = list(
            session.execute(
                select(SimulatedBrokerStateTransitionRecord)
                .where(
                    SimulatedBrokerStateTransitionRecord.broker == self.broker,
                    SimulatedBrokerStateTransitionRecord.account == self.account,
                )
                .order_by(SimulatedBrokerStateTransitionRecord.revision.asc())
            ).scalars()
        )
        if not rows:
            raise SimulatedBrokerStateIntegrityError("SIMULATED_BROKER_STATE_AUDIT_MISSING")

        predecessor: str | None = None
        for expected_revision, row in enumerate(rows):
            if row.schema_version != self.schema_version or row.revision != expected_revision:
                raise SimulatedBrokerStateIntegrityError(
                    "SIMULATED_BROKER_STATE_AUDIT_REVISION_INVALID"
                )
            if (expected_revision == 0 and row.action != "initialize") or (
                expected_revision > 0 and row.action == "initialize"
            ):
                raise SimulatedBrokerStateIntegrityError(
                    "SIMULATED_BROKER_STATE_AUDIT_ACTION_INVALID"
                )
            if row.predecessor_transition_hash != predecessor:
                raise SimulatedBrokerStateIntegrityError(
                    "SIMULATED_BROKER_STATE_AUDIT_PREDECESSOR_INVALID"
                )
            row_state = _decode_state(row.state_json)
            self._validate_state(row_state)
            if row.state_hash != self._state_hash(row.state_json):
                raise SimulatedBrokerStateIntegrityError(
                    "SIMULATED_BROKER_STATE_AUDIT_STATE_HASH_MISMATCH"
                )
            expected_transition_hash = self._transition_hash(
                action=row.action,
                revision=row.revision,
                state_hash=row.state_hash,
                predecessor_transition_hash=predecessor,
                occurred_at=ensure_utc(row.occurred_at),
            )
            if row.transition_hash != expected_transition_hash:
                raise SimulatedBrokerStateIntegrityError(
                    "SIMULATED_BROKER_STATE_AUDIT_HASH_MISMATCH"
                )
            predecessor = row.transition_hash

        final = rows[-1]
        if (
            record.revision != final.revision
            or record.state_hash != final.state_hash
            or record.last_transition_hash != final.transition_hash
            or record.state_json != final.state_json
        ):
            raise SimulatedBrokerStateIntegrityError("SIMULATED_BROKER_STATE_AUDIT_TAIL_MISMATCH")

    def _validate_state(self, state: dict[str, Any]) -> None:
        try:
            self._state_validator(state)
        except SimulatedBrokerStateIntegrityError:
            raise
        except (TypeError, ValueError) as exc:
            raise SimulatedBrokerStateIntegrityError(
                "SIMULATED_BROKER_STATE_PAYLOAD_INVALID"
            ) from exc

    def _state_hash(self, state_json: str) -> str:
        return _sha256(
            {
                "format": _STATE_FORMAT,
                "broker": self.broker,
                "account": self.account,
                "schema_version": self.schema_version,
                "state": json.loads(state_json),
            }
        )

    def _transition_hash(
        self,
        *,
        action: str,
        revision: int,
        state_hash: str,
        predecessor_transition_hash: str | None,
        occurred_at: datetime,
    ) -> str:
        return _sha256(
            {
                "format": _TRANSITION_FORMAT,
                "broker": self.broker,
                "account": self.account,
                "schema_version": self.schema_version,
                "revision": revision,
                "action": action,
                "state_hash": state_hash,
                "predecessor_transition_hash": predecessor_transition_hash,
                "occurred_at": ensure_utc(occurred_at).isoformat(),
            }
        )


def _clone_state(state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise SimulatedBrokerStateIntegrityError("SIMULATED_BROKER_STATE_OBJECT_REQUIRED")
    return _decode_state(_encode_state(state))


def _decode_state(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SimulatedBrokerStateIntegrityError("SIMULATED_BROKER_STATE_JSON_INVALID") from exc
    if not isinstance(decoded, dict):
        raise SimulatedBrokerStateIntegrityError("SIMULATED_BROKER_STATE_OBJECT_REQUIRED")
    return cast(dict[str, Any], decoded)


def _encode_state(state: dict[str, Any]) -> str:
    try:
        return json.dumps(
            state,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise SimulatedBrokerStateIntegrityError("SIMULATED_BROKER_STATE_JSON_INVALID") from exc


def _sha256(payload: object) -> str:
    return sha256(_encode_state(cast(dict[str, Any], payload)).encode("utf-8")).hexdigest()


__all__ = [
    "LockedSimulatedBrokerState",
    "PostgresSimulatedBrokerStateRepository",
    "SessionFactory",
    "SimulatedBrokerStateEvidence",
    "SimulatedBrokerStateIntegrityError",
    "default_simulated_broker_session",
]

#!/usr/bin/env python3
"""Durable, fail-closed release transaction journal.

The release runner records intent and completed milestones in this module before
performing the corresponding privileged operation.  Every journal event is an
immutable, canonical JSON file published with a no-replace hard-link operation;
there is intentionally no mutable ``state.json`` to repair after a crash.

This module deliberately does *not* attempt a database downgrade, a service
restart, or a rollback.  It only makes the durable state legible so the runner
can stop safely and require an explicit, audited recovery decision.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Final


class ReleaseTransactionError(RuntimeError):
    """Base error for a release-transaction journal failure."""


class ReleaseTransactionPrivilegeError(ReleaseTransactionError):
    """The production journal was not opened by Linux root."""


class ReleaseTransactionIntegrityError(ReleaseTransactionError):
    """A journal path, event, or event chain is not trustworthy."""


class ReleaseTransactionExistsError(ReleaseTransactionError):
    """A release identifier already has a durable journal directory."""


class ReleaseTransactionTransitionError(ReleaseTransactionError):
    """A requested transaction state transition is not allowed."""


class ReleaseTransactionRecoveryBlockedError(ReleaseTransactionError):
    """An unresolved durable transaction blocks a new release."""


class ReleaseTransactionState(str, Enum):
    """Strict release milestones, including intent states before mutations."""

    RECEIVED = "received"
    VERIFIED = "verified"
    STAGING_STARTED = "staging_started"
    STAGED = "staged"
    MIGRATION_STARTED = "migration_started"
    MIGRATED = "migrated"
    CANDIDATE_HEALTHY = "candidate_healthy"
    CUTOVER_STARTED = "cutover_started"
    CURRENT_SWITCHED = "current_switched"
    POST_START_HEALTHY = "post_start_healthy"
    PROMOTED = "promoted"
    FAILED = "failed"
    RECOVERY_REQUIRED = "recovery_required"


class ReleaseRecoveryCategory(str, Enum):
    """How a runner must treat a durable transaction after a restart."""

    NONE = "none"
    KNOWN_PRE_MIGRATION_FAILURE = "known_pre_migration_failure"
    UNRESOLVED_PRE_MIGRATION = "unresolved_pre_migration"
    DATABASE_RECOVERY_REQUIRED = "database_recovery_required"
    CUTOVER_RECOVERY_REQUIRED = "cutover_recovery_required"
    UNREADABLE_JOURNAL = "unreadable_journal"


_FORMAT: Final = "northstar.release-transaction.v1"
_RELEASE_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_EVENT_FIELDS: Final = frozenset(
    {
        "artifact_sha256",
        "format",
        "occurred_at",
        "previous_event_sha256",
        "previous_release_id",
        "release_id",
        "request_sha256",
        "sequence",
        "state",
    }
)
_EVENT_DIRECTORY_NAME: Final = "events"
_EVENT_FILE_PATTERN: Final = re.compile(r"^[0-9]{8}\.json$")
_NOFOLLOW: Final = getattr(os, "O_NOFOLLOW", 0)

_ALLOWED_TRANSITIONS: Final = {
    ReleaseTransactionState.RECEIVED: frozenset(
        {
            ReleaseTransactionState.VERIFIED,
            ReleaseTransactionState.FAILED,
            ReleaseTransactionState.RECOVERY_REQUIRED,
        }
    ),
    ReleaseTransactionState.VERIFIED: frozenset(
        {
            ReleaseTransactionState.STAGING_STARTED,
            ReleaseTransactionState.FAILED,
            ReleaseTransactionState.RECOVERY_REQUIRED,
        }
    ),
    ReleaseTransactionState.STAGING_STARTED: frozenset(
        {
            ReleaseTransactionState.STAGED,
            ReleaseTransactionState.FAILED,
            ReleaseTransactionState.RECOVERY_REQUIRED,
        }
    ),
    ReleaseTransactionState.STAGED: frozenset(
        {
            ReleaseTransactionState.MIGRATION_STARTED,
            ReleaseTransactionState.FAILED,
            ReleaseTransactionState.RECOVERY_REQUIRED,
        }
    ),
    ReleaseTransactionState.MIGRATION_STARTED: frozenset(
        {
            ReleaseTransactionState.MIGRATED,
            ReleaseTransactionState.RECOVERY_REQUIRED,
        }
    ),
    ReleaseTransactionState.MIGRATED: frozenset(
        {
            ReleaseTransactionState.CANDIDATE_HEALTHY,
            ReleaseTransactionState.RECOVERY_REQUIRED,
        }
    ),
    ReleaseTransactionState.CANDIDATE_HEALTHY: frozenset(
        {
            ReleaseTransactionState.CUTOVER_STARTED,
            ReleaseTransactionState.RECOVERY_REQUIRED,
        }
    ),
    ReleaseTransactionState.CUTOVER_STARTED: frozenset(
        {
            ReleaseTransactionState.CURRENT_SWITCHED,
            ReleaseTransactionState.RECOVERY_REQUIRED,
        }
    ),
    ReleaseTransactionState.CURRENT_SWITCHED: frozenset(
        {
            ReleaseTransactionState.POST_START_HEALTHY,
            ReleaseTransactionState.RECOVERY_REQUIRED,
        }
    ),
    ReleaseTransactionState.POST_START_HEALTHY: frozenset(
        {
            ReleaseTransactionState.PROMOTED,
            ReleaseTransactionState.RECOVERY_REQUIRED,
        }
    ),
    ReleaseTransactionState.PROMOTED: frozenset(),
    ReleaseTransactionState.FAILED: frozenset(),
    ReleaseTransactionState.RECOVERY_REQUIRED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class ReleaseTransactionEvent:
    """One immutable, chained transaction journal event."""

    release_id: str
    sequence: int
    state: ReleaseTransactionState
    occurred_at: datetime
    request_sha256: str
    artifact_sha256: str
    previous_release_id: str | None
    previous_event_sha256: str | None

    @property
    def sha256(self) -> str:
        """Digest of the exact canonical event bytes persisted on disk."""

        return hashlib.sha256(_canonical_event_bytes(self)).hexdigest()


@dataclass(frozen=True, slots=True)
class ReleaseTransaction:
    """Validated, immutable event chain for a single release identifier."""

    release_id: str
    events: tuple[ReleaseTransactionEvent, ...]

    @property
    def state(self) -> ReleaseTransactionState:
        return self.events[-1].state

    @property
    def current_event(self) -> ReleaseTransactionEvent:
        return self.events[-1]


@dataclass(frozen=True, slots=True)
class ReleaseRecoveryDecision:
    """A fail-closed recovery classification; it never performs recovery."""

    release_id: str
    category: ReleaseRecoveryCategory
    last_state: ReleaseTransactionState | None
    requires_operator_action: bool
    automatic_database_rollback_allowed: bool = False
    automatic_service_resume_allowed: bool = False


def _canonical_event_bytes(event: ReleaseTransactionEvent) -> bytes:
    payload: dict[str, object] = {
        "artifact_sha256": event.artifact_sha256,
        "format": _FORMAT,
        "occurred_at": _format_timestamp(event.occurred_at),
        "previous_event_sha256": event.previous_event_sha256,
        "previous_release_id": event.previous_release_id,
        "release_id": event.release_id,
        "request_sha256": event.request_sha256,
        "sequence": event.sequence,
        "state": event.state.value,
    }
    return (
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReleaseTransactionIntegrityError("transaction timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ReleaseTransactionIntegrityError("transaction timestamp is not canonical UTC")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ReleaseTransactionIntegrityError("transaction timestamp is invalid") from exc
    if _format_timestamp(parsed) != value:
        raise ReleaseTransactionIntegrityError("transaction timestamp is not canonical")
    return parsed


def _require_release_id(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _RELEASE_ID_PATTERN.fullmatch(value):
        raise ReleaseTransactionIntegrityError(f"{field} is not a safe release identifier")
    return value


def _require_sha256(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise ReleaseTransactionIntegrityError(f"{field} is not a lowercase SHA-256 digest")
    return value


def _coerce_state(value: ReleaseTransactionState | str) -> ReleaseTransactionState:
    if isinstance(value, ReleaseTransactionState):
        return value
    try:
        return ReleaseTransactionState(value)
    except ValueError as exc:
        raise ReleaseTransactionTransitionError("transaction state is unknown") from exc


class ReleaseTransactionStore:
    """Root-only, append-only journal store used by a fixed release runner.

    ``unsafe_allow_non_root_for_tests`` exists only so cross-platform unit tests
    can exercise persistence semantics.  Production callers must use the
    default value, which requires Linux root and a root:root ``0700`` store.
    """

    def __init__(
        self,
        root: Path,
        *,
        unsafe_allow_non_root_for_tests: bool = False,
    ) -> None:
        self._root = Path(root)
        self._unsafe_allow_non_root_for_tests = unsafe_allow_non_root_for_tests

    @property
    def root(self) -> Path:
        return self._root

    def initialize(self) -> None:
        """Open or create the exact root-only transaction directory."""

        self._require_production_privilege_boundary()
        if os.path.lexists(self._root):
            self._assert_directory(self._root, mode=0o700, label="transaction journal root")
            return

        parent = self._root.parent
        if not os.path.lexists(parent):
            raise ReleaseTransactionIntegrityError(
                "transaction journal parent must already exist"
            )
        self._assert_root_controlled_parent_chain(parent)
        try:
            os.mkdir(self._root, mode=0o700)
        except FileExistsError as exc:
            raise ReleaseTransactionIntegrityError(
                "transaction journal root appeared during creation"
            ) from exc
        except OSError as exc:
            raise ReleaseTransactionIntegrityError(
                "unable to create transaction journal root"
            ) from exc
        try:
            os.chmod(self._root, 0o700)
            self._assert_directory(self._root, mode=0o700, label="transaction journal root")
            _fsync_directory(parent)
        except OSError as exc:
            raise ReleaseTransactionIntegrityError(
                "unable to make transaction journal root durable"
            ) from exc

    def begin(
        self,
        *,
        release_id: str,
        request_sha256: str,
        artifact_sha256: str,
        previous_release_id: str | None,
        occurred_at: datetime | None = None,
    ) -> ReleaseTransaction:
        """Create a no-replace transaction and its immutable RECEIVED event."""

        safe_release_id = _require_release_id(release_id, field="release_id")
        safe_request_sha256 = _require_sha256(request_sha256, field="request_sha256")
        safe_artifact_sha256 = _require_sha256(artifact_sha256, field="artifact_sha256")
        safe_previous_release_id = (
            None
            if previous_release_id is None
            else _require_release_id(previous_release_id, field="previous_release_id")
        )
        if safe_previous_release_id == safe_release_id:
            raise ReleaseTransactionIntegrityError(
                "previous_release_id must not equal release_id"
            )
        self.initialize()
        transaction_dir = self._transaction_dir(safe_release_id)
        if os.path.lexists(transaction_dir):
            raise ReleaseTransactionExistsError(
                f"release transaction already exists: {safe_release_id}"
            )
        self._assert_no_recovery_blockers()
        try:
            os.mkdir(transaction_dir, mode=0o700)
        except FileExistsError as exc:
            raise ReleaseTransactionExistsError(
                f"release transaction already exists: {safe_release_id}"
            ) from exc
        except OSError as exc:
            raise ReleaseTransactionIntegrityError(
                "unable to create release transaction directory"
            ) from exc
        self._assert_directory(transaction_dir, mode=0o700, label="transaction directory")
        events_dir = transaction_dir / _EVENT_DIRECTORY_NAME
        try:
            os.mkdir(events_dir, mode=0o700)
            os.chmod(events_dir, 0o700)
            self._assert_directory(events_dir, mode=0o700, label="transaction event directory")
            _fsync_directory(transaction_dir)
        except OSError as exc:
            raise ReleaseTransactionIntegrityError(
                "unable to create transaction event directory"
            ) from exc

        event = ReleaseTransactionEvent(
            release_id=safe_release_id,
            sequence=1,
            state=ReleaseTransactionState.RECEIVED,
            occurred_at=occurred_at or datetime.now(UTC),
            request_sha256=safe_request_sha256,
            artifact_sha256=safe_artifact_sha256,
            previous_release_id=safe_previous_release_id,
            previous_event_sha256=None,
        )
        self._append_event(events_dir, event)
        return ReleaseTransaction(release_id=safe_release_id, events=(event,))

    def load(self, release_id: str) -> ReleaseTransaction:
        """Load and validate every event before returning the current state."""

        self.initialize()
        safe_release_id = _require_release_id(release_id, field="release_id")
        transaction_dir = self._transaction_dir(safe_release_id)
        self._assert_directory(transaction_dir, mode=0o700, label="transaction directory")
        events_dir = transaction_dir / _EVENT_DIRECTORY_NAME
        self._assert_directory(events_dir, mode=0o700, label="transaction event directory")

        entries = sorted(events_dir.iterdir(), key=lambda entry: entry.name)
        if not entries:
            raise ReleaseTransactionIntegrityError("transaction journal has no events")
        events: list[ReleaseTransactionEvent] = []
        for expected_sequence, entry in enumerate(entries, start=1):
            expected_name = f"{expected_sequence:08d}.json"
            if entry.name != expected_name or not _EVENT_FILE_PATTERN.fullmatch(entry.name):
                raise ReleaseTransactionIntegrityError(
                    "transaction journal contains an unexpected event entry"
                )
            event = self._read_event(entry)
            if event.release_id != safe_release_id:
                raise ReleaseTransactionIntegrityError("event release identifier does not match directory")
            if event.sequence != expected_sequence:
                raise ReleaseTransactionIntegrityError("event sequence does not match event filename")
            if expected_sequence == 1:
                if event.state is not ReleaseTransactionState.RECEIVED:
                    raise ReleaseTransactionIntegrityError("first event must be received")
                if event.previous_event_sha256 is not None:
                    raise ReleaseTransactionIntegrityError("first event has a previous digest")
            else:
                previous = events[-1]
                if event.previous_event_sha256 != previous.sha256:
                    raise ReleaseTransactionIntegrityError("event hash chain is broken")
                if event.request_sha256 != previous.request_sha256:
                    raise ReleaseTransactionIntegrityError("request digest changed within transaction")
                if event.artifact_sha256 != previous.artifact_sha256:
                    raise ReleaseTransactionIntegrityError("artifact digest changed within transaction")
                if event.previous_release_id != previous.previous_release_id:
                    raise ReleaseTransactionIntegrityError(
                        "previous release changed within transaction"
                    )
                if event.state not in _ALLOWED_TRANSITIONS[previous.state]:
                    raise ReleaseTransactionIntegrityError("event transition is not allowed")
            events.append(event)
        return ReleaseTransaction(release_id=safe_release_id, events=tuple(events))

    def transition(
        self,
        release_id: str,
        state: ReleaseTransactionState | str,
        *,
        occurred_at: datetime | None = None,
    ) -> ReleaseTransaction:
        """Append exactly one allowed, immutable next state event."""

        transaction = self.load(release_id)
        next_state = _coerce_state(state)
        if next_state not in _ALLOWED_TRANSITIONS[transaction.state]:
            raise ReleaseTransactionTransitionError(
                f"cannot transition {transaction.state.value} to {next_state.value}"
            )
        previous = transaction.current_event
        event = ReleaseTransactionEvent(
            release_id=transaction.release_id,
            sequence=previous.sequence + 1,
            state=next_state,
            occurred_at=occurred_at or datetime.now(UTC),
            request_sha256=previous.request_sha256,
            artifact_sha256=previous.artifact_sha256,
            previous_release_id=previous.previous_release_id,
            previous_event_sha256=previous.sha256,
        )
        self._append_event(self._transaction_dir(transaction.release_id) / _EVENT_DIRECTORY_NAME, event)
        return ReleaseTransaction(release_id=transaction.release_id, events=(*transaction.events, event))

    def recovery_decision(self, release_id: str) -> ReleaseRecoveryDecision:
        """Classify one journal without mutating it or resuming any service."""

        safe_release_id = _require_release_id(release_id, field="release_id")
        try:
            transaction = self.load(safe_release_id)
        except ReleaseTransactionIntegrityError:
            return ReleaseRecoveryDecision(
                release_id=safe_release_id,
                category=ReleaseRecoveryCategory.UNREADABLE_JOURNAL,
                last_state=None,
                requires_operator_action=True,
            )

        state = transaction.state
        if state is ReleaseTransactionState.PROMOTED:
            return ReleaseRecoveryDecision(
                release_id=safe_release_id,
                category=ReleaseRecoveryCategory.NONE,
                last_state=state,
                requires_operator_action=False,
            )
        if state is ReleaseTransactionState.FAILED:
            return ReleaseRecoveryDecision(
                release_id=safe_release_id,
                category=ReleaseRecoveryCategory.KNOWN_PRE_MIGRATION_FAILURE,
                last_state=state,
                requires_operator_action=False,
            )

        effective_state: ReleaseTransactionState = state
        if state is ReleaseTransactionState.RECOVERY_REQUIRED:
            if len(transaction.events) < 2:
                return ReleaseRecoveryDecision(
                    release_id=safe_release_id,
                    category=ReleaseRecoveryCategory.UNREADABLE_JOURNAL,
                    last_state=state,
                    requires_operator_action=True,
                )
            effective_state = transaction.events[-2].state

        if effective_state in {
            ReleaseTransactionState.RECEIVED,
            ReleaseTransactionState.VERIFIED,
            ReleaseTransactionState.STAGING_STARTED,
            ReleaseTransactionState.STAGED,
        }:
            category = ReleaseRecoveryCategory.UNRESOLVED_PRE_MIGRATION
        elif effective_state in {
            ReleaseTransactionState.MIGRATION_STARTED,
            ReleaseTransactionState.MIGRATED,
            ReleaseTransactionState.CANDIDATE_HEALTHY,
        }:
            category = ReleaseRecoveryCategory.DATABASE_RECOVERY_REQUIRED
        elif effective_state in {
            ReleaseTransactionState.CUTOVER_STARTED,
            ReleaseTransactionState.CURRENT_SWITCHED,
            ReleaseTransactionState.POST_START_HEALTHY,
        }:
            category = ReleaseRecoveryCategory.CUTOVER_RECOVERY_REQUIRED
        else:
            category = ReleaseRecoveryCategory.UNREADABLE_JOURNAL
        return ReleaseRecoveryDecision(
            release_id=safe_release_id,
            category=category,
            last_state=state,
            requires_operator_action=True,
        )

    def recovery_decisions(self) -> tuple[ReleaseRecoveryDecision, ...]:
        """Return every journal classification, failing closed on unsafe entries."""

        self.initialize()
        decisions: list[ReleaseRecoveryDecision] = []
        for entry in sorted(self._root.iterdir(), key=lambda item: item.name):
            release_id = _require_release_id(entry.name, field="transaction directory name")
            self._assert_directory(entry, mode=0o700, label="transaction directory")
            decisions.append(self.recovery_decision(release_id))
        return tuple(decisions)

    def _assert_no_recovery_blockers(self) -> None:
        blockers = [
            decision.release_id
            for decision in self.recovery_decisions()
            if decision.requires_operator_action
        ]
        if blockers:
            raise ReleaseTransactionRecoveryBlockedError(
                "unresolved release transactions require operator recovery: "
                + ", ".join(blockers)
            )

    def _transaction_dir(self, release_id: str) -> Path:
        return self._root / release_id

    def _append_event(self, events_dir: Path, event: ReleaseTransactionEvent) -> None:
        self._assert_directory(events_dir, mode=0o700, label="transaction event directory")
        final_path = events_dir / f"{event.sequence:08d}.json"
        if os.path.lexists(final_path):
            raise ReleaseTransactionExistsError("transaction event already exists")
        temporary_path = events_dir / f".event-{event.sequence:08d}-{uuid.uuid4().hex}.tmp"
        payload = _canonical_event_bytes(event)
        descriptor = -1
        durable = False
        try:
            descriptor = os.open(
                temporary_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW,
                0o600,
            )
            os.fchmod(descriptor, 0o600)
            _write_all(descriptor, payload)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            # link(2) supplies the required atomic no-replace publication; an
            # existing final event is a concurrent writer or a prior journal.
            os.link(temporary_path, final_path, follow_symlinks=False)
            _fsync_directory(events_dir)
            durable = True
        except FileExistsError as exc:
            raise ReleaseTransactionExistsError("transaction event already exists") from exc
        except OSError as exc:
            raise ReleaseTransactionIntegrityError("unable to publish transaction event") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if durable and os.path.lexists(temporary_path):
                try:
                    os.unlink(temporary_path)
                    _fsync_directory(events_dir)
                except OSError:
                    # A failed cleanup leaves tangible evidence.  The next load
                    # rejects the unexpected entry instead of silently repairing
                    # a possibly interrupted transaction.
                    raise ReleaseTransactionIntegrityError(
                        "published event has an unremovable temporary sibling"
                    ) from None
            # If the event was not fully durable, intentionally preserve any
            # temporary sibling (and perhaps its final hard link).  A later
            # load then rejects the journal rather than guessing whether the
            # caller reached the mutation guarded by this intent record.

    def _read_event(self, path: Path) -> ReleaseTransactionEvent:
        self._assert_regular_file(path, mode=0o600, label="transaction event")
        descriptor = -1
        try:
            descriptor = os.open(path, os.O_RDONLY | _NOFOLLOW)
            metadata = os.fstat(descriptor)
            if not self._unsafe_allow_non_root_for_tests:
                self._assert_file_metadata(metadata, mode=0o600, label="transaction event")
            chunks: list[bytes] = []
            while True:
                block = os.read(descriptor, 64 * 1024)
                if not block:
                    break
                chunks.append(block)
        except OSError as exc:
            raise ReleaseTransactionIntegrityError("unable to read transaction event") from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        raw = b"".join(chunks)
        try:
            decoded = raw.decode("utf-8")
            payload = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReleaseTransactionIntegrityError("transaction event is not valid JSON") from exc
        if not isinstance(payload, dict) or set(payload) != _EVENT_FIELDS:
            raise ReleaseTransactionIntegrityError("transaction event fields are invalid")
        event = self._event_from_payload(payload)
        if raw != _canonical_event_bytes(event):
            raise ReleaseTransactionIntegrityError("transaction event is not canonical JSON")
        return event

    def _event_from_payload(self, payload: dict[str, object]) -> ReleaseTransactionEvent:
        if payload["format"] != _FORMAT:
            raise ReleaseTransactionIntegrityError("transaction event format is unsupported")
        sequence = payload["sequence"]
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
            raise ReleaseTransactionIntegrityError("transaction event sequence is invalid")
        previous_release_id_value = payload["previous_release_id"]
        if previous_release_id_value is not None:
            previous_release_id = _require_release_id(
                previous_release_id_value,
                field="previous_release_id",
            )
        else:
            previous_release_id = None
        previous_event_sha256_value = payload["previous_event_sha256"]
        if previous_event_sha256_value is not None:
            previous_event_sha256 = _require_sha256(
                previous_event_sha256_value,
                field="previous_event_sha256",
            )
        else:
            previous_event_sha256 = None
        try:
            state = ReleaseTransactionState(payload["state"])
        except (TypeError, ValueError) as exc:
            raise ReleaseTransactionIntegrityError("transaction event state is invalid") from exc
        return ReleaseTransactionEvent(
            release_id=_require_release_id(payload["release_id"], field="release_id"),
            sequence=sequence,
            state=state,
            occurred_at=_parse_timestamp(payload["occurred_at"]),
            request_sha256=_require_sha256(payload["request_sha256"], field="request_sha256"),
            artifact_sha256=_require_sha256(payload["artifact_sha256"], field="artifact_sha256"),
            previous_release_id=previous_release_id,
            previous_event_sha256=previous_event_sha256,
        )

    def _require_production_privilege_boundary(self) -> None:
        if self._unsafe_allow_non_root_for_tests:
            return
        if sys.platform != "linux" or getattr(os, "geteuid", lambda: -1)() != 0:
            raise ReleaseTransactionPrivilegeError(
                "release transaction journal requires Linux root"
            )
        if not self._root.is_absolute() or any(part in {".", ".."} for part in self._root.parts):
            raise ReleaseTransactionIntegrityError(
                "production transaction journal path must be absolute and normalized"
            )

    def _assert_root_controlled_parent_chain(self, path: Path) -> None:
        if self._unsafe_allow_non_root_for_tests:
            if not path.is_dir() or path.is_symlink():
                raise ReleaseTransactionIntegrityError("transaction journal parent is not a directory")
            return
        if not path.is_absolute():
            raise ReleaseTransactionIntegrityError("transaction journal parent must be absolute")
        current = Path(path.anchor)
        self._assert_directory(
            current,
            mode=None,
            label="transaction journal parent",
            require_root_group=False,
        )
        for part in path.parts[1:]:
            current = current / part
            self._assert_directory(
                current,
                mode=None,
                label="transaction journal parent",
                require_root_group=False,
            )

    def _assert_directory(
        self,
        path: Path,
        *,
        mode: int | None,
        label: str,
        require_root_group: bool = True,
    ) -> None:
        try:
            metadata = os.lstat(path)
        except OSError as exc:
            raise ReleaseTransactionIntegrityError(f"{label} is unreadable") from exc
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ReleaseTransactionIntegrityError(f"{label} must be a non-symlink directory")
        if self._unsafe_allow_non_root_for_tests:
            return
        self._assert_directory_metadata(
            metadata,
            mode=mode,
            label=label,
            require_root_group=require_root_group,
        )

    def _assert_regular_file(self, path: Path, *, mode: int, label: str) -> None:
        try:
            metadata = os.lstat(path)
        except OSError as exc:
            raise ReleaseTransactionIntegrityError(f"{label} is unreadable") from exc
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ReleaseTransactionIntegrityError(f"{label} must be a non-symlink regular file")
        if self._unsafe_allow_non_root_for_tests:
            return
        self._assert_file_metadata(metadata, mode=mode, label=label)

    @staticmethod
    def _assert_directory_metadata(
        metadata: os.stat_result,
        *,
        mode: int | None,
        label: str,
        require_root_group: bool,
    ) -> None:
        actual_mode = stat.S_IMODE(metadata.st_mode)
        if metadata.st_uid != 0 or (require_root_group and metadata.st_gid != 0):
            expected_owner = "root:root" if require_root_group else "root"
            raise ReleaseTransactionIntegrityError(f"{label} must be owned by {expected_owner}")
        if actual_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ReleaseTransactionIntegrityError(f"{label} must not be group/other writable")
        if mode is not None and actual_mode != mode:
            raise ReleaseTransactionIntegrityError(f"{label} has an unexpected mode")

    @staticmethod
    def _assert_file_metadata(metadata: os.stat_result, *, mode: int, label: str) -> None:
        actual_mode = stat.S_IMODE(metadata.st_mode)
        if metadata.st_uid != 0 or metadata.st_gid != 0:
            raise ReleaseTransactionIntegrityError(f"{label} must be owned by root:root")
        if actual_mode != mode or metadata.st_nlink != 1:
            raise ReleaseTransactionIntegrityError(f"{label} has unsafe ownership metadata")


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write while persisting transaction event")
        view = view[written:]


def _fsync_directory(path: Path) -> None:
    """Synchronize directory metadata where the production platform supports it."""

    if os.name == "nt":
        return
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        os.fsync(descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)

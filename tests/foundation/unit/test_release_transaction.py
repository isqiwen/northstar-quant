from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.deploy import platform_support
from scripts.deploy.release_transaction import (
    ReleaseRecoveryCategory,
    ReleaseTransactionError,
    ReleaseTransactionExistsError,
    ReleaseTransactionIntegrityError,
    ReleaseTransactionPrivilegeError,
    ReleaseTransactionRecoveryBlockedError,
    ReleaseTransactionState,
    ReleaseTransactionStore,
    ReleaseTransactionTransitionError,
)


_REQUEST_SHA = "a" * 64
_ARTIFACT_SHA = "b" * 64
_NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> ReleaseTransactionStore:
    return ReleaseTransactionStore(
        tmp_path / "transactions",
        unsafe_allow_non_root_for_tests=True,
    )


def _begin(store: ReleaseTransactionStore, release_id: str = "abc123-20260823"):
    return store.begin(
        release_id=release_id,
        request_sha256=_REQUEST_SHA,
        artifact_sha256=_ARTIFACT_SHA,
        previous_release_id="abc122-20260822",
        occurred_at=_NOW,
    )


def _transition_path_to(
    store: ReleaseTransactionStore,
    *,
    release_id: str,
    target: ReleaseTransactionState,
) -> None:
    order = (
        ReleaseTransactionState.VERIFIED,
        ReleaseTransactionState.STAGING_STARTED,
        ReleaseTransactionState.STAGED,
        ReleaseTransactionState.MIGRATION_STARTED,
        ReleaseTransactionState.MIGRATED,
        ReleaseTransactionState.CANDIDATE_HEALTHY,
        ReleaseTransactionState.CUTOVER_STARTED,
        ReleaseTransactionState.CURRENT_SWITCHED,
        ReleaseTransactionState.POST_START_HEALTHY,
        ReleaseTransactionState.PROMOTED,
    )
    for offset, state in enumerate(order, start=1):
        store.transition(release_id, state, occurred_at=_NOW + timedelta(seconds=offset))
        if state is target:
            return
    raise AssertionError(f"target {target} is not on the normal release path")


def test_begin_publishes_a_canonical_no_replace_received_event(tmp_path: Path) -> None:
    store = _store(tmp_path)

    transaction = _begin(store)

    assert transaction.state is ReleaseTransactionState.RECEIVED
    event_path = store.root / transaction.release_id / "events" / "00000001.json"
    raw = event_path.read_bytes()
    payload = json.loads(raw)
    assert raw.endswith(b"\n")
    assert payload == {
        "artifact_sha256": _ARTIFACT_SHA,
        "format": "northstar.release-transaction.v1",
        "occurred_at": "2026-08-23T12:00:00.000000Z",
        "previous_event_sha256": None,
        "previous_release_id": "abc122-20260822",
        "release_id": "abc123-20260823",
        "request_sha256": _REQUEST_SHA,
        "sequence": 1,
        "state": "received",
    }
    assert store.load(transaction.release_id) == transaction

    with pytest.raises(ReleaseTransactionExistsError):
        _begin(store)
    assert event_path.read_bytes() == raw


def test_transition_is_append_only_and_hash_chained(tmp_path: Path) -> None:
    store = _store(tmp_path)
    transaction = _begin(store)

    verified = store.transition(
        transaction.release_id,
        ReleaseTransactionState.VERIFIED,
        occurred_at=_NOW + timedelta(seconds=1),
    )
    staging_started = store.transition(
        transaction.release_id,
        "staging_started",
        occurred_at=_NOW + timedelta(seconds=2),
    )

    assert verified.events[-1].previous_event_sha256 == transaction.current_event.sha256
    assert staging_started.events[-1].previous_event_sha256 == verified.current_event.sha256
    assert sorted(path.name for path in (store.root / transaction.release_id / "events").iterdir()) == [
        "00000001.json",
        "00000002.json",
        "00000003.json",
    ]
    assert store.load(transaction.release_id) == staging_started


def test_transition_rejects_skips_terminal_states_and_post_migration_failed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    transaction = _begin(store)

    with pytest.raises(ReleaseTransactionTransitionError, match="cannot transition"):
        store.transition(transaction.release_id, ReleaseTransactionState.MIGRATED)

    _transition_path_to(
        store,
        release_id=transaction.release_id,
        target=ReleaseTransactionState.MIGRATION_STARTED,
    )
    with pytest.raises(ReleaseTransactionTransitionError, match="cannot transition"):
        store.transition(transaction.release_id, ReleaseTransactionState.FAILED)

    recovery = store.transition(
        transaction.release_id,
        ReleaseTransactionState.RECOVERY_REQUIRED,
    )
    assert recovery.state is ReleaseTransactionState.RECOVERY_REQUIRED
    with pytest.raises(ReleaseTransactionTransitionError, match="cannot transition"):
        store.transition(transaction.release_id, ReleaseTransactionState.MIGRATED)


@pytest.mark.parametrize(
    ("target", "expected"),
    (
        (
            ReleaseTransactionState.MIGRATION_STARTED,
            ReleaseRecoveryCategory.DATABASE_RECOVERY_REQUIRED,
        ),
        (
            ReleaseTransactionState.CUTOVER_STARTED,
            ReleaseRecoveryCategory.CUTOVER_RECOVERY_REQUIRED,
        ),
    ),
)
def test_incomplete_transactions_are_categorized_fail_closed(
    tmp_path: Path,
    target: ReleaseTransactionState,
    expected: ReleaseRecoveryCategory,
) -> None:
    store = _store(tmp_path)
    transaction = _begin(store)
    _transition_path_to(store, release_id=transaction.release_id, target=target)

    decision = store.recovery_decision(transaction.release_id)

    assert decision.category is expected
    assert decision.requires_operator_action is True
    assert decision.automatic_database_rollback_allowed is False
    assert decision.automatic_service_resume_allowed is False


def test_explicit_pre_migration_failure_is_not_an_unresolved_crash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    transaction = _begin(store)
    failed = store.transition(transaction.release_id, ReleaseTransactionState.FAILED)

    decision = store.recovery_decision(transaction.release_id)

    assert failed.state is ReleaseTransactionState.FAILED
    assert decision.category is ReleaseRecoveryCategory.KNOWN_PRE_MIGRATION_FAILURE
    assert decision.requires_operator_action is False
    assert decision.automatic_database_rollback_allowed is False


def test_promoted_transaction_has_no_recovery_action(tmp_path: Path) -> None:
    store = _store(tmp_path)
    transaction = _begin(store)
    _transition_path_to(
        store,
        release_id=transaction.release_id,
        target=ReleaseTransactionState.PROMOTED,
    )

    decision = store.recovery_decision(transaction.release_id)

    assert decision.category is ReleaseRecoveryCategory.NONE
    assert decision.requires_operator_action is False
    assert decision.automatic_database_rollback_allowed is False


def test_unreadable_journal_blocks_new_transactions_without_repairing_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    transaction = _begin(store)
    events_dir = store.root / transaction.release_id / "events"
    poisoned_path = events_dir / ".interrupted-publication.tmp"
    poisoned_path.write_bytes(b"incomplete")

    decision = store.recovery_decision(transaction.release_id)

    assert decision.category is ReleaseRecoveryCategory.UNREADABLE_JOURNAL
    assert decision.requires_operator_action is True
    with pytest.raises(ReleaseTransactionRecoveryBlockedError, match=transaction.release_id):
        _begin(store, release_id="def456-20260823")
    assert poisoned_path.read_bytes() == b"incomplete"


def test_interrupted_no_replace_publication_preserves_evidence_and_blocks_reuse(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import scripts.deploy.release_transaction as release_transaction

    store = _store(tmp_path)
    transaction = _begin(store)
    original_fsync_directory = release_transaction._fsync_directory

    def fail_event_directory_sync(path: Path) -> None:
        if path.name == "events":
            raise OSError("simulated journal sync failure")
        original_fsync_directory(path)

    monkeypatch.setattr(release_transaction, "_fsync_directory", fail_event_directory_sync)
    with pytest.raises(ReleaseTransactionIntegrityError, match="publish transaction event"):
        store.transition(transaction.release_id, ReleaseTransactionState.VERIFIED)

    events_dir = store.root / transaction.release_id / "events"
    assert (events_dir / "00000002.json").is_file()
    temporary_entries = [entry for entry in events_dir.iterdir() if entry.name.endswith(".tmp")]
    assert len(temporary_entries) == 1
    assert store.recovery_decision(transaction.release_id).category is ReleaseRecoveryCategory.UNREADABLE_JOURNAL


def test_broken_hash_chain_is_fail_closed_and_never_overwritten(tmp_path: Path) -> None:
    store = _store(tmp_path)
    transaction = _begin(store)
    store.transition(transaction.release_id, ReleaseTransactionState.VERIFIED)
    second_event = store.root / transaction.release_id / "events" / "00000002.json"
    original = second_event.read_text(encoding="utf-8")
    tampered = original.replace(_REQUEST_SHA, "c" * 64)
    second_event.write_text(tampered, encoding="utf-8")

    with pytest.raises(ReleaseTransactionIntegrityError, match="request digest changed"):
        store.load(transaction.release_id)
    decision = store.recovery_decision(transaction.release_id)
    assert decision.category is ReleaseRecoveryCategory.UNREADABLE_JOURNAL
    assert second_event.read_text(encoding="utf-8") == tampered


@pytest.mark.parametrize(
    ("system_name", "machine"),
    (("Windows", "AMD64"), ("Linux", "aarch64")),
)
def test_production_store_requires_linux_x86_64_root_before_touching_the_filesystem(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    system_name: str,
    machine: str,
) -> None:
    monkeypatch.setattr(platform_support.platform, "system", lambda: system_name)
    monkeypatch.setattr(platform_support.platform, "machine", lambda: machine)
    store = ReleaseTransactionStore(tmp_path / "production-transactions")

    with pytest.raises(ReleaseTransactionPrivilegeError, match="Linux x86_64 root"):
        store.initialize()
    assert not store.root.exists()


def test_test_only_nonroot_bypass_never_bypasses_the_linux_host_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(platform_support.platform, "system", lambda: "Windows")
    monkeypatch.setattr(platform_support.platform, "machine", lambda: "AMD64")
    store = ReleaseTransactionStore(
        tmp_path / "test-transactions",
        unsafe_allow_non_root_for_tests=True,
    )

    with pytest.raises(ReleaseTransactionPrivilegeError, match="Linux x86_64 root"):
        store.initialize()
    assert not store.root.exists()


def test_release_id_and_digest_inputs_fail_closed_before_journal_creation(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(ReleaseTransactionError):
        store.begin(
            release_id="../escape",
            request_sha256=_REQUEST_SHA,
            artifact_sha256=_ARTIFACT_SHA,
            previous_release_id=None,
        )
    with pytest.raises(ReleaseTransactionError):
        store.begin(
            release_id="safe-release",
            request_sha256="A" * 64,
            artifact_sha256=_ARTIFACT_SHA,
            previous_release_id=None,
        )
    assert not store.root.exists()

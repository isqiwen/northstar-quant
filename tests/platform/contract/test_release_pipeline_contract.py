"""Cross-platform contracts for the signed, root-owned release pipeline."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.deploy import release_manifest
from scripts.deploy import release_transaction_hook as hook
from scripts.deploy import root_release_runner as runner
from scripts.deploy.release_transaction import ReleaseTransactionState, ReleaseTransactionStore


_REQUEST_SHA256 = "a" * 64
_ARTIFACT_SHA256 = "b" * 64
_RELEASE_ID = "20260823-release-gate"
_PREVIOUS_RELEASE_ID = "20260822-promoted"

_NORMAL_LIFECYCLE = (
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


def _test_store(tmp_path: Path) -> ReleaseTransactionStore:
    return ReleaseTransactionStore(
        tmp_path / "transactions",
        unsafe_allow_non_root_for_tests=True,
    )


def _run_hook(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    store: ReleaseTransactionStore,
    *arguments: str,
) -> dict[str, object]:
    """Exercise the CLI without requiring Linux root in Windows CI."""

    monkeypatch.setattr(hook, "_store", lambda _root: store)
    monkeypatch.setattr(hook.sys, "argv", ["release_transaction_hook.py", *arguments])

    assert hook.main() == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize(
    ("previous_release_id", "expected_previous_release_id"),
    ((None, None), (_PREVIOUS_RELEASE_ID, _PREVIOUS_RELEASE_ID)),
)
def test_transaction_hook_begin_accepts_optional_previous_release_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    previous_release_id: str | None,
    expected_previous_release_id: str | None,
) -> None:
    store = _test_store(tmp_path)
    arguments = [
        "--root",
        str(store.root),
        "begin",
        _RELEASE_ID,
        _REQUEST_SHA256,
        _ARTIFACT_SHA256,
    ]
    if previous_release_id is not None:
        arguments.extend(("--previous-release-id", previous_release_id))

    result = _run_hook(monkeypatch, capsys, store, *arguments)

    assert result == {"release_id": _RELEASE_ID, "state": "received"}
    transaction = store.load(_RELEASE_ID)
    assert transaction.state is ReleaseTransactionState.RECEIVED
    assert transaction.current_event.previous_release_id == expected_previous_release_id


def test_transaction_hook_persists_the_only_permitted_lifecycle_without_auto_recovery(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    store = _test_store(tmp_path)
    _run_hook(
        monkeypatch,
        capsys,
        store,
        "--root",
        str(store.root),
        "begin",
        _RELEASE_ID,
        _REQUEST_SHA256,
        _ARTIFACT_SHA256,
        "--previous-release-id",
        _PREVIOUS_RELEASE_ID,
    )

    observed_states = [ReleaseTransactionState.RECEIVED]
    for state in _NORMAL_LIFECYCLE:
        result = _run_hook(
            monkeypatch,
            capsys,
            store,
            "--root",
            str(store.root),
            "transition",
            _RELEASE_ID,
            state.value,
        )
        assert result == {"release_id": _RELEASE_ID, "state": state.value}
        observed_states.append(state)

    assert observed_states == [ReleaseTransactionState.RECEIVED, *_NORMAL_LIFECYCLE]
    assert [event.state for event in store.load(_RELEASE_ID).events] == observed_states

    recovery = _run_hook(
        monkeypatch,
        capsys,
        store,
        "--root",
        str(store.root),
        "inspect",
        _RELEASE_ID,
    )
    assert recovery["automatic_database_rollback_allowed"] is False
    assert recovery["automatic_service_resume_allowed"] is False
    assert recovery["requires_operator_action"] is False


def test_root_gate_holds_a_nonblocking_exclusive_global_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "release-gate.lock"
    lock_path.write_bytes(b"")
    operations: list[tuple[int, int]] = []

    fake_fcntl = SimpleNamespace(
        LOCK_EX=0x01,
        LOCK_NB=0x04,
        LOCK_UN=0x08,
        flock=lambda descriptor, operation: operations.append((descriptor, operation)),
    )
    monkeypatch.setattr(runner, "DEPLOY_LOCK_PATH", lock_path)
    monkeypatch.setattr(runner, "fcntl", fake_fcntl)
    monkeypatch.setattr(runner, "_assert_root_controlled_path", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner.os, "O_CLOEXEC", 0, raising=False)
    monkeypatch.setattr(runner.os, "open", lambda *args, **kwargs: 57)
    monkeypatch.setattr(runner.os, "close", lambda _descriptor: None)

    with runner._exclusive_deploy_lock():
        assert operations == [(57, fake_fcntl.LOCK_EX | fake_fcntl.LOCK_NB)]

    assert operations == [
        (57, fake_fcntl.LOCK_EX | fake_fcntl.LOCK_NB),
        (57, fake_fcntl.LOCK_UN),
    ]


def test_root_gate_verifies_public_manifest_and_private_environment_signatures_before_locking() -> None:
    """The private environment stays out of the public signed manifest."""

    submit_source = inspect.getsource(runner.submit_from_stream)
    assert "_verify_signature(manifest_bytes, signature, namespace=SIGNATURE_NAMESPACE)" in submit_source
    assert "_verify_environment_signature(" in submit_source
    assert submit_source.index("_verify_signature(") < submit_source.index("incoming = _new_incoming_directory()")
    assert submit_source.index("_verify_environment_signature(") < submit_source.index(
        "with _exclusive_deploy_lock():"
    )
    assert "environment" not in release_manifest._MANIFEST_FIELDS
    assert "environment_upload" in release_manifest._MANIFEST_FIELDS
    assert "environment_hash" in release_manifest._SECRET_FIELD_MARKERS

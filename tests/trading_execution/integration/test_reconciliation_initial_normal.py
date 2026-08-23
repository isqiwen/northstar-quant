from datetime import UTC, datetime, timedelta
from threading import Barrier, Thread

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

import northstar_quant.trading_execution.reconciliation.reconciliation as reconciliation_module
from northstar_quant.platform.db.models import (
    BrokerSyncLog,
    ReconciliationSafetyStateRecord,
)
from northstar_quant.platform.db.repositories import acquire_reconciliation_safety_fence
from northstar_quant.platform.common.time import utc_now
from northstar_quant.trading_execution.execution.models import BrokerStateSnapshot
from northstar_quant.trading_execution.reconciliation.reconciliation import (
    begin_reconciliation_manual_recovery,
    complete_reconciliation_manual_recovery,
    halt_for_reconciliation,
    reconcile_broker_state,
)


class _FakeBroker:
    def __init__(self, snapshot: BrokerStateSnapshot) -> None:
        self.snapshot = snapshot

    def sync_state(self) -> BrokerStateSnapshot:
        return self.snapshot

    def get_name(self) -> str:
        return "ctp"

    def get_account(self) -> str:
        return "DU123456"


def _clean_snapshot(*, asof: datetime) -> BrokerStateSnapshot:
    return BrokerStateSnapshot(
        account="DU123456",
        account_values={"NetLiquidation": 100_000.0},
        asof=asof,
    )


def _safety_rows(session: Session) -> list[ReconciliationSafetyStateRecord]:
    return list(
        session.scalars(
            select(ReconciliationSafetyStateRecord)
            .where(
                ReconciliationSafetyStateRecord.profile_id == "initial-normal-profile",
                ReconciliationSafetyStateRecord.broker == "ctp",
                ReconciliationSafetyStateRecord.account == "DU123456",
            )
            .order_by(ReconciliationSafetyStateRecord.id.asc())
        )
    )


def test_clean_first_reconciliation_appends_exactly_one_initial_normal_state(
    postgresql_engine,
):
    asof = datetime(2026, 8, 23, 1, 2, tzinfo=UTC)
    snapshot = _clean_snapshot(asof=asof)

    with Session(postgresql_engine, future=True) as session:
        reconcile_broker_state(
            session,
            _FakeBroker(snapshot),
            profile_id="initial-normal-profile",
        )
        reconcile_broker_state(
            session,
            _FakeBroker(snapshot),
            profile_id="initial-normal-profile",
        )
        rows = _safety_rows(session)

    assert len(rows) == 1
    assert rows[0].state == "NORMAL"
    assert rows[0].occurred_at == asof
    assert rows[0].reason == (
        "INITIAL_CLEAN_RECONCILIATION:initial-normal-profile:ctp:DU123456"
    )


def test_reconciliation_refuses_a_preexisting_session_transaction_without_side_effects(
    postgresql_engine,
):
    """A fenced reconcile must never commit or roll back caller-owned work."""

    profile_id = "reconciliation-active-session-refusal"
    snapshot = _clean_snapshot(asof=utc_now() - timedelta(seconds=1))
    with Session(postgresql_engine, future=True) as session:
        session.execute(text("SELECT 1"))
        assert session.in_transaction()
        sync_log_count = session.scalar(select(func.count()).select_from(BrokerSyncLog))
        with pytest.raises(RuntimeError, match="RECONCILIATION_SESSION_MUST_BE_CLEAN"):
            reconcile_broker_state(
                session,
                _FakeBroker(snapshot),
                profile_id=profile_id,
            )
        # The refusal neither ends the caller transaction nor appends any
        # safety/sync fact into it.
        assert session.in_transaction()
        assert session.scalar(
            select(ReconciliationSafetyStateRecord).where(
                ReconciliationSafetyStateRecord.profile_id == profile_id
            )
        ) is None
        assert session.scalar(select(func.count()).select_from(BrokerSyncLog)) == sync_log_count
        session.rollback()


def test_clean_reconciliation_never_auto_recovers_an_existing_non_normal_state(
    postgresql_engine,
):
    snapshot = _clean_snapshot(asof=utc_now())

    with Session(postgresql_engine, future=True) as session:
        reconcile_broker_state(
            session,
            _FakeBroker(snapshot),
            profile_id="initial-normal-profile",
        )
        halt_for_reconciliation(
            session,
            profile_id="initial-normal-profile",
            broker="ctp",
            account="DU123456",
            reason="test halt",
            evidence={"test": "existing non-normal state"},
        )
        reconcile_broker_state(
            session,
            _FakeBroker(snapshot),
            profile_id="initial-normal-profile",
        )
        rows = _safety_rows(session)

    assert [row.state for row in rows] == ["NORMAL", "HALT"]


def test_clean_reconciliation_never_auto_recovers_manual_recovery(
    postgresql_engine,
):
    snapshot = _clean_snapshot(asof=utc_now())

    with Session(postgresql_engine, future=True) as session:
        reconcile_broker_state(
            session,
            _FakeBroker(snapshot),
            profile_id="initial-normal-profile",
        )
        halt_for_reconciliation(
            session,
            profile_id="initial-normal-profile",
            broker="ctp",
            account="DU123456",
            reason="test halt",
            evidence={"test": "manual recovery must remain blocked"},
        )
        begin_reconciliation_manual_recovery(
            session,
            profile_id="initial-normal-profile",
            broker="ctp",
            account="DU123456",
            approver_id="risk-owner",
            reason="manual review in progress",
        )
        reconcile_broker_state(
            session,
            _FakeBroker(snapshot),
            profile_id="initial-normal-profile",
        )
        rows = _safety_rows(session)

    assert [row.state for row in rows] == ["NORMAL", "HALT", "MANUAL_RECOVERY"]


def test_future_clean_snapshot_halts_without_minting_a_future_normal_state(
    postgresql_engine,
):
    """A clock-skewed clean snapshot cannot poison the immutable safety chain."""

    future_clean = _clean_snapshot(asof=utc_now() + timedelta(hours=1))
    incomplete_now = BrokerStateSnapshot(
        account="DU123456",
        account_values={"NetLiquidation": 100_000.0},
        state_complete=False,
        state_errors=["test incomplete after future snapshot refusal"],
        asof=utc_now(),
    )

    with Session(postgresql_engine, future=True) as session:
        with pytest.raises(RuntimeError, match="BROKER_STATE_SNAPSHOT_IN_FUTURE"):
            reconcile_broker_state(
                session,
                _FakeBroker(future_clean),
                profile_id="initial-normal-profile",
            )
        with pytest.raises(RuntimeError, match="test incomplete"):
            reconcile_broker_state(
                session,
                _FakeBroker(incomplete_now),
                profile_id="initial-normal-profile",
            )
        rows = _safety_rows(session)

    assert [row.state for row in rows] == ["HALT"]
    assert rows[0].occurred_at < future_clean.asof


@pytest.mark.parametrize("failure_kind", ("invalid_snapshot", "unexplained_order"))
def test_each_reconciliation_refusal_records_one_failed_sync_log_and_one_halt(
    postgresql_engine,
    failure_kind: str,
) -> None:
    """The fenced failure path must not duplicate audit facts on one refusal."""

    profile_id = f"reconciliation-single-failure-{failure_kind}"
    asof = utc_now()
    if failure_kind == "invalid_snapshot":
        snapshot = BrokerStateSnapshot(
            account="DU123456",
            account_values={"NetLiquidation": 100_000.0},
            state_complete=False,
            state_errors=["single invalid snapshot failure"],
            asof=asof,
        )
        expected_error = "single invalid snapshot failure"
    else:
        snapshot = BrokerStateSnapshot(
            account="DU123456",
            completed_orders=[
                {
                    "broker_order_id": "unexplained-single-failure",
                    "account": "DU123456",
                    "symbol": "RB2405",
                    "side": "BUY",
                    "qty": 1.0,
                    "status": "Cancelled",
                }
            ],
            asof=asof,
        )
        expected_error = "BROKER_ORDER_UNEXPLAINED"

    with Session(postgresql_engine, future=True) as session:
        with pytest.raises(RuntimeError, match=expected_error):
            reconcile_broker_state(
                session,
                _FakeBroker(snapshot),
                profile_id=profile_id,
            )
        # The failure recorder owns and commits its fenced transaction; a
        # caller can immediately retry with the same Session.
        assert not session.in_transaction()

    with Session(postgresql_engine, future=True) as verify:
        failed_logs = list(
            verify.scalars(
                select(BrokerSyncLog)
                .where(
                    BrokerSyncLog.broker == "ctp",
                    BrokerSyncLog.status == "failed",
                )
                .order_by(BrokerSyncLog.id.asc())
            )
        )
        safety_rows = list(
            verify.scalars(
                select(ReconciliationSafetyStateRecord)
                .where(
                    ReconciliationSafetyStateRecord.profile_id == profile_id,
                    ReconciliationSafetyStateRecord.broker == "ctp",
                    ReconciliationSafetyStateRecord.account == "DU123456",
                )
                .order_by(ReconciliationSafetyStateRecord.id.asc())
            )
        )

    # This isolated PostgreSQL schema starts empty for each test, so every
    # failed sync log observed here belongs to this one refusal.
    assert len(failed_logs) == 1
    assert [row.state for row in safety_rows] == ["HALT"]


def test_concurrent_first_clean_reconciliations_append_one_initial_normal(
    postgresql_engine,
    monkeypatch,
) -> None:
    """The shared account fence serializes first-NORMAL creation."""

    profile_id = "initial-normal-concurrent-fence"
    snapshot = _clean_snapshot(asof=utc_now() - timedelta(seconds=1))
    barrier = Barrier(2)
    original_fence = reconciliation_module.acquire_reconciliation_safety_fence
    failures: list[Exception] = []

    def _synchronized_fence(*args, **kwargs):
        barrier.wait(timeout=5)
        return original_fence(*args, **kwargs)

    monkeypatch.setattr(
        reconciliation_module,
        "acquire_reconciliation_safety_fence",
        _synchronized_fence,
    )

    def _reconcile() -> None:
        try:
            with Session(postgresql_engine, future=True) as session:
                reconcile_broker_state(
                    session,
                    _FakeBroker(snapshot),
                    profile_id=profile_id,
                )
        except Exception as exc:  # Thread boundary; assert below.
            failures.append(exc)

    first = Thread(target=_reconcile)
    second = Thread(target=_reconcile)
    first.start()
    second.start()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive()
    assert not second.is_alive()
    assert failures == []
    with Session(postgresql_engine, future=True) as session:
        rows = list(
            session.scalars(
                select(ReconciliationSafetyStateRecord)
                .where(
                    ReconciliationSafetyStateRecord.profile_id == profile_id,
                    ReconciliationSafetyStateRecord.broker == "ctp",
                    ReconciliationSafetyStateRecord.account == "DU123456",
                )
                .order_by(ReconciliationSafetyStateRecord.id.asc())
            )
        )
    assert [row.state for row in rows] == ["NORMAL"]


@pytest.mark.parametrize(
    "branch",
    (
        "halt_existing",
        "begin_requires_halt",
        "complete_requires_recovery",
        "complete_wrong_approver",
    ),
)
def test_safety_transition_noops_and_refusals_release_the_account_fence(
    postgresql_engine,
    branch: str,
) -> None:
    """Every fenced branch ends its transaction before returning or refusing."""

    profile_id = f"reconciliation-fence-cleanup-{branch}"
    with Session(postgresql_engine, future=True) as session:
        if branch == "halt_existing":
            halt_for_reconciliation(
                session,
                profile_id=profile_id,
                broker="ctp",
                account="DU123456",
                reason="test halt",
                evidence={"test": branch},
            )
            halt_for_reconciliation(
                session,
                profile_id=profile_id,
                broker="ctp",
                account="DU123456",
                reason="duplicate test halt",
                evidence={"test": branch},
            )
        elif branch == "begin_requires_halt":
            with pytest.raises(
                RuntimeError,
                match="RECONCILIATION_MANUAL_RECOVERY_REQUIRES_HALT",
            ):
                begin_reconciliation_manual_recovery(
                    session,
                    profile_id=profile_id,
                    broker="ctp",
                    account="DU123456",
                    approver_id="risk-owner",
                    reason="must refuse without halt",
                )
        elif branch == "complete_requires_recovery":
            halt_for_reconciliation(
                session,
                profile_id=profile_id,
                broker="ctp",
                account="DU123456",
                reason="test halt",
                evidence={"test": branch},
            )
            with pytest.raises(
                RuntimeError,
                match="RECONCILIATION_MANUAL_RECOVERY_REQUIRED",
            ):
                complete_reconciliation_manual_recovery(
                    session,
                    profile_id=profile_id,
                    broker="ctp",
                    account="DU123456",
                    approver_id="risk-owner",
                    reason="must refuse before manual recovery",
                )
        else:
            halt_for_reconciliation(
                session,
                profile_id=profile_id,
                broker="ctp",
                account="DU123456",
                reason="test halt",
                evidence={"test": branch},
            )
            begin_reconciliation_manual_recovery(
                session,
                profile_id=profile_id,
                broker="ctp",
                account="DU123456",
                approver_id="risk-owner",
                reason="test manual recovery",
            )
            with pytest.raises(
                PermissionError,
                match="RECONCILIATION_RECOVERY_APPROVER_MISMATCH",
            ):
                complete_reconciliation_manual_recovery(
                    session,
                    profile_id=profile_id,
                    broker="ctp",
                    account="DU123456",
                    approver_id="different-approver",
                    reason="must refuse unapproved completion",
                )
        assert not session.in_transaction()

    with Session(postgresql_engine, future=True) as probe:
        probe.execute(text("SET LOCAL lock_timeout = '100ms'"))
        acquire_reconciliation_safety_fence(
            probe,
            profile_id=profile_id,
            broker="ctp",
            account="DU123456",
        )
        probe.rollback()


def test_failed_reconciliation_retains_the_safety_fence_until_halt_commits(
    postgresql_engine,
    monkeypatch,
) -> None:
    """Failure logging and HALT are one fenced outer transaction."""

    profile_id = "reconciliation-failure-fence"
    snapshot = BrokerStateSnapshot(
        account="DU123456",
        account_values={"NetLiquidation": 100_000.0},
        state_complete=False,
        state_errors=["test incomplete state"],
        asof=utc_now(),
    )
    original_halt = reconciliation_module.halt_for_reconciliation
    probe_refused = False

    def _halt_after_failure_log(session, **kwargs):
        nonlocal probe_refused
        with Session(postgresql_engine, future=True) as probe:
            probe.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(OperationalError):
                acquire_reconciliation_safety_fence(
                    probe,
                    profile_id=profile_id,
                    broker="ctp",
                    account="DU123456",
                )
            probe.rollback()
        probe_refused = True
        return original_halt(session, **kwargs)

    monkeypatch.setattr(
        reconciliation_module,
        "halt_for_reconciliation",
        _halt_after_failure_log,
    )
    with Session(postgresql_engine, future=True) as session:
        with pytest.raises(RuntimeError, match="test incomplete state"):
            reconcile_broker_state(
                session,
                _FakeBroker(snapshot),
                profile_id=profile_id,
            )
        rows = list(
            session.scalars(
                select(ReconciliationSafetyStateRecord)
                .where(
                    ReconciliationSafetyStateRecord.profile_id == profile_id,
                    ReconciliationSafetyStateRecord.broker == "ctp",
                    ReconciliationSafetyStateRecord.account == "DU123456",
                )
                .order_by(ReconciliationSafetyStateRecord.id.asc())
            )
        )

    assert probe_refused is True
    assert [row.state for row in rows] == ["HALT"]


def test_persistence_savepoint_failure_retains_fence_until_halt_commits(
    postgresql_engine,
    monkeypatch,
) -> None:
    """A flushed persistence failure rolls back only the savepoint, not the fence."""

    profile_id = "reconciliation-persistence-failure-fence"
    snapshot = _clean_snapshot(asof=utc_now() - timedelta(seconds=1))
    original_halt = reconciliation_module.halt_for_reconciliation
    probe_refused = False

    def _halt_after_persistence_failure(session, **kwargs):
        nonlocal probe_refused
        with Session(postgresql_engine, future=True) as probe:
            probe.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(OperationalError):
                acquire_reconciliation_safety_fence(
                    probe,
                    profile_id=profile_id,
                    broker="ctp",
                    account="DU123456",
                )
            probe.rollback()
        probe_refused = True
        return original_halt(session, **kwargs)

    def _raise_from_save_fill_snapshots(*_args, **_kwargs):
        raise RuntimeError("test persistence savepoint failure")

    monkeypatch.setattr(
        reconciliation_module,
        "halt_for_reconciliation",
        _halt_after_persistence_failure,
    )
    monkeypatch.setattr(
        reconciliation_module,
        "save_fill_snapshots",
        _raise_from_save_fill_snapshots,
    )
    with Session(postgresql_engine, future=True) as session:
        with pytest.raises(RuntimeError, match="test persistence savepoint failure"):
            reconcile_broker_state(
                session,
                _FakeBroker(snapshot),
                profile_id=profile_id,
            )
        rows = list(
            session.scalars(
                select(ReconciliationSafetyStateRecord)
                .where(
                    ReconciliationSafetyStateRecord.profile_id == profile_id,
                    ReconciliationSafetyStateRecord.broker == "ctp",
                    ReconciliationSafetyStateRecord.account == "DU123456",
                )
                .order_by(ReconciliationSafetyStateRecord.id.asc())
            )
        )

    assert probe_refused is True
    assert [row.state for row in rows] == ["HALT"]


def test_begin_nested_failure_records_a_fenced_halt(
    postgresql_engine,
    monkeypatch,
) -> None:
    """Failure to establish the savepoint is still fail-closed and auditable."""

    profile_id = "reconciliation-savepoint-setup-failure"
    snapshot = _clean_snapshot(asof=utc_now() - timedelta(seconds=1))
    with Session(postgresql_engine, future=True) as session:
        def _raise_from_begin_nested():
            raise RuntimeError("test begin_nested failure")

        monkeypatch.setattr(session, "begin_nested", _raise_from_begin_nested)
        with pytest.raises(RuntimeError, match="test begin_nested failure"):
            reconcile_broker_state(
                session,
                _FakeBroker(snapshot),
                profile_id=profile_id,
            )

    with Session(postgresql_engine, future=True) as session:
        rows = list(
            session.scalars(
                select(ReconciliationSafetyStateRecord)
                .where(
                    ReconciliationSafetyStateRecord.profile_id == profile_id,
                    ReconciliationSafetyStateRecord.broker == "ctp",
                    ReconciliationSafetyStateRecord.account == "DU123456",
                )
                .order_by(ReconciliationSafetyStateRecord.id.asc())
            )
        )
    assert [row.state for row in rows] == ["HALT"]


def test_outer_commit_failure_rolls_back_then_records_a_new_fenced_halt(
    postgresql_engine,
    monkeypatch,
) -> None:
    """An outer commit error never tries to write HALT on an aborted transaction."""

    profile_id = "reconciliation-outer-commit-failure"
    snapshot = _clean_snapshot(asof=utc_now() - timedelta(seconds=1))
    with Session(postgresql_engine, future=True) as session:
        original_commit = session.commit
        commit_attempts = 0

        def _fail_only_the_outer_success_commit() -> None:
            nonlocal commit_attempts
            commit_attempts += 1
            if commit_attempts == 1:
                raise RuntimeError("test outer reconciliation commit failure")
            original_commit()

        monkeypatch.setattr(session, "commit", _fail_only_the_outer_success_commit)
        with pytest.raises(
            RuntimeError,
            match="test outer reconciliation commit failure",
        ):
            reconcile_broker_state(
                session,
                _FakeBroker(snapshot),
                profile_id=profile_id,
            )
        assert commit_attempts == 2

    with Session(postgresql_engine, future=True) as session:
        rows = list(
            session.scalars(
                select(ReconciliationSafetyStateRecord)
                .where(
                    ReconciliationSafetyStateRecord.profile_id == profile_id,
                    ReconciliationSafetyStateRecord.broker == "ctp",
                    ReconciliationSafetyStateRecord.account == "DU123456",
                )
                .order_by(ReconciliationSafetyStateRecord.id.asc())
            )
        )
    assert [row.state for row in rows] == ["HALT"]

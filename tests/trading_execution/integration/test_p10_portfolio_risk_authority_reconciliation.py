"""P10-WP05 P5 reconciliation state required by P8 portfolio-risk authority."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from northstar_quant.application.portfolio_risk_authority import (
    PortfolioRiskAuthorityError,
    PortfolioRiskAuthorityResolver,
    ReconciliationSafetyStateEvidence,
)
from northstar_quant.platform.db.models import ReconciliationSafetyStateRecord
from northstar_quant.platform.db.repositories import latest_reconciliation_safety_state
from northstar_quant.trading_execution.execution.models import BrokerStateSnapshot
from northstar_quant.trading_execution.reconciliation.reconciliation import (
    reconcile_broker_state,
)
from tests.helpers.execution_provenance import build_execution_provenance_fixture


class _CleanCtpSimBroker:
    def __init__(self, snapshot: BrokerStateSnapshot) -> None:
        self.snapshot = snapshot

    def sync_state(self) -> BrokerStateSnapshot:
        return self.snapshot

    def get_name(self) -> str:
        return "ctp_sim"

    def get_account(self) -> str:
        return "ctp-sim-authority-test"


def test_clean_reconciliation_creates_exactly_one_initial_normal_state_for_authority(
    postgresql_engine,
) -> None:
    observed_at = datetime(2026, 8, 23, 1, 0, tzinfo=UTC)
    broker = _CleanCtpSimBroker(
        BrokerStateSnapshot(
            account="ctp-sim-authority-test",
            state_complete=True,
            asof=observed_at,
            account_values={
                "NetLiquidation": 100_000.0,
                "AvailableFunds": 90_000.0,
            },
        )
    )

    with Session(postgresql_engine, future=True) as session:
        reconcile_broker_state(
            session,
            broker,
            profile_id="p10-authority-profile",
            run_id="p10-authority-clean-first",
        )
        first = latest_reconciliation_safety_state(
            session,
            profile_id="p10-authority-profile",
            broker="ctp_sim",
            account="ctp-sim-authority-test",
        )
        assert first is not None
        evidence = ReconciliationSafetyStateEvidence.from_persisted_record(first)
        first_state_hash = first.state_hash
        first_reason = first.reason
        # The lookup above owns a read transaction.  Reconciliation refuses
        # inherited caller transactions, so end it before the next clean sync.
        session.rollback()

        reconcile_broker_state(
            session,
            broker,
            profile_id="p10-authority-profile",
            run_id="p10-authority-clean-second",
        )
        rows = list(
            session.scalars(
                select(ReconciliationSafetyStateRecord)
                .where(
                    ReconciliationSafetyStateRecord.profile_id
                    == "p10-authority-profile"
                )
                .order_by(ReconciliationSafetyStateRecord.id.asc())
            )
        )

    assert [row.state for row in rows] == ["NORMAL"]
    assert evidence.state_snapshot.state.value == "NORMAL"
    assert evidence.reconciliation_state_hash == first_state_hash
    assert first_reason.startswith("INITIAL_CLEAN_RECONCILIATION:")


def test_missing_persisted_reconciliation_state_cannot_be_promoted_to_normal_authority(
    postgresql_engine,
) -> None:
    with Session(postgresql_engine, future=True) as session:
        missing = latest_reconciliation_safety_state(
            session,
            profile_id="p10-missing-authority-profile",
            broker="ctp_sim",
            account="ctp-sim-missing-authority-test",
        )

    assert missing is None
    with pytest.raises(
        PortfolioRiskAuthorityError,
        match="PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_MISSING",
    ):
        ReconciliationSafetyStateEvidence.from_persisted_record(missing)


def test_first_failed_reconciliation_mints_only_genesis_halt_and_no_normal_authority(
    tmp_path,
    postgresql_engine,
) -> None:
    fixture = build_execution_provenance_fixture(tmp_path)
    broker = _CleanCtpSimBroker(
        BrokerStateSnapshot(
            account="ctp-sim-test",
            state_complete=False,
            state_errors=["test broker state incomplete"],
            asof=fixture.request.checked_at,
        )
    )

    with Session(postgresql_engine, future=True) as session:
        with pytest.raises(RuntimeError, match="test broker state incomplete"):
            reconcile_broker_state(
                session,
                broker,
                profile_id=fixture.profile.profile_id,
                run_id="p10-authority-first-failure",
            )
        rows = list(
            session.scalars(
                select(ReconciliationSafetyStateRecord)
                .where(
                    ReconciliationSafetyStateRecord.profile_id
                    == fixture.profile.profile_id
                )
                .order_by(ReconciliationSafetyStateRecord.id.asc())
            )
        )
        assert [row.state for row in rows] == ["HALT"]
        assert rows[0].predecessor_hash is None
        safety = ReconciliationSafetyStateEvidence.from_persisted_record(rows[0])

    with pytest.raises(
        PortfolioRiskAuthorityError,
        match="PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_NOT_NORMAL",
    ):
        PortfolioRiskAuthorityResolver().resolve(
            profile=fixture.profile,
            broker_state=fixture.request.account_snapshot,
            reconciliation_safety_state=safety,
            composition=fixture.composition_evidence,
            evaluated_at=fixture.request.checked_at,
        )

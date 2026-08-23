"""Verifier-backed durable P10 portfolio-risk approval authority tests."""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import func, select

import northstar_quant.application.portfolio_risk_manual_approval as manual_approval
from northstar_quant.application.portfolio_risk_manual_approval import (
    ManualRiskApprovalError,
    require_persisted_portfolio_risk_approval,
)
from northstar_quant.foundation.db.models import PortfolioRiskApprovalRecord
from tests.helpers.execution_provenance import build_execution_provenance_fixture
from tests.helpers.manual_risk_approval import (
    FakeManualRiskApprovalVerifier,
    create_test_portfolio_risk_approval_issuer,
)


def _issue(tmp_path, session):
    fixture = build_execution_provenance_fixture(tmp_path / "fixture")
    issued = create_test_portfolio_risk_approval_issuer().issue(
        session,
        profile=fixture.profile,
        broker="ctp_sim",
        account=fixture.request.account_snapshot.account,
        authority=fixture.request.portfolio_risk_authority,
        composition=fixture.composition_evidence,
        approval_id="p10-test-manual-risk-approval",
        checked_at=fixture.request.checked_at,
    )
    return fixture, issued


def test_issuer_creates_exact_p3_attestation_and_durable_grant(
    tmp_path,
    postgresql_session_factory,
) -> None:
    with postgresql_session_factory() as session:
        fixture, issued = _issue(tmp_path, session)
        assert not session.in_transaction()
        row = session.get(PortfolioRiskApprovalRecord, 1)

        assert row is not None
        assert issued.approval_request.attestation.approval_id == issued.approval_id
        assert issued.approval_request.attestation.approver_id == "risk-owner"
        assert issued.approval_evidence.approved_target is not None
        assert row.record_hash == issued.record_hash
        assert row.binding_hash == issued.binding.binding_hash
        assert row.verifier_receipt_hash == issued.verified_approval.verifier_receipt_hash
        assert not hasattr(row, "verifier_receipt")

        persisted = require_persisted_portfolio_risk_approval(
            session,
            profile=fixture.profile,
            broker="ctp_sim",
            account=fixture.request.account_snapshot.account,
            authority=fixture.request.portfolio_risk_authority,
            approval_request=issued.approval_request,
            approval_evidence=issued.approval_evidence,
            checked_at=fixture.request.checked_at,
        )

        assert persisted.binding == issued.binding
        assert persisted.record_hash == issued.record_hash


def test_issuer_is_exactly_idempotent_and_refuses_changed_verifier_result(
    tmp_path,
    postgresql_session_factory,
) -> None:
    with postgresql_session_factory() as session:
        fixture, issued = _issue(tmp_path, session)
        replay = create_test_portfolio_risk_approval_issuer().issue(
            session,
            profile=fixture.profile,
            broker="ctp_sim",
            account=fixture.request.account_snapshot.account,
            authority=fixture.request.portfolio_risk_authority,
            composition=fixture.composition_evidence,
            approval_id=issued.approval_id,
            checked_at=fixture.request.checked_at,
        )

        assert replay.record_hash == issued.record_hash
        with pytest.raises(
            ManualRiskApprovalError,
            match="MANUAL_RISK_APPROVAL_APPROVER_UNAUTHORIZED",
        ):
            create_test_portfolio_risk_approval_issuer(
                FakeManualRiskApprovalVerifier(approver_id="untrusted-risk-owner")
            ).issue(
                session,
                profile=fixture.profile,
                broker="ctp_sim",
                account=fixture.request.account_snapshot.account,
                authority=fixture.request.portfolio_risk_authority,
                composition=fixture.composition_evidence,
                approval_id="p10-other-manual-risk-approval",
                checked_at=fixture.request.checked_at,
            )


def test_unavailable_verifier_and_unpersisted_or_changed_claim_fail_closed(
    tmp_path,
    postgresql_session_factory,
) -> None:
    with postgresql_session_factory() as session:
        fixture = build_execution_provenance_fixture(tmp_path / "fixture")
        with pytest.raises(
            ManualRiskApprovalError,
            match="MANUAL_RISK_APPROVAL_VERIFIER_UNAVAILABLE",
        ):
            manual_approval._PortfolioRiskApprovalIssuer().issue(
                session,
                profile=fixture.profile,
                broker="ctp_sim",
                account=fixture.request.account_snapshot.account,
                authority=fixture.request.portfolio_risk_authority,
                composition=fixture.composition_evidence,
                approval_id="p10-unavailable-manual-risk-approval",
                checked_at=fixture.request.checked_at,
            )
        assert session.scalar(select(func.count()).select_from(PortfolioRiskApprovalRecord)) == 0

        issued_fixture, issued = _issue(tmp_path / "issued", session)
        changed = replace(
            issued.approval_request,
            attestation=replace(
                issued.approval_request.attestation,
                rationale="changed rationale after verification",
            ),
        )
        with pytest.raises(
            ManualRiskApprovalError,
            match="PERSISTED_PORTFOLIO_RISK_APPROVAL_EVIDENCE_MISMATCH",
        ):
            require_persisted_portfolio_risk_approval(
                session,
                profile=issued_fixture.profile,
                broker="ctp_sim",
                account=issued_fixture.request.account_snapshot.account,
                authority=issued_fixture.request.portfolio_risk_authority,
                approval_request=changed,
                approval_evidence=issued.approval_evidence,
                checked_at=issued_fixture.request.checked_at,
            )


def test_persisted_reader_normalizes_tampered_repository_record_to_fail_closed_error(
    tmp_path,
    postgresql_session_factory,
) -> None:
    with postgresql_session_factory() as session:
        fixture, issued = _issue(tmp_path, session)
        row = session.get(PortfolioRiskApprovalRecord, 1)
        assert row is not None
        row.record_hash = "0" * 64

        with pytest.raises(
            ManualRiskApprovalError,
            match="PERSISTED_PORTFOLIO_RISK_APPROVAL_RECORD_INVALID",
        ):
            require_persisted_portfolio_risk_approval(
                session,
                profile=fixture.profile,
                broker="ctp_sim",
                account=fixture.request.account_snapshot.account,
                authority=fixture.request.portfolio_risk_authority,
                approval_request=issued.approval_request,
                approval_evidence=issued.approval_evidence,
                checked_at=fixture.request.checked_at,
            )

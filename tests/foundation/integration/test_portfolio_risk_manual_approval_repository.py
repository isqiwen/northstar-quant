"""Append-only verifier-backed manual approval repository tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import inspect
from pathlib import Path

import pytest
from sqlalchemy import inspect as sqlalchemy_inspect

from northstar_quant.foundation.db.models import PortfolioRiskApprovalRecord
from northstar_quant.foundation.db.repositories import (
    find_portfolio_risk_approval,
    record_portfolio_risk_approval,
)


def _hash(name: str) -> str:
    return sha256(name.encode("utf-8")).hexdigest()


def _kwargs(*, rationale: str = "manual approval accepted") -> dict[str, object]:
    reviewed_at = datetime(2026, 8, 23, 9, tzinfo=UTC)
    return {
        "approval_id": "p10-repository-approval",
        "profile_id": "cn_futures_daily_trend_simulated",
        "broker": "ctp_sim",
        "account": "ctp-sim-test",
        "review_hash": _hash("review"),
        "evidence_hash": _hash("evidence"),
        "portfolio_target_hash": _hash("target"),
        "approved_target_hash": _hash("approved-target"),
        "composition_hash": _hash("composition"),
        "composition_evidence_hash": _hash("composition-evidence"),
        "authority_hash": _hash("authority"),
        "policy_hash": _hash("policy"),
        "reconciliation_state_hash": _hash("reconciliation"),
        "binding_hash": _hash("binding"),
        "attestation_hash": _hash("attestation"),
        "approver_id": "risk-owner",
        "verifier_id": "ctp-sim-manual-risk-verifier-v1",
        "verifier_receipt_hash": _hash("verifier-receipt"),
        "rationale": rationale,
        "review_evaluated_at": reviewed_at,
        "approved_at": reviewed_at,
        "verified_at": reviewed_at,
        "valid_until": reviewed_at + timedelta(minutes=5),
        "issued_at": reviewed_at,
    }


def test_repository_is_exactly_idempotent_and_never_persists_raw_receipt(
    postgresql_session_factory,
) -> None:
    with postgresql_session_factory() as session:
        row = record_portfolio_risk_approval(session, **_kwargs())
        replay = record_portfolio_risk_approval(session, **_kwargs())

        assert replay.id == row.id
        assert row.record_hash
        assert not hasattr(row, "verifier_receipt")
        assert find_portfolio_risk_approval(
            session,
            approval_id="p10-repository-approval",
            profile_id="cn_futures_daily_trend_simulated",
            broker="ctp_sim",
            account="ctp-sim-test",
        ) == row

        with pytest.raises(RuntimeError, match="PORTFOLIO_RISK_APPROVAL_IDEMPOTENCY_CONFLICT"):
            record_portfolio_risk_approval(
                session,
                **_kwargs(rationale="altered durable approval"),
            )


def test_model_migration_and_repository_expose_only_a_verifier_receipt_hash() -> None:
    columns = {column.key for column in sqlalchemy_inspect(PortfolioRiskApprovalRecord).columns}
    parameters = set(inspect.signature(record_portfolio_risk_approval).parameters)
    migration = (
        Path(__file__).parents[3]
        / "alembic"
        / "versions"
        / "0008_portfolio_risk_approval.py"
    ).read_text(encoding="utf-8")

    assert "verifier_receipt_hash" in columns
    assert "verifier_receipt" not in columns
    assert "verifier_receipt_hash" in parameters
    assert "verifier_receipt" not in parameters
    assert 'sa.Column("verifier_receipt",' not in migration


def test_repository_rejects_tampered_hash_wrong_time_or_non_sim_broker(
    postgresql_session_factory,
) -> None:
    with postgresql_session_factory() as session:
        row = record_portfolio_risk_approval(session, **_kwargs())
        row.record_hash = _hash("tampered")
        with pytest.raises(RuntimeError, match="PORTFOLIO_RISK_APPROVAL_RECORD_TAMPERED"):
            find_portfolio_risk_approval(
                session,
                approval_id="p10-repository-approval",
                profile_id="cn_futures_daily_trend_simulated",
                broker="ctp_sim",
                account="ctp-sim-test",
            )
        session.rollback()

    with postgresql_session_factory() as session:
        invalid_time = _kwargs()
        invalid_time["issued_at"] = invalid_time["valid_until"]
        with pytest.raises(ValueError, match="time ordering"):
            record_portfolio_risk_approval(session, **invalid_time)
        non_sim = _kwargs()
        non_sim["broker"] = "paper"
        with pytest.raises(PermissionError, match="PORTFOLIO_RISK_APPROVAL_BROKER_REFUSED"):
            record_portfolio_risk_approval(session, **non_sim)

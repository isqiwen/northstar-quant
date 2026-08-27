"""P10-WP05 trusted-source derivation for P3 portfolio-risk reviews."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from northstar_quant.application.execution_provenance_preflight import (
    ExecutionProvenancePreflight,
    ExecutionProvenancePreflightError,
)
from northstar_quant.application.portfolio_risk_authority import (
    PortfolioRiskAuthorityError,
    PortfolioRiskAuthorityResolver,
    ReconciliationSafetyStateEvidence,
)
from northstar_quant.portfolio_risk.risk import RiskState, RiskStateSnapshot
from tests.helpers.execution_provenance import (
    ExecutionProvenanceFixture,
    build_execution_provenance_fixture,
)


def _normal_safety(
    fixture: ExecutionProvenanceFixture,
) -> ReconciliationSafetyStateEvidence:
    """Build the typed equivalent of the first clean persisted P5 state."""

    snapshot = fixture.request.account_snapshot
    state = (
        fixture.portfolio_risk_approval_request.review_request.risk_state.state_snapshot
    )
    assert state is not None and state.state is RiskState.NORMAL
    return ReconciliationSafetyStateEvidence(
        profile_id=fixture.profile.profile_id,
        broker="ctp_sim",
        account_id=snapshot.account,
        state_snapshot=state,
        reconciliation_state_hash=state.state_hash,
    )


def _resolve(
    fixture: ExecutionProvenanceFixture,
    *,
    profile=None,
    broker_state=None,
    safety=None,
):
    return PortfolioRiskAuthorityResolver().resolve(
        profile=fixture.profile if profile is None else profile,
        broker_state=(
            fixture.request.account_snapshot if broker_state is None else broker_state
        ),
        reconciliation_safety_state=(
            _normal_safety(fixture) if safety is None else safety
        ),
        composition=fixture.composition_evidence,
        evaluated_at=fixture.request.checked_at,
        contract_authority=fixture.contract_authority,
    )


def test_resolver_derives_policy_capacity_taxonomy_and_normal_state_from_typed_sources(
    tmp_path,
) -> None:
    fixture = build_execution_provenance_fixture(tmp_path)
    authority = _resolve(fixture)
    config = fixture.profile.portfolio_risk_approval
    assert config is not None

    review = authority.review_request
    assert (authority.profile_id, authority.policy_id, authority.policy_version) == (
        fixture.profile.profile_id,
        config.policy_id,
        config.policy_version,
    )
    assert review.policy.authority_id == authority.authority_id
    assert review.policy.policy_hash == authority.policy_hash
    assert (
        review.account_snapshot.equity
        == fixture.request.account_snapshot.account_values["NetLiquidation"]
    )
    assert (
        review.account_snapshot.margin_capacity
        == fixture.request.account_snapshot.account_values["AvailableFunds"]
    )
    assert review.risk_state.state_snapshot is not None
    assert review.risk_state.state_snapshot.state is RiskState.NORMAL
    assert [
        (item.commodity_id, item.sector_id)
        for item in review.instrument_snapshots
    ] == [("rb", "ferrous")]
    assert authority.execution_rules[0].margin_rate == 0.1
    assert len(authority.authority_hash) == 64


def test_resolver_refuses_missing_portfolio_policy_or_broker_margin_capacity(
    tmp_path,
) -> None:
    fixture = build_execution_provenance_fixture(tmp_path)

    with pytest.raises(
        PortfolioRiskAuthorityError,
        match="PORTFOLIO_RISK_AUTHORITY_CONFIG_MISSING",
    ):
        _resolve(
            fixture,
            profile=replace(fixture.profile, portfolio_risk_approval=None),
        )

    missing_capacity = replace(
        fixture.request.account_snapshot,
        account_values={"NetLiquidation": 100_000.0},
    )
    with pytest.raises(
        PortfolioRiskAuthorityError,
        match="PORTFOLIO_RISK_AUTHORITY_AVAILABLEFUNDS_MISSING",
    ):
        _resolve(fixture, broker_state=missing_capacity)


def test_resolver_refuses_non_normal_or_hash_forged_reconciliation_state(
    tmp_path,
) -> None:
    fixture = build_execution_provenance_fixture(tmp_path)
    normal = _normal_safety(fixture)
    halted = normal.state_snapshot.transition(
        target=RiskState.HALT,
        occurred_at=fixture.request.checked_at,
        reason="test halt",
    )
    halted_evidence = ReconciliationSafetyStateEvidence(
        profile_id=normal.profile_id,
        broker=normal.broker,
        account_id=normal.account_id,
        state_snapshot=halted,
        reconciliation_state_hash=halted.state_hash,
    )

    with pytest.raises(
        PortfolioRiskAuthorityError,
        match="PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_NOT_NORMAL",
    ):
        _resolve(fixture, safety=halted_evidence)

    with pytest.raises(
        PortfolioRiskAuthorityError,
        match="PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_TAMPERED",
    ):
        ReconciliationSafetyStateEvidence(
            profile_id=normal.profile_id,
            broker=normal.broker,
            account_id=normal.account_id,
            state_snapshot=normal.state_snapshot,
            reconciliation_state_hash="0" * 64,
        )


def test_resolver_refuses_persisted_reconciliation_scope_or_hash_tampering(
    tmp_path,
) -> None:
    fixture = build_execution_provenance_fixture(tmp_path)
    normal = _normal_safety(fixture)
    mismatched_record = SimpleNamespace(
        profile_id="other-profile",
        broker=normal.broker,
        account=normal.account_id,
        state=normal.state_snapshot.state.value,
        occurred_at=normal.state_snapshot.occurred_at,
        reason=normal.state_snapshot.reason,
        predecessor_hash=normal.state_snapshot.predecessor_hash,
        recovery_approver_id=normal.state_snapshot.recovery_approver_id,
        state_hash=normal.state_snapshot.state_hash,
    )
    mismatched_scope = ReconciliationSafetyStateEvidence.from_persisted_record(
        mismatched_record
    )

    with pytest.raises(
        PortfolioRiskAuthorityError,
        match="PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_SCOPE_MISMATCH",
    ):
        _resolve(fixture, safety=mismatched_scope)

    tampered_record = SimpleNamespace(
        profile_id=normal.profile_id,
        broker=normal.broker,
        account=normal.account_id,
        state=normal.state_snapshot.state.value,
        occurred_at=normal.state_snapshot.occurred_at,
        reason=normal.state_snapshot.reason,
        predecessor_hash=normal.state_snapshot.predecessor_hash,
        recovery_approver_id=normal.state_snapshot.recovery_approver_id,
        state_hash="0" * 64,
    )
    with pytest.raises(
        PortfolioRiskAuthorityError,
        match="PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_TAMPERED",
    ):
        ReconciliationSafetyStateEvidence.from_persisted_record(tampered_record)


def test_authority_record_refuses_a_forged_p3_policy_claim(tmp_path) -> None:
    fixture = build_execution_provenance_fixture(tmp_path)
    authority = _resolve(fixture)
    forged_policy = replace(authority.review_request.policy, policy_id="forged-policy")
    forged_review = replace(authority.review_request, policy=forged_policy)

    with pytest.raises(
        PortfolioRiskAuthorityError,
        match="PORTFOLIO_RISK_AUTHORITY_POLICY_MISMATCH",
    ):
        replace(authority, review_request=forged_review)


def test_resolver_refuses_profile_taxonomy_that_no_longer_covers_the_target_product(
    tmp_path,
) -> None:
    fixture = build_execution_provenance_fixture(tmp_path)
    config = fixture.profile.portfolio_risk_approval
    assert config is not None
    drifted_config = replace(
        config,
        taxonomy=tuple(item for item in config.taxonomy if item.product_id != "rb"),
        ctp_sim_execution_rules=tuple(
            item
            for item in config.ctp_sim_execution_rules
            if item.product_id != "rb"
        ),
    )
    drifted_profile = replace(
        fixture.profile,
        portfolio_risk_approval=drifted_config,
    )

    with pytest.raises(
        PortfolioRiskAuthorityError,
        match="PORTFOLIO_RISK_AUTHORITY_CONTRACT_CONFIG_MISMATCH",
    ):
        _resolve(fixture, profile=drifted_profile)


@pytest.mark.parametrize(
    "claim_kind",
    ("forged-policy", "inflated-account"),
)
def test_preflight_refuses_a_claimed_p3_review_that_differs_from_authority(
    tmp_path,
    claim_kind: str,
) -> None:
    fixture = build_execution_provenance_fixture(tmp_path)
    original_claim = fixture.request.portfolio_risk_approval_request
    original_review = original_claim.review_request
    if claim_kind == "forged-policy":
        claimed_review = replace(
            original_review,
            policy=replace(original_review.policy, authority_id="forged-authority"),
        )
    else:
        claimed_review = replace(
            original_review,
            account_snapshot=replace(
                original_review.account_snapshot,
                equity=9_999_999_999.0,
                margin_capacity=9_999_999_999.0,
            ),
        )
    claimed_request = replace(original_claim, review_request=claimed_review)

    with pytest.raises(
        ExecutionProvenancePreflightError,
        match="PORTFOLIO_RISK_AUTHORITY_CLAIM_MISMATCH",
    ):
        ExecutionProvenancePreflight().verify(
            replace(
                fixture.request,
                portfolio_risk_approval_request=claimed_request,
            )
        )


def test_preflight_refuses_handcrafted_normal_reconciliation_claim(tmp_path) -> None:
    fixture = build_execution_provenance_fixture(tmp_path)
    snapshot = fixture.request.account_snapshot
    handcrafted = RiskStateSnapshot(
        state=RiskState.NORMAL,
        occurred_at=fixture.request.checked_at,
        reason="handcrafted normal is not the persisted source",
    )
    fake_safety = ReconciliationSafetyStateEvidence(
        profile_id=fixture.profile.profile_id,
        broker="ctp_sim",
        account_id=snapshot.account,
        state_snapshot=handcrafted,
        reconciliation_state_hash=handcrafted.state_hash,
    )

    with pytest.raises(
        ExecutionProvenancePreflightError,
        match="PORTFOLIO_RISK_AUTHORITY_REPLAY_MISMATCH",
    ):
        ExecutionProvenancePreflight().verify(
            replace(fixture.request, reconciliation_safety_state=fake_safety)
        )

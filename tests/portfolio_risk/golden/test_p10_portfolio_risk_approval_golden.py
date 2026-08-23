"""Golden regression for P10-WP05 fixture-only P3 approval evidence."""

from __future__ import annotations

import json
from pathlib import Path

from tests.helpers.canonical_portfolio_risk import (
    build_canonical_portfolio_risk_fixture,
)


FIXTURE = Path(__file__).with_name("p10_portfolio_risk_approval_v1.json")


def test_portfolio_risk_approval_matches_the_fixture_only_golden_identity() -> None:
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fixture = build_canonical_portfolio_risk_fixture()
    review = fixture.review
    review_mapping = review.as_mapping()
    approved_target = fixture.approval_evidence.approved_target

    assert expected["format"] == "northstar.p10-portfolio-risk-approval-golden.v1"
    assert expected["fixture_only"] is True
    assert expected["composition_evidence_hash"] == fixture.composition_fixture.evidence.evidence_hash
    assert expected["portfolio_target_hash"] == review.portfolio_target.target_hash
    assert expected["account_snapshot_hash"] == fixture.account_snapshot.snapshot_hash
    assert expected["instrument_snapshot_hashes"] == [
        item.snapshot_hash for item in fixture.instrument_snapshots
    ]
    assert expected["risk_state_evidence_hash"] == fixture.risk_state.evidence_hash
    assert expected["policy_hash"] == fixture.policy.policy_hash
    assert expected["stress_policy_hash"] == fixture.policy.stress_policy.policy_hash
    assert expected["review"]["positions"] == [item.as_mapping() for item in review.positions]
    assert expected["review"]["exposure"] == review.exposure.as_mapping()
    assert expected["review"]["measurements"] == review_mapping["measurements"]
    assert expected["review"]["limit_checks"] == review_mapping["limit_checks"]
    assert expected["review"]["stress_checks"] == review_mapping["stress_checks"]
    assert expected["review"]["observed_risk_state"] == review_mapping["observed_risk_state"]
    assert expected["review"]["status"] == review.status.value
    assert expected["review"]["review_hash"] == review.review_hash
    assert expected["review"]["approval_valid_until"] == review.approval_valid_until.isoformat()
    assert expected["attestation"] == fixture.attestation.as_mapping()
    assert approved_target is not None
    assert expected["approved_target"] == approved_target.as_mapping()
    assert expected["approval_evidence_hash"] == fixture.approval_evidence.evidence_hash
    assert expected["eligible_for_execution"] is False
    assert expected["eligible_for_broker_order"] is False

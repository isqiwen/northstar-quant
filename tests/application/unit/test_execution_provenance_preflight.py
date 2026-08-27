"""P8 provenance replay tests; none of these cases submit an order."""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import timedelta

import pytest

import northstar_quant.application.execution_provenance_preflight as provenance
from northstar_quant.application.execution_provenance_preflight import (
    ExecutionProvenancePreflight,
    ExecutionProvenancePreflightError,
    ExecutionProvenanceRequest,
)
from northstar_quant.application.research_strategy_activation import (
    ResearchStrategyTargetActivator,
)
from northstar_quant.portfolio_risk.limits import RiskLimitSet
from northstar_quant.portfolio_risk.portfolio import (
    PortfolioRiskApprovalEvidence,
    PortfolioRiskApprovalGate,
    PortfolioRiskApprovalRequest,
    PortfolioRiskPolicy,
)
from northstar_quant.portfolio_risk.risk import RiskState, RiskStateSnapshot
from northstar_quant.trading_execution.execution.models import FuturesExecutionRule
from tests.helpers.execution_provenance import (
    ExecutionProvenanceFixture,
    build_execution_provenance_fixture,
)


def _approval_for(
    fixture: ExecutionProvenanceFixture,
    *,
    review_request,
) -> tuple[PortfolioRiskApprovalRequest, PortfolioRiskApprovalEvidence]:
    """Build a new, exactly attested P3 approval result for a review input."""

    gate = PortfolioRiskApprovalGate()
    review = gate.review(review_request)
    approval_request = PortfolioRiskApprovalRequest(
        review_request=review_request,
        attestation=replace(
            fixture.portfolio_risk_approval_request.attestation,
            review_hash=review.review_hash,
        ),
    )
    return approval_request, gate.evaluate(approval_request)


def _rejected_approval(
    fixture: ExecutionProvenanceFixture,
    outcome: str,
) -> tuple[PortfolioRiskApprovalRequest, PortfolioRiskApprovalEvidence]:
    base = fixture.portfolio_risk_approval_request.review_request
    if outcome == "UNKNOWN":
        review_request = replace(
            base,
            account_snapshot=replace(base.account_snapshot, equity=None),
        )
    elif outcome in {"WARN", "BLOCK"}:
        per_contract = 0.125 if outcome == "WARN" else 0.05
        review_request = replace(
            base,
            policy=PortfolioRiskPolicy(
                policy_id=base.policy.policy_id,
                policy_version=base.policy.policy_version,
                authority_id=base.policy.authority_id,
                limits=RiskLimitSet(
                    per_contract,
                    1,
                    1,
                    1,
                    1,
                    1,
                    2,
                    1,
                    0.8,
                ),
                stress_policy=base.policy.stress_policy,
                max_input_age_seconds=base.policy.max_input_age_seconds,
            ),
        )
    elif outcome == "HALT":
        halted = RiskStateSnapshot.initial(occurred_at=base.evaluated_at).transition(
            target=RiskState.HALT,
            occurred_at=base.evaluated_at,
            reason="p8 test halt",
        )
        review_request = replace(
            base,
            risk_state=replace(base.risk_state, state_snapshot=halted),
        )
    else:  # pragma: no cover - parametrization is intentionally closed.
        raise AssertionError(f"unexpected P3 rejection outcome {outcome}")
    return _approval_for(fixture, review_request=review_request)


def test_replays_real_canonical_multi_strategy_risk_approval_and_ctp_sim_preflight_without_trade_authority(
    tmp_path,
) -> None:
    fixture = build_execution_provenance_fixture(tmp_path)
    verifier = ExecutionProvenancePreflight()

    receipt = verifier.verify(fixture.request)
    repeated = verifier.verify(fixture.request)
    approved_target = fixture.portfolio_risk_approval_evidence.approved_target
    assert approved_target is not None

    assert receipt == repeated
    assert receipt.receipt_hash == repeated.receipt_hash
    assert receipt.activation_hashes == tuple(
        sorted(item.activation_hash for item in fixture.activation_receipts)
    )
    assert receipt.portfolio_target_hash == fixture.composition_evidence.portfolio_target.target_hash
    assert receipt.approved_target_hash == approved_target.approval_hash
    assert receipt.composition_evidence_hash == fixture.composition_evidence.evidence_hash
    assert (
        receipt.portfolio_risk_approval_evidence_hash
        == fixture.portfolio_risk_approval_evidence.evidence_hash
    )
    assert receipt.risk_evidence_hash == approved_target.risk_evidence_hash
    assert (
        receipt.portfolio_risk_authority_hash
        == fixture.request.portfolio_risk_authority.authority_hash
    )
    assert (
        receipt.portfolio_risk_policy_hash
        == fixture.request.portfolio_risk_authority.policy_hash
    )
    assert receipt.broker_state_hash == fixture.request.portfolio_risk_authority.broker_state_hash
    assert (
        receipt.reconciliation_state_hash
        == fixture.request.reconciliation_safety_state.reconciliation_state_hash
    )
    assert receipt.plan_id == fixture.request.plan_id
    assert receipt.valid_until == fixture.request.checked_at + timedelta(
        seconds=fixture.settings.runtime_risk_gate_max_age_seconds
    )
    assert len(receipt.order_commitments) == 1
    order = receipt.order_commitments[0]
    assert (order.symbol, order.side, order.qty) == ("RB2610", "BUY", 3.0)
    assert (order.instrument_id, order.exchange_id, order.ctp_offset) == ("rb2610", "SHFE", "open")
    assert order.reference_price == 3100.0
    assert order.required_margin == 9300.0
    assert receipt.eligible_for_ctp_sim is False
    assert receipt.eligible_for_trading is False
    assert receipt.eligible_for_live is False


def test_rejects_activation_sources_that_do_not_exactly_match_canonical_composition(tmp_path) -> None:
    fixture = build_execution_provenance_fixture(tmp_path)
    request = fixture.request
    original = fixture.activation_requests[0]
    proposal = replace(original.target_proposal, target_id="p8-unbound-source-target")
    replacement_request = replace(
        original,
        target_proposal=proposal,
        activation_approval=replace(
            original.activation_approval,
            activation_id="p8-unbound-source-activation",
            target_proposal_hash=proposal.proposal_hash,
        ),
    )
    replacement_receipt = ResearchStrategyTargetActivator().activate(replacement_request)

    with pytest.raises(ExecutionProvenancePreflightError, match="STRATEGY_TARGET_SOURCE_MISMATCH"):
        ExecutionProvenancePreflight().verify(
            replace(
                request,
                activation_requests=(replacement_request, request.activation_requests[1]),
                activation_receipts=(replacement_receipt, request.activation_receipts[1]),
            )
        )


def test_rejects_a_claimed_p3_approval_evidence_result_that_does_not_exactly_replay(tmp_path) -> None:
    fixture = build_execution_provenance_fixture(tmp_path)
    request = fixture.request
    alternate_request = replace(
        fixture.portfolio_risk_approval_request,
        attestation=replace(
            fixture.portfolio_risk_approval_request.attestation,
            approval_id="p8-different-attestation",
        ),
    )
    alternate_evidence = PortfolioRiskApprovalGate().evaluate(alternate_request)

    with pytest.raises(ExecutionProvenancePreflightError, match="PORTFOLIO_RISK_APPROVAL_REPLAY_MISMATCH"):
        ExecutionProvenancePreflight().verify(
            replace(request, portfolio_risk_approval_evidence=alternate_evidence)
        )


@pytest.mark.parametrize(
    ("plan_created_at", "checked_at"),
    (
        ("approval_valid_until", "approval_valid_until"),
        ("one_second_before", "approval_valid_until"),
    ),
)
def test_rejects_plan_or_check_at_or_after_the_p3_approval_validity_horizon(
    tmp_path,
    plan_created_at: str,
    checked_at: str,
) -> None:
    fixture = build_execution_provenance_fixture(tmp_path)
    approval_valid_until = (
        fixture.portfolio_risk_approval_evidence.review.approval_valid_until
    )
    before_horizon = approval_valid_until - timedelta(seconds=1)
    settings = fixture.request.settings.model_copy(
        update={
            "runtime_risk_max_state_age_seconds": 600,
            "runtime_risk_gate_max_age_seconds": 600,
        }
    )

    with pytest.raises(
        ExecutionProvenancePreflightError,
        match="PORTFOLIO_RISK_APPROVAL_VALIDITY_EXPIRED",
    ):
        ExecutionProvenancePreflight().verify(
            replace(
                fixture.request,
                settings=settings,
                plan_created_at=(
                    approval_valid_until
                    if plan_created_at == "approval_valid_until"
                    else before_horizon
                ),
                checked_at=(
                    approval_valid_until
                    if checked_at == "approval_valid_until"
                    else before_horizon
                ),
            )
        )


def test_p8_receipt_validity_is_bounded_by_the_p3_approval_horizon(tmp_path) -> None:
    fixture = build_execution_provenance_fixture(tmp_path)
    settings = fixture.request.settings.model_copy(
        update={"runtime_risk_gate_max_age_seconds": 600}
    )

    receipt = ExecutionProvenancePreflight().verify(
        replace(fixture.request, settings=settings)
    )

    assert (
        receipt.valid_until
        == fixture.portfolio_risk_approval_evidence.review.approval_valid_until
    )


@pytest.mark.parametrize("outcome", ("UNKNOWN", "WARN", "BLOCK", "HALT"))
def test_rejected_canonical_portfolio_risk_evidence_cannot_emit_a_p8_receipt_or_build_a_plan(
    tmp_path,
    monkeypatch,
    outcome: str,
) -> None:
    fixture = build_execution_provenance_fixture(tmp_path)
    approval_request, approval_evidence = _rejected_approval(fixture, outcome)
    assert approval_evidence.approved_target is None

    plan_attempted = False

    def _plan_sentinel(*args, **kwargs):
        nonlocal plan_attempted
        plan_attempted = True
        raise AssertionError("rejected P3 evidence must not reach P5 planning")

    monkeypatch.setattr(provenance, "build_approved_execution_plan", _plan_sentinel)
    with pytest.raises(
        ExecutionProvenancePreflightError,
        match="PORTFOLIO_RISK_AUTHORITY_CLAIM_MISMATCH",
    ):
        ExecutionProvenancePreflight().verify(
            replace(
                fixture.request,
                portfolio_risk_approval_request=approval_request,
                portfolio_risk_approval_evidence=approval_evidence,
            )
        )
    assert plan_attempted is False


def test_rejects_stale_or_untrusted_runtime_quote_evidence(tmp_path) -> None:
    fixture = build_execution_provenance_fixture(tmp_path)
    request = fixture.request
    stale_quote = replace(
        request.quotes[0],
        asof=request.checked_at
        - timedelta(seconds=request.settings.runtime_risk_max_quote_age_seconds + 1),
    )

    with pytest.raises(ExecutionProvenancePreflightError, match="QUOTE_STALE"):
        ExecutionProvenancePreflight().verify(replace(request, quotes=(stale_quote,)))

    untrusted_quote = replace(request.quotes[0], source="broker_snapshot")
    with pytest.raises(ExecutionProvenancePreflightError, match="QUOTE_SOURCE_REFUSED"):
        ExecutionProvenancePreflight().verify(replace(request, quotes=(untrusted_quote,)))


def test_rejects_p5_contract_margin_rules_that_drift_from_the_replayed_p3_review(tmp_path) -> None:
    fixture = build_execution_provenance_fixture(tmp_path)
    request = fixture.request
    mismatched_rule = replace(
        request.contract_rules[0],
        rule=FuturesExecutionRule(margin_rate=0.2, max_position_lots=100),
    )

    with pytest.raises(
        ExecutionProvenancePreflightError,
        match="CONTRACT_AUTHORITY_RULE_EVIDENCE_MISMATCH",
    ):
        ExecutionProvenancePreflight().verify(
            replace(request, contract_rules=(mismatched_rule,))
        )


def test_rejects_p3_review_contract_identity_that_does_not_match_p5_execution_rules(tmp_path) -> None:
    fixture = build_execution_provenance_fixture(tmp_path)
    base = fixture.portfolio_risk_approval_request.review_request
    review_request = replace(
        base,
        instrument_snapshots=(
            replace(base.instrument_snapshots[0], exchange_id="CZCE"),
        ),
    )
    approval_request, approval_evidence = _approval_for(
        fixture,
        review_request=review_request,
    )
    assert approval_evidence.approved_target is not None

    with pytest.raises(
        ExecutionProvenancePreflightError,
        match="PORTFOLIO_RISK_AUTHORITY_CLAIM_MISMATCH",
    ):
        ExecutionProvenancePreflight().verify(
            replace(
                fixture.request,
                portfolio_risk_approval_request=approval_request,
                portfolio_risk_approval_evidence=approval_evidence,
            )
        )


def test_refuses_live_enablement_and_stale_broker_state_before_any_execution_plan(tmp_path) -> None:
    fixture = build_execution_provenance_fixture(tmp_path)
    request = fixture.request
    live_settings = request.settings.model_copy(update={"live_trading_enabled": True})

    with pytest.raises(ExecutionProvenancePreflightError, match="LIVE_TRADING_REFUSED"):
        ExecutionProvenancePreflight().verify(replace(request, settings=live_settings))

    stale_snapshot = replace(
        request.account_snapshot,
        asof=request.checked_at
        - timedelta(seconds=request.settings.runtime_risk_max_state_age_seconds + 1),
    )
    with pytest.raises(ExecutionProvenancePreflightError, match="BROKER_STATE_STALE"):
        ExecutionProvenancePreflight().verify(replace(request, account_snapshot=stale_snapshot))


def test_request_has_no_caller_supplied_target_approval_risk_or_execution_artifacts() -> None:
    request_fields = {item.name for item in fields(ExecutionProvenanceRequest)}

    assert {
        "portfolio_target",
        "approved_target",
        "risk_evidence",
        "execution_plan",
        "preflight_result",
        "broker_order",
    }.isdisjoint(request_fields)
    assert {
        "portfolio_risk_approval_request",
        "portfolio_risk_approval_evidence",
        "portfolio_risk_authority",
        "reconciliation_safety_state",
    }.issubset(request_fields)

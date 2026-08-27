"""P10-WP05 session-backed authority refusal at the P8 candidate boundary."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pytest
from sqlalchemy import select

import northstar_quant.application.ctp_sim_candidate_execution as candidate_execution
import northstar_quant.trading_execution.broker.ctp_sim_broker as ctp_sim_broker
from northstar_quant.application.contract_authority import FuturesContractAuthority
from northstar_quant.application.ctp_sim_candidate_execution import (
    CtpSimCandidateExecutionError,
)
from northstar_quant.application.execution_provenance_preflight import (
    ExecutionContractRuleEvidence,
)
from northstar_quant.application.portfolio_risk_authority import (
    PortfolioRiskAuthorityResolver,
    ReconciliationSafetyStateEvidence,
)
from northstar_quant.application.research_strategy_activation import (
    ResearchStrategyTargetActivator,
    StrategyTargetProposal,
)
from northstar_quant.foundation.db.models import (
    ExecutionPlanRecord,
    ExecutionProvenanceConsumptionRecord,
    OrderRecord,
)
from northstar_quant.foundation.db.repositories import latest_reconciliation_safety_state
from northstar_quant.portfolio_risk.portfolio import (
    CanonicalPortfolioComposer,
    PortfolioCompositionRequest,
    PortfolioRiskApprovalGate,
    PortfolioRiskApprovalRequest,
    PortfolioRiskReviewStatus,
    RiskApprovalAttestation,
    TargetPosition,
)
from northstar_quant.portfolio_risk.risk import RiskState, RiskStateSnapshot
from northstar_quant.trading_execution.broker.ctp_sim_broker import CtpSimBrokerAdapter
from northstar_quant.trading_execution.execution.models import (
    FuturesExecutionRule,
    MarketQuoteSnapshot,
    OrderRequest,
)
from northstar_quant.trading_execution.orders.durable_submission import (
    DurableBrokerAdapter,
)
from northstar_quant.trading_execution.reconciliation.reconciliation import (
    reconcile_broker_state,
)
from tests.helpers.ctp_sim_submission import create_test_ctp_sim_submission_authority
from tests.helpers.ctp_sim_candidate_execution import (
    create_test_ctp_sim_candidate_executor,
)
from tests.helpers.contract_authority import build_test_futures_contract_authority
from tests.helpers.execution_provenance import build_execution_provenance_fixture
from tests.helpers.manual_risk_approval import (
    create_test_portfolio_risk_approval_issuer,
)


def _resolve_test_contract_authority(
    authority_id: str,
    broker: str,
    decision_at: datetime,
) -> FuturesContractAuthority:
    return build_test_futures_contract_authority(
        authority_id=authority_id,
        broker=broker,
        decision_at=decision_at,
    )


def _with_persisted_manual_risk_approval(
    session,
    request,
    *,
    approval_id: str,
):
    """Seed the test-only external approval boundary for one exact P3 claim."""

    issued = create_test_portfolio_risk_approval_issuer().issue(
        session,
        profile=request.profile,
        broker="ctp_sim",
        account=request.account_snapshot.account,
        authority=request.portfolio_risk_authority,
        composition=request.portfolio_risk_approval_request.review_request.composition,
        approval_id=approval_id,
        checked_at=request.checked_at,
    )
    return replace(
        request,
        portfolio_risk_approval_request=issued.approval_request,
        portfolio_risk_approval_evidence=issued.approval_evidence,
    )


def _authority_bound_candidate(tmp_path, monkeypatch, postgresql_session_factory):
    """Return a candidate whose P3 inputs bind the freshly persisted P5 state."""

    bootstrap = build_execution_provenance_fixture(tmp_path / "bootstrap")
    now = {"value": bootstrap.request.checked_at}
    settings = bootstrap.settings.model_copy(
        update={
            "broker": "ctp_sim",
            "live_trading_enabled": False,
            "kill_switch_enabled": False,
            "storage_dir": tmp_path / "storage",
            "downloads_dir": tmp_path / "storage" / "downloads",
            "reports_dir": tmp_path / "reports",
            "log_dir": tmp_path / "logs",
            "ctp_sim_account": "ctp-sim-test",
            "default_cash": 100_000.0,
        }
    )
    monkeypatch.setattr(ctp_sim_broker, "get_settings", lambda: settings)
    monkeypatch.setattr(ctp_sim_broker, "utc_now", lambda: now["value"])
    executor = create_test_ctp_sim_candidate_executor(
        settings_provider=lambda: settings,
        clock=lambda: now["value"],
        contract_authority_resolver=_resolve_test_contract_authority,
        session_factory=postgresql_session_factory,
    )
    broker = executor.create_broker(
        profile=bootstrap.profile,
        decision_at=bootstrap.request.market_snapshot_at,
    )
    broker.connect()
    broker.seed_market_quotes({"RB2610": 3_100.0}, asof=now["value"])
    snapshot = broker.read_state_snapshot()
    with postgresql_session_factory() as session:
        reconcile_broker_state(
            session,
            broker,
            snapshot=snapshot,
            run_id="p10-authority-bound-bootstrap",
            profile_id=bootstrap.profile.profile_id,
        )
        row = latest_reconciliation_safety_state(
            session,
            profile_id=bootstrap.profile.profile_id,
            broker="ctp_sim",
            account=broker.get_account(),
        )
        assert row is not None
        safety = ReconciliationSafetyStateEvidence.from_persisted_record(row)
    fixture = build_execution_provenance_fixture(
        tmp_path / "authority-bound",
        broker_state=snapshot,
        reconciliation_safety_state=safety,
        reviewed_at=now["value"],
    )
    return executor, broker, fixture, snapshot, safety


def _two_order_request(
    fixture,
    *,
    broker_state,
    reconciliation_safety_state: ReconciliationSafetyStateEvidence,
):
    """Build an exact P2-to-P3 request whose one batch has RB and SA legs."""

    base_request = fixture.activation_requests[0]
    base_proposal = base_request.target_proposal
    proposal = StrategyTargetProposal(
        target_id="p10-two-leg-target",
        source_strategy_id=base_proposal.source_strategy_id,
        source_strategy_version=base_proposal.source_strategy_version,
        generated_at=base_proposal.generated_at,
        effective_at=base_proposal.effective_at,
        expires_at=base_proposal.expires_at,
        positions=(
            TargetPosition("RB2610", 0.1),
            TargetPosition("SA2609", 0.1),
        ),
    )
    activation_request = replace(
        base_request,
        target_proposal=proposal,
        activation_approval=replace(
            base_request.activation_approval,
            activation_id="p10-two-leg-activation",
            target_proposal_hash=proposal.proposal_hash,
        ),
    )
    activation_receipt = ResearchStrategyTargetActivator().activate(activation_request)
    activation_requests = (activation_request, fixture.activation_requests[1])
    activation_receipts = (activation_receipt, fixture.activation_receipts[1])
    composition_request = PortfolioCompositionRequest(
        target_id="p10-two-leg-portfolio",
        generated_at=fixture.composition_evidence.request.generated_at,
        effective_at=fixture.composition_evidence.request.effective_at,
        expires_at=fixture.composition_evidence.request.expires_at,
        allocation_policy=fixture.composition_evidence.request.allocation_policy,
        allocation_inputs=tuple(
            replace(allocation, strategy_target=receipt.strategy_target)
            for allocation, receipt in zip(
                fixture.composition_evidence.request.allocation_inputs,
                activation_receipts,
                strict=True,
            )
        ),
    )
    composition = CanonicalPortfolioComposer().compose(composition_request)
    authority = PortfolioRiskAuthorityResolver().resolve(
        profile=fixture.profile,
        broker_state=broker_state,
        reconciliation_safety_state=reconciliation_safety_state,
        composition=composition,
        evaluated_at=composition_request.effective_at,
        contract_authority=fixture.contract_authority,
    )
    risk_gate = PortfolioRiskApprovalGate()
    review = risk_gate.review(authority.review_request)
    approval_request = PortfolioRiskApprovalRequest(
        review_request=authority.review_request,
        attestation=RiskApprovalAttestation(
            approval_id="p10-two-leg-risk-approval",
            review_hash=review.review_hash,
            approver_id="risk-owner",
            approved_at=authority.review_request.evaluated_at,
            rationale="two-leg candidate review approved for the isolated simulator",
        ),
    )
    approval_evidence = risk_gate.evaluate(approval_request)
    assert approval_evidence.approved_target is not None
    source_rule = fixture.request.contract_rules[0]
    contract_rules = tuple(
        ExecutionContractRuleEvidence(
            symbol=rule.symbol,
            instrument_id=rule.instrument_id,
            exchange_id=rule.exchange_id,
            volume_multiple=rule.volume_multiple,
            rule=FuturesExecutionRule(
                margin_rate=rule.margin_rate,
                max_position_lots=rule.max_position_lots,
            ),
            available_at=source_rule.available_at,
            effective_at=source_rule.effective_at,
            expires_at=source_rule.expires_at,
        )
        for rule in authority.execution_rules
    )
    checked_at = fixture.request.checked_at
    return replace(
        fixture.request,
        preflight_id="p10-two-leg-preflight",
        activation_requests=activation_requests,
        activation_receipts=activation_receipts,
        portfolio_risk_approval_request=approval_request,
        portfolio_risk_approval_evidence=approval_evidence,
        portfolio_risk_authority=authority,
        reconciliation_safety_state=reconciliation_safety_state,
        quotes=(
            fixture.request.quotes[0],
            MarketQuoteSnapshot(
                symbol="SA2609",
                bid=1399.0,
                ask=1401.0,
                last=1400.0,
                market_price=1400.0,
                asof=checked_at,
                source="ctp_sim_market_data",
            ),
        ),
        contract_rules=contract_rules,
        plan_id="p10-two-leg-plan",
    )


def _assert_no_candidate_execution_side_effects(
    session,
    broker,
    state_before,
) -> None:
    """P5 may persist reconciliation facts; rejection mints no P8 intent."""

    assert session.scalar(select(ExecutionPlanRecord)) is None
    assert session.scalar(select(ExecutionProvenanceConsumptionRecord)) is None
    assert session.scalar(select(OrderRecord)) is None
    assert broker.simulator_state_evidence() == state_before
    state = broker.read_state_snapshot()
    assert state.open_orders == []
    assert state.completed_orders == []
    assert state.fills == []


def _external_drift_order(*, profile_id: str, account: str) -> OrderRequest:
    """Construct a test-only simulator mutation that has no candidate receipt."""

    return OrderRequest(
        strategy_id="p10-external-drift",
        symbol="RB2610",
        side="BUY",
        qty=1.0,
        profile_id=profile_id,
        account=account,
        order_semantic="OPEN",
        reason="test external simulator drift",
        reference_price=3100.0,
        reference_price_source="test",
        run_id="p10-external-drift-run",
        batch_id="p10-external-drift-batch",
        plan_id="p10-external-drift-plan",
        ctp_offset="OPEN",
        margin_rate=0.1,
        currency="CNY",
    )


@pytest.mark.parametrize(
    ("claim_kind", "error_code"),
    (
        (
            "handcrafted-normal",
            "PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_MISMATCH",
        ),
        (
            "forged-authority",
            "PERSISTED_PORTFOLIO_RISK_APPROVAL_AUTHORITY_MISMATCH",
        ),
    ),
)
def test_candidate_refuses_untrusted_safety_or_authority_without_side_effects(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
    claim_kind: str,
    error_code: str,
) -> None:
    """Constructor-valid claims are not substitutes for fresh P5 source facts."""

    executor, broker, fixture, snapshot, safety = _authority_bound_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        if claim_kind == "handcrafted-normal":
            handcrafted_state = RiskStateSnapshot(
                state=RiskState.NORMAL,
                occurred_at=fixture.request.checked_at,
                reason="handcrafted normal is not a persisted reconciliation fact",
            )
            rejected_request = replace(
                fixture.request,
                reconciliation_safety_state=ReconciliationSafetyStateEvidence(
                    profile_id=fixture.profile.profile_id,
                    broker="ctp_sim",
                    account_id=broker.get_account(),
                    state_snapshot=handcrafted_state,
                    reconciliation_state_hash=handcrafted_state.state_hash,
                ),
            )
        else:
            forged_snapshot = replace(
                snapshot,
                account_values={
                    **snapshot.account_values,
                    "NetLiquidation": 9_999_999.0,
                    "AvailableFunds": 9_999_999.0,
                },
            )
            forged_authority = PortfolioRiskAuthorityResolver().resolve(
                profile=fixture.profile,
                broker_state=forged_snapshot,
                reconciliation_safety_state=safety,
                composition=fixture.composition_evidence,
                evaluated_at=fixture.request.checked_at,
                contract_authority=fixture.contract_authority,
            )
            rejected_request = replace(
                fixture.request,
                portfolio_risk_authority=forged_authority,
            )

        plan_persistence_attempted = False
        order_assembly_attempted = False

        def _plan_persistence_sentinel(*_args, **_kwargs) -> None:
            nonlocal plan_persistence_attempted
            plan_persistence_attempted = True
            raise AssertionError(
                "a rejected authority claim cannot persist an execution plan"
            )

        def _order_assembly_sentinel(*_args, **_kwargs) -> None:
            nonlocal order_assembly_attempted
            order_assembly_attempted = True
            raise AssertionError(
                "a rejected authority claim cannot assemble a candidate order"
            )

        monkeypatch.setattr(
            candidate_execution,
            "save_execution_plan_records",
            _plan_persistence_sentinel,
        )
        monkeypatch.setattr(broker, "prepare_order", _order_assembly_sentinel)
        state_before = broker.simulator_state_evidence()
        bundle = None
        with postgresql_session_factory() as session:
            with pytest.raises(CtpSimCandidateExecutionError, match=error_code):
                bundle = executor.prepare(
                    rejected_request,
                    session=session,
                    broker=broker,
                    run_id=f"p10-{claim_kind}-run",
                    batch_id=f"p10-{claim_kind}-batch",
                )
            assert bundle is None
            assert plan_persistence_attempted is False
            assert order_assembly_attempted is False
            _assert_no_candidate_execution_side_effects(session, broker, state_before)
    finally:
        broker.disconnect()


def test_authority_bound_p3_block_cannot_reach_candidate_plan_intent_or_simulator(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """An exact P3 BLOCK must reach the gate, then fail before any execution work."""

    executor, broker, fixture, snapshot, safety = _authority_bound_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        original_config = fixture.profile.portfolio_risk_approval
        assert original_config is not None
        strict_profile = replace(
            fixture.profile,
            portfolio_risk_approval=replace(
                original_config,
                policy_id="p10-strict-per-contract",
                limits=replace(original_config.limits, per_contract=0.0),
            ),
        )
        strict_authority = PortfolioRiskAuthorityResolver().resolve(
            profile=strict_profile,
            broker_state=snapshot,
            reconciliation_safety_state=safety,
            composition=fixture.composition_evidence,
            evaluated_at=fixture.request.checked_at,
            contract_authority=fixture.contract_authority,
        )
        risk_gate = PortfolioRiskApprovalGate()
        review = risk_gate.review(strict_authority.review_request)
        assert review.status is PortfolioRiskReviewStatus.BLOCK
        assert any(
            check.limit_id == "per_contract" and check.status.value == "BLOCK"
            for check in review.limit_checks
        )
        approval_request = PortfolioRiskApprovalRequest(
            review_request=strict_authority.review_request,
            attestation=RiskApprovalAttestation(
                approval_id="p10-blocked-risk-approval",
                review_hash=review.review_hash,
                approver_id="risk-owner",
                approved_at=fixture.request.checked_at,
                rationale=(
                    "attestation records the blocked review without granting a target"
                ),
            ),
        )
        approval_evidence = risk_gate.evaluate(approval_request)
        assert approval_evidence.review == review
        assert approval_evidence.approved_target is None
        assert approval_evidence.rejection_reasons == ("REVIEW_BLOCK",)
        rejected_request = replace(
            fixture.request,
            profile=strict_profile,
            portfolio_risk_authority=strict_authority,
            portfolio_risk_approval_request=approval_request,
            portfolio_risk_approval_evidence=approval_evidence,
        )
        # This test's strict profile is deliberately the active policy source,
        # not a caller-owned substitute for the configured profile.
        monkeypatch.setattr(
            candidate_execution,
            "load_trading_profile_uncached",
            lambda *_args, **_kwargs: strict_profile,
        )

        plan_persistence_attempted = False
        order_assembly_attempted = False
        broker_submission_attempted = False

        def _plan_persistence_sentinel(*_args, **_kwargs) -> None:
            nonlocal plan_persistence_attempted
            plan_persistence_attempted = True
            raise AssertionError("a P3 BLOCK cannot persist an execution plan")

        def _order_assembly_sentinel(*_args, **_kwargs) -> None:
            nonlocal order_assembly_attempted
            order_assembly_attempted = True
            raise AssertionError("a P3 BLOCK cannot assemble a candidate order")

        def _broker_submission_sentinel(*_args, **_kwargs) -> None:
            nonlocal broker_submission_attempted
            broker_submission_attempted = True
            raise AssertionError("a P3 BLOCK cannot submit a broker order")

        monkeypatch.setattr(
            candidate_execution,
            "save_execution_plan_records",
            _plan_persistence_sentinel,
        )
        monkeypatch.setattr(broker, "prepare_order", _order_assembly_sentinel)
        monkeypatch.setattr(broker, "submit_order", _broker_submission_sentinel)
        state_before = broker.simulator_state_evidence()
        bundle = None
        with postgresql_session_factory() as session:
            with pytest.raises(
                CtpSimCandidateExecutionError,
                match="MANUAL_RISK_APPROVAL_P3_REFUSED",
            ):
                bundle = executor.prepare(
                    rejected_request,
                    session=session,
                    broker=broker,
                    run_id="p10-authority-bound-p3-block-run",
                    batch_id="p10-authority-bound-p3-block-batch",
                )
            assert bundle is None
            assert plan_persistence_attempted is False
            assert order_assembly_attempted is False
            assert broker_submission_attempted is False
            _assert_no_candidate_execution_side_effects(session, broker, state_before)
    finally:
        broker.disconnect()


def test_external_drift_after_first_candidate_leg_blocks_the_second_leg(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """Only the gate's own post-submit snapshot may advance a candidate batch."""

    executor, broker, fixture, _snapshot, safety = _authority_bound_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    external_broker = None
    try:
        broker.seed_market_quotes({"SA2609": 1_400.0}, asof=fixture.request.checked_at)
        snapshot = broker.read_state_snapshot()
        request = _two_order_request(
            fixture,
            broker_state=snapshot,
            reconciliation_safety_state=safety,
        )
        with postgresql_session_factory() as session:
            request = _with_persisted_manual_risk_approval(
                session,
                request,
                approval_id="p10-two-leg-durable-manual-approval",
            )
            bundle = executor.prepare(
                request,
                session=session,
                broker=broker,
                run_id="p10-two-leg-run",
                batch_id="p10-two-leg-batch",
            )
        assert len(bundle.orders) == 2

        external_broker = CtpSimBrokerAdapter(
            registry=broker.registry,
            registry_publication_hash=broker.registry_publication_hash,
            account=broker.get_account(),
            submission_authority=create_test_ctp_sim_submission_authority(),
            session_factory=postgresql_session_factory,
        )
        external_broker.connect()
        external_order = external_broker.prepare_order(
            _external_drift_order(
                profile_id=fixture.profile.profile_id,
                account=broker.get_account(),
            )
        )
        injected = []
        original_submit = DurableBrokerAdapter.submit_order

        def _submit_then_inject_external_drift(adapter, order):
            result = original_submit(adapter, order)
            if adapter.delegate is broker and not injected:
                injected.append(external_broker.submit_order(external_order))
            return result

        monkeypatch.setattr(
            DurableBrokerAdapter,
            "submit_order",
            _submit_then_inject_external_drift,
        )
        with postgresql_session_factory() as session:
            with pytest.raises(
                CtpSimCandidateExecutionError,
                match="CTP_SIM_CANDIDATE_BROKER_STATE_CHANGED",
            ):
                bundle.submit(session)
            candidate_orders = session.scalars(
                select(OrderRecord)
                .where(OrderRecord.plan_id.in_([plan.plan_id for plan in bundle.plans]))
                .order_by(OrderRecord.order_ref)
            ).all()

        assert len(injected) == 1 and injected[0].accepted is True
        assert len(candidate_orders) == 1
        assert candidate_orders[0].order_ref == bundle.orders[0].order_ref
        state = broker.read_state_snapshot()
        observed_refs = {
            str(row.get("order_ref") or "")
            for row in [*state.open_orders, *state.completed_orders]
        }
        assert bundle.orders[0].order_ref in observed_refs
        assert bundle.orders[1].order_ref not in observed_refs
        assert external_order.order_ref in observed_refs
    finally:
        if external_broker is not None:
            external_broker.disconnect()
        broker.disconnect()

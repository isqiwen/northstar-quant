"""P8-WP05 candidate provenance → CTP-sim → reconciliation workflow."""

from __future__ import annotations

from dataclasses import replace

from sqlalchemy import select

import northstar_quant.trading_execution.broker.ctp_sim_broker as ctp_sim_broker
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
    FillRecord,
    OrderRecord,
    PositionSnapshotRecord,
)
from northstar_quant.foundation.db.repositories import latest_reconciliation_safety_state
from northstar_quant.portfolio_risk.portfolio import (
    CanonicalPortfolioComposer,
    PortfolioCompositionRequest,
    PortfolioRiskApprovalGate,
    PortfolioRiskApprovalRequest,
    RiskApprovalAttestation,
    TargetPosition,
)
from northstar_quant.trading_execution.execution.models import (
    FuturesExecutionRule,
    MarketQuoteSnapshot,
)
from northstar_quant.trading_execution.reconciliation.reconciliation import (
    reconcile_broker_state,
)
from tests.helpers.execution_provenance import build_execution_provenance_fixture
from tests.helpers.ctp_sim_candidate_execution import (
    create_test_ctp_sim_candidate_executor,
)
from tests.helpers.manual_risk_approval import (
    create_test_portfolio_risk_approval_issuer,
)


def _persisted_normal_safety_state(
    session,
    *,
    broker,
    profile_id: str,
    snapshot,
    run_id: str,
) -> ReconciliationSafetyStateEvidence:
    reconcile_broker_state(
        session,
        broker,
        snapshot=snapshot,
        run_id=run_id,
        profile_id=profile_id,
    )
    row = latest_reconciliation_safety_state(
        session,
        profile_id=profile_id,
        broker="ctp_sim",
        account=broker.get_account(),
    )
    assert row is not None
    evidence = ReconciliationSafetyStateEvidence.from_persisted_record(row)
    # The durable NORMAL fact is committed; do not pass this helper's SELECT
    # transaction into the candidate clean-session boundary.
    session.rollback()
    return evidence


def _with_persisted_manual_risk_approval(
    session,
    request,
    *,
    approval_id: str,
):
    """Issue only a test-composition grant for this exact rebuilt P3 claim."""

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


def _two_order_candidate_request(
    fixture,
    *,
    broker_state,
    reconciliation_safety_state: ReconciliationSafetyStateEvidence,
):
    """Build a real P2→P3 candidate whose approved target has two contracts."""

    base_request = fixture.activation_requests[0]
    base_proposal = base_request.target_proposal
    proposal = StrategyTargetProposal(
        target_id="p8-multi-contract-target",
        source_strategy_id=base_proposal.source_strategy_id,
        source_strategy_version=base_proposal.source_strategy_version,
        generated_at=base_proposal.generated_at,
        effective_at=base_proposal.effective_at,
        expires_at=base_proposal.expires_at,
        positions=(
            TargetPosition("RB2610", 0.1),
            TargetPosition("SA609", 0.1),
        ),
    )
    activation_request = replace(
        base_request,
        target_proposal=proposal,
        activation_approval=replace(
            base_request.activation_approval,
            activation_id="p8-multi-contract-activation",
            target_proposal_hash=proposal.proposal_hash,
        ),
    )
    activation_receipt = ResearchStrategyTargetActivator().activate(activation_request)
    activation_requests = (activation_request, fixture.activation_requests[1])
    activation_receipts = (activation_receipt, fixture.activation_receipts[1])
    composition_request = PortfolioCompositionRequest(
        target_id="p8-multi-contract-portfolio",
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
    composition_evidence = CanonicalPortfolioComposer().compose(composition_request)
    authority = PortfolioRiskAuthorityResolver().resolve(
        profile=fixture.profile,
        broker_state=broker_state,
        reconciliation_safety_state=reconciliation_safety_state,
        composition=composition_evidence,
        evaluated_at=composition_request.effective_at,
    )
    gate = PortfolioRiskApprovalGate()
    review = gate.review(authority.review_request)
    approval_request = PortfolioRiskApprovalRequest(
        review_request=authority.review_request,
        attestation=RiskApprovalAttestation(
            approval_id="p8-multi-contract-risk-approval",
            review_hash=review.review_hash,
            approver_id="risk-owner",
            approved_at=authority.review_request.evaluated_at,
            rationale="canonical multi-contract portfolio review approved",
        ),
    )
    approval_evidence = gate.evaluate(approval_request)
    assert approval_evidence.approved_target is not None
    checked_at = fixture.request.checked_at
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
    request = replace(
        fixture.request,
        preflight_id="p8-multi-contract-preflight",
        activation_requests=activation_requests,
        activation_receipts=activation_receipts,
        portfolio_risk_approval_request=approval_request,
        portfolio_risk_approval_evidence=approval_evidence,
        portfolio_risk_authority=authority,
        reconciliation_safety_state=reconciliation_safety_state,
        quotes=(
            fixture.request.quotes[0],
            MarketQuoteSnapshot(
                symbol="SA609",
                bid=1399.0,
                ask=1401.0,
                last=1400.0,
                market_price=1400.0,
                asof=checked_at,
                source="ctp_sim_market_data",
            ),
        ),
        contract_rules=contract_rules,
        plan_id="p8-multi-contract-plan",
    )
    return fixture, request


def test_candidate_provenance_to_guarded_ctp_sim_fill_and_reconciliation_e2e(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """No handwritten target, preflight, order, or receipt can enter this path."""

    bootstrap = build_execution_provenance_fixture(tmp_path / "bootstrap")
    checked_at = bootstrap.request.checked_at
    settings = bootstrap.settings.model_copy(
        update={
            "broker": "ctp_sim",
            "live_trading_enabled": False,
            "kill_switch_enabled": False,
            "storage_dir": tmp_path / "storage",
            "downloads_dir": tmp_path / "storage" / "downloads",
            "reports_dir": tmp_path / "reports",
            "log_dir": tmp_path / "logs",
            "ctp_sim_state_path": tmp_path / "storage" / "ctp-sim-state.json",
            "ctp_sim_account": "ctp-sim-test",
            "default_cash": 100_000.0,
        }
    )
    monkeypatch.setattr(ctp_sim_broker, "get_settings", lambda: settings)
    monkeypatch.setattr(ctp_sim_broker, "utc_now", lambda: checked_at)
    executor = create_test_ctp_sim_candidate_executor(
        settings_provider=lambda: settings,
        clock=lambda: checked_at,
    )
    broker = executor.create_broker()
    broker.connect()
    try:
        broker.seed_market_quotes({"RB2610": 3_100.0}, asof=checked_at)
        with postgresql_session_factory() as session:
            snapshot = broker.read_state_snapshot()
            safety = _persisted_normal_safety_state(
                session,
                broker=broker,
                profile_id=bootstrap.profile.profile_id,
                snapshot=snapshot,
                run_id="ctp-sim-candidate-e2e-bootstrap",
            )
            fixture = build_execution_provenance_fixture(
                tmp_path / "authority-bound",
                broker_state=snapshot,
                reconciliation_safety_state=safety,
            )
            request = _with_persisted_manual_risk_approval(
                session,
                fixture.request,
                approval_id="ctp-sim-candidate-e2e-manual-approval",
            )
            bundle = executor.prepare(
                request,
                session=session,
                broker=broker,
                run_id="ctp-sim-candidate-e2e-run",
                batch_id="ctp-sim-candidate-e2e-batch",
            )

        assert bundle.receipt.eligible_for_ctp_sim is False
        assert bundle.receipt.eligible_for_trading is False
        assert bundle.receipt.eligible_for_live is False
        assert len(bundle.orders) == 1

        with postgresql_session_factory() as session:
            submitted = bundle.submit(session)
            reconciliation, evidence = bundle.reconcile(session)
            order_row = session.scalar(
                select(OrderRecord).where(
                    OrderRecord.plan_id == bundle.plans[0].plan_id
                )
            )
            consumption_row = session.scalar(
                select(ExecutionProvenanceConsumptionRecord).where(
                    ExecutionProvenanceConsumptionRecord.plan_hash
                    == bundle.receipt.plan_hash
                )
            )
            fill_row = session.scalar(select(FillRecord))
            position_row = session.scalar(
                select(PositionSnapshotRecord)
                .where(PositionSnapshotRecord.instrument_id == "rb2610")
                .order_by(PositionSnapshotRecord.id.desc())
            )
            plan_row = session.scalar(
                select(ExecutionPlanRecord).where(
                    ExecutionPlanRecord.plan_id == bundle.plans[0].plan_id
                )
            )

        assert len(submitted) == 1 and submitted[0].accepted is True
        assert reconciliation["fills_synced"] == 1
        assert consumption_row is not None
        assert consumption_row.receipt_hash == bundle.receipt.receipt_hash
        assert consumption_row.order_hash == bundle.receipt.order_commitments[0].order_hash
        assert order_row is not None and order_row.status == "Filled"
        assert fill_row is not None and fill_row.order_id == order_row.id
        assert position_row is not None and position_row.long_today_qty == 3.0
        assert plan_row is not None and plan_row.plan_id == bundle.plans[0].plan_id
        assert evidence.consumption_order_hashes == (
            bundle.receipt.order_commitments[0].order_hash,
        )
        assert len(evidence.observed_fill_exec_ids) == 1
    finally:
        broker.disconnect()


def test_candidate_ctp_sim_batch_revalidates_each_leg_after_prior_fill(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """A two-contract candidate batch progresses only on its own authorized state."""

    bootstrap = build_execution_provenance_fixture(tmp_path / "bootstrap")
    checked_at = bootstrap.request.checked_at
    settings = bootstrap.settings.model_copy(
        update={
            "broker": "ctp_sim",
            "live_trading_enabled": False,
            "kill_switch_enabled": False,
            "storage_dir": tmp_path / "storage",
            "downloads_dir": tmp_path / "storage" / "downloads",
            "reports_dir": tmp_path / "reports",
            "log_dir": tmp_path / "logs",
            "ctp_sim_state_path": tmp_path / "storage" / "ctp-sim-multi-state.json",
            "ctp_sim_account": "ctp-sim-test",
            "default_cash": 100_000.0,
        }
    )
    monkeypatch.setattr(ctp_sim_broker, "get_settings", lambda: settings)
    monkeypatch.setattr(ctp_sim_broker, "utc_now", lambda: checked_at)
    executor = create_test_ctp_sim_candidate_executor(
        settings_provider=lambda: settings,
        clock=lambda: checked_at,
    )
    broker = executor.create_broker()
    broker.connect()
    try:
        broker.seed_market_quotes(
            {"RB2610": 3_100.0, "SA609": 1_400.0},
            asof=checked_at,
        )
        with postgresql_session_factory() as session:
            snapshot = broker.read_state_snapshot()
            safety = _persisted_normal_safety_state(
                session,
                broker=broker,
                profile_id=bootstrap.profile.profile_id,
                snapshot=snapshot,
                run_id="ctp-sim-candidate-multi-e2e-bootstrap",
            )
            fixture = build_execution_provenance_fixture(
                tmp_path / "authority-bound",
                broker_state=snapshot,
                reconciliation_safety_state=safety,
            )
            _fixture, request = _two_order_candidate_request(
                fixture,
                broker_state=snapshot,
                reconciliation_safety_state=safety,
            )
            request = _with_persisted_manual_risk_approval(
                session,
                request,
                approval_id="ctp-sim-candidate-multi-e2e-manual-approval",
            )
            bundle = executor.prepare(
                request,
                session=session,
                broker=broker,
                run_id="ctp-sim-candidate-multi-e2e-run",
                batch_id="ctp-sim-candidate-multi-e2e-batch",
            )
        assert len(bundle.orders) == 2

        with postgresql_session_factory() as session:
            submitted = bundle.submit(session)
            reconciliation, evidence = bundle.reconcile(session)
            consumptions = session.scalars(
                select(ExecutionProvenanceConsumptionRecord)
                .where(
                    ExecutionProvenanceConsumptionRecord.plan_hash
                    == bundle.receipt.plan_hash
                )
                .order_by(ExecutionProvenanceConsumptionRecord.order_ref)
            ).all()
            orders = session.scalars(
                select(OrderRecord)
                .where(OrderRecord.plan_id.in_([plan.plan_id for plan in bundle.plans]))
                .order_by(OrderRecord.order_ref)
            ).all()

        assert len(submitted) == 2
        assert all(item.accepted for item in submitted)
        assert reconciliation["fills_synced"] == 2
        assert len(consumptions) == 2
        assert len({item.order_ref for item in consumptions}) == 2
        assert all(item.receipt_hash == bundle.receipt.receipt_hash for item in consumptions)
        assert len(orders) == 2
        assert {item.status for item in orders} == {"Filled"}
        assert len(evidence.consumption_order_hashes) == 2
        assert len(evidence.observed_fill_exec_ids) == 2
    finally:
        broker.disconnect()

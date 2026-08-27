"""P8 deterministic evidence-matrix acceptance scenarios.

This is deliberately not a Research-to-Order path.  The
``INTELLIGENCE_TO_RESEARCH`` seam below is built through the real P4 → P1 →
P2 test path; the other unimplemented production seams stay explicitly
``BLOCKED`` rather than being inferred from independent lane results.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

import northstar_quant.trading_execution.broker.ctp_sim_broker as ctp_sim_broker
from northstar_quant.application.candidate_acceptance import (
    CandidateAcceptanceRequest,
    CandidateAcceptanceState,
    CandidateAcceptanceVerifier,
    CandidateEnvironment,
    CandidateEvidenceLane,
    CandidateEvidenceStatus,
    CandidateLaneEvidence,
    CandidateSeam,
    CandidateSeamEvidence,
)
from northstar_quant.application.contract_authority import FuturesContractAuthority
from northstar_quant.application.portfolio_risk_authority import (
    ReconciliationSafetyStateEvidence,
)
from northstar_quant.data.artifacts.fingerprints import (
    canonical_json_sha256,
)
from northstar_quant.foundation.db.models import (
    ExecutionProvenanceConsumptionRecord,
    FillRecord,
    OrderRecord,
)
from northstar_quant.foundation.db.repositories import latest_reconciliation_safety_state
from northstar_quant.application.research_strategy_activation import (
    HumanStrategyTargetActivationApproval,
    ResearchStrategyActivationRequest,
    ResearchStrategyTargetActivator,
    StrategyTargetProposal,
)
from northstar_quant.data.market.pit import MarketDataPITSelector, MarketDataPITSpec
from northstar_quant.portfolio_risk.portfolio import TargetPosition
from northstar_quant.research.features import FeatureRegistry, register_canonical_feature
from northstar_quant.research.features.intelligence import EVENT_CONFIDENCE, INTELLIGENCE_EVENT_INPUT
from northstar_quant.trading_execution.reconciliation.reconciliation import (
    reconcile_broker_state,
)
from tests.helpers.intelligence_feature_projection import (
    publish_authorized_intelligence_feature_projection_fixture,
)
from tests.helpers.research_candidate import build_research_candidate_chain
from tests.helpers.execution_provenance import build_execution_provenance_fixture
from tests.helpers.ctp_sim_candidate_execution import (
    create_test_ctp_sim_candidate_executor,
)
from tests.helpers.contract_authority import build_test_futures_contract_authority
from tests.helpers.manual_risk_approval import (
    create_test_portfolio_risk_approval_issuer,
)


_AS_OF = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
_SEAM_ENDPOINTS = {
    CandidateSeam.DATA_PIT_TO_RESEARCH: (
        CandidateEvidenceLane.DATA_PIT,
        CandidateEvidenceLane.RESEARCH,
    ),
    CandidateSeam.INTELLIGENCE_TO_RESEARCH: (
        CandidateEvidenceLane.INTELLIGENCE,
        CandidateEvidenceLane.RESEARCH,
    ),
    CandidateSeam.RESEARCH_TO_PORTFOLIO_RISK: (
        CandidateEvidenceLane.RESEARCH,
        CandidateEvidenceLane.PORTFOLIO_RISK,
    ),
    CandidateSeam.PORTFOLIO_RISK_TO_EXECUTION_SIMULATION: (
        CandidateEvidenceLane.PORTFOLIO_RISK,
        CandidateEvidenceLane.EXECUTION_SIMULATION,
    ),
}


def _resolve_test_contract_authority(
    authority_id: str,
    broker: str,
    decision_at: datetime,
) -> FuturesContractAuthority:
    """Provide explicit immutable contract facts for the simulator test seam."""

    return build_test_futures_contract_authority(
        authority_id=authority_id,
        broker=broker,
        decision_at=decision_at,
    )


@dataclass(frozen=True, slots=True)
class _IntelligenceResearchBridge:
    """Hash-only identifiers from the actual authorized P4 → P1 → P2 path."""

    intelligence_identity_hash: str
    intelligence_evidence_hashes: tuple[str, ...]
    intelligence_available_at: datetime
    research_identity_hash: str
    research_evidence_hashes: tuple[str, ...]
    research_available_at: datetime
    seam_evidence_hashes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ResearchPortfolioBridge:
    """Hash-only identifiers from a real P2 candidate to P3 activated target path."""

    research_identity_hash: str
    research_evidence_hashes: tuple[str, ...]
    research_available_at: datetime
    portfolio_identity_hash: str
    portfolio_evidence_hashes: tuple[str, ...]
    portfolio_available_at: datetime
    seam_evidence_hashes: tuple[str, ...]


def _real_intelligence_to_research_bridge(tmp_path) -> _IntelligenceResearchBridge:
    """Materialize a real projection without creating target, order, or approval state."""

    published = publish_authorized_intelligence_feature_projection_fixture(tmp_path)
    contract = INTELLIGENCE_EVENT_INPUT
    pit_spec = MarketDataPITSpec(
        kind=contract.kind,
        key_columns=(*contract.entity_key_columns, contract.event_time_column),
        event_time_column=contract.event_time_column,
        available_at_column=contract.available_at_column,
        value_columns=contract.value_columns or (),
        schema_version=contract.schema_version,
    )
    snapshot = MarketDataPITSelector(published.store).select(
        dataset_version_hash=published.publication.dataset.dataset_version.version_hash,
        spec=pit_spec,
        as_of=_AS_OF,
    )
    registry = FeatureRegistry(artifact_store=published.store)
    version = register_canonical_feature(
        registry,
        feature_id=EVENT_CONFIDENCE.feature_id,
        version="1.0.0",
        code_revision="p8-intelligence-to-research-e2e",
    )
    lineage = registry.create_market_data_lineage(
        feature_version_hash=version.version_hash,
        market_snapshot=snapshot,
        parameters={},
    )
    backfill = registry.materialize_deterministic_backfill(lineage)
    evidence = lineage.dependencies[0].dataset_evidence
    assert evidence is not None
    assert version.feature_id == EVENT_CONFIDENCE.feature_id
    assert version.spec_hash == EVENT_CONFIDENCE.feature_spec().spec_hash
    assert lineage.feature_version_hash == version.version_hash
    assert evidence.dataset_version_hash == published.publication.dataset.dataset_version.version_hash
    assert evidence.selected_frame_hash == snapshot.selected_frame_hash
    assert backfill.feature_version_hash == version.version_hash
    assert backfill.decision_time_safe is False

    projection = published.projection
    return _IntelligenceResearchBridge(
        intelligence_identity_hash=projection.projection_hash,
        intelligence_evidence_hashes=(
            projection.observations[0].observation_hash,
            projection.observations[0].evidence_bundle_hash,
        ),
        intelligence_available_at=projection.available_at,
        research_identity_hash=lineage.lineage_hash,
        research_evidence_hashes=(
            version.version_hash,
            backfill.backfill_hash,
            evidence.evidence_hash,
        ),
        research_available_at=backfill.available_at,
        seam_evidence_hashes=tuple(
            sorted(
                (
                    projection.projection_hash,
                    published.publication.dataset.dataset_version.version_hash,
                    version.version_hash,
                    evidence.evidence_hash,
                    lineage.lineage_hash,
                    backfill.backfill_hash,
                )
            )
        ),
    )


def _real_research_to_portfolio_bridge(tmp_path) -> _ResearchPortfolioBridge:
    """Activate one P2 candidate manually into a P3 target without execution authority."""

    chain = build_research_candidate_chain(tmp_path)
    candidate_approval = chain.card.decision.approval
    assert candidate_approval is not None
    generated_at = candidate_approval.approved_at + timedelta(minutes=1)
    proposal = StrategyTargetProposal(
        target_id="p8-candidate-target",
        source_strategy_id=chain.strategy.strategy_id,
        source_strategy_version=chain.strategy.version,
        generated_at=generated_at,
        effective_at=generated_at + timedelta(minutes=2),
        expires_at=generated_at + timedelta(hours=1),
        positions=(TargetPosition("SHFE.RB2610", 0.1),),
    )
    activation_approval = HumanStrategyTargetActivationApproval(
        activation_id="p8-candidate-manual-activation",
        approver_id="strategy-owner",
        approved_at=generated_at + timedelta(minutes=1),
        target_proposal_hash=proposal.proposal_hash,
        research_card_hash=chain.card.card_hash,
        research_decision_hash=chain.card.decision.decision_hash,
        experiment_spec_hash=chain.experiment.spec_hash,
        strategy_version_hash=chain.strategy.reference_hash,
        rationale="candidate-target-reviewed",
    )
    receipt = ResearchStrategyTargetActivator().activate(
        ResearchStrategyActivationRequest(
            research_card=chain.card,
            experiment_spec=chain.experiment,
            experiment_run=chain.experiment_run,
            target_proposal=proposal,
            activation_approval=activation_approval,
        )
    )
    evidence = chain.card.validation_report.evidence
    assert receipt.eligible_for_trading is False
    assert receipt.decision_time_safe is False
    assert receipt.strategy_target.activation.activation_hash == receipt.activation_hash

    return _ResearchPortfolioBridge(
        research_identity_hash=chain.card.card_hash,
        research_evidence_hashes=tuple(
            sorted(
                (
                    chain.card.decision.decision_hash,
                    chain.card.decision.evidence.evidence_hash,
                    chain.card.decision.approval.approval_hash,
                    chain.card.validation_report.report_hash,
                    *evidence.dataset_version_hashes,
                    *evidence.feature_version_hashes,
                    chain.strategy.reference_hash,
                )
            )
        ),
        research_available_at=candidate_approval.approved_at,
        portfolio_identity_hash=receipt.strategy_target.target_hash,
        portfolio_evidence_hashes=(
            receipt.activation_hash,
            receipt.activation_approval.approval_hash,
            receipt.strategy_target.target_hash,
        ),
        portfolio_available_at=activation_approval.approved_at,
        seam_evidence_hashes=tuple(
            sorted(
                (
                    chain.card.card_hash,
                    chain.card.decision.decision_hash,
                    receipt.activation_approval.approval_hash,
                    receipt.activation_hash,
                    receipt.strategy_target.target_hash,
                )
            )
        ),
    )


def _hash(value: int) -> str:
    return f"{value:064x}"


def _verified_lanes(
    *,
    bridge: _IntelligenceResearchBridge | None = None,
) -> tuple[CandidateLaneEvidence, ...]:
    return tuple(
        CandidateLaneEvidence(
            lane=lane,
            status=CandidateEvidenceStatus.VERIFIED,
            identity_hash=(
                bridge.intelligence_identity_hash
                if bridge is not None and lane is CandidateEvidenceLane.INTELLIGENCE
                else (
                    bridge.research_identity_hash
                    if bridge is not None and lane is CandidateEvidenceLane.RESEARCH
                    else _hash(100 + index)
                )
            ),
            evidence_hashes=(
                bridge.intelligence_evidence_hashes
                if bridge is not None and lane is CandidateEvidenceLane.INTELLIGENCE
                else (
                    bridge.research_evidence_hashes
                    if bridge is not None and lane is CandidateEvidenceLane.RESEARCH
                    else (_hash(200 + index),)
                )
            ),
            available_at=(
                bridge.intelligence_available_at
                if bridge is not None and lane is CandidateEvidenceLane.INTELLIGENCE
                else (
                    bridge.research_available_at
                    if bridge is not None and lane is CandidateEvidenceLane.RESEARCH
                    else _AS_OF
                )
            ),
        )
        for index, lane in enumerate(CandidateEvidenceLane)
    )


def _seams(
    lanes: tuple[CandidateLaneEvidence, ...],
    *,
    blocked: frozenset[CandidateSeam],
    bridge: _IntelligenceResearchBridge | None = None,
) -> tuple[CandidateSeamEvidence, ...]:
    lane_by_kind = {item.lane: item for item in lanes}
    return tuple(
        CandidateSeamEvidence(
            seam=seam,
            status=(
                CandidateEvidenceStatus.BLOCKED
                if seam in blocked
                else CandidateEvidenceStatus.VERIFIED
            ),
            source_identity_hash=lane_by_kind[_SEAM_ENDPOINTS[seam][0]].identity_hash,
            destination_identity_hash=lane_by_kind[_SEAM_ENDPOINTS[seam][1]].identity_hash,
            evidence_hashes=(
                bridge.seam_evidence_hashes
                if bridge is not None and seam is CandidateSeam.INTELLIGENCE_TO_RESEARCH
                else (_hash(300 + index),)
            ),
            available_at=(
                bridge.research_available_at
                if bridge is not None and seam is CandidateSeam.INTELLIGENCE_TO_RESEARCH
                else _AS_OF
            ),
        )
        for index, seam in enumerate(CandidateSeam)
    )


def _request(
    *,
    blocked: frozenset[CandidateSeam],
    bridge: _IntelligenceResearchBridge | None = None,
) -> CandidateAcceptanceRequest:
    lanes = _verified_lanes(bridge=bridge)
    return CandidateAcceptanceRequest(
        candidate_id="p8-evidence-matrix",
        environment=CandidateEnvironment.CTP_SIM,
        as_of=_AS_OF,
        lanes=lanes,
        seams=_seams(lanes, blocked=blocked, bridge=bridge),
    )


def _research_portfolio_request(
    bridge: _ResearchPortfolioBridge,
) -> CandidateAcceptanceRequest:
    lanes = tuple(
        CandidateLaneEvidence(
            lane=lane,
            status=CandidateEvidenceStatus.VERIFIED,
            identity_hash=(
                bridge.research_identity_hash
                if lane is CandidateEvidenceLane.RESEARCH
                else (
                    bridge.portfolio_identity_hash
                    if lane is CandidateEvidenceLane.PORTFOLIO_RISK
                    else _hash(500 + index)
                )
            ),
            evidence_hashes=(
                bridge.research_evidence_hashes
                if lane is CandidateEvidenceLane.RESEARCH
                else (
                    bridge.portfolio_evidence_hashes
                    if lane is CandidateEvidenceLane.PORTFOLIO_RISK
                    else (_hash(600 + index),)
                )
            ),
            available_at=(
                bridge.research_available_at
                if lane is CandidateEvidenceLane.RESEARCH
                else (
                    bridge.portfolio_available_at
                    if lane is CandidateEvidenceLane.PORTFOLIO_RISK
                    else _AS_OF
                )
            ),
        )
        for index, lane in enumerate(CandidateEvidenceLane)
    )
    lane_by_kind = {item.lane: item for item in lanes}
    blocked = frozenset(
        {
            CandidateSeam.DATA_PIT_TO_RESEARCH,
            CandidateSeam.INTELLIGENCE_TO_RESEARCH,
            CandidateSeam.PORTFOLIO_RISK_TO_EXECUTION_SIMULATION,
        }
    )
    seams = tuple(
        CandidateSeamEvidence(
            seam=seam,
            status=(
                CandidateEvidenceStatus.BLOCKED
                if seam in blocked
                else CandidateEvidenceStatus.VERIFIED
            ),
            source_identity_hash=lane_by_kind[_SEAM_ENDPOINTS[seam][0]].identity_hash,
            destination_identity_hash=lane_by_kind[_SEAM_ENDPOINTS[seam][1]].identity_hash,
            evidence_hashes=(
                bridge.seam_evidence_hashes
                if seam is CandidateSeam.RESEARCH_TO_PORTFOLIO_RISK
                else (_hash(700 + index),)
            ),
            available_at=(
                bridge.portfolio_available_at
                if seam is CandidateSeam.RESEARCH_TO_PORTFOLIO_RISK
                else _AS_OF
            ),
        )
        for index, seam in enumerate(CandidateSeam)
    )
    return CandidateAcceptanceRequest(
        candidate_id="p8-research-portfolio-bridge",
        environment=CandidateEnvironment.CTP_SIM,
        as_of=_AS_OF,
        lanes=lanes,
        seams=seams,
    )


def test_real_intelligence_to_research_projection_verifies_only_its_own_seam(tmp_path) -> None:
    """A real P4→P1→P2 lineage does not fabricate the three remaining bridges."""

    bridge = _real_intelligence_to_research_bridge(tmp_path)
    remaining_blocked = frozenset(
        {
            CandidateSeam.DATA_PIT_TO_RESEARCH,
            CandidateSeam.RESEARCH_TO_PORTFOLIO_RISK,
            CandidateSeam.PORTFOLIO_RISK_TO_EXECUTION_SIMULATION,
        }
    )
    result = CandidateAcceptanceVerifier().evaluate(
        _request(blocked=remaining_blocked, bridge=bridge)
    )

    intelligence_seam = next(
        item for item in result.seams if item.seam is CandidateSeam.INTELLIGENCE_TO_RESEARCH
    )
    assert intelligence_seam.status is CandidateEvidenceStatus.VERIFIED
    assert intelligence_seam.source_identity_hash == bridge.intelligence_identity_hash
    assert intelligence_seam.destination_identity_hash == bridge.research_identity_hash
    assert intelligence_seam.evidence_hashes == bridge.seam_evidence_hashes

    assert result.state is CandidateAcceptanceState.BLOCKED
    assert result.blocking_lanes == ()
    assert result.blocking_seams == tuple(
        seam for seam in CandidateSeam if seam in remaining_blocked
    )
    assert result.eligible_for_trading is False


def test_real_research_candidate_to_manually_activated_target_verifies_only_its_own_seam(
    tmp_path,
) -> None:
    """A P2 candidate reaches P3 only through a named activation receipt, never an order."""

    bridge = _real_research_to_portfolio_bridge(tmp_path)
    result = CandidateAcceptanceVerifier().evaluate(_research_portfolio_request(bridge))

    research_portfolio_seam = next(
        item
        for item in result.seams
        if item.seam is CandidateSeam.RESEARCH_TO_PORTFOLIO_RISK
    )
    assert research_portfolio_seam.status is CandidateEvidenceStatus.VERIFIED
    assert research_portfolio_seam.source_identity_hash == bridge.research_identity_hash
    assert research_portfolio_seam.destination_identity_hash == bridge.portfolio_identity_hash
    assert research_portfolio_seam.evidence_hashes == bridge.seam_evidence_hashes
    assert result.state is CandidateAcceptanceState.BLOCKED
    assert result.blocking_lanes == ()
    assert result.blocking_seams == (
        CandidateSeam.DATA_PIT_TO_RESEARCH,
        CandidateSeam.INTELLIGENCE_TO_RESEARCH,
        CandidateSeam.PORTFOLIO_RISK_TO_EXECUTION_SIMULATION,
    )
    assert result.eligible_for_trading is False


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
    # Reconciliation committed NORMAL.  This helper owns only the following
    # lookup transaction and must not leak it into candidate prepare.
    session.rollback()
    return evidence


def test_guarded_ctp_sim_execution_verifies_only_the_portfolio_to_execution_seam(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """Only durable consumption, simulated fill, and reconciliation verify P3→P5."""

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
            "ctp_sim_account": "ctp-sim-test",
            "default_cash": 100_000.0,
        }
    )
    monkeypatch.setattr(ctp_sim_broker, "get_settings", lambda: settings)
    monkeypatch.setattr(ctp_sim_broker, "utc_now", lambda: checked_at)
    executor = create_test_ctp_sim_candidate_executor(
        settings_provider=lambda: settings,
        clock=lambda: checked_at,
        contract_authority_resolver=_resolve_test_contract_authority,
        session_factory=postgresql_session_factory,
    )
    broker = executor.create_broker(
        profile=bootstrap.profile,
        decision_at=bootstrap.request.market_snapshot_at,
    )
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
                run_id="candidate-matrix-ctp-sim-bootstrap",
            )
            fixture = build_execution_provenance_fixture(
                tmp_path / "authority-bound",
                broker_state=snapshot,
                reconciliation_safety_state=safety,
            )
            issued = create_test_portfolio_risk_approval_issuer().issue(
                session,
                profile=fixture.profile,
                broker="ctp_sim",
                account=fixture.request.account_snapshot.account,
                authority=fixture.request.portfolio_risk_authority,
                composition=fixture.composition_evidence,
                approval_id="candidate-matrix-ctp-sim-manual-approval",
                checked_at=fixture.request.checked_at,
            )
            request = replace(
                fixture.request,
                portfolio_risk_approval_request=issued.approval_request,
                portfolio_risk_approval_evidence=issued.approval_evidence,
            )
            bundle = executor.prepare(
                request,
                session=session,
                broker=broker,
                run_id="candidate-matrix-ctp-sim-run",
                batch_id="candidate-matrix-ctp-sim-batch",
            )
        with postgresql_session_factory() as session:
            submitted = bundle.submit(session)
            reconciliation, execution_evidence = bundle.reconcile(session)
            consumption = session.scalar(
                select(ExecutionProvenanceConsumptionRecord).where(
                    ExecutionProvenanceConsumptionRecord.plan_hash
                    == bundle.receipt.plan_hash
                )
            )
            order = session.scalar(
                select(OrderRecord).where(
                    OrderRecord.plan_id == bundle.plans[0].plan_id
                )
            )
            fill = session.scalar(select(FillRecord))
    finally:
        broker.disconnect()

    assert len(submitted) == 1 and submitted[0].accepted is True
    assert reconciliation["fills_synced"] == 1
    assert consumption is not None
    assert order is not None and order.request_fingerprint
    assert fill is not None and fill.exec_id
    assert execution_evidence.observed_fill_exec_ids == (fill.exec_id,)
    approved_target = fixture.portfolio_risk_approval_evidence.approved_target
    assert approved_target is not None
    candidate_approvals = tuple(chain.card.decision.approval for chain in fixture.chains)
    assert all(item is not None for item in candidate_approvals)
    resolved_approvals = tuple(item for item in candidate_approvals if item is not None)
    research_identity_hash = canonical_json_sha256(
        {
            "format": "northstar.candidate-research-source-set.v1",
            "card_hashes": sorted(chain.card.card_hash for chain in fixture.chains),
        }
    )
    research_evidence_hashes = tuple(
        sorted(
            {
                evidence_hash
                for chain, candidate_approval in zip(
                    fixture.chains,
                    resolved_approvals,
                    strict=True,
                )
                for evidence_hash in (
                    chain.card.decision.decision_hash,
                    chain.card.decision.evidence.evidence_hash,
                    candidate_approval.approval_hash,
                    chain.card.validation_report.report_hash,
                    chain.strategy.reference_hash,
                )
            }
        )
    )
    activation_hashes = tuple(
        sorted(receipt.activation_hash for receipt in fixture.activation_receipts)
    )
    activated_target_hashes = tuple(
        sorted(receipt.strategy_target.target_hash for receipt in fixture.activation_receipts)
    )
    bridge = _ResearchPortfolioBridge(
        research_identity_hash=research_identity_hash,
        research_evidence_hashes=research_evidence_hashes,
        research_available_at=max(item.approved_at for item in resolved_approvals),
        portfolio_identity_hash=approved_target.approval_hash,
        portfolio_evidence_hashes=tuple(
            sorted(
                {
                    *activation_hashes,
                    *activated_target_hashes,
                    fixture.composition_evidence.evidence_hash,
                    fixture.composition_evidence.portfolio_target.target_hash,
                    fixture.portfolio_risk_approval_evidence.review.review_hash,
                    fixture.portfolio_risk_approval_evidence.evidence_hash,
                    approved_target.approval_hash,
                    approved_target.risk_evidence_hash,
                }
            )
        ),
        portfolio_available_at=approved_target.approved_at,
        seam_evidence_hashes=tuple(
            sorted(
                {
                    *(chain.card.card_hash for chain in fixture.chains),
                    *activation_hashes,
                    fixture.composition_evidence.evidence_hash,
                    fixture.portfolio_risk_approval_evidence.evidence_hash,
                    approved_target.approval_hash,
                }
            )
        ),
    )
    request = _research_portfolio_request(bridge)
    complete_execution_evidence_hash = canonical_json_sha256(
        {
            "format": "northstar.candidate-ctp-sim-execution-seam.v1",
            "receipt_hash": bundle.receipt.receipt_hash,
            "plan_hash": bundle.receipt.plan_hash,
            "consumption_order_hashes": list(
                execution_evidence.consumption_order_hashes
            ),
            "durable_order_request_fingerprint": order.request_fingerprint,
            "fill_exec_ids": list(execution_evidence.observed_fill_exec_ids),
            "reconciliation_hash": execution_evidence.reconciliation_hash,
        }
    )
    execution_hashes = tuple(
        sorted(
            (
                bundle.receipt.receipt_hash,
                bundle.receipt.plan_hash,
                bundle.receipt.preflight_hash,
                consumption.order_hash,
                order.request_fingerprint,
                execution_evidence.evidence_hash,
                complete_execution_evidence_hash,
            )
        )
    )
    lanes = tuple(
        replace(
            lane,
            identity_hash=execution_evidence.evidence_hash,
            evidence_hashes=execution_hashes,
            available_at=checked_at,
        )
        if lane.lane is CandidateEvidenceLane.EXECUTION_SIMULATION
        else lane
        for lane in request.lanes
    )
    seams = tuple(
        replace(
            seam,
            status=CandidateEvidenceStatus.VERIFIED,
            destination_identity_hash=execution_evidence.evidence_hash,
            evidence_hashes=execution_hashes,
            available_at=checked_at,
        )
        if seam.seam is CandidateSeam.PORTFOLIO_RISK_TO_EXECUTION_SIMULATION
        else seam
        for seam in request.seams
    )
    result = CandidateAcceptanceVerifier().evaluate(
        replace(request, lanes=lanes, seams=seams)
    )

    execution_seam = next(
        item
        for item in result.seams
        if item.seam is CandidateSeam.PORTFOLIO_RISK_TO_EXECUTION_SIMULATION
    )
    assert execution_seam.status is CandidateEvidenceStatus.VERIFIED
    assert execution_seam.source_identity_hash == approved_target.approval_hash
    assert execution_seam.destination_identity_hash == execution_evidence.evidence_hash
    assert execution_seam.evidence_hashes == execution_hashes
    assert bundle.receipt.eligible_for_ctp_sim is False
    assert bundle.receipt.eligible_for_trading is False
    assert bundle.receipt.eligible_for_live is False
    assert result.state is CandidateAcceptanceState.BLOCKED
    assert result.blocking_seams == (
        CandidateSeam.DATA_PIT_TO_RESEARCH,
        CandidateSeam.INTELLIGENCE_TO_RESEARCH,
    )


def test_complete_hypothetical_matrix_is_evaluator_only_and_never_trade_authority() -> None:
    """This pure evaluator case is not evidence that the remaining seams are built."""

    request = _request(blocked=frozenset())
    verifier = CandidateAcceptanceVerifier()

    first = verifier.evaluate(request)
    second = verifier.evaluate(request)

    assert first == second
    assert first.receipt_hash == second.receipt_hash
    assert first.state is CandidateAcceptanceState.CANDIDATE_EVIDENCE_ONLY
    assert first.blocking_lanes == ()
    assert first.blocking_seams == ()
    assert first.eligible_for_trading is False

"""P8-WP05 final CTP-sim candidate-gate failure coverage."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from threading import Event, Thread, current_thread

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

import northstar_quant.application.ctp_sim_candidate_execution as candidate_execution
import northstar_quant.trading_execution.broker.ctp_sim_broker as ctp_sim_broker
import northstar_quant.trading_execution.reconciliation.reconciliation as reconciliation_module
from northstar_quant.application.ctp_sim_candidate_execution import (
    CtpSimCandidateExecutionError,
)
from northstar_quant.application.portfolio_risk_authority import (
    PortfolioRiskAuthorityResolver,
    ReconciliationSafetyStateEvidence,
)
from northstar_quant.platform.common.time import utc_now
from northstar_quant.platform.db.models import (
    ExecutionPlanRecord,
    ExecutionProvenanceConsumptionRecord,
    OrderRecord,
)
from northstar_quant.platform.db.repositories import (
    acquire_reconciliation_safety_fence,
    latest_reconciliation_safety_state,
    record_execution_provenance_consumption,
)
from northstar_quant.trading_execution.execution.models import BrokerStateSnapshot
from northstar_quant.trading_execution.reconciliation.reconciliation import (
    halt_for_reconciliation,
    reconcile_broker_state,
)
from northstar_quant.trading_execution.orders.durable_submission import (
    DurableBrokerAdapter,
)
from northstar_quant.trading_execution.broker.ctp_sim_broker import CtpSimBrokerAdapter
from northstar_quant.portfolio_risk.portfolio import (
    PortfolioRiskApprovalGate,
    PortfolioRiskApprovalRequest,
)
from tests.helpers.ctp_sim_submission import create_test_ctp_sim_submission_authority
from tests.helpers.ctp_sim_candidate_execution import (
    create_test_ctp_sim_candidate_executor,
)
from tests.helpers.execution_provenance import build_execution_provenance_fixture
from tests.helpers.manual_risk_approval import (
    create_test_portfolio_risk_approval_issuer,
)


def _with_persisted_manual_risk_approval(
    session,
    fixture,
    *,
    approval_id: str,
):
    """Replace the pure P3 claim with a test-issued immutable approval grant."""

    issued = create_test_portfolio_risk_approval_issuer().issue(
        session,
        profile=fixture.profile,
        broker="ctp_sim",
        account=fixture.request.account_snapshot.account,
        authority=fixture.request.portfolio_risk_authority,
        composition=fixture.composition_evidence,
        approval_id=approval_id,
        checked_at=fixture.request.checked_at,
    )
    return replace(
        fixture.request,
        portfolio_risk_approval_request=issued.approval_request,
        portfolio_risk_approval_evidence=issued.approval_evidence,
    )


def _prepared_candidate(tmp_path, monkeypatch, postgresql_session_factory):
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
            "ctp_sim_state_path": tmp_path / "storage" / "ctp-sim-state.json",
            "ctp_sim_account": "ctp-sim-test",
            "default_cash": 100_000.0,
        }
    )
    monkeypatch.setattr(ctp_sim_broker, "get_settings", lambda: settings)
    monkeypatch.setattr(ctp_sim_broker, "utc_now", lambda: now["value"])
    executor = create_test_ctp_sim_candidate_executor(
        settings_provider=lambda: settings,
        clock=lambda: now["value"],
    )
    broker = executor.create_broker()
    broker.connect()
    broker.seed_market_quotes({"RB2610": 3_100.0}, asof=now["value"])
    snapshot = broker.read_state_snapshot()
    with postgresql_session_factory() as session:
        reconcile_broker_state(
            session,
            broker,
            snapshot=snapshot,
            run_id="ctp-sim-candidate-bootstrap-run",
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
    )
    with postgresql_session_factory() as session:
        approved_request = _with_persisted_manual_risk_approval(
            session,
            fixture,
            approval_id="ctp-sim-candidate-unit-approval",
        )
        bundle = executor.prepare(
            approved_request,
            session=session,
            broker=broker,
            run_id="ctp-sim-candidate-unit-run",
            batch_id="ctp-sim-candidate-unit-batch",
        )
    return now, executor, broker, bundle


def _rejected_portfolio_risk_request(fixture):
    """Return an exact P3 UNKNOWN result rather than a hand-built approval."""

    base = fixture.portfolio_risk_approval_request.review_request
    review_request = replace(
        base,
        account_snapshot=replace(base.account_snapshot, equity=None),
    )
    gate = PortfolioRiskApprovalGate()
    review = gate.review(review_request)
    approval_request = PortfolioRiskApprovalRequest(
        review_request=review_request,
        attestation=replace(
            fixture.portfolio_risk_approval_request.attestation,
            review_hash=review.review_hash,
        ),
    )
    approval_evidence = gate.evaluate(approval_request)
    assert approval_evidence.approved_target is None
    return replace(
        fixture.request,
        portfolio_risk_approval_request=approval_request,
        portfolio_risk_approval_evidence=approval_evidence,
    )


def _with_changed_manual_approval_claim(
    request,
    *,
    approval_id: str | None = None,
    rationale: str | None = None,
):
    """Create a pure-P3 claim that is not necessarily a durable grant."""

    original = request.portfolio_risk_approval_request
    attestation = replace(
        original.attestation,
        **{
            key: value
            for key, value in (
                ("approval_id", approval_id),
                ("rationale", rationale),
            )
            if value is not None
        },
    )
    approval_request = replace(original, attestation=attestation)
    approval_evidence = PortfolioRiskApprovalGate().evaluate(approval_request)
    assert approval_evidence.approved_target is not None
    return replace(
        request,
        portfolio_risk_approval_request=approval_request,
        portfolio_risk_approval_evidence=approval_evidence,
    )


def _assert_no_candidate_execution_records(session) -> None:
    assert session.scalar(select(ExecutionPlanRecord)) is None
    assert session.scalar(select(ExecutionProvenanceConsumptionRecord)) is None
    assert session.scalar(select(OrderRecord)) is None


def test_direct_raw_and_durable_ctp_sim_paths_are_denied_before_persistence(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    _now, _executor, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        with pytest.raises(
            CtpSimCandidateExecutionError,
            match="CTP_SIM_CANDIDATE_RECONCILIATION_REQUIRED",
        ):
            broker.submit_order(bundle.orders[0])
        assert broker.sync_state().open_orders == []

        with postgresql_session_factory() as session:
            authority = broker.submission_authority
            assert authority is not None
            with pytest.raises(
                CtpSimCandidateExecutionError,
                match="CTP_SIM_CANDIDATE_RECONCILIATION_REQUIRED",
            ):
                DurableBrokerAdapter(
                    broker,
                    session,
                    ctp_sim_submission_authority=authority,
                ).submit_order(bundle.orders[0])
            with pytest.raises(
                PermissionError,
                match="CTP_SIM_FINAL_SUBMISSION_AUTHORITY_REQUIRED",
            ):
                DurableBrokerAdapter(broker, session).submit_order(bundle.orders[0])
            with pytest.raises(
                PermissionError,
                match="CTP_SIM_SUBMISSION_AUTHORITY_INVALID",
            ):
                DurableBrokerAdapter(
                    broker,
                    session,
                    ctp_sim_submission_authority=object(),  # type: ignore[arg-type]
                ).submit_order(bundle.orders[0])
            assert session.scalar(select(OrderRecord)) is None
    finally:
        broker.disconnect()


def test_prepare_ends_its_read_transaction_before_submit_on_the_same_session(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """Prepare must not strand an autobegun read transaction before submit."""

    _now, executor, broker, original_bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        with postgresql_session_factory() as session:
            bundle = executor.prepare(
                original_bundle.source_request,
                session=session,
                broker=broker,
                run_id="ctp-sim-candidate-same-session-prepare-run",
                batch_id="ctp-sim-candidate-same-session-prepare-batch",
            )
            assert not session.in_transaction()
            results = bundle.submit(session)

        assert len(results) == len(bundle.orders)
        state = broker.read_state_snapshot()
        assert len(state.open_orders) == len(bundle.orders)
    finally:
        broker.disconnect()


def test_rejected_canonical_p3_evidence_cannot_reach_candidate_intent_or_broker_mutation(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    fixture = build_execution_provenance_fixture(tmp_path)
    now = {"value": fixture.request.checked_at}
    settings = fixture.settings.model_copy(
        update={
            "broker": "ctp_sim",
            "live_trading_enabled": False,
            "kill_switch_enabled": False,
            "storage_dir": tmp_path / "storage",
            "downloads_dir": tmp_path / "storage" / "downloads",
            "reports_dir": tmp_path / "reports",
            "log_dir": tmp_path / "logs",
            "ctp_sim_state_path": tmp_path / "storage" / "rejected-state.json",
            "ctp_sim_account": "ctp-sim-test",
            "default_cash": 100_000.0,
        }
    )
    monkeypatch.setattr(ctp_sim_broker, "get_settings", lambda: settings)
    monkeypatch.setattr(ctp_sim_broker, "utc_now", lambda: now["value"])
    executor = create_test_ctp_sim_candidate_executor(
        settings_provider=lambda: settings,
        clock=lambda: now["value"],
    )
    broker = executor.create_broker()
    broker.connect()
    try:
        broker.seed_market_quotes({"RB2610": 3_100.0}, asof=now["value"])
        intent_attempted = False
        broker_operation_attempted = False

        def _intent_sentinel(*args, **kwargs):
            nonlocal intent_attempted
            intent_attempted = True
            raise AssertionError("rejected P3 evidence must not reach durable intent")

        def _broker_sentinel(*args, **kwargs):
            nonlocal broker_operation_attempted
            broker_operation_attempted = True
            raise AssertionError("rejected P3 evidence must not reach candidate order assembly")

        monkeypatch.setattr(
            candidate_execution,
            "save_execution_plan_records",
            _intent_sentinel,
        )
        monkeypatch.setattr(broker, "prepare_order", _broker_sentinel)
        state_before_prepare = broker.state_path.read_bytes()
        with postgresql_session_factory() as session:
            with pytest.raises(
                CtpSimCandidateExecutionError,
                match="PORTFOLIO_RISK_AUTHORITY_RECONCILIATION_MISMATCH",
            ):
                executor.prepare(
                    _rejected_portfolio_risk_request(fixture),
                    session=session,
                    broker=broker,
                    run_id="ctp-sim-rejected-p3-run",
                    batch_id="ctp-sim-rejected-p3-batch",
                )
        assert intent_attempted is False
        assert broker_operation_attempted is False
        assert broker.state_path.read_bytes() == state_before_prepare
        state = broker.read_state_snapshot()
        assert state.open_orders == []
        assert state.completed_orders == []
        assert state.fills == []
        with postgresql_session_factory() as session:
            assert session.scalar(select(ExecutionProvenanceConsumptionRecord)) is None
            assert session.scalar(select(OrderRecord)) is None
    finally:
        broker.disconnect()


def test_self_minted_p3_attestation_without_a_durable_grant_cannot_prepare(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """A caller-constructible P3 attestation is a claim, never execution authority."""

    _now, executor, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        forged_request = _with_changed_manual_approval_claim(
            bundle.source_request,
            approval_id="self-minted-p3-approval",
        )
        state_before = broker.state_path.read_bytes()
        preflight_called = False
        order_assembly_called = False

        def _preflight_sentinel(*_args, **_kwargs):
            nonlocal preflight_called
            preflight_called = True
            raise AssertionError("missing manual grant must reject before P8 receipt")

        def _order_sentinel(*_args, **_kwargs):
            nonlocal order_assembly_called
            order_assembly_called = True
            raise AssertionError("missing manual grant must reject before order assembly")

        monkeypatch.setattr(
            candidate_execution.ExecutionProvenancePreflight,
            "_evaluate",
            _preflight_sentinel,
        )
        monkeypatch.setattr(broker, "prepare_order", _order_sentinel)
        with postgresql_session_factory() as session:
            with pytest.raises(
                CtpSimCandidateExecutionError,
                match="PERSISTED_PORTFOLIO_RISK_APPROVAL_MISSING",
            ):
                executor.prepare(
                    forged_request,
                    session=session,
                    broker=broker,
                    run_id="ctp-sim-candidate-self-minted-run",
                    batch_id="ctp-sim-candidate-self-minted-batch",
                )
            _assert_no_candidate_execution_records(session)

        assert preflight_called is False
        assert order_assembly_called is False
        assert broker.state_path.read_bytes() == state_before
    finally:
        broker.disconnect()


def test_persisted_manual_grant_mismatch_cannot_prepare(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """A real grant cannot be repurposed by changing its P3 attestation claim."""

    _now, executor, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        mismatched_request = _with_changed_manual_approval_claim(
            bundle.source_request,
            rationale="forged rationale must not reuse the issued grant",
        )
        state_before = broker.state_path.read_bytes()
        with postgresql_session_factory() as session:
            with pytest.raises(
                CtpSimCandidateExecutionError,
                match="PERSISTED_PORTFOLIO_RISK_APPROVAL_MISMATCH",
            ):
                executor.prepare(
                    mismatched_request,
                    session=session,
                    broker=broker,
                    run_id="ctp-sim-candidate-manual-mismatch-run",
                    batch_id="ctp-sim-candidate-manual-mismatch-batch",
                )
            _assert_no_candidate_execution_records(session)

        assert broker.state_path.read_bytes() == state_before
    finally:
        broker.disconnect()


def test_expired_manual_approval_claim_cannot_prepare(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """An approval may never outlive its signed P3 validity horizon."""

    now, executor, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        review = bundle.source_request.portfolio_risk_approval_evidence.review
        now["value"] = review.approval_valid_until
        broker.seed_market_quotes({"RB2610": 3_100.0}, asof=now["value"])
        state_before = broker.state_path.read_bytes()
        with postgresql_session_factory() as session:
            with pytest.raises(
                CtpSimCandidateExecutionError,
                match="MANUAL_RISK_APPROVAL_CHECKED_AT_OUTSIDE_VALIDITY",
            ):
                executor.prepare(
                    bundle.source_request,
                    session=session,
                    broker=broker,
                    run_id="ctp-sim-candidate-manual-expired-run",
                    batch_id="ctp-sim-candidate-manual-expired-batch",
                )
            _assert_no_candidate_execution_records(session)

        assert broker.state_path.read_bytes() == state_before
    finally:
        broker.disconnect()


def test_changed_order_field_is_refused_before_consumption_or_broker_mutation(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    _now, _executor, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        bundle.orders[0].qty += 1.0
        with postgresql_session_factory() as session:
            with pytest.raises(
                CtpSimCandidateExecutionError,
                match="CTP_SIM_CANDIDATE_ORDER_BINDING_MISMATCH",
            ):
                bundle.submit(session)
            assert session.scalar(select(ExecutionProvenanceConsumptionRecord)) is None
            assert session.scalar(select(OrderRecord)) is None
        assert broker.sync_state().open_orders == []
    finally:
        broker.disconnect()


def test_expired_prepared_receipt_is_refused_before_plan_persistence_or_consumption(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    now, _executor, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        now["value"] = bundle.receipt.valid_until
        plan_persistence_attempted = False
        order_assembly_attempted = False

        def _plan_persistence_sentinel(*args, **kwargs):
            nonlocal plan_persistence_attempted
            plan_persistence_attempted = True
            raise AssertionError("expired receipt must not persist execution plans")

        def _order_assembly_sentinel(*args, **kwargs):
            nonlocal order_assembly_attempted
            order_assembly_attempted = True
            raise AssertionError("expired receipt must not assemble a durable order")

        monkeypatch.setattr(
            candidate_execution,
            "save_execution_plan_records",
            _plan_persistence_sentinel,
        )
        monkeypatch.setattr(broker, "prepare_order", _order_assembly_sentinel)
        with postgresql_session_factory() as session:
            with pytest.raises(
                CtpSimCandidateExecutionError,
                match="CTP_SIM_CANDIDATE_RECEIPT_EXPIRED",
            ):
                bundle.submit(session)
            assert plan_persistence_attempted is False
            assert order_assembly_attempted is False
            assert session.scalar(select(ExecutionPlanRecord)) is None
            assert session.scalar(select(ExecutionProvenanceConsumptionRecord)) is None
            assert session.scalar(select(OrderRecord)) is None
        assert broker.sync_state().open_orders == []
        assert broker.sync_state().completed_orders == []
    finally:
        broker.disconnect()


def test_consumption_is_one_time_even_when_repository_is_called_again(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    now, _executor, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        with postgresql_session_factory() as session:
            bundle.submit(session)
            commitment = bundle.receipt.order_commitments[0]
            with pytest.raises(
                PermissionError,
                match="EXECUTION_PROVENANCE_ORDER_ALREADY_CONSUMED",
            ):
                record_execution_provenance_consumption(
                    session,
                    preflight_id=bundle.receipt.preflight_id,
                    receipt_hash=bundle.receipt.receipt_hash,
                    plan_hash=bundle.receipt.plan_hash,
                    order_hash=commitment.order_hash,
                    profile_id=bundle.receipt.profile_id,
                    broker="ctp_sim",
                    account=broker.get_account(),
                    order_ref=bundle.orders[0].order_ref or "",
                    checked_at=bundle.receipt.checked_at,
                    valid_until=bundle.receipt.valid_until,
                    consumed_at=now["value"],
                )
    finally:
        broker.disconnect()


def test_reconciliation_reads_simulator_state_and_halts_for_unexplained_order(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    now, _executor, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        unexplained_broker = CtpSimBrokerAdapter(
            state_path=broker.state_path,
            account=broker.get_account(),
            submission_authority=create_test_ctp_sim_submission_authority(),
        )
        unexplained_broker.connect()
        with postgresql_session_factory() as session:
            with pytest.raises(TypeError, match="unexpected keyword argument 'snapshot'"):
                bundle.reconcile(
                    session,
                    snapshot=BrokerStateSnapshot(
                        account=broker.get_account(),
                        state_complete=True,
                        asof=now["value"],
                    ),
                )
            unexplained_broker.seed_market_quotes({"RB2610": 3_100.0}, asof=now["value"])
            unexplained_broker.submit_order(bundle.orders[0])
            with pytest.raises(
                CtpSimCandidateExecutionError,
                match="BROKER_ORDER_UNEXPLAINED",
            ):
                bundle.reconcile(session)
            assert not session.in_transaction()
            safety = latest_reconciliation_safety_state(
                session,
                profile_id=bundle.receipt.profile_id,
                broker="ctp_sim",
                account=broker.get_account(),
            )
        assert safety is not None and safety.state == "HALT"
    finally:
        if "unexplained_broker" in locals():
            unexplained_broker.disconnect()
        broker.disconnect()


def test_signed_p3_review_survives_a_fresh_unchanged_simulator_observation(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """A fresh as-of proves continuity but never rewrites signed P3 facts."""

    now, executor, broker, original_bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        signed_request = original_bundle.source_request
        now["value"] = signed_request.checked_at + timedelta(seconds=10)
        broker.seed_market_quotes({"RB2610": 3_100.0}, asof=now["value"])
        with postgresql_session_factory() as session:
            refreshed_bundle = executor.prepare(
                signed_request,
                session=session,
                broker=broker,
                run_id="ctp-sim-candidate-fresh-observation-run",
                batch_id="ctp-sim-candidate-fresh-observation-batch",
            )

        prepared = refreshed_bundle.prepared_request
        assert prepared.checked_at == now["value"]
        assert prepared.account_snapshot == signed_request.account_snapshot
        assert (
            prepared.portfolio_risk_authority
            == signed_request.portfolio_risk_authority
        )
        assert (
            prepared.portfolio_risk_approval_request
            == signed_request.portfolio_risk_approval_request
        )
        assert (
            prepared.portfolio_risk_approval_evidence
            == signed_request.portfolio_risk_approval_evidence
        )
        assert prepared.quotes[0].asof == now["value"]
        state = broker.read_state_snapshot()
        assert state.open_orders == []
        assert state.completed_orders == []
        assert state.fills == []
    finally:
        broker.disconnect()


def test_prepare_rejects_self_consistent_relaxed_profile_claim_before_broker_read(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """A caller cannot relax a same-ID profile and re-sign P3 around it."""

    now, executor, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        source = bundle.source_request
        config = source.profile.portfolio_risk_approval
        assert config is not None
        relaxed_profile = replace(
            source.profile,
            portfolio_risk_approval=replace(
                config,
                limits=replace(config.limits, per_contract=10.0),
            ),
        )
        relaxed_authority = PortfolioRiskAuthorityResolver().resolve(
            profile=relaxed_profile,
            broker_state=source.account_snapshot,
            reconciliation_safety_state=source.reconciliation_safety_state,
            composition=source.portfolio_risk_approval_request.review_request.composition,
            evaluated_at=source.checked_at,
        )
        risk_gate = PortfolioRiskApprovalGate()
        relaxed_review = risk_gate.review(relaxed_authority.review_request)
        relaxed_approval_request = PortfolioRiskApprovalRequest(
            review_request=relaxed_authority.review_request,
            attestation=replace(
                source.portfolio_risk_approval_request.attestation,
                review_hash=relaxed_review.review_hash,
            ),
        )
        relaxed_evidence = risk_gate.evaluate(relaxed_approval_request)
        assert relaxed_evidence.approved_target is not None
        relaxed_request = replace(
            source,
            profile=relaxed_profile,
            portfolio_risk_authority=relaxed_authority,
            portfolio_risk_approval_request=relaxed_approval_request,
            portfolio_risk_approval_evidence=relaxed_evidence,
        )

        state_before = broker.state_path.read_bytes()

        def _unexpected_broker_read():
            raise AssertionError("profile mismatch must reject before broker access")

        monkeypatch.setattr(broker, "read_state_snapshot", _unexpected_broker_read)
        with postgresql_session_factory() as session:
            with pytest.raises(
                CtpSimCandidateExecutionError,
                match="CTP_SIM_CANDIDATE_PROFILE_MISMATCH",
            ):
                executor.prepare(
                    relaxed_request,
                    session=session,
                    broker=broker,
                    run_id="ctp-sim-candidate-relaxed-profile-run",
                    batch_id="ctp-sim-candidate-relaxed-profile-batch",
                )
            assert session.scalar(select(ExecutionPlanRecord)) is None
            assert session.scalar(select(ExecutionProvenanceConsumptionRecord)) is None
            assert session.scalar(select(OrderRecord)) is None
        assert broker.state_path.read_bytes() == state_before
    finally:
        broker.disconnect()


def test_submit_rechecks_active_profile_before_reconciliation_or_persistence(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """A config-file change after prepare invalidates the prepared bundle."""

    _now, _executor, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        config = bundle.prepared_request.profile.portfolio_risk_approval
        assert config is not None
        drifted_profile = replace(
            bundle.prepared_request.profile,
            portfolio_risk_approval=replace(
                config,
                limits=replace(config.limits, per_contract=10.0),
            ),
        )
        monkeypatch.setattr(
            candidate_execution,
            "load_trading_profile_uncached",
            lambda *_args, **_kwargs: drifted_profile,
        )
        reconciled = False

        def _reconcile_sentinel(_bundle, _session) -> None:
            nonlocal reconciled
            reconciled = True
            raise AssertionError("profile drift must reject before reconciliation")

        monkeypatch.setattr(
            candidate_execution.CtpSimCandidateExecutionBundle,
            "reconcile",
            _reconcile_sentinel,
        )
        with postgresql_session_factory() as session:
            with pytest.raises(
                CtpSimCandidateExecutionError,
                match="CTP_SIM_CANDIDATE_PROFILE_MISMATCH",
            ):
                bundle.submit(session)
            assert reconciled is False
            assert session.scalar(select(ExecutionPlanRecord)) is None
            assert session.scalar(select(ExecutionProvenanceConsumptionRecord)) is None
            assert session.scalar(select(OrderRecord)) is None
        state = broker.read_state_snapshot()
        assert state.open_orders == []
        assert state.completed_orders == []
        assert state.fills == []
    finally:
        broker.disconnect()


def test_clean_reconciliation_after_old_normal_allows_fresh_attestation(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """Immutable NORMAL transitions use a fresh clean snapshot as their anchor."""

    now, executor, broker, original_bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        config = original_bundle.source_request.profile.portfolio_risk_approval
        assert config is not None
        profile_id = original_bundle.receipt.profile_id
        with postgresql_session_factory() as session:
            initial = latest_reconciliation_safety_state(
                session,
                profile_id=profile_id,
                broker="ctp_sim",
                account=broker.get_account(),
            )
            assert initial is not None
            initial_hash = initial.state_hash
            initial_occurred_at = initial.occurred_at

        now["value"] += timedelta(seconds=config.max_input_age_seconds + 1)
        broker.seed_market_quotes({"RB2610": 3_100.0}, asof=now["value"])
        with postgresql_session_factory() as session:
            fresh_snapshot = broker.read_state_snapshot()
            reconcile_broker_state(
                session,
                broker,
                snapshot=fresh_snapshot,
                run_id="ctp-sim-candidate-old-normal-clean-reconcile",
                profile_id=profile_id,
            )
            persisted = latest_reconciliation_safety_state(
                session,
                profile_id=profile_id,
                broker="ctp_sim",
                account=broker.get_account(),
            )
            assert persisted is not None
            assert persisted.state == "NORMAL"
            assert persisted.state_hash == initial_hash
            assert persisted.occurred_at == initial_occurred_at
            safety = ReconciliationSafetyStateEvidence.from_persisted_record(persisted)

        fixture = build_execution_provenance_fixture(
            tmp_path / "fresh-attestation",
            broker_state=fresh_snapshot,
            reconciliation_safety_state=safety,
            reviewed_at=now["value"],
        )
        with postgresql_session_factory() as session:
            approved_request = _with_persisted_manual_risk_approval(
                session,
                fixture,
                approval_id="ctp-sim-candidate-old-normal-approval",
            )
            bundle = executor.prepare(
                approved_request,
                session=session,
                broker=broker,
                run_id="ctp-sim-candidate-old-normal-fresh-attestation-run",
                batch_id="ctp-sim-candidate-old-normal-fresh-attestation-batch",
            )

        review = fixture.portfolio_risk_approval_evidence.review
        assert review.evaluated_at == now["value"]
        assert review.request.risk_state.state_snapshot.occurred_at == initial_occurred_at
        assert review.request.risk_state.available_at == fresh_snapshot.asof
        assert bundle.prepared_request.account_snapshot == fresh_snapshot
        state = broker.read_state_snapshot()
        assert state.open_orders == []
        assert state.completed_orders == []
        assert state.fills == []
    finally:
        broker.disconnect()


def test_prepare_samples_real_clock_after_state_and_quote_observations(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """Normal unpatched clocks cannot make a just-read simulator fact future."""

    bootstrap = build_execution_provenance_fixture(
        tmp_path / "bootstrap",
        reviewed_at=utc_now(),
    )
    settings = bootstrap.settings.model_copy(
        update={
            "broker": "ctp_sim",
            "live_trading_enabled": False,
            "kill_switch_enabled": False,
            "storage_dir": tmp_path / "storage",
            "downloads_dir": tmp_path / "storage" / "downloads",
            "reports_dir": tmp_path / "reports",
            "log_dir": tmp_path / "logs",
            "ctp_sim_state_path": tmp_path / "storage" / "real-clock-state.json",
            "ctp_sim_account": "ctp-sim-test",
            "default_cash": 100_000.0,
        }
    )
    monkeypatch.setattr(ctp_sim_broker, "get_settings", lambda: settings)
    executor = create_test_ctp_sim_candidate_executor(
        settings_provider=lambda: settings,
    )
    broker = executor.create_broker()
    broker.connect()
    try:
        broker.seed_market_quotes({"RB2610": 3_100.0}, asof=utc_now())
        with postgresql_session_factory() as session:
            snapshot = broker.read_state_snapshot()
            reconcile_broker_state(
                session,
                broker,
                snapshot=snapshot,
                run_id="ctp-sim-candidate-real-clock-bootstrap",
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
            tmp_path / "real-clock-attestation",
            broker_state=snapshot,
            reconciliation_safety_state=safety,
            reviewed_at=snapshot.asof,
        )
        with postgresql_session_factory() as session:
            approved_request = _with_persisted_manual_risk_approval(
                session,
                fixture,
                approval_id="ctp-sim-candidate-real-clock-approval",
            )
            bundle = executor.prepare(
                approved_request,
                session=session,
                broker=broker,
                run_id="ctp-sim-candidate-real-clock-prepare",
                batch_id="ctp-sim-candidate-real-clock-batch",
            )

        assert bundle.prepared_request.checked_at >= snapshot.asof
        state = broker.read_state_snapshot()
        assert state.open_orders == []
        assert state.completed_orders == []
        assert state.fills == []
    finally:
        broker.disconnect()


def test_pre_submit_refusal_rolls_back_staged_plan_and_consumption(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """A refusal before durable intent leaves no plan or consumption committed."""

    _now, _executor, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        original_reserve = candidate_execution._CtpSimCandidateSubmissionGate.reserve
        staged_before_refusal = False

        def _reserve_then_refuse(gate, order) -> None:
            nonlocal staged_before_refusal
            original_reserve(gate, order)
            assert gate._session is not None
            gate._session.flush()
            assert gate._session.scalar(select(ExecutionPlanRecord)) is not None
            assert (
                gate._session.scalar(select(ExecutionProvenanceConsumptionRecord))
                is not None
            )
            staged_before_refusal = True
            raise CtpSimCandidateExecutionError(
                "CTP_SIM_CANDIDATE_TEST_PRE_SUBMIT_REFUSAL"
            )

        monkeypatch.setattr(
            candidate_execution._CtpSimCandidateSubmissionGate,
            "reserve",
            _reserve_then_refuse,
        )
        with postgresql_session_factory() as session:
            with pytest.raises(
                CtpSimCandidateExecutionError,
                match="CTP_SIM_CANDIDATE_TEST_PRE_SUBMIT_REFUSAL",
            ):
                bundle.submit(session)
            assert staged_before_refusal is True
            assert session.scalar(select(ExecutionPlanRecord)) is None
            assert session.scalar(select(ExecutionProvenanceConsumptionRecord)) is None
            assert session.scalar(select(OrderRecord)) is None
        state = broker.read_state_snapshot()
        assert state.open_orders == []
        assert state.completed_orders == []
        assert state.fills == []
    finally:
        broker.disconnect()


def test_completed_bundle_replay_does_not_duplicate_plan_or_consumption(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """The durable idempotency lookup precedes candidate plan staging."""

    _now, _executor, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        with postgresql_session_factory() as session:
            first = bundle.submit(session)
        with postgresql_session_factory() as session:
            replay = bundle.submit(session)
            plans = list(session.scalars(select(ExecutionPlanRecord)))
            consumptions = list(
                session.scalars(select(ExecutionProvenanceConsumptionRecord))
            )
            orders = list(session.scalars(select(OrderRecord)))

        assert first[0].replayed is False
        assert replay[0].replayed is True
        assert len(plans) == 1
        assert len(consumptions) == 1
        assert len(orders) == 1
        state = broker.read_state_snapshot()
        assert len(state.open_orders) + len(state.completed_orders) == 1
    finally:
        broker.disconnect()


@pytest.mark.parametrize(
    ("stale_fact", "error_code"),
    (
        ("state", "CTP_SIM_CANDIDATE_FINAL_BROKER_STATE_STALE"),
        ("quote", "CTP_SIM_CANDIDATE_FINAL_QUOTE_STALE"),
    ),
)
def test_final_adapter_lock_refuses_stale_runtime_facts_before_simulator_mutation(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
    stale_fact: str,
    error_code: str,
) -> None:
    """The final locked adapter check does not rely on semantic equality alone."""

    _now, _executor, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        original_assert_reserved = (
            candidate_execution._CtpSimCandidateSubmissionGate.assert_reserved
        )

        def _assert_with_stale_runtime(gate, order, *, snapshot, quotes) -> None:
            if stale_fact == "state":
                stale_snapshot = replace(
                    snapshot,
                    asof=snapshot.asof
                    - timedelta(
                        seconds=(
                            bundle.prepared_request.settings.runtime_risk_max_state_age_seconds
                            + 1
                        )
                    ),
                )
                original_assert_reserved(
                    gate,
                    order,
                    snapshot=stale_snapshot,
                    quotes=quotes,
                )
                return
            stale_quotes = tuple(
                replace(
                    item,
                    asof=item.asof
                    - timedelta(
                        seconds=(
                            bundle.prepared_request.settings.runtime_risk_max_quote_age_seconds
                            + 1
                        )
                    ),
                )
                for item in quotes
            )
            original_assert_reserved(
                gate,
                order,
                snapshot=snapshot,
                quotes=stale_quotes,
            )

        monkeypatch.setattr(
            candidate_execution._CtpSimCandidateSubmissionGate,
            "assert_reserved",
            _assert_with_stale_runtime,
        )
        with postgresql_session_factory() as session:
            with pytest.raises(CtpSimCandidateExecutionError, match=error_code):
                bundle.submit(session)
            durable = session.scalar(select(OrderRecord))
            assert durable is not None
            assert durable.status == "SubmissionUnknown"
            assert durable.broker_order_id is None
        state = broker.read_state_snapshot()
        assert state.open_orders == []
        assert state.completed_orders == []
        assert state.fills == []
    finally:
        broker.disconnect()


def test_halt_between_reservation_and_adapter_submit_blocks_without_mutation(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """The final locked check reloads the persisted recovery state, not a cache."""

    _now, _executor, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        original_assert_reserved = (
            candidate_execution._CtpSimCandidateSubmissionGate.assert_reserved
        )
        halted = False

        def _halt_before_final_adapter_check(gate, order, *, snapshot, quotes) -> None:
            nonlocal halted
            if not halted:
                halted = True
                with postgresql_session_factory() as safety_session:
                    halt_for_reconciliation(
                        safety_session,
                        profile_id=bundle.receipt.profile_id,
                        broker="ctp_sim",
                        account=broker.get_account(),
                        reason="test final adapter lock halt",
                        evidence={"test": "halt_between_reservation_and_submit"},
                    )
            original_assert_reserved(
                gate,
                order,
                snapshot=snapshot,
                quotes=quotes,
            )

        monkeypatch.setattr(
            candidate_execution._CtpSimCandidateSubmissionGate,
            "assert_reserved",
            _halt_before_final_adapter_check,
        )
        with postgresql_session_factory() as session:
            with pytest.raises(
                CtpSimCandidateExecutionError,
                match="CTP_SIM_CANDIDATE_RECONCILIATION_STATE_BLOCKED: HALT",
            ):
                bundle.submit(session)
            durable = session.scalar(select(OrderRecord))
            assert durable is not None
            assert durable.status == "SubmissionUnknown"
            assert durable.broker_order_id is None
            persisted = latest_reconciliation_safety_state(
                session,
                profile_id=bundle.receipt.profile_id,
                broker="ctp_sim",
                account=broker.get_account(),
            )
            assert persisted is not None and persisted.state == "HALT"
        assert halted is True
        state = broker.read_state_snapshot()
        assert state.open_orders == []
        assert state.completed_orders == []
        assert state.fills == []
    finally:
        broker.disconnect()


def test_prepare_rejects_a_broker_owned_by_another_executor_before_state_read(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """A candidate executor may never validate one simulator and mutate another."""

    now, _owner, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        state_before = broker.state_path.read_bytes()
        foreign_executor = create_test_ctp_sim_candidate_executor(
            settings_provider=lambda: bundle.prepared_request.settings,
            clock=lambda: now["value"],
        )

        def _unexpected_broker_read():
            raise AssertionError("foreign broker must reject before broker access")

        monkeypatch.setattr(broker, "read_state_snapshot", _unexpected_broker_read)
        with postgresql_session_factory() as session:
            with pytest.raises(
                CtpSimCandidateExecutionError,
                match="CTP_SIM_CANDIDATE_FOREIGN_BROKER",
            ):
                foreign_executor.prepare(
                    bundle.source_request,
                    session=session,
                    broker=broker,
                    run_id="ctp-sim-candidate-foreign-owner-run",
                    batch_id="ctp-sim-candidate-foreign-owner-batch",
                )
            assert session.scalar(select(ExecutionPlanRecord)) is None
            assert session.scalar(select(ExecutionProvenanceConsumptionRecord)) is None
            assert session.scalar(select(OrderRecord)) is None
        assert broker.state_path.read_bytes() == state_before
    finally:
        broker.disconnect()


def test_prepare_rechecks_its_broker_against_current_execution_settings(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """A same-executor adapter cannot survive a trusted state-path drift."""

    fixture = build_execution_provenance_fixture(tmp_path / "settings-binding")
    now = {"value": fixture.request.checked_at}
    trusted = fixture.settings.model_copy(
        update={
            "broker": "ctp_sim",
            "live_trading_enabled": False,
            "kill_switch_enabled": False,
            "storage_dir": tmp_path / "storage",
            "downloads_dir": tmp_path / "storage" / "downloads",
            "reports_dir": tmp_path / "reports",
            "log_dir": tmp_path / "logs",
            "ctp_sim_state_path": tmp_path / "storage" / "bound-state.json",
            "ctp_sim_account": "ctp-sim-test",
            "default_cash": 100_000.0,
        }
    )
    current = {"settings": trusted}
    monkeypatch.setattr(ctp_sim_broker, "get_settings", lambda: trusted)
    executor = create_test_ctp_sim_candidate_executor(
        settings_provider=lambda: current["settings"],
        clock=lambda: now["value"],
    )
    broker = executor.create_broker()
    try:
        state_before = broker.state_path.read_bytes()
        current["settings"] = trusted.model_copy(
            update={"ctp_sim_state_path": tmp_path / "storage" / "other-state.json"}
        )

        def _unexpected_broker_read():
            raise AssertionError("binding drift must reject before broker access")

        monkeypatch.setattr(broker, "read_state_snapshot", _unexpected_broker_read)
        with postgresql_session_factory() as session:
            with pytest.raises(
                CtpSimCandidateExecutionError,
                match="CTP_SIM_CANDIDATE_BROKER_BINDING_MISMATCH",
            ):
                executor.prepare(
                    fixture.request,
                    session=session,
                    broker=broker,
                    run_id="ctp-sim-candidate-binding-drift-run",
                    batch_id="ctp-sim-candidate-binding-drift-batch",
                )
            assert session.scalar(select(ExecutionPlanRecord)) is None
            assert session.scalar(select(ExecutionProvenanceConsumptionRecord)) is None
            assert session.scalar(select(OrderRecord)) is None
        assert broker.state_path.read_bytes() == state_before
    finally:
        broker.disconnect()


def test_default_executor_reloads_uncached_settings_before_broker_access(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """The default executor must not retain a cached pre-kill-switch setting."""

    fixture = build_execution_provenance_fixture(tmp_path / "uncached-settings")
    now = {"value": fixture.request.checked_at}
    trusted = fixture.settings.model_copy(
        update={
            "broker": "ctp_sim",
            "live_trading_enabled": False,
            "kill_switch_enabled": False,
            "storage_dir": tmp_path / "storage",
            "downloads_dir": tmp_path / "storage" / "downloads",
            "reports_dir": tmp_path / "reports",
            "log_dir": tmp_path / "logs",
            "ctp_sim_state_path": tmp_path / "storage" / "uncached-state.json",
            "ctp_sim_account": "ctp-sim-test",
            "default_cash": 100_000.0,
        }
    )
    values = iter(
        (
            trusted,
            trusted.model_copy(update={"kill_switch_enabled": True}),
        )
    )
    monkeypatch.setattr(candidate_execution, "load_settings", lambda: next(values))
    monkeypatch.setattr(ctp_sim_broker, "get_settings", lambda: trusted)
    executor = create_test_ctp_sim_candidate_executor(
        clock=lambda: now["value"],
    )
    broker = executor.create_broker()
    try:
        state_before = broker.state_path.read_bytes()
        with postgresql_session_factory() as session:
            with pytest.raises(
                CtpSimCandidateExecutionError,
                match="CTP_SIM_CANDIDATE_KILL_SWITCH_ENABLED",
            ):
                executor.prepare(
                    fixture.request,
                    session=session,
                    broker=broker,
                    run_id="ctp-sim-candidate-uncached-settings-run",
                    batch_id="ctp-sim-candidate-uncached-settings-batch",
                )
            assert session.scalar(select(ExecutionPlanRecord)) is None
            assert session.scalar(select(ExecutionProvenanceConsumptionRecord)) is None
            assert session.scalar(select(OrderRecord)) is None
        assert broker.state_path.read_bytes() == state_before
    finally:
        broker.disconnect()


def test_prepare_refuses_an_active_profile_with_a_different_ctp_mapping(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """Profile futures mapping is an execution identity, not caller metadata."""

    _now, executor, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        source = bundle.source_request
        assert source.profile.futures is not None
        alternate_profile = replace(
            source.profile,
            futures=replace(
                source.profile.futures,
                ctp_contract_mapping_path=str(tmp_path / "alternate-ctp-sim.yaml"),
            ),
        )
        request = replace(source, profile=alternate_profile)
        state_before = broker.state_path.read_bytes()
        monkeypatch.setattr(
            candidate_execution,
            "load_trading_profile_uncached",
            lambda *_args, **_kwargs: alternate_profile,
        )

        def _unexpected_broker_read():
            raise AssertionError("mapping mismatch must reject before broker access")

        monkeypatch.setattr(broker, "read_state_snapshot", _unexpected_broker_read)
        with postgresql_session_factory() as session:
            with pytest.raises(
                CtpSimCandidateExecutionError,
                match="CTP_SIM_CANDIDATE_CONTRACT_MAPPING_MISMATCH",
            ):
                executor.prepare(
                    request,
                    session=session,
                    broker=broker,
                    run_id="ctp-sim-candidate-profile-mapping-run",
                    batch_id="ctp-sim-candidate-profile-mapping-batch",
                )
            assert session.scalar(select(ExecutionPlanRecord)) is None
            assert session.scalar(select(ExecutionProvenanceConsumptionRecord)) is None
            assert session.scalar(select(OrderRecord)) is None
        assert broker.state_path.read_bytes() == state_before
    finally:
        broker.disconnect()


def test_prepare_refuses_a_contract_mapping_rewrite_after_broker_creation(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """The candidate gate reloads the mapping rather than trusting adapter cache."""

    _now, executor, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        assert broker.registry.contracts
        rewritten = replace(
            broker.registry,
            contracts=(
                replace(
                    broker.registry.contracts[0],
                    price_tick=broker.registry.contracts[0].price_tick + 1.0,
                ),
                *broker.registry.contracts[1:],
            ),
        )
        state_before = broker.state_path.read_bytes()
        monkeypatch.setattr(
            candidate_execution,
            "load_ctp_contract_registry",
            lambda *_args, **_kwargs: rewritten,
        )

        def _unexpected_broker_read():
            raise AssertionError("mapping rewrite must reject before broker access")

        monkeypatch.setattr(broker, "read_state_snapshot", _unexpected_broker_read)
        with postgresql_session_factory() as session:
            with pytest.raises(
                CtpSimCandidateExecutionError,
                match="CTP_SIM_CANDIDATE_CONTRACT_MAPPING_MISMATCH",
            ):
                executor.prepare(
                    bundle.source_request,
                    session=session,
                    broker=broker,
                    run_id="ctp-sim-candidate-mapping-rewrite-run",
                    batch_id="ctp-sim-candidate-mapping-rewrite-batch",
                )
            assert session.scalar(select(ExecutionPlanRecord)) is None
            assert session.scalar(select(ExecutionProvenanceConsumptionRecord)) is None
            assert session.scalar(select(OrderRecord)) is None
        assert broker.state_path.read_bytes() == state_before
    finally:
        broker.disconnect()


def test_candidate_final_fence_serializes_a_concurrent_halt_until_submission_commits(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """A HALT requested during final submit cannot interleave before mutation."""

    _now, _executor, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        candidate_fence_acquired = Event()
        halt_fence_attempted = Event()
        halt_finished = Event()
        halt_failures: list[Exception] = []
        worker: Thread | None = None
        original_candidate_fence = candidate_execution.acquire_reconciliation_safety_fence
        original_reconciliation_fence = (
            reconciliation_module.acquire_reconciliation_safety_fence
        )

        def _observe_halt_fence(*args, **kwargs):
            if current_thread().name == "candidate-final-halt":
                halt_fence_attempted.set()
            return original_reconciliation_fence(*args, **kwargs)

        def _halt() -> None:
            try:
                with postgresql_session_factory() as safety_session:
                    halt_for_reconciliation(
                        safety_session,
                        profile_id=bundle.receipt.profile_id,
                        broker="ctp_sim",
                        account=broker.get_account(),
                        reason="concurrent halt while candidate holds final fence",
                        evidence={"test": "candidate_final_fence"},
                    )
            except Exception as exc:  # Thread boundary; assert below.
                halt_failures.append(exc)
            finally:
                halt_finished.set()

        def _candidate_fence(*args, **kwargs):
            nonlocal worker
            key = original_candidate_fence(*args, **kwargs)
            if not candidate_fence_acquired.is_set():
                candidate_fence_acquired.set()
                worker = Thread(target=_halt, name="candidate-final-halt")
                worker.start()
                assert halt_fence_attempted.wait(timeout=5)
                # The candidate still owns the transaction-scoped advisory
                # lock at this point; a separate session must time out rather
                # than observe a gap before simulator mutation.
                with postgresql_session_factory() as probe:
                    probe.execute(text("SET LOCAL lock_timeout = '100ms'"))
                    with pytest.raises(OperationalError):
                        original_candidate_fence(
                            probe,
                            profile_id=bundle.receipt.profile_id,
                            broker="ctp_sim",
                            account=broker.get_account(),
                        )
                    probe.rollback()
                assert not halt_finished.is_set()
            return key

        monkeypatch.setattr(
            reconciliation_module,
            "acquire_reconciliation_safety_fence",
            _observe_halt_fence,
        )
        monkeypatch.setattr(
            candidate_execution,
            "acquire_reconciliation_safety_fence",
            _candidate_fence,
        )
        with postgresql_session_factory() as session:
            results = bundle.submit(session)
            assert results[0].accepted is True
        assert candidate_fence_acquired.is_set()
        assert worker is not None
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert halt_failures == []
        assert halt_finished.is_set()
        with postgresql_session_factory() as session:
            safety = latest_reconciliation_safety_state(
                session,
                profile_id=bundle.receipt.profile_id,
                broker="ctp_sim",
                account=broker.get_account(),
            )
            assert safety is not None and safety.state == "HALT"
            assert len(list(session.scalars(select(OrderRecord)))) == 1
        state = broker.read_state_snapshot()
        assert len(state.open_orders) + len(state.completed_orders) == 1
    finally:
        broker.disconnect()


def test_final_fence_rechecks_receipt_after_waiting_past_its_horizon(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """A receipt that expires while waiting for the final fence cannot mutate CTP-sim."""

    now, _executor, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        state_before = broker.state_path.read_bytes()
        adapter_ready = Event()
        allow_adapter = Event()
        final_fence_attempted = Event()
        submission_failures: list[Exception] = []
        original_submit = broker.submit_order
        original_fence = candidate_execution.acquire_reconciliation_safety_fence

        def _pause_before_final_adapter_lock(order):
            adapter_ready.set()
            if not allow_adapter.wait(timeout=5):
                raise AssertionError("test did not release final adapter path")
            return original_submit(order)

        def _observe_final_fence(*args, **kwargs):
            # ``assert_reserved`` calls this only after its first ready check.
            final_fence_attempted.set()
            return original_fence(*args, **kwargs)

        def _submit() -> None:
            try:
                with postgresql_session_factory() as session:
                    bundle.submit(session)
            except Exception as exc:  # Thread boundary; assert below.
                submission_failures.append(exc)

        monkeypatch.setattr(broker, "submit_order", _pause_before_final_adapter_lock)
        monkeypatch.setattr(
            candidate_execution,
            "acquire_reconciliation_safety_fence",
            _observe_final_fence,
        )
        worker = Thread(target=_submit, name="candidate-receipt-expiry-fence-wait")
        worker.start()
        assert adapter_ready.wait(timeout=5)
        with postgresql_session_factory() as holding_session:
            original_fence(
                holding_session,
                profile_id=bundle.receipt.profile_id,
                broker="ctp_sim",
                account=broker.get_account(),
            )
            allow_adapter.set()
            assert final_fence_attempted.wait(timeout=5)
            # The candidate has passed its pre-fence ready check and is now
            # blocked in PostgreSQL.  Its second post-fence check must refuse.
            now["value"] = bundle.receipt.valid_until
            holding_session.rollback()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert len(submission_failures) == 1
        assert "CTP_SIM_CANDIDATE_RECEIPT_EXPIRED" in str(submission_failures[0])
        assert broker.state_path.read_bytes() == state_before
        state = broker.read_state_snapshot()
        assert state.open_orders == []
        assert state.completed_orders == []
        assert state.fills == []
        with postgresql_session_factory() as session:
            durable = session.scalar(select(OrderRecord))
            assert durable is not None
            assert durable.broker_order_id is None
            assert durable.status == "SubmissionUnknown"
    finally:
        broker.disconnect()


def test_final_fence_does_not_reacquire_the_simulator_lock_after_mutation(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """The locked post-submit snapshot prevents the file/database ABBA cycle."""

    _now, _executor, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        original_submit = broker.submit_order
        competing_file_lock_held = Event()
        worker_failures: list[Exception] = []
        worker: Thread | None = None

        class _CompetingSubmitGuard:
            def reserve(self, order) -> None:
                del order

            def assert_reserved(self, order, *, snapshot, quotes) -> None:
                del order, snapshot, quotes
                # This runs inside the competing adapter's state-file lock.
                # It intentionally waits on the same final account fence that
                # the primary candidate currently owns.
                competing_file_lock_held.set()
                with postgresql_session_factory() as session:
                    acquire_reconciliation_safety_fence(
                        session,
                        profile_id=bundle.receipt.profile_id,
                        broker="ctp_sim",
                        account=broker.get_account(),
                    )
                    session.rollback()

            def mark_submitted(self, order, *, snapshot) -> None:
                del order, snapshot

        competing_broker = CtpSimBrokerAdapter(
            state_path=broker.state_path,
            mapping_path=broker.mapping_path,
            account=broker.get_account(),
            default_cash=broker.default_cash,
            submission_authority=create_test_ctp_sim_submission_authority(
                _CompetingSubmitGuard()
            ),
        )
        competing_broker.connect()

        def _competing_submit() -> None:
            try:
                competing_broker.submit_order(bundle.orders[0])
            except Exception as exc:  # Thread boundary; assert below.
                worker_failures.append(exc)

        def _submit_then_start_file_first_worker(order):
            nonlocal worker
            result = original_submit(order)
            worker = Thread(
                target=_competing_submit,
                name="competing-file-before-final-fence",
            )
            worker.start()
            assert competing_file_lock_held.wait(timeout=5)
            return result

        monkeypatch.setattr(broker, "submit_order", _submit_then_start_file_first_worker)
        with postgresql_session_factory() as session:
            results = bundle.submit(session)

        assert results[0].accepted is True
        assert worker is not None
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert worker_failures == []
    finally:
        if "competing_broker" in locals():
            competing_broker.disconnect()
        broker.disconnect()


def test_halt_holding_the_reconciliation_fence_blocks_candidate_before_intent(
    tmp_path,
    monkeypatch,
    postgresql_session_factory,
) -> None:
    """A pre-existing transition wins before a candidate can persist or mutate."""

    _now, _executor, broker, bundle = _prepared_candidate(
        tmp_path,
        monkeypatch,
        postgresql_session_factory,
    )
    try:
        candidate_fence_attempted = Event()
        submission_finished = Event()
        submission_failures: list[Exception] = []
        original_reconciliation_fence = (
            reconciliation_module.acquire_reconciliation_safety_fence
        )

        def _observe_candidate_fence(*args, **kwargs):
            if current_thread().name == "candidate-after-halt":
                candidate_fence_attempted.set()
            return original_reconciliation_fence(*args, **kwargs)

        def _submit() -> None:
            try:
                with postgresql_session_factory() as session:
                    bundle.submit(session)
            except Exception as exc:  # Thread boundary; assert below.
                submission_failures.append(exc)
            finally:
                submission_finished.set()

        monkeypatch.setattr(
            reconciliation_module,
            "acquire_reconciliation_safety_fence",
            _observe_candidate_fence,
        )
        with postgresql_session_factory() as holding_session:
            candidate_execution.acquire_reconciliation_safety_fence(
                holding_session,
                profile_id=bundle.receipt.profile_id,
                broker="ctp_sim",
                account=broker.get_account(),
            )
            worker = Thread(target=_submit, name="candidate-after-halt")
            worker.start()
            assert candidate_fence_attempted.wait(timeout=5)
            assert not submission_finished.is_set()
            halt_for_reconciliation(
                holding_session,
                profile_id=bundle.receipt.profile_id,
                broker="ctp_sim",
                account=broker.get_account(),
                reason="halt wins before candidate reconciliation",
                evidence={"test": "halt_before_candidate"},
            )
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert submission_finished.is_set()
        assert len(submission_failures) == 1
        assert "CTP_SIM_CANDIDATE_RECONCILIATION_STATE_BLOCKED: HALT" in str(
            submission_failures[0]
        )
        with postgresql_session_factory() as session:
            assert session.scalar(select(ExecutionPlanRecord)) is None
            assert session.scalar(select(ExecutionProvenanceConsumptionRecord)) is None
            assert session.scalar(select(OrderRecord)) is None
        state = broker.read_state_snapshot()
        assert state.open_orders == []
        assert state.completed_orders == []
        assert state.fills == []
    finally:
        broker.disconnect()

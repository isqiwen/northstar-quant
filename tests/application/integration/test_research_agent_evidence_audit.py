"""PostgreSQL integration tests for the P10-WP06 ResearchAgent audit wrapper."""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy.orm import sessionmaker

import northstar_quant.application.research_agent_evidence_audit as evidence_audit
from northstar_quant.application.research_agent_evidence_audit import (
    DurableResearchAgentRunner,
    ResearchAgentEvidenceAuditError,
)
from northstar_quant.platform.db.repositories import read_research_agent_run_audit_trail
from tests.application.unit.test_research_agent import _fixture


def _new_runner(postgresql_session_factory):
    fixture = _fixture()
    return fixture, DurableResearchAgentRunner(fixture.agent, postgresql_session_factory)


def test_runner_commits_hash_only_admission_completion_and_ordered_trace(
    postgresql_session_factory,
) -> None:
    fixture, runner = _new_runner(postgresql_session_factory)

    audited = runner.run(fixture.request)

    assert audited.result.run_id == fixture.request.run_id
    assert audited.result.eligible_for_trading is False
    assert audited.receipt.run_id == fixture.request.run_id
    assert audited.receipt.trace_count == len(audited.result.trace)
    assert audited.receipt.trace_tip_hash == audited.result.trace[-1].trace_hash
    assert audited.receipt.eligible_for_trading is False

    with postgresql_session_factory() as session:
        trail = read_research_agent_run_audit_trail(session, run_id=fixture.request.run_id)

    assert trail is not None
    events, traces = trail
    assert [event.event_kind for event in events] == ["ADMITTED", "COMPLETED"]
    assert all(event.lifecycle == "RESEARCH_ONLY" for event in events)
    assert all(event.eligible_for_trading is False for event in events)
    assert events[-1].request_hash == audited.receipt.request_hash
    assert events[-1].result_hash == audited.receipt.result_hash
    assert events[-1].trace_tail_hash == audited.receipt.trace_tip_hash
    assert events[-1].predecessor_record_hash == audited.receipt.admission_record_hash
    assert events[-1].record_hash == audited.receipt.terminal_record_hash
    assert [trace.sequence for trace in traces] == list(range(1, len(traces) + 1))
    assert [trace.trace_hash for trace in traces] == [entry.trace_hash for entry in audited.result.trace]
    assert all(trace.lifecycle == "RESEARCH_ONLY" for trace in traces)
    assert all(trace.eligible_for_trading is False for trace in traces)
    assert not hasattr(events[-1], "prompt")
    assert not hasattr(events[-1], "query")
    assert not hasattr(events[-1], "payload")
    assert not hasattr(events[-1], "rationale")
    assert not hasattr(events[-1], "exception_message")


def test_existing_cross_session_reservation_rejects_replay_before_any_agent_work(
    postgresql_session_factory,
) -> None:
    first_fixture, first_runner = _new_runner(postgresql_session_factory)
    first_runner.run(first_fixture.request)

    second_fixture, second_runner = _new_runner(postgresql_session_factory)
    with pytest.raises(ResearchAgentEvidenceAuditError, match="RESEARCH_AGENT_AUDIT_ADMISSION_REFUSED"):
        second_runner.run(second_fixture.request)

    assert second_fixture.event_catalog.calls == []
    assert second_fixture.dataset_catalog.calls == []
    assert second_fixture.feature_catalog.calls == []
    assert second_fixture.workflow.create_calls == []
    assert second_fixture.workflow.backtest_calls == []


def test_agent_exception_leaves_unresolved_reservation_and_refuses_replay(
    postgresql_session_factory,
) -> None:
    failed_fixture, failed_runner = _new_runner(postgresql_session_factory)
    failed_fixture.workflow.fail_backtest = True

    with pytest.raises(
        ResearchAgentEvidenceAuditError,
        match="RESEARCH_AGENT_AUDIT_AGENT_OUTCOME_UNRESOLVED",
    ):
        failed_runner.run(failed_fixture.request)

    with postgresql_session_factory() as session:
        trail = read_research_agent_run_audit_trail(session, run_id=failed_fixture.request.run_id)

    assert trail is not None
    events, traces = trail
    assert [event.event_kind for event in events] == ["ADMITTED"]
    assert traces == ()

    replay_fixture, replay_runner = _new_runner(postgresql_session_factory)
    with pytest.raises(ResearchAgentEvidenceAuditError, match="RESEARCH_AGENT_AUDIT_ADMISSION_REFUSED"):
        replay_runner.run(replay_fixture.request)
    assert replay_fixture.event_catalog.calls == []


def test_runner_captures_receipt_values_before_default_expiring_session_closes(
    postgresql_engine,
) -> None:
    """The wrapper must not require the project's non-expiring test factory."""

    default_factory = sessionmaker(bind=postgresql_engine, future=True)
    fixture, runner = _new_runner(default_factory)

    audited = runner.run(fixture.request)

    assert audited.receipt.admission_record_hash
    assert audited.receipt.terminal_record_hash
    with default_factory() as session:
        trail = read_research_agent_run_audit_trail(session, run_id=fixture.request.run_id)
    assert trail is not None
    assert [event.event_kind for event in trail[0]] == ["ADMITTED", "COMPLETED"]


def test_completion_uncertainty_leaves_unresolved_reservation_and_refuses_replay(
    postgresql_session_factory,
    monkeypatch,
) -> None:
    fixture, runner = _new_runner(postgresql_session_factory)

    def _completion_transport_lost(*args, **kwargs):
        del args, kwargs
        raise OSError("connection lost after terminal write may have occurred")

    monkeypatch.setattr(evidence_audit, "complete_research_agent_run", _completion_transport_lost)
    with pytest.raises(
        ResearchAgentEvidenceAuditError,
        match="RESEARCH_AGENT_AUDIT_COMPLETION_UNRESOLVED",
    ):
        runner.run(fixture.request)

    with postgresql_session_factory() as session:
        trail = read_research_agent_run_audit_trail(session, run_id=fixture.request.run_id)

    assert trail is not None
    events, traces = trail
    assert [event.event_kind for event in events] == ["ADMITTED"]
    assert traces == ()

    replay_fixture, replay_runner = _new_runner(postgresql_session_factory)
    with pytest.raises(ResearchAgentEvidenceAuditError, match="RESEARCH_AGENT_AUDIT_ADMISSION_REFUSED"):
        replay_runner.run(replay_fixture.request)
    assert replay_fixture.event_catalog.calls == []


def test_result_binding_drift_is_recorded_as_stable_failure_without_raw_result(
    postgresql_session_factory,
    monkeypatch,
) -> None:
    fixture, runner = _new_runner(postgresql_session_factory)
    original_run = fixture.agent.run

    def _result_with_mismatched_run(_self, request):
        return replace(original_run(request), run_id="different-run")

    monkeypatch.setattr(type(fixture.agent), "run", _result_with_mismatched_run)
    with pytest.raises(
        ResearchAgentEvidenceAuditError,
        match="RESEARCH_AGENT_AUDIT_RESULT_REQUEST_MISMATCH",
    ):
        runner.run(fixture.request)

    with postgresql_session_factory() as session:
        trail = read_research_agent_run_audit_trail(session, run_id=fixture.request.run_id)

    assert trail is not None
    events, traces = trail
    assert [event.event_kind for event in events] == ["ADMITTED", "FAILED"]
    assert events[-1].failure_code == "RESEARCH_AGENT_RESULT_INVALID"
    assert traces == ()

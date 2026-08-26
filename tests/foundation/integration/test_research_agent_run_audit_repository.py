"""PostgreSQL integration tests for immutable hash-only ResearchAgent audit facts."""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import ast
import inspect
import json
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine, inspect as sqlalchemy_inspect, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import sessionmaker

import northstar_quant.foundation.config.settings as settings_module
import northstar_quant.foundation.db.init_db as init_db_module
from northstar_quant.foundation.config.settings import Settings, get_settings
from northstar_quant.foundation.db.init_db import init_db
from northstar_quant.foundation.db.models import (
    ResearchAgentRunAuditEventRecord,
    ResearchAgentRunTraceEntryRecord,
)
from northstar_quant.foundation.db.repositories import (
    ResearchAgentRunAuditError,
    ResearchAgentRunTraceInput,
    admit_research_agent_run,
    complete_research_agent_run,
    fail_research_agent_run,
    read_research_agent_run_audit_trail,
)
from tests.helpers.postgresql import postgresql_test_url


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime(2026, 8, 23, 12, tzinfo=UTC)


def _trace_input(
    *,
    sequence: int,
    tool_name: str,
    predecessor_trace_hash: str | None,
) -> ResearchAgentRunTraceInput:
    request_hash = _hash(f"request-{sequence}")
    response_hash = _hash(f"response-{sequence}")
    trace_hash = sha256(
        json.dumps(
            {
                "format": "northstar.research-agent-trace.v1",
                "predecessor_trace_hash": predecessor_trace_hash,
                "request_hash": request_hash,
                "response_hash": response_hash,
                "sequence": sequence,
                "tool_name": tool_name,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return ResearchAgentRunTraceInput(
        sequence=sequence,
        tool_name=tool_name,
        request_hash=request_hash,
        response_hash=response_hash,
        predecessor_trace_hash=predecessor_trace_hash,
        trace_hash=trace_hash,
    )


def _trace_inputs() -> tuple[ResearchAgentRunTraceInput, ...]:
    first = _trace_input(
        sequence=1,
        tool_name="search_events",
        predecessor_trace_hash=None,
    )
    second = _trace_input(
        sequence=2,
        tool_name="search_datasets",
        predecessor_trace_hash=first.trace_hash,
    )
    return first, second


def _admit(session, *, run_id: str = "research-audit-run-1") -> ResearchAgentRunAuditEventRecord:
    return admit_research_agent_run(
        session,
        run_id=run_id,
        request_hash=_hash(f"request-{run_id}"),
        as_of=_now(),
        admitted_at=_now(),
    )


def _assert_database_refusal(
    engine,
    statement: str,
    *,
    expected: str,
    parameters: Mapping[str, object] | None = None,
) -> None:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(DatabaseError, match=expected):
                connection.execute(text(statement), parameters or {})
        finally:
            transaction.rollback()


def test_admission_is_non_idempotent_and_completion_writes_a_validated_trace_atomically(
    postgresql_session_factory,
) -> None:
    with postgresql_session_factory() as session:
        admitted = _admit(session)
        admitted_run_id = admitted.run_id
        request_hash = admitted.request_hash
        admitted_as_of = admitted.as_of
        admitted_at = admitted.occurred_at

        with pytest.raises(ResearchAgentRunAuditError, match="RESEARCH_AGENT_RUN_ALREADY_RESERVED"):
            admit_research_agent_run(
                session,
                run_id=admitted_run_id,
                request_hash=request_hash,
                as_of=admitted_as_of,
                admitted_at=admitted_at,
            )

        completed = complete_research_agent_run(
            session,
            run_id=admitted_run_id,
            request_hash=request_hash,
            result_hash=_hash("result-research-audit-run-1"),
            trace_entries=_trace_inputs(),
            completed_at=admitted_at + timedelta(seconds=1),
        )
        trail = read_research_agent_run_audit_trail(session, run_id=admitted_run_id)

    assert trail is not None
    events, trace_entries = trail
    assert tuple(event.event_kind for event in events) == ("ADMITTED", "COMPLETED")
    assert completed.predecessor_record_hash == admitted.record_hash
    assert completed.trace_count == len(trace_entries) == 2
    assert completed.trace_root_hash == trace_entries[0].trace_hash
    assert completed.trace_tail_hash == trace_entries[-1].trace_hash
    assert all(event.lifecycle == "RESEARCH_ONLY" for event in events)
    assert all(event.eligible_for_trading is False for event in events)
    assert all(entry.lifecycle == "RESEARCH_ONLY" for entry in trace_entries)
    assert all(entry.eligible_for_trading is False for entry in trace_entries)

    with postgresql_session_factory() as session:
        with pytest.raises(
            ResearchAgentRunAuditError,
            match="RESEARCH_AGENT_RUN_TERMINAL_ALREADY_RECORDED",
        ):
            complete_research_agent_run(
                session,
                run_id=admitted_run_id,
                request_hash=request_hash,
                result_hash=_hash("result-research-audit-run-1"),
                trace_entries=_trace_inputs(),
                completed_at=completed.occurred_at + timedelta(seconds=1),
            )


def test_failure_is_a_single_terminal_stable_code_without_trace_or_retry(
    postgresql_session_factory,
) -> None:
    with postgresql_session_factory() as session:
        admitted = _admit(session, run_id="research-audit-failure-1")
        failed = fail_research_agent_run(
            session,
            run_id=admitted.run_id,
            request_hash=admitted.request_hash,
            failure_code="RESEARCH_AGENT_RESULT_INVALID",
            failed_at=admitted.occurred_at + timedelta(seconds=1),
        )
        trail = read_research_agent_run_audit_trail(session, run_id=admitted.run_id)

    assert trail is not None
    events, trace_entries = trail
    assert tuple(event.event_kind for event in events) == ("ADMITTED", "FAILED")
    assert failed.failure_code == "RESEARCH_AGENT_RESULT_INVALID"
    assert failed.result_hash is None
    assert failed.trace_count == 0
    assert trace_entries == ()

    with postgresql_session_factory() as session:
        with pytest.raises(
            ResearchAgentRunAuditError,
            match="RESEARCH_AGENT_RUN_TERMINAL_ALREADY_RECORDED",
        ):
            fail_research_agent_run(
                session,
                run_id=admitted.run_id,
                request_hash=admitted.request_hash,
                failure_code="RESEARCH_AGENT_RESULT_INVALID",
                failed_at=failed.occurred_at + timedelta(seconds=1),
            )


def test_failure_api_refuses_unproven_terminal_failure_codes(
    postgresql_session_factory,
) -> None:
    with postgresql_session_factory() as session:
        admitted = _admit(session, run_id="research-audit-unproven-failure-1")

        with pytest.raises(
            ResearchAgentRunAuditError,
            match="RESEARCH_AGENT_RUN_AUDIT_INVALID_FAILURE_CODE",
        ):
            fail_research_agent_run(
                session,
                run_id=admitted.run_id,
                request_hash=admitted.request_hash,
                failure_code="TOOL_RESPONSE_UNKNOWN",
                failed_at=admitted.occurred_at + timedelta(seconds=1),
            )

        trail = read_research_agent_run_audit_trail(session, run_id=admitted.run_id)

    assert trail is not None
    events, trace_entries = trail
    assert tuple(event.event_kind for event in events) == ("ADMITTED",)
    assert trace_entries == ()


def test_reader_rejects_tampered_hash_or_trace_order(
    postgresql_session_factory,
) -> None:
    with postgresql_session_factory() as session:
        admitted = _admit(session, run_id="research-audit-tamper-1")
        admitted_run_id = admitted.run_id
        complete_research_agent_run(
            session,
            run_id=admitted.run_id,
            request_hash=admitted.request_hash,
            result_hash=_hash("result-research-audit-tamper-1"),
            trace_entries=_trace_inputs(),
            completed_at=admitted.occurred_at + timedelta(seconds=1),
        )
        trail = read_research_agent_run_audit_trail(session, run_id=admitted_run_id)
        assert trail is not None
        _, trace_entries = trail
        trace_entries[1].predecessor_trace_hash = _hash("tampered-predecessor")

        with pytest.raises(
            ResearchAgentRunAuditError,
            match="RESEARCH_AGENT_RUN_AUDIT_TRACE_HASH_MISMATCH",
        ):
            read_research_agent_run_audit_trail(session, run_id=admitted_run_id)
        session.rollback()

    with postgresql_session_factory() as session:
        events, _ = read_research_agent_run_audit_trail(session, run_id=admitted_run_id) or (
            (),
            (),
        )
        events[0].record_hash = _hash("tampered-admission-record")
        with pytest.raises(
            ResearchAgentRunAuditError,
            match="RESEARCH_AGENT_RUN_AUDIT_RECORD_TAMPERED",
        ):
            read_research_agent_run_audit_trail(session, run_id=admitted_run_id)
        session.rollback()


def test_concurrent_cross_session_admission_allows_one_reservation_only(
    postgresql_session_factory,
) -> None:
    barrier = Barrier(2)
    run_id = "research-audit-concurrent-1"
    request_hash = _hash("request-research-audit-concurrent-1")

    def attempt_admission() -> str:
        with postgresql_session_factory() as session:
            barrier.wait(timeout=5)
            try:
                admit_research_agent_run(
                    session,
                    run_id=run_id,
                    request_hash=request_hash,
                    as_of=_now(),
                    admitted_at=_now(),
                )
            except ResearchAgentRunAuditError as exc:
                return str(exc)
            return "ADMITTED"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(lambda _: attempt_admission(), range(2), timeout=15))

    assert outcomes.count("ADMITTED") == 1
    assert outcomes.count("RESEARCH_AGENT_RUN_ALREADY_RESERVED") == 1


def test_models_and_repository_have_only_hash_safe_audit_fields() -> None:
    event_columns = {
        column.key for column in sqlalchemy_inspect(ResearchAgentRunAuditEventRecord).columns
    }
    trace_columns = {
        column.key for column in sqlalchemy_inspect(ResearchAgentRunTraceEntryRecord).columns
    }
    forbidden_names = {
        "prompt",
        "query",
        "rationale",
        "payload",
        "error",
        "detail",
        "text",
        "json",
    }
    forbidden_types = {"JSON", "JSONB", "Text"}
    migration = (
        Path(__file__).parents[3] / "alembic" / "versions" / "0001_current_schema_baseline.py"
    ).read_text(encoding="utf-8")
    migration_tree = ast.parse(migration)
    audit_helpers = "\n".join(
        ast.get_source_segment(migration, node) or ""
        for node in migration_tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        in {
            "_apply_research_agent_run_audit",
            "_apply_research_agent_run_audit_hardening",
        }
    )

    assert not {
        name
        for name in event_columns | trace_columns
        if any(forbidden in name.casefold() for forbidden in forbidden_names)
    }
    audit_columns = (
        *sqlalchemy_inspect(ResearchAgentRunAuditEventRecord).columns,
        *sqlalchemy_inspect(ResearchAgentRunTraceEntryRecord).columns,
    )
    assert not {
        type(column.type).__name__
        for column in audit_columns
        if type(column.type).__name__ in forbidden_types
    }
    assert "sa.Text" not in audit_helpers
    assert "sa.JSON" not in audit_helpers
    assert "prompt" not in audit_helpers.casefold()
    assert "payload" not in audit_helpers.casefold()
    assert "rationale" not in audit_helpers.casefold()
    assert 'sa.Column("error"' not in audit_helpers
    assert 'revision = "0001_current_schema_baseline"' in migration
    assert "down_revision = None" in migration
    assert "ck_research_agent_audit_request_hash" in audit_helpers
    assert "ck_research_agent_trace_tool_name" in audit_helpers
    assert "ck_research_agent_audit_failure_code" in audit_helpers
    assert "BEFORE TRUNCATE ON research_agent_run_audit_events" in audit_helpers
    assert "BEFORE TRUNCATE ON research_agent_run_trace_entries" in audit_helpers
    assert "def downgrade" in migration
    assert "forward-only" in migration
    assert set(inspect.signature(admit_research_agent_run).parameters) == {
        "session",
        "run_id",
        "request_hash",
        "as_of",
        "admitted_at",
    }
    assert "trace_entries" in inspect.signature(complete_research_agent_run).parameters
    assert "failure_code" in inspect.signature(fail_research_agent_run).parameters


def test_upgrade_head_rejects_direct_raw_hash_tool_and_failure_inserts(
    tmp_path,
    monkeypatch,
) -> None:
    storage_dir = tmp_path / "storage"
    database_url = postgresql_test_url(tmp_path / "research-agent-audit-constraints")
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=database_url,
        storage_dir=storage_dir,
        downloads_dir=storage_dir / "downloads",
        reports_dir=tmp_path / "reports",
        log_dir=tmp_path / "logs",
    )
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)
    monkeypatch.setattr(init_db_module, "get_settings", lambda: settings)
    engine = create_engine(database_url, future=True)
    event_insert = """
        INSERT INTO research_agent_run_audit_events (
            run_id, event_kind, is_terminal, request_hash, result_hash,
            failure_code, trace_count, trace_root_hash, trace_tail_hash,
            as_of, occurred_at, predecessor_record_hash, lifecycle,
            eligible_for_trading, record_hash
        ) VALUES (
            :run_id, :event_kind, :is_terminal, :request_hash, :result_hash,
            :failure_code, :trace_count, :trace_root_hash, :trace_tail_hash,
            :as_of, :occurred_at, :predecessor_record_hash, :lifecycle,
            :eligible_for_trading, :record_hash
        )
    """
    trace_insert = """
        INSERT INTO research_agent_run_trace_entries (
            run_id, sequence, tool_name, request_hash, response_hash,
            predecessor_trace_hash, trace_hash, recorded_at, lifecycle,
            eligible_for_trading, record_hash
        ) VALUES (
            :run_id, :sequence, :tool_name, :request_hash, :response_hash,
            :predecessor_trace_hash, :trace_hash, :recorded_at, :lifecycle,
            :eligible_for_trading, :record_hash
        )
    """
    now = _now()
    try:
        init_db()
        admitted_event = {
            "event_kind": "ADMITTED",
            "is_terminal": False,
            "request_hash": _hash("direct-admitted-request"),
            "result_hash": None,
            "failure_code": None,
            "trace_count": 0,
            "trace_root_hash": None,
            "trace_tail_hash": None,
            "as_of": now,
            "occurred_at": now,
            "predecessor_record_hash": None,
            "lifecycle": "RESEARCH_ONLY",
            "eligible_for_trading": False,
            "record_hash": _hash("direct-admitted-record"),
        }

        for run_id, request_hash in (
            ("research-audit-direct-raw-hash", "raw prompt content is forbidden"),
            ("research-audit-direct-short-hash", "f" * 63),
        ):
            _assert_database_refusal(
                engine,
                event_insert,
                expected="ck_research_agent_audit_request_hash",
                parameters={
                    **admitted_event,
                    "run_id": run_id,
                    "request_hash": request_hash,
                },
            )

        _assert_database_refusal(
            engine,
            trace_insert,
            expected="ck_research_agent_trace_tool_name",
            parameters={
                "run_id": "research-audit-direct-invalid-tool",
                "sequence": 1,
                "tool_name": "write_secret_file",
                "request_hash": _hash("direct-trace-request"),
                "response_hash": _hash("direct-trace-response"),
                "predecessor_trace_hash": None,
                "trace_hash": _hash("direct-trace-hash"),
                "recorded_at": now,
                "lifecycle": "RESEARCH_ONLY",
                "eligible_for_trading": False,
                "record_hash": _hash("direct-trace-record"),
            },
        )

        _assert_database_refusal(
            engine,
            trace_insert,
            expected="ck_research_agent_trace_response_hash",
            parameters={
                "run_id": "research-audit-direct-invalid-trace-hash",
                "sequence": 1,
                "tool_name": "search_events",
                "request_hash": _hash("direct-valid-trace-request"),
                "response_hash": "f" * 63,
                "predecessor_trace_hash": None,
                "trace_hash": _hash("direct-valid-trace-hash"),
                "recorded_at": now,
                "lifecycle": "RESEARCH_ONLY",
                "eligible_for_trading": False,
                "record_hash": _hash("direct-valid-trace-record"),
            },
        )

        _assert_database_refusal(
            engine,
            event_insert,
            expected="ck_research_agent_audit_failure_code",
            parameters={
                "run_id": "research-audit-direct-invalid-failure",
                "event_kind": "FAILED",
                "is_terminal": True,
                "request_hash": _hash("direct-failure-request"),
                "result_hash": None,
                "failure_code": "UNPROVEN_AGENT_FAILURE",
                "trace_count": 0,
                "trace_root_hash": None,
                "trace_tail_hash": None,
                "as_of": now,
                "occurred_at": now,
                "predecessor_record_hash": _hash("direct-failure-predecessor"),
                "lifecycle": "RESEARCH_ONLY",
                "eligible_for_trading": False,
                "record_hash": _hash("direct-failure-record"),
            },
        )
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_upgrade_head_installs_full_postgresql_immutability_for_both_audit_tables(
    tmp_path,
    monkeypatch,
) -> None:
    storage_dir = tmp_path / "storage"
    database_url = postgresql_test_url(tmp_path / "research-agent-audit-migration")
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=database_url,
        storage_dir=storage_dir,
        downloads_dir=storage_dir / "downloads",
        reports_dir=tmp_path / "reports",
        log_dir=tmp_path / "logs",
    )
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)
    monkeypatch.setattr(init_db_module, "get_settings", lambda: settings)
    engine = create_engine(database_url, future=True)
    try:
        init_db()
        factory = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
        with factory() as session:
            admitted = _admit(session, run_id="research-audit-trigger-1")
            complete_research_agent_run(
                session,
                run_id=admitted.run_id,
                request_hash=admitted.request_hash,
                result_hash=_hash("result-research-audit-trigger-1"),
                trace_entries=_trace_inputs(),
                completed_at=admitted.occurred_at + timedelta(seconds=1),
            )

        for statement, parameters in (
            (
                "UPDATE research_agent_run_audit_events "
                "SET request_hash = :request_hash WHERE run_id = :run_id",
                {
                    "request_hash": _hash("attempted-event-mutation"),
                    "run_id": admitted.run_id,
                },
            ),
            (
                "DELETE FROM research_agent_run_audit_events WHERE run_id = :run_id",
                {"run_id": admitted.run_id},
            ),
            (
                "UPDATE research_agent_run_trace_entries "
                "SET response_hash = :response_hash WHERE run_id = :run_id",
                {
                    "response_hash": _hash("attempted-trace-mutation"),
                    "run_id": admitted.run_id,
                },
            ),
            (
                "DELETE FROM research_agent_run_trace_entries WHERE run_id = :run_id",
                {"run_id": admitted.run_id},
            ),
            ("TRUNCATE TABLE research_agent_run_audit_events", {}),
            ("TRUNCATE TABLE research_agent_run_trace_entries", {}),
        ):
            _assert_database_refusal(
                engine,
                statement,
                expected="RESEARCH_AGENT_RUN_AUDIT_IMMUTABLE",
                parameters=parameters,
            )
    finally:
        engine.dispose()
        get_settings.cache_clear()

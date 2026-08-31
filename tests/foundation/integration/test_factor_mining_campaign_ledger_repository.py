"""PostgreSQL integration coverage for the factor-mining campaign ledger seam."""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Barrier

import pytest
from sqlalchemy import create_engine, inspect as sqlalchemy_inspect, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import sessionmaker

import northstar_quant.foundation.config.settings as settings_module
import northstar_quant.foundation.db.init_db as init_db_module
from northstar_quant.data.artifacts.fingerprints import canonical_json_sha256
from northstar_quant.foundation.config.settings import Settings, get_settings
from northstar_quant.foundation.db.init_db import init_db
from northstar_quant.foundation.db.models import (
    FactorMiningCampaignRecord,
    FactorMiningCampaignRequestEventRecord,
)
from northstar_quant.foundation.db.repositories import (
    FactorMiningCampaignFailureCode,
    FactorMiningCampaignLedgerError,
    FactorMiningCampaignRegistration,
    _FactorMiningCampaignReplayAuthorizationInput,
    FactorMiningCampaignRequestEventAppend,
    FactorMiningCampaignRequestEventKind,
    FactorMiningCampaignRequestReservation,
    FactorMiningCampaignResourceUsage,
    factor_mining_campaign_append_event,
    _factor_mining_campaign_authorize_replay,
    factor_mining_campaign_read_request_ledger,
    factor_mining_campaign_register,
    factor_mining_campaign_reserve_request,
)
from tests.helpers.postgresql import postgresql_test_url


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _now(offset_seconds: int = 0) -> datetime:
    return datetime(2026, 8, 31, 12, tzinfo=UTC) + timedelta(seconds=offset_seconds)


def _registration(
    *,
    campaign_id: str = "factor_mining_campaign_1",
    max_concurrent_runs: int = 2,
    declaration_hash: str | None = None,
    declaration_snapshot_hash: str | None = None,
) -> FactorMiningCampaignRegistration:
    return FactorMiningCampaignRegistration(
        campaign_id=campaign_id,
        campaign_hash=_hash(f"{campaign_id}:campaign"),
        declaration_hash=(
            _hash(f"{campaign_id}:declaration")
            if declaration_hash is None
            else declaration_hash
        ),
        declaration_snapshot_hash=(
            _hash(f"{campaign_id}:declaration-snapshot")
            if declaration_snapshot_hash is None
            else declaration_snapshot_hash
        ),
        decision_replay_plan_hash=_hash(f"{campaign_id}:plan"),
        dataset_version_set_hash=_hash(f"{campaign_id}:dataset"),
        template_hash=_hash(f"{campaign_id}:template"),
        search_budget_hash=_hash(f"{campaign_id}:search-budget"),
        selection_policy_hash=_hash(f"{campaign_id}:selection"),
        generator_id="local_generator_v1",
        generator_model_revision_hash=_hash(f"{campaign_id}:generator-revision"),
        prompt_template_hash=_hash(f"{campaign_id}:generator-template"),
        source_authorization_hash=_hash(f"{campaign_id}:source-authorization"),
        runner_resource_budget_hash=_hash(f"{campaign_id}:runner-budget"),
        max_concurrent_runs=max_concurrent_runs,
        code_revision_hash=_hash(f"{campaign_id}:code-revision"),
        selection_at=_now(60),
        registered_at=_now(),
    )


def _reservation(
    registration: FactorMiningCampaignRegistration,
    *,
    request_id: str,
    request_seed: str,
    request_actor_id: str = "researcher:1",
    offset_seconds: int = 1,
    replay_authorization_hash: str | None = None,
) -> FactorMiningCampaignRequestReservation:
    return FactorMiningCampaignRequestReservation(
        campaign_id=registration.campaign_id,
        campaign_hash=registration.campaign_hash,
        request_id=request_id,
        request_hash=_hash(request_seed),
        request_actor_id=request_actor_id,
        source_authorization_hash=registration.source_authorization_hash,
        resource_budget_hash=registration.runner_resource_budget_hash,
        reserved_at=_now(offset_seconds),
        replay_authorization_hash=replay_authorization_hash,
    )


def _resource_usage(seed: str, *, concurrency: int = 1) -> FactorMiningCampaignResourceUsage:
    del seed
    values = {
        "artifact_byte_count": 5,
        "cpu_milliseconds": 1,
        "data_row_count": 4,
        "format": "northstar.factor-mining-campaign-resource-usage.v1",
        "max_concurrency_observed": concurrency,
        "peak_memory_bytes": 2,
        "wall_clock_milliseconds": 3,
    }
    return FactorMiningCampaignResourceUsage(
        resource_usage_hash=canonical_json_sha256(values),
        max_concurrency_observed=values["max_concurrency_observed"],
        cpu_milliseconds=values["cpu_milliseconds"],
        peak_memory_bytes=values["peak_memory_bytes"],
        wall_clock_milliseconds=values["wall_clock_milliseconds"],
        data_row_count=values["data_row_count"],
        artifact_byte_count=values["artifact_byte_count"],
    )


def test_resource_usage_rejects_a_hash_not_bound_to_its_measurements() -> None:
    with pytest.raises(
        FactorMiningCampaignLedgerError,
        match="FACTOR_MINING_CAMPAIGN_LEDGER_RESOURCE_USAGE_HASH_MISMATCH",
    ):
        FactorMiningCampaignResourceUsage(
            resource_usage_hash=_hash("unrelated-resource-usage"),
            max_concurrency_observed=1,
            cpu_milliseconds=1,
            peak_memory_bytes=2,
            wall_clock_milliseconds=3,
            data_row_count=4,
            artifact_byte_count=5,
        )


def _append(
    session,
    *,
    request_id: str,
    kind: FactorMiningCampaignRequestEventKind,
    offset_seconds: int,
    seed: str,
    selected_candidate_count: int | None = None,
    resource_usage: FactorMiningCampaignResourceUsage | None = None,
) -> None:
    factor_mining_campaign_append_event(
        session,
        append=FactorMiningCampaignRequestEventAppend(
            request_id=request_id,
            event_kind=kind,
            occurred_at=_now(offset_seconds),
            generation_receipt_hash=(
                _hash(f"{seed}:receipt")
                if kind is FactorMiningCampaignRequestEventKind.RECEIPT_RECORDED
                else None
            ),
            discovery_result_hash=(
                _hash(f"{seed}:discovery")
                if kind is FactorMiningCampaignRequestEventKind.DISCOVERY_RECORDED
                else None
            ),
            selection_commitment_hash=(
                _hash(f"{seed}:selection")
                if kind is FactorMiningCampaignRequestEventKind.SELECTION_COMMITTED
                else None
            ),
            oos_release_hash=(
                _hash(f"{seed}:oos")
                if kind is FactorMiningCampaignRequestEventKind.OOS_RELEASED
                else None
            ),
            bundle_snapshot_hash=(
                _hash(f"{seed}:bundle")
                if kind is FactorMiningCampaignRequestEventKind.RESULT_RECORDED
                else None
            ),
            manifest_snapshot_hash=(
                _hash(f"{seed}:manifest")
                if kind is FactorMiningCampaignRequestEventKind.RESULT_RECORDED
                else None
            ),
            result_hash=(
                _hash(f"{seed}:result")
                if kind is FactorMiningCampaignRequestEventKind.RESULT_RECORDED
                else None
            ),
            candidate_count=(
                2 if kind is FactorMiningCampaignRequestEventKind.RECEIPT_RECORDED else None
            ),
            selected_candidate_count=(
                selected_candidate_count
                if kind is FactorMiningCampaignRequestEventKind.SELECTION_COMMITTED
                else None
            ),
            resource_usage=resource_usage,
        ),
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


def test_hash_linked_stage_chain_and_terminal_resource_usage_are_verified(
    postgresql_session_factory,
) -> None:
    registration = _registration()
    with postgresql_session_factory() as session:
        factor_mining_campaign_register(session, registration=registration)
        reserved = factor_mining_campaign_reserve_request(
            session,
            reservation=_reservation(
                registration,
                request_id="factor_mining_request_1",
                request_seed="request-1",
            ),
        )
        assert reserved.active_concurrency_observed == 1
        _append(
            session,
            request_id=reserved.request_id,
            kind=FactorMiningCampaignRequestEventKind.RECEIPT_RECORDED,
            offset_seconds=2,
            seed="request-1",
        )
        _append(
            session,
            request_id=reserved.request_id,
            kind=FactorMiningCampaignRequestEventKind.DISCOVERY_RECORDED,
            offset_seconds=3,
            seed="request-1",
        )
        _append(
            session,
            request_id=reserved.request_id,
            kind=FactorMiningCampaignRequestEventKind.SELECTION_COMMITTED,
            offset_seconds=4,
            seed="request-1",
            selected_candidate_count=1,
        )
        _append(
            session,
            request_id=reserved.request_id,
            kind=FactorMiningCampaignRequestEventKind.OOS_RESERVED,
            offset_seconds=5,
            seed="request-1",
        )
        _append(
            session,
            request_id=reserved.request_id,
            kind=FactorMiningCampaignRequestEventKind.OOS_RELEASED,
            offset_seconds=6,
            seed="request-1",
        )
        _append(
            session,
            request_id=reserved.request_id,
            kind=FactorMiningCampaignRequestEventKind.RESULT_RECORDED,
            offset_seconds=7,
            seed="request-1",
            resource_usage=_resource_usage("request-1"),
        )
        ledger = factor_mining_campaign_read_request_ledger(
            session,
            request_id=reserved.request_id,
        )

    assert ledger is not None
    assert ledger.campaign.max_concurrent_runs == 2
    assert ledger.campaign.declaration_hash == registration.declaration_hash
    assert ledger.campaign.declaration_snapshot_hash == registration.declaration_snapshot_hash
    assert tuple(event.event_kind for event in ledger.events) == (
        "RESERVED",
        "RECEIPT_RECORDED",
        "DISCOVERY_RECORDED",
        "SELECTION_COMMITTED",
        "OOS_RESERVED",
        "OOS_RELEASED",
        "RESULT_RECORDED",
    )
    assert all(event.active_concurrency_observed == 1 for event in ledger.events)
    assert all(event.request_actor_id == "researcher:1" for event in ledger.events)
    assert ledger.events[-1].is_terminal is True
    assert ledger.events[-1].predecessor_record_hash == ledger.events[-2].record_hash


def test_result_cannot_be_recorded_without_complete_measured_resource_usage(
    postgresql_session_factory,
) -> None:
    registration = _registration(campaign_id="factor_mining_campaign_resource_1")
    with postgresql_session_factory() as session:
        factor_mining_campaign_register(session, registration=registration)
        reserved = factor_mining_campaign_reserve_request(
            session,
            reservation=_reservation(
                registration,
                request_id="factor_mining_resource_request_1",
                request_seed="resource-request-1",
            ),
        )
        _append(
            session,
            request_id=reserved.request_id,
            kind=FactorMiningCampaignRequestEventKind.RECEIPT_RECORDED,
            offset_seconds=2,
            seed="resource-request-1",
        )
        _append(
            session,
            request_id=reserved.request_id,
            kind=FactorMiningCampaignRequestEventKind.DISCOVERY_RECORDED,
            offset_seconds=3,
            seed="resource-request-1",
        )
        _append(
            session,
            request_id=reserved.request_id,
            kind=FactorMiningCampaignRequestEventKind.SELECTION_COMMITTED,
            offset_seconds=4,
            seed="resource-request-1",
            selected_candidate_count=0,
        )
        with pytest.raises(
            FactorMiningCampaignLedgerError,
            match="FACTOR_MINING_CAMPAIGN_LEDGER_APPEND_INPUT_INVALID",
        ):
            _append(
                session,
                request_id=reserved.request_id,
                kind=FactorMiningCampaignRequestEventKind.RESULT_RECORDED,
                offset_seconds=5,
                seed="resource-request-1",
            )
        ledger = factor_mining_campaign_read_request_ledger(
            session,
            request_id=reserved.request_id,
        )

    assert ledger is not None
    assert ledger.events[-1].event_kind == "SELECTION_COMMITTED"
    assert ledger.events[-1].is_terminal is False


def test_campaign_registration_binds_declaration_commitments_and_request_actor(
    postgresql_session_factory,
) -> None:
    registration = _registration(campaign_id="factor_mining_campaign_binding_1")
    altered_declaration = _registration(
        campaign_id=registration.campaign_id,
        declaration_snapshot_hash=_hash("altered-declaration-snapshot"),
    )
    altered_declaration_hash = _registration(
        campaign_id=registration.campaign_id,
        declaration_hash=_hash("altered-declaration"),
    )
    with postgresql_session_factory() as session:
        receipt = factor_mining_campaign_register(session, registration=registration)
        assert receipt.declaration_hash == registration.declaration_hash
        assert receipt.declaration_snapshot_hash == registration.declaration_snapshot_hash

        with pytest.raises(
            FactorMiningCampaignLedgerError,
            match="FACTOR_MINING_CAMPAIGN_LEDGER_CAMPAIGN_ID_CONFLICT",
        ):
            factor_mining_campaign_register(session, registration=altered_declaration)
        with pytest.raises(
            FactorMiningCampaignLedgerError,
            match="FACTOR_MINING_CAMPAIGN_LEDGER_CAMPAIGN_ID_CONFLICT",
        ):
            factor_mining_campaign_register(session, registration=altered_declaration_hash)

        reserved = factor_mining_campaign_reserve_request(
            session,
            reservation=_reservation(
                registration,
                request_id="factor_mining_binding_request_1",
                request_seed="binding-request-1",
                request_actor_id="researcher:declaration-binding",
            ),
        )
        ledger = factor_mining_campaign_read_request_ledger(
            session,
            request_id=reserved.request_id,
        )

    assert ledger is not None
    assert ledger.events[0].request_actor_id == "researcher:declaration-binding"


def test_restart_stays_unresolved_until_an_explicit_human_replay_authorization(
    postgresql_session_factory,
) -> None:
    registration = _registration(campaign_id="factor_mining_campaign_replay_1")
    source_reservation = _reservation(
        registration,
        request_id="factor_mining_source_request_1",
        request_seed="replay-source-request",
    )
    with postgresql_session_factory() as session:
        factor_mining_campaign_register(session, registration=registration)
        source = factor_mining_campaign_reserve_request(
            session,
            reservation=source_reservation,
        )
        before_authorization = factor_mining_campaign_read_request_ledger(
            session,
            request_id=source.request_id,
        )
        assert before_authorization is not None
        assert before_authorization.events[-1].is_terminal is False
        session.rollback()
        with pytest.raises(
            FactorMiningCampaignLedgerError,
            match="FACTOR_MINING_CAMPAIGN_LEDGER_REQUEST_ALREADY_RESERVED",
        ):
            factor_mining_campaign_reserve_request(
                session,
                reservation=source_reservation,
            )

        authorization_input = _FactorMiningCampaignReplayAuthorizationInput(
            authorization_id="factor_mining_replay_authorization_1",
            actor_id="researcher:1",
            unresolved_request_hash=source.request_hash,
            authorization_evidence_hash=_hash("human-replay-evidence"),
            authorized_at=_now(2),
        )
        authorization = _factor_mining_campaign_authorize_replay(
            session,
            authorization=authorization_input,
        )
        source_ledger = factor_mining_campaign_read_request_ledger(
            session,
            request_id=source.request_id,
        )
        assert source_ledger is not None
        assert source_ledger.events[-1].event_kind == "REPLAY_AUTHORIZED"
        assert source_ledger.events[-1].record_hash == authorization.authorization_record_hash
        session.rollback()

        replay = factor_mining_campaign_reserve_request(
            session,
            reservation=_reservation(
                registration,
                request_id="factor_mining_replay_request_1",
                request_seed="replay-request-1",
                offset_seconds=3,
                replay_authorization_hash=authorization.authorization_hash,
            ),
        )
        assert replay.predecessor_record_hash == authorization.authorization_record_hash
        with pytest.raises(
            FactorMiningCampaignLedgerError,
            match="FACTOR_MINING_CAMPAIGN_LEDGER_REPLAY_AUTHORIZATION_ALREADY_CONSUMED",
        ):
            factor_mining_campaign_reserve_request(
                session,
                reservation=_reservation(
                    registration,
                    request_id="factor_mining_replay_request_2",
                    request_seed="replay-request-2",
                    offset_seconds=4,
                    replay_authorization_hash=authorization.authorization_hash,
                ),
            )


def test_replay_authorization_refuses_a_resolved_source_request(
    postgresql_session_factory,
) -> None:
    registration = _registration(campaign_id="factor_mining_campaign_closed_1")
    with postgresql_session_factory() as session:
        factor_mining_campaign_register(session, registration=registration)
        reserved = factor_mining_campaign_reserve_request(
            session,
            reservation=_reservation(
                registration,
                request_id="factor_mining_closed_request_1",
                request_seed="closed-request-1",
            ),
        )
        factor_mining_campaign_append_event(
            session,
            append=FactorMiningCampaignRequestEventAppend(
                request_id=reserved.request_id,
                event_kind=FactorMiningCampaignRequestEventKind.FAILED,
                occurred_at=_now(2),
                failure_code=FactorMiningCampaignFailureCode.RESULT_INVALID,
            ),
        )
        with pytest.raises(
            FactorMiningCampaignLedgerError,
            match="FACTOR_MINING_CAMPAIGN_LEDGER_REPLAY_SOURCE_NOT_UNRESOLVED",
        ):
            _factor_mining_campaign_authorize_replay(
                session,
                authorization=_FactorMiningCampaignReplayAuthorizationInput(
                    authorization_id="factor_mining_closed_authorization_1",
                    actor_id="researcher:1",
                    unresolved_request_hash=reserved.request_hash,
                    authorization_evidence_hash=_hash("closed-evidence"),
                    authorized_at=_now(3),
                ),
            )


def test_campaign_lock_allows_one_duplicate_reservation_and_enforces_active_limit(
    postgresql_session_factory,
) -> None:
    duplicate_registration = _registration(
        campaign_id="factor_mining_campaign_duplicate_1",
        max_concurrent_runs=2,
    )
    with postgresql_session_factory() as session:
        factor_mining_campaign_register(session, registration=duplicate_registration)

    duplicate_barrier = Barrier(2)

    def duplicate_attempt() -> str:
        with postgresql_session_factory() as session:
            duplicate_barrier.wait(timeout=5)
            try:
                factor_mining_campaign_reserve_request(
                    session,
                    reservation=_reservation(
                        duplicate_registration,
                        request_id="factor_mining_duplicate_request_1",
                        request_seed="duplicate-request-1",
                    ),
                )
            except FactorMiningCampaignLedgerError as exc:
                return str(exc)
            return "RESERVED"

    with ThreadPoolExecutor(max_workers=2) as executor:
        duplicate_outcomes = tuple(
            executor.map(lambda _: duplicate_attempt(), range(2), timeout=15)
        )

    assert duplicate_outcomes.count("RESERVED") == 1
    assert duplicate_outcomes.count("FACTOR_MINING_CAMPAIGN_LEDGER_REQUEST_ALREADY_RESERVED") == 1

    limited_registration = _registration(
        campaign_id="factor_mining_campaign_limit_1",
        max_concurrent_runs=1,
    )
    with postgresql_session_factory() as session:
        factor_mining_campaign_register(session, registration=limited_registration)

    limit_barrier = Barrier(2)

    def limited_attempt(number: int) -> str:
        with postgresql_session_factory() as session:
            limit_barrier.wait(timeout=5)
            try:
                factor_mining_campaign_reserve_request(
                    session,
                    reservation=_reservation(
                        limited_registration,
                        request_id=f"factor_mining_limit_request_{number}",
                        request_seed=f"limit-request-{number}",
                    ),
                )
            except FactorMiningCampaignLedgerError as exc:
                return str(exc)
            return "RESERVED"

    with ThreadPoolExecutor(max_workers=2) as executor:
        limited_outcomes = tuple(executor.map(limited_attempt, range(1, 3), timeout=15))

    assert limited_outcomes.count("RESERVED") == 1
    assert limited_outcomes.count(
        "FACTOR_MINING_CAMPAIGN_LEDGER_CONCURRENCY_LIMIT_EXCEEDED"
    ) == 1


def test_reader_rejects_a_tampered_hash_link(
    postgresql_session_factory,
) -> None:
    registration = _registration(campaign_id="factor_mining_campaign_tamper_1")
    with postgresql_session_factory() as session:
        factor_mining_campaign_register(session, registration=registration)
        reserved = factor_mining_campaign_reserve_request(
            session,
            reservation=_reservation(
                registration,
                request_id="factor_mining_tamper_request_1",
                request_seed="tamper-request-1",
            ),
        )
        event = session.get(FactorMiningCampaignRequestEventRecord, 1)
        assert event is not None
        event.predecessor_record_hash = _hash("tampered-predecessor")
        with pytest.raises(
            FactorMiningCampaignLedgerError,
            match="FACTOR_MINING_CAMPAIGN_LEDGER_(INVALID_EVENT_SHAPE|EVENT_RECORD_TAMPERED)",
        ):
            factor_mining_campaign_read_request_ledger(session, request_id=reserved.request_id)
        session.rollback()


def test_ledger_models_only_store_scalar_hash_and_counter_fields() -> None:
    columns = (
        *sqlalchemy_inspect(FactorMiningCampaignRecord).columns,
        *sqlalchemy_inspect(FactorMiningCampaignRequestEventRecord).columns,
    )
    forbidden_types = {"JSON", "JSONB", "Text"}
    raw_content_names = {
        "prompt",
        "response",
        "chain_of_thought",
        "secret",
        "payload",
        "trading",
        "broker",
        "portfolio",
    }

    assert not {
        type(column.type).__name__
        for column in columns
        if type(column.type).__name__ in forbidden_types
    }
    assert not {column.key for column in columns if column.key in raw_content_names}


def test_upgrade_head_installs_immutable_factor_mining_ledger_triggers(
    tmp_path,
    monkeypatch,
) -> None:
    storage_dir = tmp_path / "storage"
    database_url = postgresql_test_url(tmp_path / "factor-mining-ledger-triggers")
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
    session_factory = sessionmaker(engine, future=True)
    registration = _registration(campaign_id="factor_mining_campaign_trigger_1")
    try:
        init_db()
        with session_factory() as session:
            factor_mining_campaign_register(session, registration=registration)
            factor_mining_campaign_reserve_request(
                session,
                reservation=_reservation(
                    registration,
                    request_id="factor_mining_trigger_request_1",
                    request_seed="trigger-request-1",
                ),
            )

        _assert_database_refusal(
            engine,
            "UPDATE factor_mining_campaign_records SET campaign_id = :campaign_id",
            expected="FACTOR_MINING_CAMPAIGN_LEDGER_IMMUTABLE",
            parameters={"campaign_id": "factor_mining_campaign_mutated_1"},
        )
        _assert_database_refusal(
            engine,
            "DELETE FROM factor_mining_campaign_request_events",
            expected="FACTOR_MINING_CAMPAIGN_LEDGER_IMMUTABLE",
        )
        _assert_database_refusal(
            engine,
            "TRUNCATE factor_mining_campaign_request_events",
            expected="FACTOR_MINING_CAMPAIGN_LEDGER_IMMUTABLE",
        )
        _assert_database_refusal(
            engine,
            "TRUNCATE factor_mining_campaign_records",
            expected="FACTOR_MINING_CAMPAIGN_LEDGER_IMMUTABLE",
        )
    finally:
        engine.dispose()
        get_settings.cache_clear()

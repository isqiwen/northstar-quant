"""Unit contracts for the durable local factor-mining campaign runner."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from hashlib import sha256
from types import SimpleNamespace

import pytest

from northstar_quant.application.durable_factor_mining_campaign import (
    DurableFactorMiningCampaignRunner,
    FactorMiningCampaignDurabilityError,
    FactorMiningCampaignExecutionResult,
    FactorMiningCampaignGeneration,
    FactorMiningCampaignKnownFailure,
    FactorMiningCampaignLedgerEvent,
    FactorMiningCampaignLedgerEventKind,
    FactorMiningCampaignLedgerEventReceipt,
    FactorMiningCampaignPreparation,
    FactorMiningCampaignPreparedExecution,
    FactorMiningCampaignPreparedSelection,
    FactorMiningCampaignRegistration,
    FactorMiningCampaignReplayAuthorization,
    FactorMiningCampaignReplayAuthorizationIntent,
    FactorMiningCampaignReservation,
    FactorMiningCampaignResourceUsage,
    FactorMiningCampaignRunRequest,
    PostgresFactorMiningCampaignLedger,
    _create_postgres_factor_mining_campaign_ledger_for_test,
    _verified_factor_mining_campaign_replay_authorization_from_trusted_verifier,
)
import northstar_quant.application.durable_factor_mining_campaign as durable_campaign_module


def _hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _request() -> FactorMiningCampaignRunRequest:
    return FactorMiningCampaignRunRequest(
        run_id="campaign_run_1",
        actor_id="researcher:1",
        declaration_snapshot_hash=_hash("declaration-snapshot"),
    )


def _preparation(request: FactorMiningCampaignRunRequest) -> FactorMiningCampaignPreparation:
    return FactorMiningCampaignPreparation(
        declaration_snapshot_hash=request.declaration_snapshot_hash,
        declaration_hash=_hash("declaration"),
        campaign_hash=_hash("campaign"),
        resource_budget_hash=_hash("resource-budget"),
        data_authorization_hashes=(_hash("data-authorization"),),
    )


def _generation() -> FactorMiningCampaignGeneration:
    return FactorMiningCampaignGeneration(
        generation_request_hash=_hash("generation-request"),
        generation_receipt_hash=_hash("generation-receipt"),
        candidate_count=2,
    )


def _execution(
    preparation: FactorMiningCampaignPreparation,
    generation: FactorMiningCampaignGeneration,
) -> FactorMiningCampaignExecutionResult:
    return FactorMiningCampaignExecutionResult(
        campaign_hash=preparation.campaign_hash,
        declaration_hash=preparation.declaration_hash,
        generation_receipt_hash=generation.generation_receipt_hash,
        bundle_snapshot_hash=_hash("bundle-snapshot"),
        discovery_result_hash=_hash("discovery-result"),
        selection_commitment_hash=_hash("selection-commitment"),
        oos_release_hash=_hash("oos-release"),
        manifest_snapshot_hash=_hash("manifest-snapshot"),
        result_hash=_hash("result"),
        selected_candidate_count=1,
        resource_usage=FactorMiningCampaignResourceUsage(
            max_concurrency_observed=1,
            cpu_milliseconds=100,
            peak_memory_bytes=1_024,
            wall_clock_milliseconds=200,
            data_row_count=10,
            artifact_byte_count=2_048,
        ),
    )


def _prepared_selection(
    preparation: FactorMiningCampaignPreparation,
    generation: FactorMiningCampaignGeneration,
) -> FactorMiningCampaignPreparedSelection:
    return FactorMiningCampaignPreparedSelection(
        campaign_hash=preparation.campaign_hash,
        declaration_hash=preparation.declaration_hash,
        generation_receipt_hash=generation.generation_receipt_hash,
        bundle_snapshot_hash=_hash("bundle-snapshot"),
        discovery_result_hash=_hash("discovery-result"),
        selection_commitment_hash=_hash("selection-commitment"),
        selected_candidate_count=1,
        resource_usage=FactorMiningCampaignResourceUsage(
            max_concurrency_observed=1,
            cpu_milliseconds=100,
            peak_memory_bytes=1_024,
            wall_clock_milliseconds=200,
            data_row_count=10,
            artifact_byte_count=0,
        ),
    )


def _prepared_execution(
    preparation: FactorMiningCampaignPreparation,
    generation: FactorMiningCampaignGeneration,
) -> FactorMiningCampaignPreparedExecution:
    return FactorMiningCampaignPreparedExecution(
        campaign_hash=preparation.campaign_hash,
        declaration_hash=preparation.declaration_hash,
        generation_receipt_hash=generation.generation_receipt_hash,
        bundle_snapshot_hash=_hash("bundle-snapshot"),
        discovery_result_hash=_hash("discovery-result"),
        selection_commitment_hash=_hash("selection-commitment"),
        oos_release_hash=_hash("oos-release"),
        result_hash=_hash("result"),
        selected_candidate_count=1,
        resource_usage=FactorMiningCampaignResourceUsage(
            max_concurrency_observed=1,
            cpu_milliseconds=100,
            peak_memory_bytes=1_024,
            wall_clock_milliseconds=200,
            data_row_count=10,
            artifact_byte_count=2_048,
        ),
    )


@dataclass
class FakeLedger:
    reserved_request_hashes: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)
    events: list[FactorMiningCampaignLedgerEvent] = field(default_factory=list)
    replay_requests: list[FactorMiningCampaignReplayAuthorizationIntent] = field(
        default_factory=list
    )

    def register_campaign(
        self,
        *,
        preparation: FactorMiningCampaignPreparation,
    ) -> FactorMiningCampaignRegistration:
        self.calls.append("register")
        return FactorMiningCampaignRegistration(
            campaign_hash=preparation.campaign_hash,
            declaration_hash=preparation.declaration_hash,
            resource_budget_hash=preparation.resource_budget_hash,
            registration_record_hash=_hash("registration-record"),
        )

    def reserve_request(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
        registration: FactorMiningCampaignRegistration,
    ) -> FactorMiningCampaignReservation:
        del registration
        self.calls.append("reserve")
        if request.request_hash in self.reserved_request_hashes:
            raise RuntimeError("duplicate reservation")
        self.reserved_request_hashes.add(request.request_hash)
        return FactorMiningCampaignReservation(
            request_hash=request.request_hash,
            campaign_hash=preparation.campaign_hash,
            declaration_hash=preparation.declaration_hash,
            reservation_record_hash=_hash("reservation-record"),
        )

    def append_event(
        self,
        *,
        event: FactorMiningCampaignLedgerEvent,
    ) -> FactorMiningCampaignLedgerEventReceipt:
        self.calls.append(f"append:{event.kind.value}")
        self.events.append(event)
        return FactorMiningCampaignLedgerEventReceipt(
            request_hash=event.request_hash,
            kind=event.kind,
            record_hash=_hash(f"event-record-{len(self.events)}"),
        )

    def authorize_replay(
        self,
        *,
        request: FactorMiningCampaignReplayAuthorizationIntent,
    ) -> FactorMiningCampaignReplayAuthorization:
        self.calls.append("authorize-replay")
        self.replay_requests.append(request)
        return FactorMiningCampaignReplayAuthorization(
            authorization_hash=_hash("verified-replay-authorization"),
            unresolved_request_hash=request.unresolved_request_hash,
            authorization_record_hash=_hash("replay-authorization-record"),
        )

    def read_request_events(
        self,
        *,
        request_hash: str,
    ) -> tuple[FactorMiningCampaignLedgerEventReceipt, ...]:
        del request_hash
        return ()


@dataclass
class FakeExecution:
    preparation: FactorMiningCampaignPreparation
    generation: FactorMiningCampaignGeneration
    prepared_selection: FactorMiningCampaignPreparedSelection
    prepared_execution: FactorMiningCampaignPreparedExecution
    execution: FactorMiningCampaignExecutionResult
    generation_error: Exception | None = None
    execution_error: Exception | None = None
    release_error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def preflight(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
    ) -> FactorMiningCampaignPreparation:
        self.calls.append("preflight")
        assert request.declaration_snapshot_hash == self.preparation.declaration_snapshot_hash
        return self.preparation

    def generate(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
        reservation: FactorMiningCampaignReservation,
    ) -> FactorMiningCampaignGeneration:
        self.calls.append("generate")
        assert request.declaration_snapshot_hash == preparation.declaration_snapshot_hash
        assert reservation.request_hash == request.request_hash
        if self.generation_error is not None:
            raise self.generation_error
        return self.generation

    def execute(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
        reservation: FactorMiningCampaignReservation,
        generation: FactorMiningCampaignGeneration,
    ) -> FactorMiningCampaignPreparedSelection:
        self.calls.append("execute")
        assert request.declaration_snapshot_hash == preparation.declaration_snapshot_hash
        assert reservation.request_hash == request.request_hash
        assert generation == self.generation
        if self.execution_error is not None:
            raise self.execution_error
        return self.prepared_selection

    def prepare_release(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
        reservation: FactorMiningCampaignReservation,
        generation: FactorMiningCampaignGeneration,
        prepared_selection: FactorMiningCampaignPreparedSelection,
    ) -> FactorMiningCampaignPreparedExecution:
        self.calls.append("prepare-release")
        assert request.declaration_snapshot_hash == preparation.declaration_snapshot_hash
        assert reservation.request_hash == request.request_hash
        assert generation == self.generation
        assert prepared_selection == self.prepared_selection
        if self.release_error is not None:
            raise self.release_error
        return self.prepared_execution

    def publish(
        self,
        *,
        request: FactorMiningCampaignRunRequest,
        preparation: FactorMiningCampaignPreparation,
        reservation: FactorMiningCampaignReservation,
        generation: FactorMiningCampaignGeneration,
        prepared_execution: FactorMiningCampaignPreparedExecution,
    ) -> FactorMiningCampaignExecutionResult:
        self.calls.append("publish")
        assert request.declaration_snapshot_hash == preparation.declaration_snapshot_hash
        assert reservation.request_hash == request.request_hash
        assert generation == self.generation
        assert prepared_execution == self.prepared_execution
        return self.execution


@dataclass(frozen=True)
class Fixture:
    request: FactorMiningCampaignRunRequest
    ledger: FakeLedger
    execution: FakeExecution
    runner: DurableFactorMiningCampaignRunner


def _fixture() -> Fixture:
    request = _request()
    preparation = _preparation(request)
    generation = _generation()
    prepared_selection = _prepared_selection(preparation, generation)
    prepared_execution = _prepared_execution(preparation, generation)
    execution = FakeExecution(
        preparation=preparation,
        generation=generation,
        prepared_selection=prepared_selection,
        prepared_execution=prepared_execution,
        execution=_execution(preparation, generation),
    )
    ledger = FakeLedger()
    return Fixture(
        request=request,
        ledger=ledger,
        execution=execution,
        runner=DurableFactorMiningCampaignRunner(ledger=ledger, execution=execution),
    )


def test_runner_reserves_records_and_completes_in_the_only_safe_order() -> None:
    fixture = _fixture()

    result = fixture.runner.run(fixture.request)

    assert fixture.execution.calls == [
        "preflight",
        "generate",
        "execute",
        "prepare-release",
        "publish",
    ]
    assert fixture.ledger.calls == [
        "register",
        "reserve",
        "append:RECEIPT_RECORDED",
        "append:DISCOVERY_RECORDED",
        "append:SELECTION_COMMITTED",
        "append:OOS_RESERVED",
        "append:OOS_RELEASED",
        "append:RESULT_RECORDED",
    ]
    assert [event.kind for event in fixture.ledger.events] == [
        FactorMiningCampaignLedgerEventKind.RECEIPT_RECORDED,
        FactorMiningCampaignLedgerEventKind.DISCOVERY_RECORDED,
        FactorMiningCampaignLedgerEventKind.SELECTION_COMMITTED,
        FactorMiningCampaignLedgerEventKind.OOS_RESERVED,
        FactorMiningCampaignLedgerEventKind.OOS_RELEASED,
        FactorMiningCampaignLedgerEventKind.RESULT_RECORDED,
    ]
    predecessor_record_hash = result.reservation_record_hash
    for position, event in enumerate(fixture.ledger.events, start=1):
        assert event.predecessor_record_hash == predecessor_record_hash
        predecessor_record_hash = _hash(f"event-record-{position}")
    assert fixture.ledger.events[-1].resource_usage is fixture.execution.execution.resource_usage
    assert result.request is fixture.request
    assert result.as_mapping()["research_only"] is True


def test_duplicate_reservation_blocks_generation_before_any_candidate_work() -> None:
    fixture = _fixture()
    fixture.ledger.reserved_request_hashes.add(fixture.request.request_hash)

    with pytest.raises(
        FactorMiningCampaignDurabilityError,
        match="FACTOR_MINING_CAMPAIGN_RESERVATION_REFUSED",
    ):
        fixture.runner.run(fixture.request)

    assert fixture.execution.calls == ["preflight"]
    assert fixture.ledger.calls == ["register", "reserve"]
    assert fixture.ledger.events == []


def test_unknown_execution_failure_stays_unresolved_without_a_failed_fact() -> None:
    fixture = _fixture()
    fixture.execution.execution_error = RuntimeError("worker transport lost")

    with pytest.raises(
        FactorMiningCampaignDurabilityError,
        match="FACTOR_MINING_CAMPAIGN_EXECUTION_UNRESOLVED",
    ):
        fixture.runner.run(fixture.request)

    assert fixture.execution.calls == ["preflight", "generate", "execute"]
    assert [event.kind for event in fixture.ledger.events] == [
        FactorMiningCampaignLedgerEventKind.RECEIPT_RECORDED
    ]
    assert FactorMiningCampaignLedgerEventKind.FAILED not in {
        event.kind for event in fixture.ledger.events
    }


def test_known_execution_failure_appends_one_failed_fact_without_completion() -> None:
    fixture = _fixture()
    fixture.execution.execution_error = FactorMiningCampaignKnownFailure(
        "FACTOR_MINING_CAMPAIGN_RESOURCE_LIMIT_EXCEEDED"
    )

    with pytest.raises(
        FactorMiningCampaignDurabilityError,
        match="FACTOR_MINING_CAMPAIGN_EXECUTION_FACTOR_MINING_CAMPAIGN_RESOURCE_LIMIT_EXCEEDED",
    ):
        fixture.runner.run(fixture.request)

    assert [event.kind for event in fixture.ledger.events] == [
        FactorMiningCampaignLedgerEventKind.RECEIPT_RECORDED,
        FactorMiningCampaignLedgerEventKind.FAILED,
    ]
    assert fixture.ledger.events[-1].predecessor_record_hash == _hash("event-record-1")
    assert FactorMiningCampaignLedgerEventKind.RESULT_RECORDED not in {
        event.kind for event in fixture.ledger.events
    }


def test_known_release_failure_after_oos_reservation_stays_unresolved() -> None:
    """A worker can no longer prove no OOS material was touched at this seam."""

    fixture = _fixture()
    fixture.execution.release_error = FactorMiningCampaignKnownFailure(
        "FACTOR_MINING_CAMPAIGN_RESOURCE_LIMIT_EXCEEDED"
    )

    with pytest.raises(
        FactorMiningCampaignDurabilityError,
        match="FACTOR_MINING_CAMPAIGN_OOS_UNRESOLVED",
    ):
        fixture.runner.run(fixture.request)

    assert fixture.execution.calls == [
        "preflight",
        "generate",
        "execute",
        "prepare-release",
    ]
    assert [event.kind for event in fixture.ledger.events] == [
        FactorMiningCampaignLedgerEventKind.RECEIPT_RECORDED,
        FactorMiningCampaignLedgerEventKind.DISCOVERY_RECORDED,
        FactorMiningCampaignLedgerEventKind.SELECTION_COMMITTED,
        FactorMiningCampaignLedgerEventKind.OOS_RESERVED,
    ]
    assert FactorMiningCampaignLedgerEventKind.FAILED not in {
        event.kind for event in fixture.ledger.events
    }


def test_replay_authorization_intent_is_a_ledger_only_operation_and_never_executes_work() -> None:
    fixture = _fixture()
    authorization_intent = FactorMiningCampaignReplayAuthorizationIntent(
        authorization_id="authorize_replay_1",
        unresolved_request_hash=fixture.request.request_hash,
    )

    authorization = fixture.runner.authorize_replay(authorization_intent)

    assert authorization.authorization_hash == _hash("verified-replay-authorization")
    assert authorization.unresolved_request_hash == fixture.request.request_hash
    assert fixture.ledger.calls == ["authorize-replay"]
    assert fixture.ledger.replay_requests == [authorization_intent]
    assert fixture.execution.calls == []
    assert fixture.ledger.events == []


def test_replay_intent_cannot_carry_a_self_attested_actor_or_evidence() -> None:
    assert tuple(item.name for item in fields(FactorMiningCampaignReplayAuthorizationIntent)) == (
        "authorization_id",
        "unresolved_request_hash",
    )


def test_postgres_replay_authorization_refuses_before_opening_a_session_without_verifier() -> None:
    session_calls: list[object] = []

    def _unexpected_session_factory() -> object:
        session_calls.append(object())
        raise AssertionError("unavailable verifier must refuse before opening PostgreSQL")

    ledger = PostgresFactorMiningCampaignLedger(session_factory=_unexpected_session_factory)
    intent = FactorMiningCampaignReplayAuthorizationIntent(
        authorization_id="authorize_replay_1",
        unresolved_request_hash=_hash("unresolved-request"),
    )

    with pytest.raises(
        FactorMiningCampaignDurabilityError,
        match="FACTOR_MINING_CAMPAIGN_REPLAY_AUTHORIZATION_VERIFIER_UNAVAILABLE",
    ):
        ledger.authorize_replay(request=intent)

    assert session_calls == []


def test_postgres_replay_authorization_uses_only_verifier_confirmed_actor_and_receipt(
    monkeypatch,
) -> None:
    intent = FactorMiningCampaignReplayAuthorizationIntent(
        authorization_id="authorize_replay_1",
        unresolved_request_hash=_hash("unresolved-request"),
    )
    captured: list[object] = []

    class _Verifier:
        def verify(self, *, intent: FactorMiningCampaignReplayAuthorizationIntent):
            return _verified_factor_mining_campaign_replay_authorization_from_trusted_verifier(
                authorization_id=intent.authorization_id,
                unresolved_request_hash=intent.unresolved_request_hash,
                approver_id="verified:approver",
                verifier_receipt_hash=_hash("verifier-issued-receipt"),
            )

    class _Session:
        def __enter__(self) -> "_Session":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def _append_authorization(_session: object, *, authorization: object) -> object:
        captured.append(authorization)
        return SimpleNamespace(
            authorization_hash=_hash("durable-authorization"),
            unresolved_request_hash=intent.unresolved_request_hash,
            authorization_record_hash=_hash("authorization-record"),
        )

    monkeypatch.setattr(
        durable_campaign_module,
        "_factor_mining_campaign_authorize_replay",
        _append_authorization,
    )
    ledger = _create_postgres_factor_mining_campaign_ledger_for_test(
        replay_authorization_verifier=_Verifier(),
        session_factory=_Session,
    )

    authorization = ledger.authorize_replay(request=intent)

    assert authorization.authorization_hash == _hash("durable-authorization")
    assert len(captured) == 1
    persisted = captured[0]
    assert getattr(persisted, "actor_id") == "verified:approver"
    assert getattr(persisted, "authorization_evidence_hash") == _hash("verifier-issued-receipt")


def test_postgres_replay_authorization_rejects_a_verifier_result_for_another_request() -> None:
    intent = FactorMiningCampaignReplayAuthorizationIntent(
        authorization_id="authorize_replay_1",
        unresolved_request_hash=_hash("unresolved-request"),
    )

    class _MismatchedVerifier:
        def verify(self, *, intent: FactorMiningCampaignReplayAuthorizationIntent):
            return _verified_factor_mining_campaign_replay_authorization_from_trusted_verifier(
                authorization_id=intent.authorization_id,
                unresolved_request_hash=_hash("another-unresolved-request"),
                approver_id="verified:approver",
                verifier_receipt_hash=_hash("verifier-issued-receipt"),
            )

    ledger = _create_postgres_factor_mining_campaign_ledger_for_test(
        replay_authorization_verifier=_MismatchedVerifier(),
        session_factory=lambda: (_ for _ in ()).throw(
            AssertionError("mismatched verifier result must not open PostgreSQL")
        ),
    )

    with pytest.raises(
        FactorMiningCampaignDurabilityError,
        match="FACTOR_MINING_CAMPAIGN_REPLAY_AUTHORIZATION_VERIFIER_BINDING_MISMATCH",
    ):
        ledger.authorize_replay(request=intent)

from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from pydantic import JsonValue
from starlette.concurrency import run_in_threadpool

from northstar_quant.live import LiveClient
from northstar_quant.web.access import (
    WorkspaceAccess,
)
from northstar_quant.web.requests import (
    ApiModel,
    EvidenceRecord,
    UUIDText,
    _string_field,
    _uuid_field,
)

from .commands import _runtime_header


class BaselineRequest(ApiModel):
    source_batch_id: UUIDText
    request_id: UUIDText


class BaselineCheckRequest(ApiModel):
    baseline_id: UUIDText
    query_batch_id: UUIDText
    request_id: UUIDText


class PositionEntryRequest(ApiModel):
    baseline_id: UUIDText
    source_batch_id: UUIDText
    request_id: UUIDText


class FundsEntryRequest(ApiModel):
    baseline_id: UUIDText
    source_batch_id: UUIDText
    request_id: UUIDText


class AccountCatchupRequest(ApiModel):
    baseline_id: UUIDText
    through_sequence: int
    request_id: UUIDText


class StreamPositionsRequest(ApiModel):
    baseline_id: UUIDText
    through_sequence: int
    request_id: UUIDText


class PositionCheckRequest(ApiModel):
    entry_id: UUIDText
    query_batch_id: UUIDText
    request_id: UUIDText


class OrderCheckRequest(ApiModel):
    position_check_id: UUIDText
    request_id: UUIDText


class QueryRequest(ApiModel):
    profile: str
    instrument: str
    request_id: UUIDText


class RuntimeStatus(ApiModel):
    runtime_id: str
    pid: int
    started_at: str
    observed_at: str
    status: str
    release: str
    protocol: str
    order_sending: bool
    cancel_sending: bool
    control_available: bool = False


class CommandRecord(EvidenceRecord):
    request_id: str
    status: str


class BrokerStatus(EvidenceRecord):
    profiles: list[dict[str, JsonValue]]
    credentials: dict[str, JsonValue]
    sdk: dict[str, JsonValue]
    execution: dict[str, bool]
    connection: str


class QueryRecord(EvidenceRecord):
    batch_id: str
    status: str
    instrument: str


class BaselineContext(EvidenceRecord):
    baseline: BaselineRecord | None = None


class LedgerContext(EvidenceRecord):
    baseline_id: str | None = None
    baseline: BaselineRecord | None = None
    entries: list[PositionEntry] = []
    checks: list[CheckRecord] = []


class FundsContext(EvidenceRecord):
    entries: list[dict[str, JsonValue]] = []


class BaselineRecord(EvidenceRecord):
    baseline_id: str


class CheckRecord(EvidenceRecord):
    check_id: str


class PositionEntry(EvidenceRecord):
    entry_id: str


class FundsEntry(EvidenceRecord):
    entry_id: str


class AccountProgress(EvidenceRecord):
    status: str


def register(app: FastAPI, access: WorkspaceAccess, live: LiveClient) -> None:
    broker = live.broker

    @app.get("/api/live/status", response_model=RuntimeStatus, response_model_exclude_unset=True)
    async def live_status(request: Request) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(live.status)

    @app.get(
        "/api/live/commands/{request_id}",
        response_model=CommandRecord,
        response_model_exclude_unset=True,
    )
    async def live_command(request: Request, request_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(live.command, request_id)

    @app.get("/api/broker/status", response_model=BrokerStatus, response_model_exclude_unset=True)
    async def broker_status(request: Request) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(broker.status)

    @app.get(
        "/api/broker/queries", response_model=list[QueryRecord], response_model_exclude_unset=True
    )
    async def broker_queries(request: Request, limit: int = 50) -> list[dict[str, object]]:
        access.require_request(request)
        return await run_in_threadpool(broker.list, limit=limit)

    @app.get(
        "/api/broker/queries/{batch_id}",
        response_model=QueryRecord,
        response_model_exclude_unset=True,
    )
    async def broker_query_detail(request: Request, batch_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(broker.get, batch_id)

    @app.get(
        "/api/broker/queries/{batch_id}/baseline-context",
        response_model=BaselineContext,
        response_model_exclude_unset=True,
    )
    async def broker_baseline_context(request: Request, batch_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(broker.baseline_context, batch_id)

    @app.post(
        "/api/broker/baselines", response_model=BaselineRecord, response_model_exclude_unset=True
    )
    async def broker_establish_baseline(
        request: Request,
        document: BaselineRequest,
        runtime: Annotated[UUID, Depends(_runtime_header)],
    ) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(runtime)
        payload = document.model_dump(mode="json", exclude_unset=True)
        return await run_in_threadpool(
            command_live.broker.establish_baseline,
            _uuid_field(payload, "source_batch_id"),
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.post(
        "/api/broker/baseline-checks", response_model=CheckRecord, response_model_exclude_unset=True
    )
    async def broker_compare_baseline(
        request: Request,
        document: BaselineCheckRequest,
        runtime: Annotated[UUID, Depends(_runtime_header)],
    ) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(runtime)
        payload = document.model_dump(mode="json", exclude_unset=True)
        return await run_in_threadpool(
            command_live.broker.compare_baseline,
            _uuid_field(payload, "baseline_id"),
            _uuid_field(payload, "query_batch_id"),
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.get(
        "/api/broker/baseline-checks/{check_id}",
        response_model=CheckRecord,
        response_model_exclude_unset=True,
    )
    async def broker_baseline_check(request: Request, check_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(broker.get_baseline_check, check_id)

    @app.get(
        "/api/broker/queries/{batch_id}/ledger-context",
        response_model=LedgerContext,
        response_model_exclude_unset=True,
    )
    async def broker_ledger_context(request: Request, batch_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(broker.ledger_context, batch_id)

    @app.post(
        "/api/broker/position-entries",
        response_model=PositionEntry,
        response_model_exclude_unset=True,
    )
    async def broker_ingest_positions(
        request: Request,
        document: PositionEntryRequest,
        runtime: Annotated[UUID, Depends(_runtime_header)],
    ) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(runtime)
        payload = document.model_dump(mode="json", exclude_unset=True)
        return await run_in_threadpool(
            command_live.broker.ingest_positions,
            _uuid_field(payload, "baseline_id"),
            _uuid_field(payload, "source_batch_id"),
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.get(
        "/api/broker/position-entries/{entry_id}",
        response_model=PositionEntry,
        response_model_exclude_unset=True,
    )
    async def broker_position_entry(request: Request, entry_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(broker.get_position_entry, entry_id)

    @app.post(
        "/api/broker/funds-entries", response_model=FundsEntry, response_model_exclude_unset=True
    )
    async def broker_observe_funds(
        request: Request,
        document: FundsEntryRequest,
        runtime: Annotated[UUID, Depends(_runtime_header)],
    ) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(runtime)
        payload = document.model_dump(mode="json", exclude_unset=True)
        return await run_in_threadpool(
            command_live.broker.observe_funds,
            _uuid_field(payload, "baseline_id"),
            _uuid_field(payload, "source_batch_id"),
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.get(
        "/api/broker/funds-entries/{entry_id}",
        response_model=FundsEntry,
        response_model_exclude_unset=True,
    )
    async def broker_funds_entry(request: Request, entry_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(broker.get_funds_entry, entry_id)

    @app.post(
        "/api/streams/{stream_id}/account-catchup",
        response_model=AccountProgress,
        response_model_exclude_unset=True,
    )
    async def broker_stream_account_catchup(
        request: Request,
        document: AccountCatchupRequest,
        stream_id: UUID,
        runtime: Annotated[UUID, Depends(_runtime_header)],
    ) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(runtime)
        payload = document.model_dump(mode="json", exclude_unset=True)
        return await run_in_threadpool(
            command_live.streams.catchup_account,
            stream_id,
            _uuid_field(payload, "baseline_id"),
            cast(int, payload["through_sequence"]),
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.post(
        "/api/streams/{stream_id}/position-entries",
        response_model=PositionEntry,
        response_model_exclude_unset=True,
    )
    async def broker_stream_positions(
        request: Request,
        document: StreamPositionsRequest,
        stream_id: UUID,
        runtime: Annotated[UUID, Depends(_runtime_header)],
    ) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(runtime)
        payload = document.model_dump(mode="json", exclude_unset=True)
        return await run_in_threadpool(
            command_live.broker.ingest_stream_positions,
            _uuid_field(payload, "baseline_id"),
            stream_id,
            cast(int, payload["through_sequence"]),
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.post(
        "/api/broker/position-checks", response_model=CheckRecord, response_model_exclude_unset=True
    )
    async def broker_compare_positions(
        request: Request,
        document: PositionCheckRequest,
        runtime: Annotated[UUID, Depends(_runtime_header)],
    ) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(runtime)
        payload = document.model_dump(mode="json", exclude_unset=True)
        return await run_in_threadpool(
            command_live.broker.compare_positions,
            _uuid_field(payload, "entry_id"),
            _uuid_field(payload, "query_batch_id"),
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.get(
        "/api/broker/position-checks/{check_id}",
        response_model=CheckRecord,
        response_model_exclude_unset=True,
    )
    async def broker_position_check(request: Request, check_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(broker.get_position_check, check_id)

    @app.post(
        "/api/broker/order-checks", response_model=CheckRecord, response_model_exclude_unset=True
    )
    async def broker_check_orders(
        request: Request,
        document: OrderCheckRequest,
        runtime: Annotated[UUID, Depends(_runtime_header)],
    ) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(runtime)
        payload = document.model_dump(mode="json", exclude_unset=True)
        return await run_in_threadpool(
            command_live.broker.check_orders,
            _uuid_field(payload, "position_check_id"),
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.get(
        "/api/broker/order-checks/{check_id}",
        response_model=CheckRecord,
        response_model_exclude_unset=True,
    )
    async def broker_order_check(request: Request, check_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(broker.get_order_check, check_id)

    @app.post("/api/broker/queries", response_model=QueryRecord, response_model_exclude_unset=True)
    async def broker_query(
        request: Request, document: QueryRequest, runtime: Annotated[UUID, Depends(_runtime_header)]
    ) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(runtime)
        payload = document.model_dump(mode="json", exclude_unset=True)
        return await run_in_threadpool(
            command_live.broker.query,
            _string_field(payload, "profile"),
            _string_field(payload, "instrument"),
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.get(
        "/api/broker/queries/{batch_id}/funds-context",
        response_model=FundsContext,
        response_model_exclude_unset=True,
    )
    async def broker_funds_context(request: Request, batch_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(broker.funds_context, batch_id)

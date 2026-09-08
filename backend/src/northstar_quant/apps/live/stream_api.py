from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal, cast
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

from .broker_api import CheckRecord
from .commands import _runtime_header


class OpeningBudgetRequest(ApiModel):
    sequence: int
    order_check_id: UUIDText
    limit_price: str
    request_id: UUIDText


class StreamRequest(ApiModel):
    query_batch_id: UUIDText
    configuration_id: str
    request_id: UUIDText
    duration_seconds: int
    allow_retention: bool
    use_basis: str


class ControlRequest(ApiModel):
    action: Literal["PAUSE", "RESUME", "STOP"]
    request_id: UUIDText


class ArchiveRequest(ApiModel):
    through_sequence: int
    session_open: str
    session_close: str
    allow_download: bool
    request_id: UUIDText


class StreamSummary(EvidenceRecord):
    stream_id: str


class StreamBindingRequest(EvidenceRecord):
    query_batch_id: UUIDText


class StreamBinding(EvidenceRecord):
    request: StreamBindingRequest


class StreamAccountProgress(EvidenceRecord):
    status: str
    through_sequence: int | None = None


class CompletedMinute(EvidenceRecord):
    completed_at: str
    close: str


class StreamStepResult(EvidenceRecord):
    bar: CompletedMinute | None
    intent: dict[str, JsonValue] | None


class StreamStep(ApiModel):
    sequence: int
    result: StreamStepResult
    committed_at: str


class StreamDetail(EvidenceRecord):
    stream_id: str
    connection: str
    paused: bool
    received: int
    cursor: int
    steps: list[StreamStep]
    binding: StreamBinding
    account_progress: StreamAccountProgress
    archives: list[dict[str, JsonValue]]


class StreamEvent(ApiModel):
    event: dict[str, JsonValue]
    committed_at: str


class StreamControl(EvidenceRecord):
    stream_id: str


class OpeningBudget(EvidenceRecord):
    budget_id: str


class BudgetContext(ApiModel):
    budgets: list[dict[str, JsonValue]]
    order_checks: list[CheckRecord]
    live_runtime: dict[str, JsonValue] | None = None


class StreamDecision(EvidenceRecord):
    sequence: int


class StreamArchive(EvidenceRecord):
    source_id: str


class LiveConfiguration(EvidenceRecord):
    configuration_id: str
    name: str
    config: dict[str, JsonValue]


def register(app: FastAPI, access: WorkspaceAccess, live: LiveClient) -> None:
    streams, opening_budgets = live.streams, live.opening_budgets

    @app.post(
        "/api/streams/{stream_id}/opening-budgets",
        response_model=OpeningBudget,
        response_model_exclude_unset=True,
    )
    async def opening_budget_create(
        request: Request,
        document: OpeningBudgetRequest,
        stream_id: UUID,
        runtime: Annotated[UUID, Depends(_runtime_header)],
    ) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(runtime)
        payload = document.model_dump(mode="json", exclude_unset=True)
        try:
            limit_price = Decimal(_string_field(payload, "limit_price"))
        except InvalidOperation as error:
            raise ValueError("限价必须是十进制数字字符串。") from error
        return await run_in_threadpool(
            command_live.opening_budgets.create,
            stream_id,
            payload["sequence"],
            _uuid_field(payload, "order_check_id"),
            limit_price=limit_price,
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.get(
        "/api/broker/opening-budgets/{budget_id}",
        response_model=OpeningBudget,
        response_model_exclude_unset=True,
    )
    async def opening_budget_get(request: Request, budget_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(opening_budgets.get, budget_id)

    @app.post(
        "/api/streams",
        status_code=201,
        response_model=StreamSummary,
        response_model_exclude_unset=True,
    )
    async def stream_start(
        request: Request,
        document: StreamRequest,
        runtime: Annotated[UUID, Depends(_runtime_header)],
    ) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(runtime)
        payload = document.model_dump(mode="json", exclude_unset=True)
        return await run_in_threadpool(
            command_live.streams.start,
            _uuid_field(payload, "query_batch_id"),
            _string_field(payload, "configuration_id"),
            request_id=_uuid_field(payload, "request_id"),
            duration_seconds=cast(int, payload["duration_seconds"]),
            allow_retention=cast(bool, payload["allow_retention"]),
            use_basis=_string_field(payload, "use_basis"),
        )

    @app.get(
        "/api/streams/{stream_id}", response_model=StreamDetail, response_model_exclude_unset=True
    )
    async def stream_state(request: Request, stream_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(streams.get, stream_id)

    @app.get(
        "/api/streams/{stream_id}/events",
        response_model=list[StreamEvent],
        response_model_exclude_unset=True,
    )
    async def stream_events(
        request: Request, stream_id: UUID, after: int = 0
    ) -> list[dict[str, object]]:
        access.require_request(request)
        return await run_in_threadpool(streams.events, stream_id, after=after)

    @app.post(
        "/api/streams/{stream_id}/control",
        response_model=StreamControl,
        response_model_exclude_unset=True,
    )
    async def stream_control(
        request: Request,
        document: ControlRequest,
        stream_id: UUID,
        runtime: Annotated[UUID, Depends(_runtime_header)],
    ) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(runtime)
        payload = document.model_dump(mode="json", exclude_unset=True)
        return await run_in_threadpool(
            command_live.streams.control,
            stream_id,
            _string_field(payload, "action"),
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.post(
        "/api/streams/{stream_id}/archive",
        response_model=StreamArchive,
        response_model_exclude_unset=True,
    )
    async def stream_archive(
        request: Request,
        document: ArchiveRequest,
        stream_id: UUID,
        runtime: Annotated[UUID, Depends(_runtime_header)],
    ) -> dict[str, object]:
        access.protect(request)
        command_live = live.for_runtime(runtime)
        payload = document.model_dump(mode="json", exclude_unset=True)
        return await run_in_threadpool(
            command_live.streams.archive,
            stream_id,
            through_sequence=payload["through_sequence"],
            session_open=_string_field(payload, "session_open"),
            session_close=_string_field(payload, "session_close"),
            allow_download=payload["allow_download"],
            request_id=_uuid_field(payload, "request_id"),
        )

    @app.get("/api/streams", response_model=list[StreamSummary], response_model_exclude_unset=True)
    async def list_streams(request: Request) -> list[dict[str, object]]:
        access.require_request(request)
        return await run_in_threadpool(streams.list)

    @app.get(
        "/api/configurations",
        response_model=list[LiveConfiguration],
        response_model_exclude_unset=True,
    )
    async def configurations(request: Request) -> list[dict[str, object]]:
        access.require_request(request)
        return await run_in_threadpool(live.read_list, "/configurations")

    @app.get(
        "/api/streams/{stream_id}/opening-budgets",
        response_model=BudgetContext,
        response_model_exclude_unset=True,
    )
    async def budgets(request: Request, stream_id: UUID) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(opening_budgets.context, stream_id)

    @app.get(
        "/api/streams/{stream_id}/decisions/{sequence}",
        response_model=StreamDecision,
        response_model_exclude_unset=True,
    )
    async def decision(request: Request, stream_id: UUID, sequence: int) -> dict[str, object]:
        access.require_request(request)
        return await run_in_threadpool(streams.decision, stream_id, sequence)

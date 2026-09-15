"""Owner browser consent goes through the current Live runtime, never local tables."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, FastAPI, Query, Request
from starlette.concurrency import run_in_threadpool

from northstar_quant.web.access import WorkspaceAccess
from northstar_quant.web.requests import ApiModel, EvidenceRecord

from .commands import _runtime_header
from .instances import Instances


class ExecutionConsent(EvidenceRecord):
    authorization_id: str
    status: str
    requires_current_admission: bool


class ConsentPage(EvidenceRecord):
    authorizations: list[ExecutionConsent]
    next_before: int | None


class ConsentRequest(ApiModel):
    request_id: str
    expires_at: str
    max_order_lots: int
    max_total_lots: int
    fee: str
    margin: str
    gross: str
    loss: str


class RevokeConsent(ApiModel):
    request_id: str


def register(app: FastAPI, access: WorkspaceAccess, instances: Instances) -> None:
    @app.get(
        "/api/streams/{stream_id}/authorizations",
        response_model=ConsentPage,
        response_model_exclude_unset=True,
    )
    async def listing(
        request: Request, stream_id: UUID, before: Annotated[int | None, Query(gt=0)] = None
    ) -> dict[str, Any]:
        access.require_request(request)
        path = f"/execution/authorizations?stream_id={stream_id}"
        if before is not None:
            path += f"&before={before}"
        return await run_in_threadpool(instances.for_request(request).read, path)

    @app.get(
        "/api/authorizations/{identifier}",
        response_model=ExecutionConsent,
        response_model_exclude_unset=True,
    )
    async def get(request: Request, identifier: UUID) -> dict[str, Any]:
        access.require_request(request)
        return await run_in_threadpool(
            instances.for_request(request).read, f"/execution/authorizations/{identifier}"
        )

    @app.post(
        "/api/streams/{stream_id}/authorizations",
        response_model=ExecutionConsent,
        response_model_exclude_unset=True,
    )
    async def grant(
        request: Request,
        stream_id: UUID,
        body: ConsentRequest,
        runtime: Annotated[UUID, Depends(_runtime_header)],
    ) -> dict[str, Any]:
        access.protect(request)
        # Canonical UTC text also makes command recovery compare exactly the same input.
        expires_at = datetime.fromisoformat(body.expires_at).isoformat().replace("+00:00", "Z")
        payload = dict(
            stream_id=str(stream_id),
            expires_at=expires_at,
            max_order_lots=body.max_order_lots,
            max_total_lots=body.max_total_lots,
            max_order_budget=dict(
                fee=body.fee, margin=body.margin, gross=body.gross, loss=body.loss
            ),
        )
        return await run_in_threadpool(
            instances.for_request(request).for_runtime(runtime).mutate,
            "/execution/authorizations",
            payload,
            UUID(body.request_id),
        )

    @app.post(
        "/api/authorizations/{identifier}/revoke",
        response_model=ExecutionConsent,
        response_model_exclude_unset=True,
    )
    async def revoke(
        request: Request,
        identifier: UUID,
        body: RevokeConsent,
        runtime: Annotated[UUID, Depends(_runtime_header)],
    ) -> dict[str, Any]:
        access.protect(request)
        return await run_in_threadpool(
            instances.for_request(request).for_runtime(runtime).mutate,
            f"/execution/authorizations/{identifier}/revoke",
            {"authorization_id": str(identifier)},
            UUID(body.request_id),
        )

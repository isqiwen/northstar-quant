"""Read local execution facts; neither observation nor a page grants sending rights."""

from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from northstar_quant.live.owner import LiveOwner

from .commands import execute_command


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stream_id: UUID


class OpeningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    budget_id: UUID
    authorization_id: UUID


class ClosingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    opening_order_id: UUID
    query_id: UUID
    authorization_id: UUID
    limit_price: Decimal = Field(gt=0, allow_inf_nan=False)


def routes(owner: LiveOwner) -> APIRouter:
    router = APIRouter()

    @router.get("/execution/health")
    def health() -> dict[str, Any]:
        return owner.read(owner.execution.health())

    @router.get("/execution/orders")
    def orders(before: Annotated[int | None, Query(gt=0)] = None) -> dict[str, Any]:
        return owner.read(owner.execution.list(before=before))

    @router.get("/execution/orders/{order_id}")
    def detail(order_id: UUID, after: Annotated[int, Query(ge=0)] = 0) -> dict[str, Any]:
        return owner.read(owner.execution.detail(str(order_id), after=after))

    @router.post("/execution/orders/{order_id}/cancel")
    def cancel(request: Request, order_id: UUID, body: CancelRequest) -> dict[str, Any]:
        if request.headers.get("x-northstar-operator") != "owner":
            raise HTTPException(403, "Only the owner may request a cancellation")
        owner.check_ownership()
        return execute_command(
            owner,
            request,
            body.model_dump(mode="json"),
            lambda identifier: owner.streams.cancel_order(
                body.stream_id, str(order_id), request_id=identifier
            ),
        )

    @router.post("/streams/{stream_id}/opening-orders")
    def opening(request: Request, stream_id: UUID, body: OpeningRequest) -> dict[str, Any]:
        if request.headers.get("x-northstar-operator") != "owner":
            raise HTTPException(403, "Only the owner may submit an opening")
        owner.check_ownership()
        return execute_command(
            owner,
            request,
            body.model_dump(mode="json"),
            lambda identifier: owner.streams.submit_opening(
                stream_id, body.budget_id, body.authorization_id, request_id=identifier
            ),
        )

    @router.post("/streams/{stream_id}/closing-orders")
    def closing(request: Request, stream_id: UUID, body: ClosingRequest) -> dict[str, Any]:
        if request.headers.get("x-northstar-operator") != "owner":
            raise HTTPException(403, "Only the owner may submit a closing")
        owner.check_ownership()
        return execute_command(
            owner,
            request,
            body.model_dump(mode="json"),
            lambda identifier: owner.streams.submit_closing(
                stream_id,
                body.opening_order_id,
                body.query_id,
                body.authorization_id,
                body.limit_price,
                request_id=identifier,
            ),
        )

    return router

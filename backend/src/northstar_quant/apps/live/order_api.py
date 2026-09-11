"""Owned Protobuf presentation of local execution facts via the kernel client."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import FastAPI, Query, Request
from pydantic import JsonValue
from starlette.concurrency import run_in_threadpool

from northstar_quant.web.access import WorkspaceAccess
from northstar_quant.web.requests import ApiModel, EvidenceRecord

from .instances import Instances


class OrderReservation(ApiModel):
    reserved_fee: str
    reserved_margin: str
    reserved_gross: str
    reserved_loss: str
    reserved_close_lots: int


class LocalOrder(EvidenceRecord):
    order_id: str
    contract_id: str
    authorization_id: str
    runtime_id: str
    attempt_id: str
    status: str
    quantity_lots: int
    filled_lots: int
    requires_reconciliation: bool
    reservation: OrderReservation
    order: dict[str, JsonValue]


class LocalOrderEvent(ApiModel):
    sequence: int
    event_id: str
    order_id: str
    kind: str
    recorded_at: str
    document: dict[str, JsonValue]


class LocalOrderPage(EvidenceRecord):
    orders: list[LocalOrder]
    next_before: int | None


class LocalOrderDetail(EvidenceRecord):
    record: LocalOrder
    events: list[LocalOrderEvent]
    next_after: int | None


def register(app: FastAPI, access: WorkspaceAccess, instances: Instances) -> None:
    @app.get("/api/orders", response_model=LocalOrderPage, response_model_exclude_unset=True)
    async def orders(
        request: Request, before: Annotated[int | None, Query(gt=0)] = None
    ) -> dict[str, Any]:
        access.require_request(request)
        path = "/execution/orders" + ("" if before is None else f"?before={before}")
        return await run_in_threadpool(instances.for_request(request).read, path)

    @app.get(
        "/api/orders/{order_id}", response_model=LocalOrderDetail, response_model_exclude_unset=True
    )
    async def detail(
        request: Request, order_id: UUID, after: Annotated[int, Query(ge=0)] = 0
    ) -> dict[str, Any]:
        access.require_request(request)
        return await run_in_threadpool(
            instances.for_request(request).read, f"/execution/orders/{order_id}?after={after}"
        )

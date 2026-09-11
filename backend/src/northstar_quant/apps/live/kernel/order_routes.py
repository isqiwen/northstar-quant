"""Read local execution facts; neither observation nor a page grants sending rights."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query

from northstar_quant.live.owner import LiveOwner


def routes(owner: LiveOwner) -> APIRouter:
    router = APIRouter()

    @router.get("/execution/orders")
    def orders(before: Annotated[int | None, Query(gt=0)] = None) -> dict[str, Any]:
        return owner.read(owner.execution.list(before=before))

    @router.get("/execution/orders/{order_id}")
    def detail(order_id: UUID, after: Annotated[int, Query(ge=0)] = 0) -> dict[str, Any]:
        return owner.read(owner.execution.detail(str(order_id), after=after))

    return router

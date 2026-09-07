"""Historical opening-budget evidence, never execution permission."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .owner import LiveOwner


class OpeningBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stream_id: UUID
    sequence: StrictInt = Field(ge=1, le=100_000)
    order_check_id: UUID
    limit_price: Decimal = Field(gt=0, allow_inf_nan=False)


def routes(owner: LiveOwner) -> APIRouter:
    router = APIRouter()

    @router.get("/streams/{stream_id}/opening-budgets")
    def context(stream_id: UUID) -> dict[str, Any]:
        return owner.opening_budgets.context(stream_id)

    @router.get("/opening-budgets/{budget_id}")
    def get(budget_id: UUID) -> dict[str, Any]:
        return owner.opening_budgets.get(budget_id)

    @router.post("/opening-budgets")
    def create(request: Request, body: OpeningBudget) -> dict[str, Any]:
        return owner.execute(
            request,
            body.model_dump(mode="json"),
            lambda identifier: owner.opening_budgets.create(
                body.stream_id,
                body.sequence,
                body.order_check_id,
                limit_price=body.limit_price,
                request_id=identifier,
            ),
        )

    return router

"""Explicit owner consent, separate from account reconciliation and order admission."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr

from northstar_quant.execution.orders import OrderBudget
from northstar_quant.live.execution_authority import ExecutionLimits
from northstar_quant.live.owner import LiveOwner

from .commands import execute_command


class Consent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stream_id: UUID
    expires_at: datetime
    max_order_lots: StrictInt
    max_total_lots: StrictInt
    max_order_budget: dict[str, StrictStr]


def routes(owner: LiveOwner) -> APIRouter:
    router = APIRouter()

    def operator(request: Request) -> str:
        value = request.headers.get("x-northstar-operator")
        if value != "owner":
            raise HTTPException(403, "Only the owner may change execution consent")
        return value

    @router.get("/execution/authorizations")
    def list_consents(stream_id: UUID, before: int | None = None) -> dict[str, Any]:
        return owner.read(owner.authority.list(stream_id, before=before))

    @router.get("/execution/authorizations/{identifier}")
    def get(identifier: UUID) -> dict[str, Any]:
        return owner.read(owner.authority.get(identifier))

    @router.post("/execution/authorizations")
    def grant(request: Request, body: Consent) -> dict[str, Any]:
        trusted_operator = operator(request)
        return execute_command(
            owner,
            request,
            body.model_dump(mode="json"),
            lambda identifier: owner.authority.grant(
                body.stream_id,
                ExecutionLimits(
                    body.max_order_lots,
                    body.max_total_lots,
                    OrderBudget.from_dict(body.max_order_budget),
                ),
                body.expires_at,
                request_id=identifier,
                operator=trusted_operator,
            ),
        )

    @router.post("/execution/authorizations/{identifier}/revoke")
    def revoke(request: Request, identifier: UUID) -> dict[str, Any]:
        trusted_operator = operator(request)
        return execute_command(
            owner,
            request,
            {"authorization_id": str(identifier)},
            lambda command_id: owner.authority.revoke(
                identifier, request_id=command_id, operator=trusted_operator
            ),
        )

    return router

"""Live-owned reception, shadow controls and retained-prefix processing."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from northstar_quant.live.owner import LiveOwner

from .commands import execute_command


class StartStream(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_batch_id: UUID
    configuration_id: str = Field(min_length=1, max_length=128)
    duration_seconds: StrictInt = Field(ge=60, le=7200)
    allow_retention: StrictBool
    use_basis: str = Field(min_length=1, max_length=500)


class ShadowControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["PAUSE", "RESUME", "STOP"]


class AccountCatchup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline_id: UUID
    through_sequence: StrictInt = Field(ge=1, le=100_000)


class ArchiveStream(BaseModel):
    model_config = ConfigDict(extra="forbid")
    through_sequence: StrictInt = Field(ge=1, le=100_000)
    session_open: str = Field(min_length=1, max_length=64)
    session_close: str = Field(min_length=1, max_length=64)
    allow_download: StrictBool = False


def routes(owner: LiveOwner) -> APIRouter:
    router = APIRouter()

    @router.get("/streams")
    def list_streams() -> list[dict[str, Any]]:
        return [owner.read(item) for item in owner.streams.list()]

    @router.post("/streams")
    def start_stream(request: Request, body: StartStream) -> dict[str, Any]:
        owner.require_account()
        if not body.allow_retention:
            raise HTTPException(422, "Reception requires explicit retention permission")
        return execute_command(
            owner,
            request,
            body.model_dump(mode="json"),
            lambda identifier: owner.streams.start(
                body.query_batch_id,
                body.configuration_id,
                request_id=identifier,
                duration_seconds=body.duration_seconds,
                allow_retention=body.allow_retention,
                use_basis=body.use_basis,
            ),
        )

    @router.get("/streams/{stream_id}")
    def get_stream(stream_id: UUID) -> dict[str, Any]:
        return owner.read(owner.streams.get(stream_id))

    @router.get("/streams/{stream_id}/events")
    def stream_events(stream_id: UUID, after: int = 0) -> list[dict[str, Any]]:
        return owner.streams.events(stream_id, after=after)

    @router.get("/streams/{stream_id}/decisions/{sequence}")
    def stream_decision(stream_id: UUID, sequence: int) -> dict[str, Any]:
        return owner.read(owner.streams.decision(stream_id, sequence))

    @router.post("/streams/{stream_id}/control")
    def control_stream(request: Request, stream_id: UUID, body: ShadowControl) -> dict[str, Any]:
        owner.streams.get(stream_id)
        return execute_command(
            owner,
            request,
            body.model_dump(mode="json"),
            lambda identifier: owner.streams.control(stream_id, body.action, request_id=identifier),
        )

    @router.post("/streams/{stream_id}/account-catchup")
    def catchup_account(request: Request, stream_id: UUID, body: AccountCatchup) -> dict[str, Any]:
        return execute_command(
            owner,
            request,
            body.model_dump(mode="json"),
            lambda _: owner.streams.catchup_account(
                stream_id, body.baseline_id, body.through_sequence
            ),
        )

    @router.post("/streams/{stream_id}/archives")
    def archive_stream(request: Request, stream_id: UUID, body: ArchiveStream) -> dict[str, Any]:
        return execute_command(
            owner,
            request,
            body.model_dump(mode="json"),
            lambda identifier: owner.streams.archive(
                stream_id,
                through_sequence=body.through_sequence,
                session_open=body.session_open,
                session_close=body.session_close,
                allow_download=body.allow_download,
                request_id=identifier,
            ),
        )

    return router

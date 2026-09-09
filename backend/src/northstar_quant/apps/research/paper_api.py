from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import JsonValue
from starlette.concurrency import run_in_threadpool

from northstar_quant.data_management.publications import DatasetReader
from northstar_quant.research.paper import PaperStore
from northstar_quant.web.access import (
    WorkspaceAccess,
)
from northstar_quant.web.requests import ApiModel, EvidenceRecord, UUIDText, _uuid_field

from .configuration_api import SavedConfiguration
from .run_api import ResearchSummary


class PaperRequest(ApiModel):
    snapshot_id: UUIDText
    configuration_id: str
    request_id: UUIDText


class AdvanceRequest(ApiModel):
    request_id: UUIDText


class PaperSession(EvidenceRecord):
    session_id: str
    configuration: SavedConfiguration
    cursor: int
    status: str
    summary: ResearchSummary


class PaperAdvanced(EvidenceRecord):
    session_id: str
    sequence: int
    step: dict[str, JsonValue]
    url: str


def register(
    app: FastAPI,
    access: WorkspaceAccess,
    library: DatasetReader,
    paper: PaperStore,
) -> None:

    @app.get("/api/paper", response_model=list[PaperSession], response_model_exclude_unset=True)
    def list_paper() -> list[dict[str, object]]:
        return paper.list()

    @app.post(
        "/api/paper",
        status_code=201,
        response_model=PaperSession,
        response_model_exclude_unset=True,
    )
    async def create_paper(request: Request, document: PaperRequest) -> dict[str, object]:
        access.protect(request)
        payload = document.model_dump(mode="json", exclude_unset=True)
        configuration_id = payload["configuration_id"]
        if not isinstance(configuration_id, str):
            raise ValueError("configuration_id 必须是已保存的配置身份。")
        result = await run_in_threadpool(
            paper.create,
            _uuid_field(payload, "snapshot_id"),
            configuration_id,
            request_id=_uuid_field(payload, "request_id"),
        )
        return result

    @app.get(
        "/api/paper/{session_id}", response_model=PaperSession, response_model_exclude_unset=True
    )
    def get_paper(session_id: UUID) -> dict[str, object]:
        return paper.get(session_id)

    @app.post(
        "/api/paper/{session_id}/advance",
        response_model=PaperAdvanced,
        response_model_exclude_unset=True,
    )
    async def advance_paper(
        request: Request, document: AdvanceRequest, session_id: UUID
    ) -> dict[str, object]:
        access.protect(request)
        payload = document.model_dump(mode="json", exclude_unset=True)
        return await run_in_threadpool(
            paper.advance, session_id, request_id=_uuid_field(payload, "request_id")
        )

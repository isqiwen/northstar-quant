from __future__ import annotations

from urllib.parse import quote
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from northstar_quant.data_management.library import DataLibrary
from northstar_quant.web.access import (
    WorkspaceAccess,
)
from northstar_quant.web.requests import (
    EvidenceRecord,
)


class SourceRecord(EvidenceRecord):
    source_id: str
    filename: str
    source_name: str
    allow_retention: bool
    allow_download: bool
    input_kind: str
    content_hash: str
    byte_count: int
    received_at: str


class AdmissionRejection(EvidenceRecord):
    rejection_id: str
    reason: str


def register(app: FastAPI, access: WorkspaceAccess, library: DataLibrary) -> None:
    @app.get("/api/sources", response_model=list[SourceRecord], response_model_exclude_unset=True)
    def list_sources(limit: int = 50) -> list[dict[str, object]]:
        return library.list_sources(limit=limit)

    @app.get(
        "/api/sources/{source_id}", response_model=SourceRecord, response_model_exclude_unset=True
    )
    def source_details(source_id: UUID) -> dict[str, object]:
        return library.source(source_id)

    @app.get("/api/sources/{source_id}/download")
    async def download_source(request: Request, source_id: UUID) -> Response:
        access.require_request(request)
        filename, content = await run_in_threadpool(library.download, source_id)
        return Response(
            content,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
            },
        )

    @app.get(
        "/api/rejections",
        response_model=list[AdmissionRejection],
        response_model_exclude_unset=True,
    )
    def rejections() -> list[dict[str, object]]:
        return library.list_rejections()

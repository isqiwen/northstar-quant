from __future__ import annotations

import base64
import binascii
from typing import cast
from urllib.parse import quote
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import Response
from pydantic import JsonValue
from starlette.concurrency import run_in_threadpool

from northstar_quant.data_management.library import DataLibrary
from northstar_quant.web.access import (
    WorkspaceAccess,
)
from northstar_quant.web.requests import (
    ApiModel,
    EvidenceRecord,
    UUIDText,
    _object,
    _string_field,
    _uuid_field,
)


class ImportRequest(ApiModel):
    content_base64: str
    filename: str
    source_name: str
    use_basis: str
    allow_retention: bool
    allow_download: bool
    input_kind: str
    upstream_source_id: UUIDText | None
    transformation_note: str | None
    # Saved before processing validation, so failures remain inspectable evidence.
    spec: dict[str, JsonValue]
    request_id: UUIDText


class ReprocessRequest(ApiModel):
    # Saved before processing validation, so failures remain inspectable evidence.
    spec: dict[str, JsonValue]
    request_id: UUIDText


class ProcessingAttempt(EvidenceRecord):
    attempt_id: str
    source_id: str
    status: str
    stage: str
    snapshot_id: str | None
    error: str | None
    parameters: dict[str, JsonValue]
    created_at: str


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
    @app.post("/api/import", response_model=ProcessingAttempt, response_model_exclude_unset=True)
    async def upload(request: Request, document: ImportRequest) -> dict[str, object]:
        access.protect(request)
        payload = document.model_dump(mode="json", exclude_unset=True)
        encoded = _string_field(payload, "content_base64")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("content_base64 必须是严格 Base64 编码的原始文件字节。") from error
        upstream = payload["upstream_source_id"]
        note = payload["transformation_note"]
        if note is not None and not isinstance(note, str):
            raise ValueError("transformation_note 必须是文本或 null。")
        return await run_in_threadpool(
            library.submit,
            content,
            filename=_string_field(payload, "filename"),
            source_name=_string_field(payload, "source_name"),
            use_basis=_string_field(payload, "use_basis"),
            allow_retention=cast(bool, payload["allow_retention"]),
            allow_download=cast(bool, payload["allow_download"]),
            input_kind=_string_field(payload, "input_kind"),
            upstream_source_id=None
            if upstream is None
            else _uuid_field(payload, "upstream_source_id"),
            transformation_note=note,
            spec=_object(payload["spec"]),
            request_id=str(_uuid_field(payload, "request_id")),
        )

    @app.get("/api/sources", response_model=list[SourceRecord], response_model_exclude_unset=True)
    def list_sources(limit: int = 50) -> list[dict[str, object]]:
        return library.list_sources(limit=limit)

    @app.get(
        "/api/sources/{source_id}", response_model=SourceRecord, response_model_exclude_unset=True
    )
    def source_details(source_id: UUID) -> dict[str, object]:
        return library.source(source_id)

    @app.get(
        "/api/attempts", response_model=list[ProcessingAttempt], response_model_exclude_unset=True
    )
    def list_attempts(limit: int = 50) -> list[dict[str, object]]:
        return library.list_attempts(limit=limit)

    @app.get(
        "/api/attempts/{attempt_id}",
        response_model=ProcessingAttempt,
        response_model_exclude_unset=True,
    )
    def attempt_details(attempt_id: UUID) -> dict[str, object]:
        return library.attempt(attempt_id)

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

    @app.post(
        "/api/sources/{source_id}/reprocess",
        response_model=ProcessingAttempt,
        response_model_exclude_unset=True,
    )
    async def reprocess_source(
        request: Request, document: ReprocessRequest, source_id: UUID
    ) -> dict[str, object]:
        access.protect(request)
        payload = document.model_dump(mode="json", exclude_unset=True)
        return await run_in_threadpool(
            library.submit_reprocess,
            source_id,
            spec=_object(payload["spec"]),
            request_id=str(_uuid_field(payload, "request_id")),
        )

    @app.get(
        "/api/rejections",
        response_model=list[AdmissionRejection],
        response_model_exclude_unset=True,
    )
    def rejections() -> list[dict[str, object]]:
        return library.list_rejections()

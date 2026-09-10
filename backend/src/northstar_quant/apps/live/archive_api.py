"""Show Live-owned archive evidence without opening storage in the management process."""

import base64
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.responses import Response
from pydantic import JsonValue
from starlette.concurrency import run_in_threadpool

from northstar_quant.web.access import WorkspaceAccess
from northstar_quant.web.datasets import DatasetDetails
from northstar_quant.web.requests import ApiModel, EvidenceRecord, UUIDText, _object, _uuid_field

from .commands import _runtime_header
from .instances import Instances


class ArchiveReprocessRequest(ApiModel):
    # Saved before processing validation, so failures remain inspectable evidence.
    spec: dict[str, JsonValue]
    request_id: UUIDText


class MaterialRequest(ApiModel):
    candidate: dict[str, JsonValue]
    request_id: UUIDText


class ArchiveSource(EvidenceRecord):
    source_id: str
    filename: str
    allow_download: bool


class ArchiveAttempt(EvidenceRecord):
    snapshot_id: str | None
    parameters: dict[str, JsonValue]
    attempt_id: str
    source_id: str
    status: str


class ArchiveDataset(DatasetDetails):
    live_runtime: dict[str, JsonValue]


class StrategyMaterial(EvidenceRecord):
    candidate_id: str


def register(app: FastAPI, access: WorkspaceAccess, instances: Instances) -> None:

    @app.post(
        "/api/sources/{source_id}/reprocess",
        response_model=ArchiveAttempt,
        response_model_exclude_unset=True,
    )
    async def reprocess(
        request: Request,
        document: ArchiveReprocessRequest,
        source_id: UUID,
        runtime: Annotated[UUID, Depends(_runtime_header)],
    ) -> dict[str, object]:
        access.protect(request)
        live = instances.for_request(request)
        command_live = live.for_runtime(runtime)
        payload = document.model_dump(mode="json", exclude_unset=True)
        return await run_in_threadpool(
            command_live.mutate,
            f"/sources/{source_id}/reprocess",
            {"spec": _object(payload["spec"])},
            _uuid_field(payload, "request_id"),
        )

    @app.get("/api/sources/{source_id}/download")
    async def download(request: Request, source_id: UUID) -> Response:
        access.require_request(request)
        live = instances.for_request(request)
        payload = await run_in_threadpool(live.read, f"/sources/{source_id}/download")
        return Response(
            base64.b64decode(payload["content_base64"], validate=True),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(payload["filename"], safe="")
            },
        )

    @app.get(
        "/api/sources/{source_id}", response_model=ArchiveSource, response_model_exclude_unset=True
    )
    async def source(request: Request, source_id: UUID) -> dict[str, object]:
        access.require_request(request)
        live = instances.for_request(request)
        return await run_in_threadpool(live.read, f"/sources/{source_id}")

    @app.get(
        "/api/attempts/{attempt_id}",
        response_model=ArchiveAttempt,
        response_model_exclude_unset=True,
    )
    async def attempt(request: Request, attempt_id: UUID) -> dict[str, object]:
        access.require_request(request)
        live = instances.for_request(request)
        return await run_in_threadpool(live.read, f"/attempts/{attempt_id}")

    @app.get(
        "/api/datasets/{snapshot_id}",
        response_model=ArchiveDataset,
        response_model_exclude_unset=True,
    )
    async def dataset(request: Request, snapshot_id: UUID) -> dict[str, object]:
        access.require_request(request)
        live = instances.for_request(request)
        return await run_in_threadpool(live.read, f"/datasets/{snapshot_id}")

    @app.get(
        "/api/strategy-materials",
        response_model=list[StrategyMaterial],
        response_model_exclude_unset=True,
    )
    async def materials(request: Request) -> list[dict[str, object]]:
        access.require_request(request)
        live = instances.for_request(request)
        return await run_in_threadpool(live.read_list, "/strategy-materials")

    @app.post(
        "/api/strategy-materials",
        response_model=StrategyMaterial,
        response_model_exclude_unset=True,
    )
    async def receive_material(
        request: Request,
        document: MaterialRequest,
        runtime: Annotated[UUID, Depends(_runtime_header)],
    ) -> dict[str, object]:
        access.protect(request)
        live = instances.for_request(request)
        body = document.model_dump(mode="json", exclude_unset=True)
        command_live = live.for_runtime(runtime)
        return await run_in_threadpool(
            command_live.mutate,
            "/strategy-materials",
            _object(body["candidate"]),
            _uuid_field(body, "request_id"),
        )

"""Live-local fixed material access; the management Web never opens its storage."""

import base64
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import Engine

from northstar_quant.data_management.library import DataLibrary
from northstar_quant.sessions import SessionStore

from .owner import LiveOwner


def routes(owner: LiveOwner, engine: Engine, library: DataLibrary) -> APIRouter:
    router = APIRouter()

    @router.get("/configurations")
    def configurations() -> list[dict[str, object]]:
        return SessionStore(engine, library).list_configurations()

    @router.get("/sources/{source_id}")
    def source(source_id: UUID) -> dict[str, object]:
        return library.source(source_id)

    @router.get("/attempts/{attempt_id}")
    def attempt(attempt_id: UUID) -> dict[str, object]:
        return library.attempt(attempt_id)

    @router.get("/datasets/{snapshot_id}")
    def dataset(snapshot_id: UUID) -> dict[str, object]:
        return library.describe_dataset(snapshot_id).to_dict()

    @router.get("/sources/{source_id}/download")
    def download(source_id: UUID) -> dict[str, str]:
        try:
            filename, content = library.download(source_id)
        except PermissionError as error:
            raise HTTPException(403, "Source download is not permitted") from error
        return {"filename": filename, "content_base64": base64.b64encode(content).decode("ascii")}

    @router.post("/sources/{source_id}/reprocess")
    def reprocess(request: Request, source_id: UUID, body: dict[str, Any]) -> dict[str, Any]:
        if set(body) != {"spec"} or not isinstance(body["spec"], dict):
            raise HTTPException(422, "Reprocessing accepts only fixed processing parameters")
        return owner.execute(
            request,
            body,
            lambda identifier: library.reprocess(
                source_id, spec=body["spec"], request_id=str(identifier)
            ),
        )

    return router

"""Read-only diagnostics from the independent owner, never from the Web host."""

from fastapi import FastAPI, Request
from pydantic import JsonValue
from starlette.concurrency import run_in_threadpool

from northstar_quant.web.access import WorkspaceAccess
from northstar_quant.web.requests import EvidenceRecord

from .instances import Instances


class Diagnostics(EvidenceRecord):
    status: str
    observed_at: str
    duration_ms: int
    database: dict[str, JsonValue]
    source_filesystem: dict[str, JsonValue]
    scope: str


def register(app: FastAPI, access: WorkspaceAccess, instances: Instances) -> None:
    @app.get("/api/live/diagnostics", response_model=Diagnostics, response_model_exclude_unset=True)
    async def diagnostics(request: Request) -> dict[str, object]:
        access.require_request(request)
        live = instances.for_request(request)
        return await run_in_threadpool(live.diagnostics)

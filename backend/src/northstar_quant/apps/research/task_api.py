"""Research task submission and control; API processes never own computation."""

from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import Field, JsonValue
from sqlalchemy import Engine

from northstar_quant.data_management.publications import DatasetReader
from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.research.tasks.store import TaskStore
from northstar_quant.web.access import WorkspaceAccess
from northstar_quant.web.requests import ApiModel, EvidenceRecord, UUIDText

from .configuration_api import ResearchConfigurationInput


class TaskRequest(ApiModel):
    request_id: UUIDText
    snapshot_id: UUIDText
    config: ResearchConfigurationInput


class TaskControl(ApiModel):
    action: str = Field(pattern="^(cancel|retry)$")


class ResearchTask(EvidenceRecord):
    task_id: str
    snapshot_id: str
    snapshot_hash: str
    code_revision: str
    created_at: str
    status: str
    completed: int
    total: int
    run_id: str | None
    reason: str
    config: dict[str, JsonValue]
    attempts: list[dict[str, JsonValue]]


def register(app: FastAPI, access: WorkspaceAccess, engine: Engine, library: DatasetReader) -> None:
    store = TaskStore(engine)

    @app.post("/api/tasks", status_code=202, response_model=ResearchTask)
    def submit(request: Request, document: TaskRequest) -> dict[str, Any]:
        access.protect(request)
        config = ResearchConfig.from_mapping(
            document.config.model_dump(mode="json", exclude_unset=True)
        )
        snapshot = UUID(document.snapshot_id)
        details = library.describe_dataset(snapshot)
        return store.submit(
            UUID(document.request_id),
            snapshot,
            details.summary.content_hash,
            config,
            details.summary.bar_count,
            details.to_dict(),
        )

    @app.get("/api/tasks", response_model=list[ResearchTask])
    def tasks() -> list[dict[str, Any]]:
        return store.list()

    @app.get("/api/tasks/{task_id}", response_model=ResearchTask)
    def task(task_id: str) -> dict[str, Any]:
        return store.get(task_id)

    @app.post("/api/tasks/{task_id}/control", response_model=ResearchTask)
    def control(request: Request, task_id: str, document: TaskControl) -> dict[str, Any]:
        access.protect(request)
        return store.control(task_id, document.action)

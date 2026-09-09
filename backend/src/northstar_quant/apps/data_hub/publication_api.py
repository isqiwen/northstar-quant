"""Read-only published manifests for Research; no remote database credentials."""

import os
from uuid import UUID

from fastapi import FastAPI
from pydantic import JsonValue

from northstar_quant.data_management.library import DataLibrary
from northstar_quant.web.requests import ApiModel


class PublicationManifest(ApiModel):
    snapshot_id: str
    storage_id: str
    path: str
    sha256: str
    bytes: int
    files: list[dict[str, JsonValue]]


class PublicationCatalog(ApiModel):
    snapshots: list[str]


def register(app: FastAPI, library: DataLibrary) -> None:
    app.state.workspace_access.publication_token = os.environ.get("NORTHSTAR_PUBLICATION_TOKEN")

    @app.get("/api/publications", response_model=PublicationCatalog)
    def catalog(limit: int = 50) -> dict[str, object]:
        return {"snapshots": [str(item.snapshot_id) for item in library.list_datasets(limit=limit)]}

    @app.get("/api/publications/{snapshot_id}", response_model=PublicationManifest)
    def manifest(snapshot_id: UUID) -> dict[str, object]:
        library.describe_dataset(snapshot_id)
        return library.publications.manifest(snapshot_id)

"""Read-only fixed dataset presentation shared by Data Hub and Research."""

from uuid import UUID

from fastapi import FastAPI
from pydantic import JsonValue

from northstar_quant.data_management.library import DataLibrary
from northstar_quant.web.access import WorkspaceAccess
from northstar_quant.web.requests import ApiModel


class ImportSpecification(ApiModel):
    exchange: str
    symbol: str
    product: str
    timezone: str
    currency: str
    quantity_unit: str
    price_tick: str
    multiplier: str
    trading_day: str
    session_open: str
    session_close: str
    source_name: str
    source_reference: str
    availability_basis: str
    availability_note: str


class DatasetSummary(ApiModel):
    snapshot_id: str
    content_hash: str
    exchange: str
    product: str
    symbol: str
    trading_day: str
    session_open: str
    session_close: str
    bar_count: int
    published_at: str


class DatasetDetails(DatasetSummary):
    source_reference: str
    availability_basis: str
    availability_note: str
    import_spec: ImportSpecification
    sources: list[dict[str, JsonValue]]
    quality: dict[str, JsonValue]
    semantics: dict[str, JsonValue]
    processing_provenance: dict[str, JsonValue] | None = None
    limitations: list[str]


class DatasetLineage(ApiModel):
    snapshot_id: str
    sources: list[dict[str, JsonValue]]
    attempts: list[dict[str, JsonValue]]
    usages: list[dict[str, JsonValue]]


def register(app: FastAPI, access: WorkspaceAccess, library: DataLibrary) -> None:
    @app.get(
        "/api/datasets", response_model=list[DatasetSummary], response_model_exclude_unset=True
    )
    def accepted_datasets(limit: int = 50) -> list[dict[str, object]]:
        return [item.to_dict() for item in library.list_datasets(limit=limit)]

    @app.get(
        "/api/datasets/{snapshot_id}",
        response_model=DatasetDetails,
        response_model_exclude_unset=True,
    )
    def dataset_details(snapshot_id: UUID) -> dict[str, object]:
        return library.describe_dataset(snapshot_id).to_dict()

    @app.get(
        "/api/datasets/{snapshot_id}/lineage",
        response_model=DatasetLineage,
        response_model_exclude_unset=True,
    )
    def dataset_lineage(snapshot_id: UUID) -> dict[str, object]:
        return library.lineage(snapshot_id)

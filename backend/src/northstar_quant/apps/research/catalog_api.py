"""Research catalog HTTP inputs; operations and records belong to Research."""

from typing import ClassVar
from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import ConfigDict, JsonValue
from starlette.concurrency import run_in_threadpool

from northstar_quant.factors import registry as factors
from northstar_quant.factors.evaluation import Binding
from northstar_quant.research.factor_catalog import FactorCatalog
from northstar_quant.research.strategy_management import StrategyVersions
from northstar_quant.strategies import registry as strategies
from northstar_quant.web.access import WorkspaceAccess
from northstar_quant.web.requests import ApiModel, EvidenceRecord, UUIDText, _object

from .configuration_api import SavedConfiguration
from .run_api import RunDetail


class FactorRevisionRequest(ApiModel):
    factor_id: str
    parameters: dict[str, str | int]


class AnnotationRequest(ApiModel):
    description: str


class FactorRunRequest(ApiModel):
    revision_id: str
    snapshot_id: UUIDText


class StrategyVersionRequest(ApiModel):
    name: str
    configuration_id: str
    run_ids: list[str]


class PublishRequest(ApiModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)


class ParameterDescription(ApiModel):
    name: str
    label: str
    default: str | int
    minimum: str | int
    maximum: str | int
    unit: str


class FactorSummary(ApiModel):
    factor_id: str
    name: str
    category: str
    description: str
    capabilities: list[str]
    limitations: str


class FactorDescription(FactorSummary):
    revision: str
    parameters: list[ParameterDescription]


class StrategySummary(ApiModel):
    strategy_id: str
    name: str
    category: str
    description: str


class StrategyDescription(StrategySummary):
    revision: str
    parameters: list[ParameterDescription]
    factor_slots: dict[str, str]


class Catalog(ApiModel):
    factors: list[FactorSummary]
    strategies: list[StrategySummary]


class FactorBinding(ApiModel):
    factor_id: str
    revision: str
    code_revision: str
    parameters: dict[str, str | int]


class Annotation(ApiModel):
    description: str
    at: str


class FactorRevision(ApiModel):
    revision_id: str
    binding: FactorBinding
    annotations: list[Annotation]


class RevisionCreated(ApiModel):
    revision_id: str


class FactorValue(EvidenceRecord):
    observation_id: str
    at: str
    value: str | None
    status: str
    reason: str


class FactorResult(ApiModel):
    inputs: dict[str, JsonValue]
    values: list[FactorValue]
    evaluation: dict[str, JsonValue]


class FactorRun(EvidenceRecord):
    attempt_id: str
    revision_id: str
    snapshot_id: str
    status: str
    code_revision: str
    result: FactorResult | None
    error: str | None
    created_at: str
    completed_at: str | None


class VersionCreated(ApiModel):
    version_id: str


class VersionDocument(ApiModel):
    configuration: SavedConfiguration
    code_revision: str
    evidence: list[RunDetail]
    validation: dict[str, JsonValue]


class StrategyVersion(ApiModel):
    version_id: str
    name: str
    document: VersionDocument
    created_at: str


class StrategyCandidate(ApiModel):
    format: int
    version_id: str
    document: VersionDocument
    same_clean_revision: bool
    candidate_id: str


def register(
    app: FastAPI, access: WorkspaceAccess, catalog: FactorCatalog, versions: StrategyVersions
) -> None:
    @app.get("/api/catalog", response_model=Catalog, response_model_exclude_unset=True)
    def installed() -> dict[str, object]:
        return {"factors": factors.catalog(), "strategies": strategies.catalog()}

    @app.get(
        "/api/factor-definitions/{factor_id}",
        response_model=FactorDescription,
        response_model_exclude_unset=True,
    )
    def factor_definition(factor_id: str) -> dict[str, object]:
        return factors.describe(factor_id)

    @app.get(
        "/api/strategy-definitions/{strategy_id}",
        response_model=StrategyDescription,
        response_model_exclude_unset=True,
    )
    def strategy_definition(strategy_id: str) -> dict[str, object]:
        return strategies.describe(strategy_id)

    @app.get(
        "/api/factor-revisions",
        response_model=list[FactorRevision],
        response_model_exclude_unset=True,
    )
    def revisions() -> list[dict[str, object]]:
        return catalog.revisions()

    @app.post(
        "/api/factor-revisions",
        status_code=201,
        response_model=RevisionCreated,
        response_model_exclude_unset=True,
    )
    async def save_factor(request: Request, document: FactorRevisionRequest) -> dict[str, str]:
        access.protect(request)
        body = document.model_dump(mode="json", exclude_unset=True)
        binding = Binding.create(str(body["factor_id"]), _object(body["parameters"]))
        identity = await run_in_threadpool(catalog.register, binding)
        return {"revision_id": identity}

    @app.post(
        "/api/factor-revisions/{revision_id}/annotations",
        response_model=RevisionCreated,
        response_model_exclude_unset=True,
    )
    async def annotate(
        request: Request, document: AnnotationRequest, revision_id: str
    ) -> dict[str, str]:
        access.protect(request)
        body = document.model_dump(mode="json", exclude_unset=True)
        await run_in_threadpool(catalog.annotate, revision_id, body["description"])
        return {"revision_id": revision_id}

    @app.get("/api/factor-runs", response_model=list[FactorRun], response_model_exclude_unset=True)
    def calculations() -> list[dict[str, object]]:
        return catalog.runs()

    @app.get(
        "/api/factor-runs/{attempt_id}", response_model=FactorRun, response_model_exclude_unset=True
    )
    def calculation(attempt_id: UUID) -> dict[str, object]:
        return catalog.get(attempt_id)

    @app.post(
        "/api/factor-runs",
        status_code=201,
        response_model=FactorRun,
        response_model_exclude_unset=True,
    )
    async def calculate(request: Request, document: FactorRunRequest) -> dict[str, object]:
        access.protect(request)
        body = document.model_dump(mode="json", exclude_unset=True)
        return await run_in_threadpool(
            catalog.calculate, str(body["revision_id"]), UUID(str(body["snapshot_id"]))
        )

    @app.get(
        "/api/strategy-versions",
        response_model=list[StrategyVersion],
        response_model_exclude_unset=True,
    )
    def saved_versions() -> list[dict[str, object]]:
        return versions.list()

    @app.post(
        "/api/strategy-versions",
        status_code=201,
        response_model=VersionCreated,
        response_model_exclude_unset=True,
    )
    async def save_version(request: Request, document: StrategyVersionRequest) -> dict[str, str]:
        access.protect(request)
        body = document.model_dump(mode="json", exclude_unset=True)
        identifiers = document.run_ids
        identity = await run_in_threadpool(
            versions.register, str(body["name"]), str(body["configuration_id"]), identifiers
        )
        return {"version_id": identity}

    @app.post(
        "/api/strategy-versions/{version_id}/publish",
        response_model=StrategyCandidate,
        response_model_exclude_unset=True,
    )
    async def publish(
        request: Request, document: PublishRequest, version_id: str
    ) -> dict[str, object]:
        access.protect(request)
        return await run_in_threadpool(versions.publish, version_id)

    @app.get(
        "/api/strategy-candidates",
        response_model=list[StrategyCandidate],
        response_model_exclude_unset=True,
    )
    def candidates() -> list[dict[str, object]]:
        return versions.candidates()

    @app.get(
        "/api/strategy-versions/{version_id}",
        response_model=StrategyVersion,
        response_model_exclude_unset=True,
    )
    def version(version_id: str) -> dict[str, object]:
        return versions.get(version_id)

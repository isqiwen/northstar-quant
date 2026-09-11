"""Finite parameter-study admission; calculations belong to the research worker."""

from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request
from pydantic import Field, JsonValue
from sqlalchemy import Engine

from northstar_quant.data_management.publications import DatasetReader
from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.research.experiments import Experiments
from northstar_quant.research.learning import LearningRecipe
from northstar_quant.web.access import WorkspaceAccess
from northstar_quant.web.requests import ApiModel, UUIDText

from .configuration_api import ResearchConfigurationInput


class ExperimentRequest(ApiModel):
    request_id: UUIDText
    hypothesis: str = Field(min_length=1, max_length=1000)
    train_snapshot: UUIDText
    validation_snapshot: UUIDText
    test_snapshot: UUIDText
    configurations: list[ResearchConfigurationInput] = Field(min_length=2, max_length=64)


class LearningRecipeInput(ApiModel):
    fast_bars: int = Field(ge=1, le=999)
    slow_bars: int = Field(ge=2, le=1000)
    horizon_bars: int = Field(ge=1, le=100)
    penalties: list[str] = Field(min_length=2, max_length=16)
    threshold: str
    target_fraction: str


class LearningExperimentRequest(ExperimentRequest):
    configurations: list[ResearchConfigurationInput] = Field(min_length=1, max_length=1)
    learning: LearningRecipeInput


class Experiment(ApiModel):
    experiment_id: str
    plan_id: str
    created_at: str
    status: str
    plan: dict[str, JsonValue]
    selection: dict[str, JsonValue] | None
    fitted: dict[str, JsonValue] | None
    trials: list[dict[str, JsonValue]]


def register(app: FastAPI, access: WorkspaceAccess, engine: Engine, library: DatasetReader) -> None:
    experiments = Experiments(engine)

    @app.post("/api/experiments", status_code=202, response_model=Experiment)
    def submit(request: Request, document: ExperimentRequest) -> dict[str, Any]:
        access.protect(request)
        return experiments.submit(
            UUID(document.request_id),
            document.hypothesis,
            (
                UUID(document.train_snapshot),
                UUID(document.validation_snapshot),
                UUID(document.test_snapshot),
            ),
            [
                ResearchConfig.from_mapping(c.model_dump(mode="json", exclude_unset=True))
                for c in document.configurations
            ],
            library,
        )

    @app.post("/api/experiments/learn", status_code=202, response_model=Experiment)
    def learn(request: Request, document: LearningExperimentRequest) -> dict[str, Any]:
        access.protect(request)
        return experiments.submit(
            UUID(document.request_id),
            document.hypothesis,
            (
                UUID(document.train_snapshot),
                UUID(document.validation_snapshot),
                UUID(document.test_snapshot),
            ),
            [
                ResearchConfig.from_mapping(
                    document.configurations[0].model_dump(mode="json", exclude_unset=True)
                )
            ],
            library,
            LearningRecipe.from_dict(document.learning.model_dump()),
        )

    @app.get("/api/experiments", response_model=list[Experiment])
    def list_experiments() -> list[dict[str, Any]]:
        return experiments.list()

    @app.get("/api/experiments/{experiment_id}", response_model=Experiment)
    def get(experiment_id: UUIDText) -> dict[str, Any]:
        return experiments.get(experiment_id)

"""Fixed Research configuration HTTP values and operations."""

from fastapi import FastAPI, Request
from pydantic import Field
from starlette.concurrency import run_in_threadpool

from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.research.configurations import ConfigurationStore
from northstar_quant.web.access import WorkspaceAccess
from northstar_quant.web.requests import ApiModel, _object


class FactorInput(ApiModel):
    factor_id: str
    parameters: dict[str, str | int] = Field(default_factory=dict)


class FixedFactor(FactorInput):
    revision: str
    code_revision: str


class StrategyInput(ApiModel):
    strategy_id: str = "trend.momentum"
    parameters: dict[str, str | int] = Field(default_factory=dict)
    factor_bindings: dict[str, FactorInput] | None = None


class FixedStrategy(ApiModel):
    strategy_id: str
    revision: str
    code_revision: str
    parameters: dict[str, str | int]
    factor_bindings: dict[str, FixedFactor]


class RiskInput(ApiModel):
    max_lots: int = 10
    max_gross_notional: str = "1000000"
    max_margin_fraction: str = "0.5"
    initial_margin_fraction: str = "0.1"
    max_adverse_price_move_fraction: str = "0.1"


class SimulationInput(ApiModel):
    initial_cash: str = "100000"
    fee_per_lot: str = "2"
    slippage_ticks: int = 1


class ResearchConfigurationInput(ApiModel):
    strategy: FixedStrategy | StrategyInput | None = None
    risk: RiskInput = Field(default_factory=RiskInput)
    simulation: SimulationInput = Field(default_factory=SimulationInput)


class ResearchConfiguration(ApiModel):
    strategy: FixedStrategy
    risk: RiskInput
    simulation: SimulationInput


class ConfigurationRequest(ApiModel):
    name: str
    config: ResearchConfigurationInput


class SavedConfiguration(ApiModel):
    configuration_id: str
    name: str
    config: ResearchConfiguration
    strategy_hash: str
    risk_hash: str
    created_at: str


def register(app: FastAPI, access: WorkspaceAccess, configurations: ConfigurationStore) -> None:
    @app.get(
        "/api/configurations",
        response_model=list[SavedConfiguration],
        response_model_exclude_unset=True,
    )
    def list_configurations() -> list[dict[str, object]]:
        return configurations.list_configurations()

    @app.post(
        "/api/configurations",
        status_code=201,
        response_model=SavedConfiguration,
        response_model_exclude_unset=True,
    )
    async def save_configuration(
        request: Request, document: ConfigurationRequest
    ) -> dict[str, object]:
        access.protect(request)
        payload = document.model_dump(mode="json", exclude_unset=True)
        configuration = ResearchConfig.from_mapping(_object(payload["config"]))
        return await run_in_threadpool(
            configurations.save_configuration, payload["name"], configuration
        )

    @app.get("/api/configuration-defaults", response_model=ResearchConfiguration)
    def defaults() -> dict[str, object]:
        return ResearchConfig().to_dict()

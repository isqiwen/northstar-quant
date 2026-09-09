"""Research owns calculations, fixed configurations and internal file Paper."""

from fastapi import FastAPI
from sqlalchemy import Engine

from northstar_quant.apps.logging import logged_application
from northstar_quant.data_management.publications import DatasetReader
from northstar_quant.research.configurations import ConfigurationStore
from northstar_quant.research.factor_catalog import FactorCatalog
from northstar_quant.research.paper import PaperStore
from northstar_quant.research.runs import RunStore
from northstar_quant.research.strategy_management import StrategyVersions
from northstar_quant.web import datasets
from northstar_quant.web.host import create_host
from northstar_quant.web.protobuf import bind

from . import catalog_api, configuration_api, paper_api, run_api, task_api


def create_app(engine: Engine, library: DatasetReader) -> FastAPI:
    app = create_host(
        "Northstar Research · 量化研究工作台",
        (("研究", "/"), ("因子与策略", "/catalog"), ("文件 Paper", "/paper")),
    )
    catalog_api.register(
        app, app.state.workspace_access, FactorCatalog(engine, library), StrategyVersions(engine)
    )
    access = app.state.workspace_access
    run_api.register(app, access, library, RunStore(engine))
    task_api.register(app, access, engine, library)
    paper_api.register(app, access, library, PaperStore(engine, library))
    configuration_api.register(app, access, ConfigurationStore(engine))
    datasets.register(app, app.state.workspace_access, library)
    bind(app, "research")
    return app


@logged_application("research", "api")
def application() -> FastAPI:
    from northstar_quant.data_management.publication_client import PublicationClient
    from northstar_quant.research.storage import open_store, require_current

    engine = open_store()
    require_current(engine)
    from northstar_quant.research.artifacts import ResearchUsages

    return create_app(
        engine, PublicationClient.from_environment(usages=ResearchUsages(engine).list)
    )

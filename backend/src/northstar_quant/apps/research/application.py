"""Research owns calculations, fixed configurations and internal file Paper."""

from fastapi import FastAPI
from sqlalchemy import Engine

from northstar_quant.data_management.library import DataLibrary
from northstar_quant.research.configurations import ConfigurationStore
from northstar_quant.research.factor_catalog import FactorCatalog
from northstar_quant.research.paper import PaperStore
from northstar_quant.research.runs import RunStore
from northstar_quant.research.strategy_management import StrategyVersions
from northstar_quant.web import datasets
from northstar_quant.web.host import create_host
from northstar_quant.web.protobuf import bind

from . import catalog_api, configuration_api, paper_api, run_api


def create_app(engine: Engine, library: DataLibrary) -> FastAPI:
    app = create_host(
        "Northstar Research · 量化研究工作台",
        (("研究", "/"), ("因子与策略", "/catalog"), ("文件 Paper", "/paper")),
    )
    catalog_api.register(
        app, app.state.workspace_access, FactorCatalog(engine, library), StrategyVersions(engine)
    )
    access = app.state.workspace_access
    run_api.register(app, access, library, RunStore(engine))
    paper_api.register(app, access, library, PaperStore(engine, library))
    configuration_api.register(app, access, ConfigurationStore(engine))
    datasets.register(app, app.state.workspace_access, library)
    bind(app, "research")
    return app


def application() -> FastAPI:
    from northstar_quant.apps.storage import open_database, require_current_database
    from northstar_quant.data_management.files import SourceFiles

    engine = open_database()
    require_current_database(engine)
    return create_app(engine, DataLibrary(engine, SourceFiles.from_environment()))

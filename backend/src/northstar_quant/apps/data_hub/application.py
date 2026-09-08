"""Data Hub owns data admission and publication; it never creates a Live client."""

from fastapi import FastAPI
from sqlalchemy import Engine

from northstar_quant.apps.logging import logged_application
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.web import datasets
from northstar_quant.web.host import create_host
from northstar_quant.web.protobuf import bind

from . import source_api


def create_app(engine: Engine, library: DataLibrary) -> FastAPI:
    app = create_host(
        "Northstar Data Hub · 数据管理中心", (("数据管理", "/"), ("来源与处理", "/sources"))
    )
    source_api.register(app, app.state.workspace_access, library)
    datasets.register(app, app.state.workspace_access, library)
    bind(app, "data_hub")
    return app


@logged_application("data_hub", "api")
def application() -> FastAPI:
    from northstar_quant.apps.storage import open_database, require_current_database

    engine = open_database()
    require_current_database(engine)
    return create_app(engine, DataLibrary(engine, SourceFiles.from_environment()))

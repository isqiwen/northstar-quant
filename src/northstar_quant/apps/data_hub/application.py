"""Data Hub owns data admission and publication; it never creates a Live client."""

from fastapi import FastAPI
from sqlalchemy import Engine

from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.web import datasets
from northstar_quant.web.host import create_host

from . import routes


def create_app(engine: Engine, library: DataLibrary) -> FastAPI:
    app = create_host(
        "Northstar Data Hub · 数据管理中心", (("数据管理", "/"), ("来源与处理", "/sources"))
    )
    routes.register(app, app.state.workspace_access, library)
    datasets.register(app, app.state.workspace_access, library)
    return app


def application() -> FastAPI:
    from northstar_quant.db import open_database, require_current_database
    from northstar_quant.nicegui_workspace import mount_workspace

    from .pages import register

    engine = open_database()
    require_current_database(engine)
    library = DataLibrary(engine, SourceFiles.from_environment())
    app = create_app(engine, library)
    mount_workspace(app, app.state.workspace_access, lambda: register(app, library))
    return app

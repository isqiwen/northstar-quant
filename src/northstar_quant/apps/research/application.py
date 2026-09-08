"""Research owns calculations, fixed configurations and internal file Paper."""

from fastapi import FastAPI
from sqlalchemy import Engine

from northstar_quant.data_management.library import DataLibrary
from northstar_quant.runs import RunStore
from northstar_quant.sessions import SessionStore
from northstar_quant.web import datasets
from northstar_quant.web.host import create_host

from . import paper_routes, routes


def create_app(engine: Engine, library: DataLibrary) -> FastAPI:
    app = create_host(
        "Northstar Research · 量化研究工作台", (("研究", "/"), ("文件 Paper", "/paper"))
    )
    access = app.state.workspace_access
    routes.register(app, access, library, RunStore(engine))
    paper_routes.register(app, access, library, SessionStore(engine, library))
    datasets.register(app, app.state.workspace_access, library)
    return app


def application() -> FastAPI:
    from northstar_quant.data_management.files import SourceFiles
    from northstar_quant.db import open_database, require_current_database
    from northstar_quant.nicegui_workspace import mount_workspace

    from .pages import register

    engine = open_database()
    require_current_database(engine)
    library = DataLibrary(engine, SourceFiles.from_environment())
    app = create_app(engine, library)
    mount_workspace(
        app, app.state.workspace_access, lambda: register(app, library, RunStore(engine))
    )
    return app

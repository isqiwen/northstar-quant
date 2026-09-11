"""Select the application's owned store; there is no combined application database."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine, inspect


def _owner(value: str | None = None) -> str:
    owner = value or os.environ.get("NORTHSTAR_DATABASE_OWNER", "data_hub")
    if owner not in {"data_hub", "research", "live"}:
        raise ValueError("unknown database owner")
    return owner


def open_database() -> Engine:
    owner = _owner()
    if owner == "data_hub":
        from northstar_quant.data_management.db.store import open_store

        return open_store()
    if owner == "research":
        from northstar_quant.research.storage import open_store as open_research

        return open_research()
    from northstar_quant.live.storage import open_store as open_live

    root = Path(os.environ.get("NORTHSTAR_LIVE_STATE_DIR", "/var/lib/northstar/state"))
    return open_live(root / "live.sqlite")


def initialize_database(engine: Engine, *, owner: str | None = None) -> None:
    selected = _owner(owner)
    if selected == "data_hub":
        from northstar_quant.data_management.db.store import initialize
    elif selected == "research":
        from northstar_quant.research.storage import initialize
    else:
        from northstar_quant.live.storage import initialize
    initialize(engine)


def require_current_database(engine: Engine) -> None:
    """Read the persisted owner before invoking its own current-shape checks."""
    with engine.connect() as connection:
        if "northstar_store" not in inspect(connection).get_table_names():
            raise ValueError("database does not have the current owned baseline")
        recorded = connection.exec_driver_sql("SELECT owner FROM northstar_store").scalar_one()
    configured = os.environ.get("NORTHSTAR_DATABASE_OWNER")
    if configured is not None and _owner(configured) != recorded:
        raise ValueError("database owner mismatch")
    if recorded == "data_hub":
        from northstar_quant.data_management.db.store import require_current
    elif recorded == "research":
        from northstar_quant.research.storage import require_current
    elif recorded == "live":
        from northstar_quant.live.storage import require_current
    else:
        raise ValueError("unknown database owner")
    require_current(engine)

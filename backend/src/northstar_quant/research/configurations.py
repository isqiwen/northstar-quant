"""Immutable configuration revisions, independent of Paper accounts and data access."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC
from typing import cast

from sqlalchemy import (
    JSON,
    Column,
    Connection,
    Engine,
    MetaData,
    String,
    Table,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.engine import RowMapping

from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.research.storage import UTCDateTime

_metadata = MetaData()
_configurations = Table(
    "paper_configurations",
    _metadata,
    Column("configuration_id", String(64), primary_key=True),
    Column("name", String(80), nullable=False),
    Column("config", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("created_at", UTCDateTime(), nullable=False, server_default=func.now()),
)


def initialize_configuration_store(connection: Connection) -> None:
    """Install revision storage explicitly, retaining existing revision identities."""
    _metadata.create_all(connection)
    if connection.dialect.name == "sqlite":
        from .storage import immutable

        immutable(connection, "paper_configurations")
        return
    connection.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION configuration_reject_fact_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Configuration revisions are immutable';
        END;
        $$ LANGUAGE plpgsql
    """)
    connection.exec_driver_sql("DROP TRIGGER IF EXISTS immutable ON paper_configurations")
    connection.exec_driver_sql(
        "CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON paper_configurations "
        "FOR EACH ROW EXECUTE FUNCTION configuration_reject_fact_change()"
    )


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("stored configuration content must be an object")
    return cast(dict[str, object], value)


def _configuration(row: RowMapping) -> dict[str, object]:
    config = ResearchConfig.from_mapping(_object(row["config"])).to_dict()
    name = str(row["name"])
    identity = _hash({"name": name, "config": config})
    if identity != row["configuration_id"]:
        raise ValueError("stored configuration no longer matches its immutable identity")
    return {
        "configuration_id": identity,
        "name": name,
        "config": config,
        "strategy_hash": _hash(config["strategy"]),
        "risk_hash": _hash(config["risk"]),
        "created_at": row["created_at"].astimezone(UTC).isoformat(),
    }


class ConfigurationStore:
    """Save and read fixed revisions without constructing a simulated account."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def save_configuration(self, name: str, config: ResearchConfig) -> dict[str, object]:
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ValueError("configuration name must contain 1 to 80 characters")
        name = name.strip()
        content = config.to_dict()
        identity = _hash({"name": name, "config": content})
        with self._engine.begin() as connection:
            from northstar_quant.research.factor_catalog import register_binding

            for _, binding in config.strategy.factors:
                register_binding(connection, binding)
            connection.execute(
                insert(_configurations)
                .values(configuration_id=identity, name=name, config=content)
                .on_conflict_do_nothing(index_elements=[_configurations.c.configuration_id])
            )
            return read_configuration(connection, identity)

    def list_configurations(self) -> list[dict[str, object]]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    select(_configurations)
                    .order_by(
                        _configurations.c.created_at.desc(), _configurations.c.configuration_id
                    )
                    .limit(100)
                )
                .mappings()
                .all()
            )
            return [_configuration(row) for row in rows]

    def get_configuration(self, configuration_id: str) -> dict[str, object]:
        """Read an exact immutable revision, without creating a Paper account."""
        with self._engine.connect() as connection:
            return read_configuration(connection, configuration_id)


def read_configuration(connection: Connection, identity: str) -> dict[str, object]:
    if not isinstance(identity, str) or re.fullmatch(r"[0-9a-f]{64}", identity) is None:
        raise ValueError("configuration_id must be a lowercase SHA-256 identity")
    row = (
        connection.execute(
            select(_configurations).where(_configurations.c.configuration_id == identity)
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError("Configuration revision not found")
    return _configuration(row)


def read_configurations(
    connection: Connection, identities: set[str]
) -> dict[str, dict[str, object]]:
    """Read the revisions for a Paper listing inside its consistent transaction."""
    rows = connection.execute(
        select(_configurations).where(_configurations.c.configuration_id.in_(identities))
    ).mappings()
    return {row["configuration_id"]: _configuration(row) for row in rows}

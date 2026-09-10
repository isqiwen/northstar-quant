"""Live-local receipt of fixed candidates; receiving never creates a trading session."""

from datetime import datetime
from typing import Any

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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from northstar_quant.broker.records import EvidenceTimestamp
from northstar_quant.live.storage import write_transaction
from northstar_quant.strategies.artifacts import verify_candidate

_metadata = MetaData()
_materials = Table(
    "live_strategy_materials",
    _metadata,
    Column("candidate_id", String(64), primary_key=True),
    Column("document", JSON().with_variant(JSONB, "postgresql"), nullable=False),
    Column("received_at", EvidenceTimestamp(), server_default=func.now(), nullable=False),
)


def initialize_materials(connection: Connection) -> None:
    _metadata.create_all(connection)
    if connection.dialect.name == "sqlite":
        for table in ("live_strategy_materials",):
            for action in ("UPDATE", "DELETE"):
                connection.exec_driver_sql(
                    f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action} "
                    f"BEFORE {action} ON {table} "
                    "BEGIN SELECT RAISE(ABORT, 'Confirmed facts are immutable'); END"
                )
        return
    connection.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION live_material_immutable() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'Live strategy materials are immutable'; END; $$ LANGUAGE plpgsql;
        DROP TRIGGER IF EXISTS immutable ON live_strategy_materials;
        CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON live_strategy_materials
            FOR EACH ROW EXECUTE FUNCTION live_material_immutable();
    """)


class StrategyMaterials:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def accept(self, candidate: dict[str, Any]) -> dict[str, Any]:
        verified = verify_candidate(candidate, production=True)
        with write_transaction(self._engine) as connection:
            connection.execute(
                (sqlite_insert if connection.dialect.name == "sqlite" else pg_insert)(_materials)
                .values(candidate_id=verified["candidate_id"], document=verified)
                .on_conflict_do_nothing()
            )
        return {
            "candidate_id": verified["candidate_id"],
            "status": "RECEIVED",
            "execution_authorized": False,
        }

    def list(self) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    select(_materials).order_by(_materials.c.received_at.desc()).limit(100)
                )
                .mappings()
                .all()
            )
        result = []
        for row in rows:
            verify_candidate(row["document"])
            result.append(
                {
                    **{
                        key: value.isoformat() if isinstance(value, datetime) else value
                        for key, value in row.items()
                    },
                    "status": "RECEIVED",
                    "execution_authorized": False,
                }
            )
        return result

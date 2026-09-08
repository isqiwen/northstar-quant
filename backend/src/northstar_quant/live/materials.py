"""Live-local receipt of fixed candidates; receiving never creates a trading session."""

from datetime import datetime
from typing import Any

from sqlalchemy import Column, Connection, DateTime, Engine, MetaData, String, Table, func, select
from sqlalchemy.dialects.postgresql import JSONB, insert

from northstar_quant.strategies.artifacts import verify_candidate

_metadata = MetaData()
_materials = Table(
    "live_strategy_materials",
    _metadata,
    Column("candidate_id", String(64), primary_key=True),
    Column("document", JSONB, nullable=False),
    Column("received_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)


def initialize_materials(connection: Connection) -> None:
    _metadata.create_all(connection)
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
        with self._engine.begin() as connection:
            connection.execute(
                insert(_materials)
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

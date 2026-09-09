"""Persist complete, reproducible research runs in the application database."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import cast
from uuid import UUID as Identifier
from uuid import uuid4

from sqlalchemy import (
    Column,
    Connection,
    DateTime,
    Engine,
    MetaData,
    String,
    Table,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID, insert

from northstar_quant import code_revision
from northstar_quant.data_management.research import ResearchDataset
from northstar_quant.research.backtesting import ResearchResult
from northstar_quant.research.configuration import ResearchConfig

_metadata = MetaData()
_runs = Table(
    "research_runs",
    _metadata,
    Column("run_id", String(64), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("code_revision", String(64), nullable=False),
    Column("snapshot_id", UUID(as_uuid=True), nullable=False),
    Column("snapshot_hash", String(64), nullable=False),
    Column("config", JSONB, nullable=False),
    Column("result", JSONB, nullable=False),
)


_attempts = Table(
    "research_attempts",
    _metadata,
    Column("attempt_id", UUID(as_uuid=True), primary_key=True),
    Column("snapshot_id", UUID(as_uuid=True), nullable=False),
    Column("config", JSONB, nullable=False),
    Column("code_revision", String(64), nullable=False),
    Column("status", String(20), nullable=False),
    Column("run_id", String(64)),
    Column("error", String(1000)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


def initialize_run_store(engine: Engine | Connection) -> None:
    """Create the current result table during explicit database initialization."""

    if engine.dialect.name != "postgresql":
        raise ValueError("research results require PostgreSQL")
    _metadata.create_all(engine)

    def guards(connection: Connection) -> None:
        connection.exec_driver_sql("""
            CREATE OR REPLACE FUNCTION research_attempt_transition() RETURNS trigger AS $$
            BEGIN
              IF TG_OP = 'DELETE' OR OLD.status <> 'RUNNING'
                 OR NEW.status NOT IN ('SUCCEEDED','FAILED')
                 OR ROW(NEW.attempt_id, NEW.snapshot_id, NEW.config,
                    NEW.code_revision, NEW.created_at)
                    IS DISTINCT FROM ROW(OLD.attempt_id, OLD.snapshot_id, OLD.config,
                    OLD.code_revision, OLD.created_at)
              THEN RAISE EXCEPTION 'research attempt identity and terminal facts are immutable';
              END IF;
              RETURN NEW;
            END; $$ LANGUAGE plpgsql;
            DROP TRIGGER IF EXISTS immutable ON research_attempts;
            CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON research_attempts
                FOR EACH ROW EXECUTE FUNCTION research_attempt_transition();
        """)

    if isinstance(engine, Connection):
        guards(engine)
    else:
        with engine.begin() as connection:
            guards(connection)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


class RunStore:
    """Save a whole run atomically; content identity makes retries idempotent."""

    def __init__(self, engine: Engine) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("research results require PostgreSQL")
        self._engine = engine

    def begin_attempt(self, snapshot_id: Identifier, config: ResearchConfig) -> Identifier:
        identifier = uuid4()
        with self._engine.begin() as connection:
            connection.execute(
                insert(_attempts).values(
                    attempt_id=identifier,
                    snapshot_id=snapshot_id,
                    config=config.to_dict(),
                    code_revision=code_revision(),
                    status="RUNNING",
                )
            )
        return identifier

    def finish_attempt(
        self, identifier: Identifier, *, run_id: str | None = None, error: str | None = None
    ) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                update(_attempts)
                .where(_attempts.c.attempt_id == identifier, _attempts.c.status == "RUNNING")
                .values(
                    status="FAILED" if error is not None else "SUCCEEDED",
                    run_id=run_id,
                    error=error,
                )
            )

    def attempts(self) -> list[dict[str, object]]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    select(_attempts).order_by(_attempts.c.created_at.desc()).limit(200)
                )
                .mappings()
                .all()
            )
        return [
            {
                key: str(value)
                if isinstance(value, Identifier)
                else value.isoformat()
                if isinstance(value, datetime)
                else value
                for key, value in row.items()
            }
            for row in rows
        ]

    def save(self, dataset: ResearchDataset, config: ResearchConfig, result: ResearchResult) -> str:
        configuration = config.to_dict()
        complete_result = result.to_dict()
        snapshot = {"id": str(dataset.snapshot_id), "content_hash": dataset.content_hash}
        if complete_result["snapshot"] != snapshot or complete_result["config"] != configuration:
            raise ValueError("the result does not belong to the supplied data and configuration")
        evidence = None if dataset.details is None else dataset.details.to_dict()
        if complete_result["data"] != evidence:
            raise ValueError("the result does not contain the supplied snapshot's source evidence")
        implementation = code_revision()
        payload = {
            "code_revision": implementation,
            "snapshot": snapshot,
            "config": configuration,
            "result": complete_result,
        }
        run_id = hashlib.sha256(_canonical(payload)).hexdigest()
        # Config and snapshot have their own columns; keep one stored copy.
        stored_result = {
            key: value
            for key, value in complete_result.items()
            if key not in {"config", "snapshot"}
        }
        with self._engine.begin() as connection:
            from northstar_quant.research.factor_catalog import register_binding

            for _, binding in config.strategy.factors:
                register_binding(connection, binding)
            connection.execute(
                insert(_runs)
                .values(
                    run_id=run_id,
                    code_revision=implementation,
                    snapshot_id=dataset.snapshot_id,
                    snapshot_hash=dataset.content_hash,
                    config=configuration,
                    result=stored_result,
                )
                .on_conflict_do_nothing(index_elements=[_runs.c.run_id])
            )
        from .artifacts import ResearchArtifacts, publish_usage

        if os.environ.get("NORTHSTAR_RESEARCH_DIR"):
            ResearchArtifacts.from_environment().save("BACKTEST", run_id, payload)
            publish_usage(self._engine, dataset.snapshot_id)
        return run_id

    def get(self, run_id: str) -> dict[str, object]:
        if len(run_id) != 64 or any(character not in "0123456789abcdef" for character in run_id):
            raise ValueError("run_id must be a lowercase SHA-256 identity")
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(_runs).where(_runs.c.run_id == run_id))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError("research run not found")
        snapshot = {"id": str(row["snapshot_id"]), "content_hash": str(row["snapshot_hash"])}
        config = cast(dict[str, object], row["config"])
        result = {**cast(dict[str, object], row["result"]), "snapshot": snapshot, "config": config}
        identity = {
            "code_revision": str(row["code_revision"]),
            "snapshot": snapshot,
            "config": config,
            "result": result,
        }
        if hashlib.sha256(_canonical(identity)).hexdigest() != run_id:
            raise ValueError("stored research content no longer matches its immutable identity")
        return {
            "run_id": str(row["run_id"]),
            "created_at": _timestamp(cast(datetime, row["created_at"])),
            "committed_code": not str(row["code_revision"]).endswith("-dirty"),
            "code_revision": str(row["code_revision"]),
            "snapshot": snapshot,
            "config": config,
            "result": result,
        }

    def list(self, limit: int = 50) -> list[dict[str, object]]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        query = (
            select(
                _runs.c.run_id,
                _runs.c.created_at,
                _runs.c.code_revision,
                _runs.c.snapshot_id,
                _runs.c.snapshot_hash,
                _runs.c.config,
                _runs.c.result["summary"].label("summary"),
                _runs.c.result["market"].label("market"),
            )
            .order_by(_runs.c.created_at.desc(), _runs.c.run_id)
            .limit(limit)
        )
        with self._engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        return [
            {
                "run_id": str(row["run_id"]),
                "created_at": _timestamp(cast(datetime, row["created_at"])),
                "committed_code": not str(row["code_revision"]).endswith("-dirty"),
                "code_revision": str(row["code_revision"]),
                "snapshot": {
                    "id": str(row["snapshot_id"]),
                    "content_hash": str(row["snapshot_hash"]),
                },
                "config": row["config"],
                "summary": row["summary"],
                "market": row["market"],
            }
            for row in rows
        ]


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

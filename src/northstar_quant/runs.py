"""Persist complete, reproducible research runs in the application database."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import Column, Connection, DateTime, Engine, MetaData, String, Table, func, select
from sqlalchemy.dialects.postgresql import JSONB, UUID, insert

from northstar_quant.data.research import ResearchDataset
from northstar_quant.research import ResearchConfig, ResearchResult
from northstar_quant.runtime import implementation_hash

_metadata = MetaData()
_runs = Table(
    "research_runs",
    _metadata,
    Column("run_id", String(64), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("implementation_hash", String(64), nullable=False),
    Column("snapshot_id", UUID(as_uuid=True), nullable=False),
    Column("snapshot_hash", String(64), nullable=False),
    Column("config", JSONB, nullable=False),
    Column("result", JSONB, nullable=False),
)


def initialize_run_store(engine: Engine | Connection) -> None:
    """Create the current result table during explicit database initialization."""

    if engine.dialect.name != "postgresql":
        raise ValueError("research results require PostgreSQL")
    _metadata.create_all(engine)


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

    def save(self, dataset: ResearchDataset, config: ResearchConfig, result: ResearchResult) -> str:
        configuration = config.to_dict()
        complete_result = result.to_dict()
        snapshot = {"id": str(dataset.snapshot_id), "content_hash": dataset.content_hash}
        if complete_result["snapshot"] != snapshot or complete_result["config"] != configuration:
            raise ValueError("the result does not belong to the supplied data and configuration")
        evidence = None if dataset.details is None else dataset.details.to_dict()
        if complete_result["data"] != evidence:
            raise ValueError("the result does not contain the supplied snapshot's source evidence")
        implementation = implementation_hash()
        payload = {
            "implementation_hash": implementation,
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
            connection.execute(
                insert(_runs)
                .values(
                    run_id=run_id,
                    implementation_hash=implementation,
                    snapshot_id=dataset.snapshot_id,
                    snapshot_hash=dataset.content_hash,
                    config=configuration,
                    result=stored_result,
                )
                .on_conflict_do_nothing(index_elements=[_runs.c.run_id])
            )
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
            "implementation_hash": str(row["implementation_hash"]),
            "snapshot": snapshot,
            "config": config,
            "result": result,
        }
        if hashlib.sha256(_canonical(identity)).hexdigest() != run_id:
            raise ValueError("stored research content no longer matches its immutable identity")
        return {
            "run_id": str(row["run_id"]),
            "created_at": _timestamp(cast(datetime, row["created_at"])),
            "implementation_hash": str(row["implementation_hash"]),
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
                _runs.c.implementation_hash,
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
                "implementation_hash": str(row["implementation_hash"]),
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

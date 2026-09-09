"""Fixed factor revisions, audited descriptions and bounded calculation attempts."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

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
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from northstar_quant import code_revision
from northstar_quant.data_management.publications import DatasetReader
from northstar_quant.factors.definition import Bar, Inputs, content_id
from northstar_quant.factors.evaluation import Binding, evaluate

_metadata = MetaData()
_revisions = Table(
    "factor_revisions",
    _metadata,
    Column("revision_id", String(64), primary_key=True),
    Column("binding", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)
_notes = Table(
    "factor_annotations",
    _metadata,
    Column("annotation_id", PGUUID(as_uuid=True), primary_key=True),
    Column("revision_id", String(64), nullable=False),
    Column("description", String(2000), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)
_runs = Table(
    "factor_runs",
    _metadata,
    Column("attempt_id", PGUUID(as_uuid=True), primary_key=True),
    Column("revision_id", String(64), nullable=False),
    Column("snapshot_id", PGUUID(as_uuid=True), nullable=False),
    Column("input_hash", String(64), nullable=False),
    Column("code_revision", String(64), nullable=False),
    Column("status", String(20), nullable=False),
    Column("result", JSONB),
    Column("result_hash", String(64)),
    Column("error", String(1000)),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
)


def initialize_factor_catalog(connection: Connection) -> None:
    _metadata.create_all(connection)
    connection.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION factor_immutable() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'factor records are immutable'; END; $$ LANGUAGE plpgsql;
        CREATE OR REPLACE FUNCTION factor_attempt_transition() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' OR OLD.status <> 'RUNNING'
                 OR NEW.status NOT IN ('SUCCEEDED','FAILED','ABANDONED')
             OR ROW(NEW.attempt_id, NEW.revision_id, NEW.snapshot_id,
                    NEW.input_hash,
                    NEW.code_revision, NEW.created_at)
                IS DISTINCT FROM ROW(OLD.attempt_id, OLD.revision_id, OLD.snapshot_id,
                    OLD.input_hash,
                    OLD.code_revision, OLD.created_at)
          THEN RAISE EXCEPTION 'factor attempt identity and terminal facts are immutable';
              END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    for table in ("factor_revisions", "factor_annotations"):
        connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS immutable ON {table}")
        connection.exec_driver_sql(
            f"CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION factor_immutable()"
        )
    connection.exec_driver_sql("DROP TRIGGER IF EXISTS immutable ON factor_runs")
    connection.exec_driver_sql(
        "CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON factor_runs "
        "FOR EACH ROW EXECUTE FUNCTION factor_attempt_transition()"
    )


def register_binding(connection: Connection, binding: Binding) -> str:
    connection.execute(
        insert(_revisions)
        .values(revision_id=binding.identity, binding=binding.to_dict())
        .on_conflict_do_nothing()
    )
    return binding.identity


class FactorCatalog:
    def __init__(self, engine: Engine, library: DatasetReader) -> None:
        self._engine, self._library = engine, library

    def register(self, binding: Binding) -> str:
        with self._engine.begin() as connection:
            return register_binding(connection, binding)

    def revision(self, revision_id: str) -> dict[str, Any]:
        with self._engine.connect() as connection:
            document = connection.scalar(
                select(_revisions.c.binding).where(_revisions.c.revision_id == revision_id)
            )
        if document is None:
            raise LookupError("factor revision not found")
        if content_id(document) != revision_id:
            raise ValueError("factor revision integrity failure")
        return dict(document)

    def binding(self, revision_id: str) -> Binding:
        return Binding.from_dict(self.revision(revision_id))

    def annotate(self, revision_id: str, description: str) -> None:
        self.revision(revision_id)
        if not isinstance(description, str) or not 1 <= len(description.strip()) <= 2000:
            raise ValueError("description must contain 1 to 2000 characters")
        with self._engine.begin() as connection:
            connection.execute(
                insert(_notes).values(
                    annotation_id=uuid4(), revision_id=revision_id, description=description.strip()
                )
            )

    def revisions(self) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            revisions = (
                connection.execute(
                    select(_revisions).order_by(_revisions.c.created_at.desc()).limit(200)
                )
                .mappings()
                .all()
            )
            annotations = (
                connection.execute(select(_notes).order_by(_notes.c.created_at)).mappings().all()
            )
        return [
            {
                "revision_id": row["revision_id"],
                "binding": self.revision(row["revision_id"]),
                "annotations": [
                    {"description": item["description"], "at": item["created_at"].isoformat()}
                    for item in annotations
                    if item["revision_id"] == row["revision_id"]
                ],
            }
            for row in revisions
        ]

    def calculate(self, revision_id: str, snapshot_id: UUID) -> dict[str, Any]:
        binding = self.binding(revision_id)
        dataset = self._library.load_dataset(snapshot_id)
        if not 1 <= len(dataset.bars) <= 10000:
            raise ValueError("factor calculation requires 1 to 10000 accepted bars")
        material = {
            "binding": binding.to_dict(),
            "snapshot_id": str(snapshot_id),
            "snapshot_hash": dataset.content_hash,
            "contract_id": str(dataset.market.contract_id),
            "interval_seconds": dataset.market.interval_seconds,
            "price_basis": "REAL_CONTRACT",
            "availability": "COMPLETED_AND_AVAILABLE_PREFIX",
            "numeric": "DECIMAL_96_HALF_EVEN",
            "initial_state": "EMPTY",
        }
        identity = content_id(material)
        implementation = code_revision()
        if not implementation.endswith("-dirty") and binding.code_revision == implementation:
            with self._engine.connect() as connection:
                prior = connection.scalar(
                    select(_runs.c.attempt_id)
                    .where(
                        _runs.c.input_hash == identity,
                        _runs.c.code_revision == implementation,
                        _runs.c.status == "SUCCEEDED",
                    )
                    .limit(1)
                )
            if prior is not None:
                return self.get(prior)
        attempt = uuid4()
        with self._engine.begin() as connection:
            connection.execute(
                insert(_runs).values(
                    attempt_id=attempt,
                    revision_id=revision_id,
                    snapshot_id=snapshot_id,
                    input_hash=identity,
                    code_revision=implementation,
                    status="RUNNING",
                )
            )
        try:
            bars = sorted(
                dataset.bars,
                key=lambda item: (item.available_at, item.completed_at, str(item.observation_id)),
            )
            history: list[Bar] = []
            results = []
            for bar in bars:
                history.append(
                    Bar(
                        bar.observation_id,
                        dataset.market.contract_id,
                        bar.completed_at,
                        bar.available_at,
                        bar.close,
                    )
                )
                history = history[-binding.history_bars :]
                value = evaluate(
                    binding,
                    Inputs(
                        tuple(history),
                        bar.available_at,
                        dataset.market.contract_id,
                        dataset.market.interval_seconds,
                        source_scope=dataset.content_hash,
                    ),
                )
                results.append(
                    {
                        "observation_id": str(bar.observation_id),
                        "at": bar.available_at.isoformat(),
                        **value.to_dict(),
                    }
                )
            counts = dict(Counter(str(item["status"]) for item in results))
            document = {
                "inputs": material,
                "values": results,
                "evaluation": {
                    "plan": "BOUNDED_AVAILABILITY_COVERAGE_V1",
                    "counts": counts,
                    "sample_count": len(results),
                    "limitations": [
                        "可用性与覆盖率检查，不是收益评价、样本外验证或交易许可。",
                        "固定快照及空预热；历史可得时间依据仍以来源证据为准。",
                    ],
                },
            }
            with self._engine.begin() as connection:
                connection.execute(
                    update(_runs)
                    .where(_runs.c.attempt_id == attempt, _runs.c.status == "RUNNING")
                    .values(
                        status="SUCCEEDED",
                        result=document,
                        result_hash=content_id(document),
                        completed_at=datetime.now(UTC),
                    )
                )
        except Exception as error:
            with self._engine.begin() as connection:
                connection.execute(
                    update(_runs)
                    .where(_runs.c.attempt_id == attempt, _runs.c.status == "RUNNING")
                    .values(
                        status="FAILED", error=str(error)[:1000], completed_at=datetime.now(UTC)
                    )
                )
        import os

        from .artifacts import ResearchArtifacts

        saved = self.get(attempt)
        if saved["status"] == "SUCCEEDED" and os.environ.get("NORTHSTAR_RESEARCH_DIR"):
            ResearchArtifacts.from_environment().save("FACTOR", str(attempt), saved)
        return saved

    def get(self, attempt_id: UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(_runs).where(_runs.c.attempt_id == attempt_id))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError("factor calculation not found")
        binding = self.revision(row["revision_id"])
        if row["status"] == "SUCCEEDED" and (
            content_id(row["result"]) != row["result_hash"]
            or content_id(row["result"]["inputs"]) != row["input_hash"]
            or row["result"]["inputs"]["binding"] != binding
            or row["result"]["inputs"]["snapshot_id"] != str(row["snapshot_id"])
        ):
            raise ValueError("factor result integrity failure")
        return {
            key: str(value)
            if isinstance(value, UUID)
            else value.isoformat()
            if isinstance(value, datetime)
            else value
            for key, value in row.items()
        }

    def runs(self) -> list[dict[str, Any]]:
        with self._engine.connect() as connection:
            identifiers = connection.scalars(
                select(_runs.c.attempt_id).order_by(_runs.c.created_at.desc()).limit(200)
            ).all()
        return [self.get(identifier) for identifier in identifiers]

    def abandon(self, attempt_id: UUID) -> dict[str, Any]:
        with self._engine.begin() as connection:
            connection.execute(
                update(_runs)
                .where(_runs.c.attempt_id == attempt_id, _runs.c.status == "RUNNING")
                .values(
                    status="ABANDONED",
                    error="Explicitly abandoned; no automatic retry",
                    completed_at=datetime.now(UTC),
                )
            )
        return self.get(attempt_id)

    def verify_all(self) -> None:
        with self._engine.connect() as connection:
            revisions = connection.scalars(select(_revisions.c.revision_id)).all()
            runs = connection.scalars(select(_runs.c.attempt_id)).all()
        for revision in revisions:
            self.revision(revision)
        for identifier in runs:
            saved = self.get(identifier)
            if saved["status"] == "SUCCEEDED":
                dataset = self._library.load_dataset(UUID(saved["snapshot_id"]))
                if dataset.content_hash != saved["result"]["inputs"]["snapshot_hash"]:
                    raise ValueError("factor calculation snapshot reference mismatch")

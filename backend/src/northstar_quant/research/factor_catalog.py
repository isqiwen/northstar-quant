"""Fixed factor revisions, audited descriptions and bounded calculation attempts."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from threading import Event
from time import monotonic
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Column,
    Connection,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    func,
    select,
    update,
)
from sqlalchemy import Uuid as PGUUID
from sqlalchemy.dialects.postgresql import JSONB, insert

from northstar_quant import code_revision
from northstar_quant.data_management.publications import DatasetReader
from northstar_quant.factors.definition import Bar, Inputs, content_id
from northstar_quant.factors.evaluation import Binding, evaluate
from northstar_quant.persistence.sql import UTCDateTime, write_transaction

from .factor_analysis import analyze

_metadata = MetaData()
_revisions = Table(
    "factor_revisions",
    _metadata,
    Column("revision_id", String(64), primary_key=True),
    Column("binding", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("created_at", UTCDateTime(), server_default=func.now(), nullable=False),
)
_notes = Table(
    "factor_annotations",
    _metadata,
    Column("annotation_id", PGUUID(as_uuid=True), primary_key=True),
    Column("revision_id", String(64), nullable=False),
    Column("description", String(2000), nullable=False),
    Column("created_at", UTCDateTime(), server_default=func.now(), nullable=False),
)
_runs = Table(
    "factor_runs",
    _metadata,
    Column("attempt_id", PGUUID(as_uuid=True), primary_key=True),
    Column("revision_id", String(64), nullable=False),
    Column("snapshot_id", PGUUID(as_uuid=True), nullable=False),
    Column("input_hash", String(64), nullable=False),
    Column("inputs", JSON().with_variant(JSONB(), "postgresql"), nullable=False),
    Column("total", Integer, nullable=False),
    Column("done", Integer, nullable=False, default=0),
    Column("code_revision", String(64), nullable=False),
    Column("status", String(20), nullable=False),
    Column("result", JSON().with_variant(JSONB(), "postgresql")),
    Column("result_hash", String(64)),
    Column("error", String(1000)),
    Column("created_at", UTCDateTime(), server_default=func.now(), nullable=False),
    Column("completed_at", UTCDateTime()),
)


def initialize_factor_catalog(connection: Connection) -> None:
    _metadata.create_all(connection)
    fixed = (
        "attempt_id",
        "revision_id",
        "snapshot_id",
        "input_hash",
        "inputs",
        "total",
        "code_revision",
        "created_at",
    )
    invalid = """NOT (
        (OLD.status='QUEUED' AND NEW.status IN ('RUNNING','CANCELED')) OR
        (OLD.status='RUNNING' AND NEW.status IN
          ('RUNNING','SUCCEEDED','FAILED','INTERRUPTED','CANCEL_REQUESTED')) OR
        (OLD.status='CANCEL_REQUESTED' AND NEW.status IN ('CANCELED','INTERRUPTED')))
        OR NEW.done < OLD.done OR NEW.done > NEW.total"""
    if connection.dialect.name == "sqlite":
        from .storage import immutable

        for table in ("factor_revisions", "factor_annotations"):
            immutable(connection, table)
        changed = " OR ".join(f"OLD.{name} IS NOT NEW.{name}" for name in fixed)
        connection.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS factor_runs_retained BEFORE DELETE ON factor_runs "
            "BEGIN SELECT RAISE(ABORT, 'factor attempts are immutable'); END"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER IF NOT EXISTS factor_runs_transition BEFORE UPDATE ON factor_runs "
            f"WHEN {invalid} OR {changed} "
            "BEGIN SELECT RAISE(ABORT, 'factor attempts are immutable'); END"
        )
        return
    before = ",".join("OLD." + name for name in fixed)
    after = ",".join("NEW." + name for name in fixed)
    connection.exec_driver_sql(f"""
        CREATE OR REPLACE FUNCTION factor_immutable() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'factor records are immutable'; END; $$ LANGUAGE plpgsql;
        CREATE OR REPLACE FUNCTION factor_attempt_transition() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' OR {invalid}
             OR ROW({before}) IS DISTINCT FROM ROW({after})
          THEN RAISE EXCEPTION 'factor attempt facts are immutable'; END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    for table in ("factor_revisions", "factor_annotations", "factor_runs"):
        function = "factor_attempt_transition" if table == "factor_runs" else "factor_immutable"
        connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS immutable ON {table}")
        connection.exec_driver_sql(
            f"CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {function}()"
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
        with write_transaction(self._engine) as connection:
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
        with write_transaction(self._engine) as connection:
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

    def submit(self, revision_id: str, snapshot_id: UUID, request_id: UUID) -> dict[str, Any]:
        binding = self.binding(revision_id)
        details = self._library.describe_dataset(snapshot_id)
        total = details.summary.bar_count
        if not 1 <= total <= 10000:
            raise ValueError("factor calculation requires 1 to 10000 accepted bars")
        material = {
            "binding": binding.to_dict(),
            "snapshot_id": str(snapshot_id),
            "snapshot_hash": details.summary.content_hash,
            "snapshot_evidence": details.to_dict(),
            "price_basis": "REAL_CONTRACT",
            "availability": "COMPLETED_AND_AVAILABLE_PREFIX",
            "numeric": "DECIMAL_96_HALF_EVEN",
            "initial_state": "EMPTY",
            "analysis": "SINGLE_CONTRACT_TIME_SERIES_FORWARD_RETURNS_V1",
        }
        identity = content_id(material)
        with write_transaction(self._engine) as connection:
            prior = (
                connection.execute(select(_runs).where(_runs.c.attempt_id == request_id))
                .mappings()
                .one_or_none()
            )
            if prior is not None:
                if prior["input_hash"] != identity:
                    raise ValueError("factor request identity is already bound to other inputs")
            else:
                connection.execute(
                    insert(_runs).values(
                        attempt_id=request_id,
                        revision_id=revision_id,
                        snapshot_id=snapshot_id,
                        input_hash=identity,
                        inputs=material,
                        total=total,
                        done=0,
                        code_revision=code_revision(),
                        status="QUEUED",
                    )
                )
        return self.get(request_id)

    def queued(self) -> dict[str, Any] | None:
        with self._engine.connect() as connection:
            identity = connection.scalar(
                select(_runs.c.attempt_id)
                .where(_runs.c.status == "QUEUED")
                .order_by(_runs.c.created_at)
                .limit(1)
            )
        return self.get(identity) if identity is not None else None

    def claim(self, attempt: UUID) -> bool:
        with write_transaction(self._engine) as connection:
            return (
                connection.execute(
                    update(_runs)
                    .where(_runs.c.attempt_id == attempt, _runs.c.status == "QUEUED")
                    .values(status="RUNNING")
                    .returning(_runs.c.attempt_id)
                ).scalar_one_or_none()
                is not None
            )

    def interrupt(self, attempt: UUID | None = None) -> None:
        # Only the exclusive supervisor can declare a dead process interrupted.
        with write_transaction(self._engine) as connection:
            statement = update(_runs).where(_runs.c.status.in_(("RUNNING", "CANCEL_REQUESTED")))
            if attempt is not None:
                statement = statement.where(_runs.c.attempt_id == attempt)
            connection.execute(
                statement.values(
                    status="INTERRUPTED",
                    error="计算进程已退出；需要明确提交新任务",
                    completed_at=datetime.now(UTC),
                )
            )

    def cancel(self, attempt: UUID) -> dict[str, Any]:
        with write_transaction(self._engine) as connection:
            for old, new in (("QUEUED", "CANCELED"), ("RUNNING", "CANCEL_REQUESTED")):
                connection.execute(
                    update(_runs)
                    .where(_runs.c.attempt_id == attempt, _runs.c.status == old)
                    .values(
                        status=new,
                        **({"completed_at": datetime.now(UTC)} if new == "CANCELED" else {}),
                    )
                )
        return self.get(attempt)

    def execute(self, attempt: UUID, *, stop: Event | None = None) -> dict[str, Any]:
        saved = self.get(attempt)
        if saved["status"] not in {"RUNNING", "CANCEL_REQUESTED"}:
            raise ValueError("factor attempt must be claimed by the worker")
        material = saved["inputs"]
        last = 0.0

        def progress(done: int, *, force: bool = False) -> None:
            nonlocal last
            if stop is not None and stop.is_set():
                raise InterruptedError("研究进程停止")
            if force or monotonic() - last >= 0.2:
                with write_transaction(self._engine) as connection:
                    status = connection.scalar(
                        select(_runs.c.status).where(_runs.c.attempt_id == attempt)
                    )
                    if status != "RUNNING":
                        raise InterruptedError("因子任务已取消或失去执行权")
                    connection.execute(
                        update(_runs).where(_runs.c.attempt_id == attempt).values(done=done)
                    )
                last = monotonic()

        try:
            progress(0, force=True)
            if saved["code_revision"] != code_revision():
                raise ValueError("实现版本不匹配，请创建新任务")
            binding = self.binding(saved["revision_id"])
            dataset = self._library.load_dataset(UUID(saved["snapshot_id"]))
            if (
                dataset.details is None
                or dataset.details.to_dict() != material["snapshot_evidence"]
                or dataset.content_hash != material["snapshot_hash"]
                or len(dataset.bars) != saved["total"]
            ):
                raise ValueError("固定因子输入身份不匹配")
            bars = sorted(
                dataset.bars,
                key=lambda item: (item.available_at, item.completed_at, str(item.observation_id)),
            )
            history: list[Bar] = []
            results = []
            for index, bar in enumerate(bars):
                progress(index)
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
                "analysis": analyze(dataset, results),
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
            progress(len(bars), force=True)
            with write_transaction(self._engine) as connection:
                status = connection.scalar(
                    select(_runs.c.status).where(_runs.c.attempt_id == attempt)
                )
                if status != "RUNNING":
                    raise InterruptedError("因子任务已取消或失去执行权")
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
            with write_transaction(self._engine) as connection:
                status = connection.scalar(
                    select(_runs.c.status).where(_runs.c.attempt_id == attempt)
                )
                terminal = (
                    "CANCELED"
                    if status == "CANCEL_REQUESTED"
                    else "INTERRUPTED"
                    if isinstance(error, InterruptedError)
                    else "FAILED"
                )
                connection.execute(
                    update(_runs)
                    .where(
                        _runs.c.attempt_id == attempt,
                        _runs.c.status.in_(("RUNNING", "CANCEL_REQUESTED")),
                    )
                    .values(
                        status=terminal,
                        error=str(error)[:1000],
                        completed_at=datetime.now(UTC),
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
        if content_id(row["inputs"]) != row["input_hash"]:
            raise ValueError("factor input integrity failure")
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

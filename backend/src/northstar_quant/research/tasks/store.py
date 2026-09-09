"""Durable fixed-input jobs; attempts are fenced by explicit execution identities."""

from __future__ import annotations

from datetime import UTC, datetime
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
    insert,
    select,
    update,
)

from northstar_quant import code_revision
from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.research.storage import UTCDateTime

metadata = MetaData()
jobs = Table(
    "research_jobs",
    metadata,
    Column("task_id", String(36), primary_key=True),
    Column("snapshot_id", String(36), nullable=False),
    Column("snapshot_hash", String(64), nullable=False),
    Column("snapshot_evidence", JSON, nullable=False),
    Column("config", JSON, nullable=False),
    Column("code_revision", String(64), nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
    Column("status", String(24), nullable=False),
    Column("attempt_id", String(36)),
    Column("completed", Integer, nullable=False),
    Column("total", Integer, nullable=False),
    Column("run_id", String(64)),
    Column("reason", String(1000), nullable=False),
)
attempts = Table(
    "research_job_attempts",
    metadata,
    Column("attempt_id", String(36), primary_key=True),
    Column("task_id", String(36), nullable=False, index=True),
    Column("status", String(24), nullable=False),
    Column("started_at", UTCDateTime(), nullable=False),
    Column("finished_at", UTCDateTime()),
    Column("resources", JSON, nullable=False),
    Column("reason", String(1000), nullable=False),
)


def initialize(connection: Engine | Connection) -> None:
    metadata.create_all(connection)

    def guards(c: Connection) -> None:
        fixed = (
            "task_id",
            "snapshot_id",
            "snapshot_hash",
            "snapshot_evidence",
            "config",
            "code_revision",
            "created_at",
            "total",
        )
        if c.dialect.name == "sqlite":
            changed = " OR ".join(f"OLD.{k} IS NOT NEW.{k}" for k in fixed)
            c.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS research_jobs_fixed BEFORE UPDATE ON research_jobs "
                f"WHEN {changed} BEGIN SELECT RAISE(ABORT, 'task inputs are immutable'); END"
            )
            c.exec_driver_sql(
                "CREATE TRIGGER IF NOT EXISTS research_jobs_retained "
                "BEFORE DELETE ON research_jobs "
                "BEGIN SELECT RAISE(ABORT, 'task evidence is retained'); END"
            )
        else:
            before, after = ",".join("OLD." + k for k in fixed), ",".join("NEW." + k for k in fixed)
            # JSON is compared as canonical text for fixed inputs; updates never rewrite it.
            before, after = (
                before.replace("OLD.config", "OLD.config::jsonb").replace(
                    "OLD.snapshot_evidence", "OLD.snapshot_evidence::jsonb"
                ),
                after.replace("NEW.config", "NEW.config::jsonb").replace(
                    "NEW.snapshot_evidence", "NEW.snapshot_evidence::jsonb"
                ),
            )
            c.exec_driver_sql(
                f"CREATE OR REPLACE FUNCTION research_jobs_fixed() RETURNS trigger AS $$ "
                f"BEGIN IF TG_OP='DELETE' OR ROW({before}) IS DISTINCT FROM ROW({after}) "
                "THEN RAISE EXCEPTION 'task inputs are immutable'; "
                "END IF; RETURN NEW; END; $$ LANGUAGE plpgsql"
            )
            c.exec_driver_sql("DROP TRIGGER IF EXISTS research_jobs_fixed ON research_jobs")
            c.exec_driver_sql(
                "CREATE TRIGGER research_jobs_fixed BEFORE UPDATE OR DELETE ON research_jobs "
                "FOR EACH ROW EXECUTE FUNCTION research_jobs_fixed()"
            )

    if isinstance(connection, Connection):
        guards(connection)
    else:
        with connection.begin() as c:
            guards(c)


class TaskStore:
    def __init__(self, engine: Engine):
        self.engine = engine

    def submit(
        self,
        identity: UUID,
        snapshot: UUID,
        digest: str,
        config: ResearchConfig,
        total: int,
        evidence: dict[str, object],
    ) -> dict[str, Any]:
        import re

        if (
            not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not config.strategy.history_bars <= total <= 100000
        ):
            raise ValueError("task requires a bounded usable fixed snapshot")
        frozen = dict(
            snapshot_id=str(snapshot),
            snapshot_hash=digest,
            snapshot_evidence=evidence,
            config=config.to_dict(),
            code_revision=code_revision(),
        )
        with self.engine.begin() as c:
            old = c.execute(select(jobs).where(jobs.c.task_id == str(identity))).mappings().first()
            if old is not None:
                if any(old[k] != v for k, v in frozen.items()):
                    raise ValueError("task identity was reused with different fixed inputs")
            else:
                c.execute(
                    insert(jobs).values(
                        task_id=str(identity),
                        **frozen,
                        created_at=datetime.now(UTC),
                        status="QUEUED",
                        completed=0,
                        total=total,
                        reason="等待独立研究 worker",
                    )
                )
        from northstar_quant.research.artifacts import publish_usage

        publish_usage(self.engine, snapshot)
        return self.get(str(identity))

    def get(self, identity: str) -> dict[str, Any]:
        with self.engine.connect() as c:
            row = c.execute(select(jobs).where(jobs.c.task_id == identity)).mappings().first()
            if row is None:
                raise LookupError("research task not found")
            result = dict(row)
            result["attempts"] = [
                dict(r)
                for r in c.execute(
                    select(attempts)
                    .where(attempts.c.task_id == identity)
                    .order_by(attempts.c.started_at)
                ).mappings()
            ]
        for item in [result, *result["attempts"]]:
            for k, v in item.items():
                if isinstance(v, datetime):
                    item[k] = v.isoformat()
        return result

    def list(self) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            ids = (
                c.execute(select(jobs.c.task_id).order_by(jobs.c.created_at.desc()).limit(200))
                .scalars()
                .all()
            )
        return [self.get(i) for i in ids]

    def queued(self) -> dict[str, Any] | None:
        with self.engine.connect() as c:
            identity = c.execute(
                select(jobs.c.task_id)
                .where(jobs.c.status == "QUEUED")
                .order_by(jobs.c.created_at)
                .limit(1)
            ).scalar()
        return None if identity is None else self.get(identity)

    def waiting(self, identity: str, reason: str) -> None:
        with self.engine.begin() as c:
            c.execute(
                update(jobs)
                .where(jobs.c.task_id == identity, jobs.c.status == "QUEUED")
                .values(reason=reason)
            )

    def claim(self) -> dict[str, Any] | None:
        with self.engine.begin() as c:
            row = (
                c.execute(
                    select(jobs)
                    .where(jobs.c.status == "QUEUED")
                    .order_by(jobs.c.created_at)
                    .limit(1)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            identity, attempt = row["task_id"], str(uuid4())
            c.execute(
                update(jobs)
                .where(jobs.c.task_id == identity, jobs.c.status == "QUEUED")
                .values(status="RUNNING", attempt_id=attempt, completed=0, reason="核验固定输入")
            )
            c.execute(
                insert(attempts).values(
                    attempt_id=attempt,
                    task_id=identity,
                    status="RUNNING",
                    started_at=datetime.now(UTC),
                    resources={},
                    reason="",
                )
            )
        return self.get(identity)

    def progress(self, identity: str, attempt: str, completed: int) -> None:
        with self.engine.begin() as c:
            count = c.execute(
                update(jobs)
                .where(
                    jobs.c.task_id == identity,
                    jobs.c.attempt_id == attempt,
                    jobs.c.status == "RUNNING",
                    jobs.c.completed <= completed,
                    jobs.c.total >= completed,
                )
                .values(completed=completed, reason="计算中")
            ).rowcount
            if count != 1:
                raise InterruptedError("任务取消或执行权已失效")

    def measure(self, identity: str, resources: dict[str, int | str]) -> None:
        with self.engine.begin() as c:
            c.execute(
                update(attempts)
                .where(attempts.c.attempt_id == identity, attempts.c.status == "RUNNING")
                .values(resources=resources)
            )

    def memory_budget(self, total: int) -> int:
        with self.engine.connect() as c:
            measurements = c.execute(
                select(attempts.c.resources, jobs.c.total)
                .join(jobs, jobs.c.task_id == attempts.c.task_id)
                .where(attempts.c.status == "SUCCEEDED")
                .order_by(attempts.c.started_at.desc())
                .limit(50)
            ).all()
        per_bar = max(
            [
                12000,
                *[
                    max(0, int(r.get("peak_rss_bytes", 0)) - 256 * 1024**2) // max(1, int(n))
                    for r, n in measurements
                ],
            ]
        )
        return int(256 * 1024**2 + total * per_bar)

    def finalizing(self, identity: str, attempt: str) -> None:
        with self.engine.begin() as c:
            count = c.execute(
                update(jobs)
                .where(
                    jobs.c.task_id == identity,
                    jobs.c.attempt_id == attempt,
                    jobs.c.status == "RUNNING",
                )
                .values(status="FINALIZING", reason="保存固定结果，取消窗口已结束")
            ).rowcount
            if count != 1:
                raise InterruptedError("任务取消或执行权失效")

    def finish(
        self, identity: str, attempt: str, status: str, reason: str = "", run_id: str | None = None
    ) -> None:
        if status not in {"SUCCEEDED", "FAILED", "CANCELLED", "INTERRUPTED"}:
            raise ValueError("invalid terminal status")
        with self.engine.begin() as c:
            row = (
                c.execute(select(jobs).where(jobs.c.task_id == identity).with_for_update())
                .mappings()
                .one()
            )
            if row["attempt_id"] != attempt or row["status"] not in {
                "RUNNING",
                "CANCEL_REQUESTED",
                "FINALIZING",
            }:
                return
            if row["status"] == "CANCEL_REQUESTED":
                status, run_id, reason = "CANCELLED", None, "取消已确认"
            c.execute(
                update(jobs)
                .where(jobs.c.task_id == identity)
                .values(status=status, reason=reason[:1000], run_id=run_id)
            )
            c.execute(
                update(attempts)
                .where(attempts.c.attempt_id == attempt)
                .values(status=status, reason=reason[:1000], finished_at=datetime.now(UTC))
            )

    def control(self, identity: str, action: str) -> dict[str, Any]:
        with self.engine.begin() as c:
            row = (
                c.execute(select(jobs).where(jobs.c.task_id == identity).with_for_update())
                .mappings()
                .first()
            )
            if row is None:
                raise LookupError("research task not found")
            if action == "cancel":
                if row["status"] == "QUEUED":
                    state = "CANCELLED"
                elif row["status"] == "RUNNING":
                    state = "CANCEL_REQUESTED"
                elif row["status"] == "FINALIZING":
                    raise ValueError("结果正在保存，取消窗口已结束")
                else:
                    state = row["status"]
                c.execute(
                    update(jobs)
                    .where(jobs.c.task_id == identity)
                    .values(
                        status=state,
                        reason="用户请求取消"
                        if state in {"CANCELLED", "CANCEL_REQUESTED"}
                        else row["reason"],
                    )
                )
            elif action == "retry":
                if row["status"] not in {"FAILED", "INTERRUPTED"}:
                    raise ValueError("only failed or interrupted tasks can retry")
                if row["code_revision"] != code_revision():
                    raise ValueError("代码已变更，请创建新任务")
                c.execute(
                    update(jobs)
                    .where(jobs.c.task_id == identity)
                    .values(status="QUEUED", reason="等待重试", completed=0)
                )
            else:
                raise ValueError("unknown task action")
        return self.get(identity)

    def recover(self) -> None:
        """Only the supervisor holding the lifetime process lock may call this."""
        with self.engine.connect() as c:
            ids = (
                c.execute(
                    select(jobs.c.task_id).where(
                        jobs.c.status.in_(["RUNNING", "CANCEL_REQUESTED", "FINALIZING"])
                    )
                )
                .scalars()
                .all()
            )
        for identity in ids:
            task = self.get(identity)
            if task["status"] in {"RUNNING", "CANCEL_REQUESTED", "FINALIZING"}:
                self.finish(
                    task["task_id"],
                    task["attempt_id"],
                    "INTERRUPTED",
                    "执行进程已结束，可显式重试固定输入",
                )

"""Fixed finite parameter studies composed from the existing durable backtest jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import (
    JSON,
    Column,
    Connection,
    Engine,
    MetaData,
    String,
    Table,
    func,
    insert,
    or_,
    select,
)

from northstar_quant import code_revision
from northstar_quant.data_management.publications import DatasetReader
from northstar_quant.data_management.research import DatasetDetails
from northstar_quant.factors.definition import content_id
from northstar_quant.persistence.sql import UTCDateTime, write_transaction

from .configuration import ResearchConfig
from .learning import LearningRecipe, fit
from .runs import RunStore
from .tasks.store import TaskStore, jobs

_metadata = MetaData()
_plans = Table(
    "research_experiments",
    _metadata,
    Column("experiment_id", String(36), primary_key=True),
    Column("plan_id", String(64), nullable=False),
    Column("plan", JSON, nullable=False),
    Column("created_at", UTCDateTime(), nullable=False),
)
_selections = Table(
    "research_experiment_selections",
    _metadata,
    Column("experiment_id", String(36), primary_key=True),
    Column("decision", JSON, nullable=False),
    Column("decision_id", String(64), nullable=False),
)
_fits = Table(
    "research_experiment_fits",
    _metadata,
    Column("experiment_id", String(36), primary_key=True),
    Column("document", JSON, nullable=False),
    Column("fit_id", String(64), nullable=False),
)
_PHASES = ("train", "validation", "test")
_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}


def initialize(connection: Connection) -> None:
    from .storage import immutable

    _metadata.create_all(connection)
    for table in (_plans, _selections, _fits):
        if connection.dialect.name == "sqlite":
            immutable(connection, table.name)
        else:
            connection.exec_driver_sql("""
                CREATE OR REPLACE FUNCTION experiment_immutable() RETURNS trigger AS $$
                BEGIN RAISE EXCEPTION 'experiment facts are immutable'; END;
                $$ LANGUAGE plpgsql
            """)
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS immutable ON {table.name}")
            connection.exec_driver_sql(
                f"CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON {table.name} "
                "FOR EACH ROW EXECUTE FUNCTION experiment_immutable()"
            )


def task_id(identity: str, candidate: str, phase: str) -> str:
    return str(uuid5(UUID(identity), f"{candidate}/{phase}"))


class Experiments:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.tasks = TaskStore(engine)

    def submit(
        self,
        identity: UUID,
        hypothesis: str,
        snapshots: tuple[UUID, UUID, UUID],
        configurations: list[ResearchConfig],
        library: DatasetReader,
        learning: LearningRecipe | None = None,
    ) -> dict[str, Any]:
        valid_count = len(configurations) == 1 if learning else 2 <= len(configurations) <= 64
        if not 1 <= len(hypothesis.strip()) <= 1000 or not valid_count:
            raise ValueError("experiment requires a hypothesis and 2 to 64 fixed candidates")
        candidates = {content_id(c.to_dict()): c.to_dict() for c in configurations}
        if len(candidates) != len(configurations):
            raise ValueError("experiment candidates must be distinct")
        first = configurations[0]
        if any(c.risk != first.risk or c.simulation != first.simulation for c in configurations):
            raise ValueError("candidate comparison requires identical account, risk and costs")
        details = [library.describe_dataset(s) for s in snapshots]
        if len(set(snapshots)) != 3:
            raise ValueError("train, validation and test require distinct fixed snapshots")
        for previous, current in zip(details, details[1:]):
            if (
                max(previous.summary.session_close, previous.available_at_cutoff)
                > current.summary.session_open
            ):
                raise ValueError("train, validation and test windows must be ordered and disjoint")

        def economics(d: DatasetDetails) -> tuple[object, ...]:
            spec = d.import_specs[0]
            return (
                d.summary.exchange,
                d.summary.symbol,
                d.volume_unit,
                d.adjustment,
                d.timestamp_convention,
                spec.price_tick,
                spec.multiplier,
                spec.currency,
                spec.quantity_unit,
                spec.availability_basis,
            )

        if any(economics(d) != economics(details[0]) for d in details):
            raise ValueError("experiment windows must share contract economics and clock semantics")
        if any(
            not max(c.strategy.history_bars for c in configurations)
            <= d.summary.bar_count
            <= 100000
            for d in details
        ):
            raise ValueError("each fixed window must contain enough bounded warmup and input")
        plan = {
            "revision": "fixed-research-experiment/1",
            "learning": None if learning is None else learning.to_dict(),
            "base": None if learning is None else first.to_dict(),
            "hypothesis": hypothesis.strip(),
            "code_revision": code_revision(),
            "candidates": dict(sorted(candidates.items())) if learning is None else {},
            "windows": {phase: d.to_dict() for phase, d in zip(_PHASES, details, strict=True)},
            "selection": "MAX_VALIDATION_NET_RETURN_THEN_CANDIDATE_ID",
            "initial_state": "INDEPENDENT_EMPTY_ACCOUNT_AND_WARMUP_PER_WINDOW",
            "fit": "FIXED_STRATEGY_PARAMETER_GRID_NO_LEARNED_MODEL"
            if learning is None
            else "TRAIN_ONLY_RIDGE_RETURN_MODEL",
            "sample_use": "HELD_OUT_WITHIN_PLAN_NOT_PROOF_OF_UNUSED_DATA",
        }
        if learning and any(
            not learning.slow_bars + learning.horizon_bars + 10 <= d.summary.bar_count <= 10000
            for d in details
        ):
            raise ValueError("learning windows require enough bounded labelled input")
        with write_transaction(self.engine) as c:
            old = (
                c.execute(select(_plans).where(_plans.c.experiment_id == str(identity)))
                .mappings()
                .first()
            )
            if old is not None:
                if old["plan_id"] != content_id(plan):
                    raise ValueError("experiment identity reused with different fixed inputs")
            else:
                c.execute(
                    insert(_plans).values(
                        experiment_id=str(identity),
                        plan_id=content_id(plan),
                        plan=plan,
                        created_at=datetime.now(UTC),
                    )
                )
        from .artifacts import publish_usage

        for snapshot in snapshots:
            publish_usage(self.engine, snapshot)
        # A crash here leaves an admitted plan; the supervisor resumes its deterministic jobs.
        return self.get(str(identity))

    def get(self, identity: str) -> dict[str, Any]:
        with self.engine.connect() as c:
            row = (
                c.execute(select(_plans).where(_plans.c.experiment_id == identity))
                .mappings()
                .one_or_none()
            )
            selected = (
                c.execute(select(_selections).where(_selections.c.experiment_id == identity))
                .mappings()
                .one_or_none()
            )
            fitted = (
                c.execute(select(_fits).where(_fits.c.experiment_id == identity))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError("experiment not found")
        plan = row["plan"]
        if content_id(plan) != row["plan_id"]:
            raise ValueError("experiment plan integrity failure")
        decision = None if selected is None else selected["decision"]
        if selected is not None and content_id(decision) != selected["decision_id"]:
            raise ValueError("experiment selection integrity failure")
        learned = None if fitted is None else fitted["document"]
        if fitted is not None and content_id(learned) != fitted["fit_id"]:
            raise ValueError("experiment fitted artifact integrity failure")
        candidates = plan["candidates"] if learned is None else learned.get("candidates", {})
        trials = []
        for candidate in candidates:
            for phase in _PHASES:
                if phase == "test" and (decision is None or decision["winner"] != candidate):
                    continue
                job = task_id(identity, candidate, phase)
                try:
                    task = self.tasks.get(job)
                except LookupError:
                    task = {"task_id": job, "status": "NOT_QUEUED", "run_id": None, "reason": ""}
                trials.append({"candidate_id": candidate, "phase": phase, **task})
        test = next((t for t in trials if t["phase"] == "test"), None)
        status = (
            "SEARCHING"
            if decision is None
            else "FAILED"
            if decision["winner"] is None
            else (test["status"] if test and test["status"] in _TERMINAL else "TESTING")
        )
        if plan["learning"] is not None:
            if learned is None:
                status = "FITTING"
            elif learned["status"] == "FAILED":
                status = "FAILED"
        if any(t["status"] == "INTERRUPTED" for t in trials):
            status = "INTERRUPTED"
        if plan["code_revision"] != code_revision() and status not in _TERMINAL:
            status = "IMPLEMENTATION_MISMATCH"
        return {
            "experiment_id": identity,
            "plan_id": row["plan_id"],
            "plan": plan,
            "created_at": row["created_at"].isoformat(),
            "status": status,
            "selection": decision,
            "fitted": learned,
            "trials": trials,
        }

    def list(self) -> list[dict[str, Any]]:
        with self.engine.connect() as c:
            ids = c.scalars(
                select(_plans.c.experiment_id).order_by(_plans.c.created_at.desc()).limit(200)
            ).all()
        return [self.get(i) for i in ids]

    def pending(self) -> tuple[str, ...]:
        """Query only unfinished plans; completed reports never enter the polling path."""
        query = (
            select(_plans.c.experiment_id)
            .outerjoin(_fits, _fits.c.experiment_id == _plans.c.experiment_id)
            .where(func.coalesce(_fits.c.document["status"].as_string(), "READY") != "FAILED")
            .outerjoin(_selections, _selections.c.experiment_id == _plans.c.experiment_id)
            .outerjoin(jobs, jobs.c.task_id == _selections.c.decision["test_task_id"].as_string())
            .where(
                or_(
                    _selections.c.experiment_id.is_(None),
                    (
                        _selections.c.decision["winner"].as_string().is_not(None)
                        & func.coalesce(jobs.c.status, "NOT_QUEUED").not_in(_TERMINAL)
                    ),
                )
            )
        )
        with self.engine.connect() as c:
            return tuple(c.scalars(query))

    def advance(self, identity: str, library: DatasetReader | None = None) -> bool:
        state = self.get(identity)
        if state["status"] in _TERMINAL | {"IMPLEMENTATION_MISMATCH", "INTERRUPTED"}:
            return False
        plan = state["plan"]
        if plan["learning"] is not None and state["fitted"] is None:
            if library is None:
                raise ValueError("learning requires the fixed publication reader")
            try:
                window = plan["windows"]["train"]
                dataset = library.load_dataset(UUID(window["snapshot_id"]))
                if dataset.details is None or dataset.details.to_dict() != window:
                    raise ValueError("fixed training input identity changed")
                document = {
                    "status": "SUCCEEDED",
                    **fit(
                        dataset,
                        LearningRecipe.from_dict(plan["learning"]),
                        ResearchConfig.from_mapping(plan["base"]),
                    ),
                }
            except Exception as error:
                document = {"status": "FAILED", "error": str(error)[:1000]}
            with write_transaction(self.engine) as c:
                c.execute(
                    insert(_fits).values(
                        experiment_id=identity, document=document, fit_id=content_id(document)
                    )
                )
            return True
        available = plan["candidates"] if state["fitted"] is None else state["fitted"]["candidates"]
        decision = state["selection"]
        phases = ("train", "validation") if decision is None else ("test",)
        candidates = (
            available if decision is None else {decision["winner"]: available[decision["winner"]]}
        )
        for candidate, config in candidates.items():
            for phase in phases:
                existing = next(
                    (
                        t
                        for t in state["trials"]
                        if t["candidate_id"] == candidate and t["phase"] == phase
                    ),
                    None,
                )
                if existing is not None and existing["status"] != "NOT_QUEUED":
                    continue
                window = plan["windows"][phase]
                self.tasks.submit(
                    UUID(task_id(identity, candidate, phase)),
                    UUID(window["snapshot_id"]),
                    window["content_hash"],
                    ResearchConfig.from_mapping(config),
                    window["bar_count"],
                    window,
                )
        if decision is not None:
            return False
        trials = self.get(identity)["trials"]
        if any(t["status"] not in _TERMINAL for t in trials):
            return False
        scores: list[dict[str, Any]] = []
        for candidate in available:
            candidate_trials = [t for t in trials if t["candidate_id"] == candidate]
            if any(t["status"] != "SUCCEEDED" for t in candidate_trials):
                scores.append({"candidate_id": candidate, "status": "FAILED", "score": None})
                continue
            validation = next(t for t in candidate_trials if t["phase"] == "validation")
            result: Any = RunStore(self.engine).get(validation["run_id"])
            score = str(result["result"]["summary"]["total_return"])
            if not Decimal(score).is_finite():
                raise ValueError("nonfinite validation score")
            scores.append({"candidate_id": candidate, "status": "SUCCEEDED", "score": score})
        ranked = sorted(
            (s for s in scores if s["status"] == "SUCCEEDED"),
            key=lambda s: (-Decimal(s["score"]), s["candidate_id"]),
        )
        decision = {
            "plan_id": state["plan_id"],
            "winner": ranked[0]["candidate_id"] if ranked else None,
            "test_task_id": task_id(identity, ranked[0]["candidate_id"], "test")
            if ranked
            else None,
            "scores": scores,
            "run_ids": [t["run_id"] for t in trials],
        }
        # Only the lifetime-locked supervisor selects; decision precedes test admission.
        with write_transaction(self.engine) as c:
            current = (
                c.execute(
                    select(jobs)
                    .where(jobs.c.task_id.in_([t["task_id"] for t in trials]))
                    .with_for_update()
                )
                .mappings()
                .all()
            )
            observed = {t["task_id"]: (t["status"], t["run_id"]) for t in trials}
            if any(observed[t["task_id"]] != (t["status"], t["run_id"]) for t in current):
                return False  # a retry raced selection; wait for the newly admitted attempt
            c.execute(
                insert(_selections).values(
                    experiment_id=identity, decision=decision, decision_id=content_id(decision)
                )
            )

        return False

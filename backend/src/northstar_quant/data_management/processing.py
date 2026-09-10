"""Durable Data processing claims and source-to-publication execution.

The database advisory lock excludes other processors for the complete bounded
operation. No lease expiration or API restart grants concurrent ownership.
Only RUNNING attempts are interrupted after the previous owner's lock is gone;
PENDING admissions survive restarts. Publication remains hidden until confirmed.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import cast, overload
from uuid import UUID

from sqlalchemy import select, text, update

from northstar_quant import code_revision

from .library import DataLibrary, _attempts
from .maintenance import library_write
from .research import ImportSpec, _import_csv, _import_stream

_PROCESSING_LOCK = 0x4E535150524F43


@contextmanager
def processing_claim(library: DataLibrary) -> Iterator[None]:
    with library_write(library._engine), library._engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": _PROCESSING_LOCK}
            )
        with library._engine.begin() as writer:
            writer.execute(
                update(_attempts)
                .where(_attempts.c.status == "RUNNING")
                .values(
                    status="FAILED",
                    updated_at=datetime.now(UTC),
                    error="Processing interrupted before a confirmed outcome; "
                    "explicitly retry as a new attempt.",
                )
            )
        yield


@overload
def process_attempt(library: DataLibrary, attempt_id: UUID) -> dict[str, object]: ...


@overload
def process_attempt(library: DataLibrary, attempt_id: None = None) -> dict[str, object] | None: ...


def process_attempt(
    library: DataLibrary, attempt_id: UUID | None = None
) -> dict[str, object] | None:
    """Execute a fixed attempt, or the oldest pending admission; never blindly retry."""
    with processing_claim(library):
        # Repair the committed publication outbox before accepting further work.
        # Readers only see the final atomic file; an interrupted export is retried.
        with library._engine.connect() as connection:
            published = (
                connection.execute(
                    select(_attempts.c.snapshot_id).where(_attempts.c.status == "PUBLISHED")
                )
                .scalars()
                .all()
            )
        for snapshot in set(published):
            if not (library.publications.root / f"{snapshot}.json").exists():
                library.publications.publish(library.load_dataset(snapshot))
        with library._engine.connect() as connection:
            statement = select(_attempts)
            if attempt_id is None:
                statement = (
                    statement.where(_attempts.c.status == "PENDING")
                    .order_by(_attempts.c.created_at, _attempts.c.attempt_id)
                    .limit(1)
                )
            else:
                statement = statement.where(_attempts.c.attempt_id == attempt_id)
            row = connection.execute(statement).mappings().one_or_none()
        if row is None:
            if attempt_id is not None:
                raise LookupError("processing attempt not found")
            return None
        if row["status"] != "PENDING":
            return library.attempt(row["attempt_id"])
        return _process(library, library._source_row(row["source_id"]), dict(row))


def _process(
    library: DataLibrary, source: dict[str, object], attempt: dict[str, object]
) -> dict[str, object]:
    attempt_id = cast(UUID, attempt["attempt_id"])
    current_stage = "VALIDATING"
    evidence: dict[str, object] = {}

    def stage(name: str, details: dict[str, object]) -> None:
        nonlocal current_stage
        current_stage = name
        evidence.update(details)
        library._update(attempt_id, status="RUNNING", stage=name, quality=dict(evidence))

    try:
        if attempt["code_revision"] != code_revision():
            raise ValueError(
                "Queued implementation differs from this worker; "
                "explicitly retry with current code."
            )
        stage("VALIDATING", {})
        library._verify_source(source)
        parameters = cast(dict[str, object], attempt["parameters"])
        spec = None
        if source["input_kind"] != "CTP_CALLBACK_SEGMENT":
            spec = ImportSpec.from_mapping(parameters)
            if spec.source_name.upper() != str(source["source_name"]).upper():
                raise ValueError("data.source_name differs from the retained source declaration")
        with library._engine.connect() as connection:
            previous = connection.scalar(
                select(_attempts.c.snapshot_id)
                .where(
                    _attempts.c.processing_hash == attempt["processing_hash"],
                    _attempts.c.status == "PUBLISHED",
                )
                .order_by(_attempts.c.created_at)
                .limit(1)
            )
        if previous is not None and not str(attempt["code_revision"]).endswith("-dirty"):
            dataset = library.load_dataset(previous)
            reused = True
        else:
            content = library._files.read(
                str(source["content_hash"]), cast(int, source["byte_count"])
            )
            archive = {
                "source_id": str(source["source_id"]),
                "evidence_hash": source["evidence_hash"],
            }
            if source["input_kind"] == "CTP_CALLBACK_SEGMENT":
                dataset = _import_stream(
                    library._engine,
                    content,
                    parameters=parameters,
                    archive=archive,
                    processing_hash=str(attempt["processing_hash"]),
                    stage=stage,
                )
            else:
                assert spec is not None
                dataset = _import_csv(
                    library._engine,
                    content,
                    spec,
                    archive=archive,
                    processing_hash=str(attempt["processing_hash"]),
                    stage=stage,
                )
            library._verify_dataset_sources(dataset)
            reused = False
        assert dataset.details is not None
        library._update(
            attempt_id,
            status="PUBLISHED",
            stage="PUBLISHED",
            error=None,
            snapshot_id=dataset.snapshot_id,
            quality=dataset.details.to_dict()["quality"],
            reused_product=reused,
        )
    except (ValueError, LookupError, OSError) as error:
        library._update(attempt_id, status="FAILED", stage=current_stage, error=str(error)[:1024])
    except Exception:
        # Persistence failures must never be mislabeled as successful publication.
        # If the database is unavailable the last durable stage remains RUNNING;
        # the next writer/maintenance audit marks it interrupted.
        library._update(
            attempt_id,
            status="FAILED",
            stage=current_stage,
            error="Processing interrupted by an internal failure; inspect application logs.",
        )
        raise
    if library.attempt(attempt_id)["status"] == "PUBLISHED":
        library.publications.publish(library.load_dataset(dataset.snapshot_id))
    return library.attempt(attempt_id)

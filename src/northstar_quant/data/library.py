"""The application's source-to-publication boundary.

An admitted source is immutable evidence, not a successful import. Each explicit
processing request has its own durable attempt. Canonical observations pin the
archive declaration used by their original import; later retries cannot relabel
that evidence. Raw bytes live in SourceFiles, and no caller-supplied path is read.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import NoReturn, cast
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Connection,
    DateTime,
    Engine,
    ForeignKey,
    MetaData,
    String,
    Table,
    Text,
    delete,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.engine import RowMapping

from northstar_quant.data.catalog.models import DatasetSnapshotManifest
from northstar_quant.data.files import SourceFiles
from northstar_quant.data.maintenance import library_write
from northstar_quant.data.research import (
    DatasetDetails,
    DatasetSummary,
    ImportSpec,
    ResearchDataset,
    _digest,
    _import_csv,
    _load_dataset,
    _source_evidence,
    _timestamp,
)
from northstar_quant.runtime import implementation_hash

_metadata = MetaData()
_sources = Table(
    "data_sources",
    _metadata,
    Column("source_id", PGUUID(as_uuid=True), primary_key=True),
    Column("filename", String(160), nullable=False),
    Column("source_name", String(64), nullable=False),
    Column("use_basis", String(1024), nullable=False),
    Column("allow_retention", Boolean, nullable=False),
    Column("allow_download", Boolean, nullable=False),
    Column("input_kind", String(32), nullable=False),
    Column("upstream_source_id", PGUUID(as_uuid=True), ForeignKey("data_sources.source_id")),
    Column("transformation_note", String(1024)),
    Column("upstream_evidence_hash", String(64)),
    Column("content_hash", String(64), nullable=False),
    Column("byte_count", BigInteger, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("evidence_hash", String(64), nullable=False),
    CheckConstraint("allow_retention AND byte_count > 0 AND byte_count <= 5242880"),
    CheckConstraint("input_kind IN ('RECEIVED_CSV', 'CONVERTED_CSV')"),
)
_attempts = Table(
    "data_processing_attempts",
    _metadata,
    Column("attempt_id", PGUUID(as_uuid=True), primary_key=True),
    Column("source_id", PGUUID(as_uuid=True), ForeignKey(_sources.c.source_id), nullable=False),
    Column("request_id", String(36), nullable=False, unique=True),
    Column("request_hash", String(64), nullable=False),
    Column("processing_hash", String(64), nullable=False, index=True),
    Column("implementation_hash", String(64), nullable=False),
    Column("parameters", JSONB, nullable=False),
    Column("status", String(16), nullable=False),
    Column("stage", String(24), nullable=False),
    Column("error", Text),
    Column("quality", JSONB, nullable=False),
    Column("retry_of", PGUUID(as_uuid=True), ForeignKey("data_processing_attempts.attempt_id")),
    Column("snapshot_id", PGUUID(as_uuid=True), ForeignKey(DatasetSnapshotManifest.id)),
    Column("reused_product", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("status IN ('PENDING', 'RUNNING', 'FAILED', 'PUBLISHED')"),
    CheckConstraint(
        "(status = 'PUBLISHED' AND snapshot_id IS NOT NULL AND error IS NULL) "
        "OR (status <> 'PUBLISHED' AND snapshot_id IS NULL)"
    ),
)
_rejections = Table(
    "data_admission_rejections",
    _metadata,
    Column("rejection_id", PGUUID(as_uuid=True), primary_key=True),
    Column("request_id", String(36)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("reason", String(512), nullable=False),
)
_PROCESSING_LOCK = 0x4E535150524F43
_REJECTION_LIMIT = 1000


class AdmissionRejected(ValueError):
    """A bounded admission refusal; no rejected content is retained."""

    def __init__(self, reason: str, rejection_id: UUID) -> None:
        super().__init__(reason)
        self.rejection_id = str(rejection_id)


def initialize_library(connection: Connection) -> None:
    """Create library metadata as part of the current atomic PostgreSQL baseline."""

    _metadata.create_all(connection)
    connection.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION data_reject_source_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'Source archive declarations are immutable';
        END;
        $$ LANGUAGE plpgsql
    """)
    connection.exec_driver_sql("DROP TRIGGER IF EXISTS immutable ON data_sources")
    connection.exec_driver_sql("""
        CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON data_sources
        FOR EACH ROW EXECUTE FUNCTION data_reject_source_change()
    """)
    connection.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION data_protect_processing_attempt() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR OLD.status IN ('FAILED', 'PUBLISHED') THEN
                RAISE EXCEPTION 'Terminal processing attempts are immutable';
            END IF;
            IF (NEW.attempt_id, NEW.source_id, NEW.request_id, NEW.request_hash,
                NEW.processing_hash, NEW.implementation_hash, NEW.parameters,
                NEW.retry_of, NEW.created_at)
                IS DISTINCT FROM
                (OLD.attempt_id, OLD.source_id, OLD.request_id, OLD.request_hash,
                OLD.processing_hash, OLD.implementation_hash, OLD.parameters,
                OLD.retry_of, OLD.created_at) THEN
                RAISE EXCEPTION 'Processing input identity is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    connection.exec_driver_sql("DROP TRIGGER IF EXISTS immutable ON data_processing_attempts")
    connection.exec_driver_sql("""
        CREATE TRIGGER immutable BEFORE UPDATE OR DELETE ON data_processing_attempts
        FOR EACH ROW EXECUTE FUNCTION data_protect_processing_attempt()
    """)


def manifest(connection: Connection) -> list[dict[str, object]]:
    """Read archive references inside the caller's consistent backup snapshot."""

    return [
        _json_row(row)
        for row in connection.execute(
            select(_sources.c.source_id, _sources.c.content_hash, _sources.c.byte_count).order_by(
                _sources.c.source_id
            )
        ).mappings()
    ]


class DataLibrary:
    """Receive, process, inspect and reopen controlled local research data."""

    def __init__(self, engine: Engine, files: SourceFiles) -> None:
        if engine.dialect.name != "postgresql":
            raise ValueError("data library requires PostgreSQL")
        self._engine = engine
        self._files = files

    @contextmanager
    def _writer(self) -> Iterator[None]:
        # One bounded local processing operation at a time. Besides avoiding
        # catalog races this lets a new owner identify crashed, incomplete work.
        with library_write(self._engine), self._engine.begin() as connection:
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": _PROCESSING_LOCK}
            )
            self._interrupt_unfinished()
            yield

    def receive(
        self,
        content: bytes,
        *,
        filename: str,
        source_name: str,
        use_basis: str,
        allow_retention: bool,
        allow_download: bool,
        spec: dict[str, object],
        request_id: str,
        input_kind: str = "RECEIVED_CSV",
        upstream_source_id: UUID | None = None,
        transformation_note: str | None = None,
    ) -> dict[str, object]:
        """Archive admitted bytes and record an attempt before interpreting spec.

        A repeated request UUID retrieves its original outcome. A different UUID
        creates a new attempt; equivalent successful products may be reused.
        Admission failures raise AdmissionRejected, processing failures return a
        FAILED attempt whose source remains inspectable and reprocessable.
        """

        with self._writer():
            try:
                request_id = _request_id(request_id)
                parameters = _parameters(spec)
                declaration = self._declaration(
                    filename,
                    source_name,
                    use_basis,
                    allow_retention,
                    allow_download,
                    input_kind,
                    upstream_source_id,
                    transformation_note,
                )
                if (
                    not isinstance(content, bytes)
                    or not 1 <= len(content) <= self._files.max_file_bytes
                ):
                    raise ValueError(
                        "source must be nonempty bytes within the configured upload limit"
                    )
                declaration.update(
                    content_hash=hashlib.sha256(content).hexdigest(), byte_count=len(content)
                )
                identity = _digest(
                    {
                        "operation": "receive",
                        "source": _source_evidence(declaration),
                        "spec": parameters,
                    }
                )
                existing = self._existing_request(request_id, identity)
                if existing is not None:
                    return self.attempt(existing)
                stored = self._files.store(content)
                if stored.content_hash != declaration["content_hash"] or stored.byte_count != len(
                    content
                ):
                    raise ValueError("durable source identity differs from the received bytes")
            except (ValueError, OSError) as error:
                self._reject(request_id, _safe_admission_error(error))
            source: dict[str, object] = {
                **declaration,
                "source_id": uuid4(),
                "received_at": datetime.now(UTC),
            }
            source["evidence_hash"] = _digest(_source_evidence(source))
            attempt = self._new_attempt(source, parameters, request_id, identity)
            with self._engine.begin() as connection:
                connection.execute(_sources.insert().values(**source))
                connection.execute(_attempts.insert().values(**attempt))
            return self._process(source, attempt)

    def reprocess(
        self, source_id: UUID, *, spec: dict[str, object], request_id: str
    ) -> dict[str, object]:
        """Create a distinct processing attempt without accepting replacement bytes."""

        with self._writer():
            try:
                request_id = _request_id(request_id)
                parameters = _parameters(spec)
                source = self._source_row(source_id)
                identity = _digest(
                    {"operation": "reprocess", "source_id": str(source_id), "spec": parameters}
                )
                existing = self._existing_request(request_id, identity)
                if existing is not None:
                    return self.attempt(existing)
            except ValueError as error:
                self._reject(request_id, str(error))
            attempt = self._new_attempt(source, parameters, request_id, identity)
            with self._engine.begin() as connection:
                connection.execute(_attempts.insert().values(**attempt))
            return self._process(source, attempt)

    def _declaration(
        self,
        filename: str,
        source_name: str,
        use_basis: str,
        allow_retention: bool,
        allow_download: bool,
        input_kind: str,
        upstream_source_id: UUID | None,
        transformation_note: str | None,
    ) -> dict[str, object]:
        _bounded_text(filename, "filename", 160)
        if filename in {".", ".."} or "/" in filename or "\\" in filename:
            raise ValueError("filename must be a display name, never a path")
        if (
            not isinstance(source_name, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", source_name) is None
        ):
            raise ValueError("source_name must be a bounded source identifier")
        _bounded_text(use_basis, "use_basis", 1024)
        if allow_retention is not True or type(allow_download) is not bool:
            raise ValueError(
                "explicit retention permission and a boolean download permission are required"
            )
        if input_kind not in {"RECEIVED_CSV", "CONVERTED_CSV"}:
            raise ValueError("input_kind must be RECEIVED_CSV or CONVERTED_CSV")
        if input_kind == "RECEIVED_CSV" and (
            upstream_source_id is not None or transformation_note is not None
        ):
            raise ValueError("upstream transformation metadata requires CONVERTED_CSV")
        if input_kind == "CONVERTED_CSV":
            _bounded_text(transformation_note, "transformation_note", 1024)
        upstream_hash = None
        if upstream_source_id is not None:
            upstream = self._source_row(upstream_source_id)
            self._verify_source(upstream)
            upstream_hash = upstream["evidence_hash"]
        return {
            "filename": filename,
            "source_name": source_name,
            "use_basis": use_basis,
            "allow_retention": allow_retention,
            "allow_download": allow_download,
            "input_kind": input_kind,
            "upstream_source_id": upstream_source_id,
            "transformation_note": transformation_note,
            "upstream_evidence_hash": upstream_hash,
        }

    def _new_attempt(
        self,
        source: dict[str, object],
        parameters: dict[str, object],
        request_id: str,
        request_hash: str,
    ) -> dict[str, object]:
        implementation = implementation_hash()
        meaning = {
            key: value
            for key, value in _source_evidence(source).items()
            if key not in {"source_id", "received_at", "filename"}
        }
        processing_hash = _digest(
            {"source": meaning, "parameters": parameters, "implementation": implementation}
        )
        with self._engine.connect() as connection:
            previous = connection.scalar(
                select(_attempts.c.attempt_id)
                .where(
                    (_attempts.c.processing_hash == processing_hash)
                    | (_attempts.c.source_id == source["source_id"])
                )
                .order_by(_attempts.c.created_at.desc())
                .limit(1)
            )
        now = datetime.now(UTC)
        return {
            "attempt_id": uuid4(),
            "source_id": source["source_id"],
            "request_id": request_id,
            "request_hash": request_hash,
            "processing_hash": processing_hash,
            "implementation_hash": implementation,
            "parameters": parameters,
            "status": "PENDING",
            "stage": "RECEIVED",
            "error": None,
            "quality": {},
            "retry_of": previous,
            "snapshot_id": None,
            "reused_product": False,
            "created_at": now,
            "updated_at": now,
        }

    def _process(self, source: dict[str, object], attempt: dict[str, object]) -> dict[str, object]:
        attempt_id = cast(UUID, attempt["attempt_id"])
        current_stage = "VALIDATING"
        evidence: dict[str, object] = {}

        def stage(name: str, details: dict[str, object]) -> None:
            nonlocal current_stage
            current_stage = name
            evidence.update(details)
            self._update(attempt_id, status="RUNNING", stage=name, quality=dict(evidence))

        try:
            stage("VALIDATING", {})
            self._verify_source(source)
            spec = ImportSpec.from_mapping(cast(dict[str, object], attempt["parameters"]))
            if spec.source_name.upper() != str(source["source_name"]).upper():
                raise ValueError("data.source_name differs from the retained source declaration")
            with self._engine.connect() as connection:
                previous = connection.scalar(
                    select(_attempts.c.snapshot_id)
                    .where(
                        _attempts.c.processing_hash == attempt["processing_hash"],
                        _attempts.c.status == "PUBLISHED",
                    )
                    .order_by(_attempts.c.created_at)
                    .limit(1)
                )
            if previous is not None:
                dataset = self.load_dataset(previous)
                reused = True
            else:
                content = self._files.read(
                    str(source["content_hash"]), cast(int, source["byte_count"])
                )
                dataset = _import_csv(
                    self._engine,
                    content,
                    spec,
                    archive={
                        "source_id": str(source["source_id"]),
                        "evidence_hash": source["evidence_hash"],
                    },
                    processing_hash=str(attempt["processing_hash"]),
                    stage=stage,
                )
                self._verify_dataset_sources(dataset)
                reused = False
            assert dataset.details is not None
            self._update(
                attempt_id,
                status="PUBLISHED",
                stage="PUBLISHED",
                error=None,
                snapshot_id=dataset.snapshot_id,
                quality=dataset.details.to_dict()["quality"],
                reused_product=reused,
            )
        except (ValueError, LookupError, OSError) as error:
            self._update(attempt_id, status="FAILED", stage=current_stage, error=str(error)[:1024])
        except Exception:
            # Persistence failures must never be mislabeled as successful publication.
            # If the database is unavailable the last durable stage remains RUNNING;
            # the next writer/maintenance audit marks it interrupted.
            self._update(
                attempt_id,
                status="FAILED",
                stage=current_stage,
                error="Processing interrupted by an internal failure; inspect application logs.",
            )
            raise
        return self.attempt(attempt_id)

    def _update(self, attempt_id: UUID, **values: object) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                update(_attempts)
                .where(_attempts.c.attempt_id == attempt_id)
                .values(**values, updated_at=datetime.now(UTC))
            )

    def _existing_request(self, request_id: str, identity: str) -> UUID | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                select(_attempts.c.attempt_id, _attempts.c.request_hash).where(
                    _attempts.c.request_id == request_id
                )
            ).one_or_none()
        if row is None:
            return None
        if row.request_hash != identity:
            raise ValueError(
                "request_id is already bound to different input or processing parameters"
            )
        return cast(UUID, row.attempt_id)

    def _reject(self, request_id: object, reason: str) -> NoReturn:
        rejection_id = uuid4()
        try:
            identifier = _request_id(request_id)
        except ValueError:
            identifier = None
        with self._engine.begin() as connection:
            connection.execute(
                _rejections.insert().values(
                    rejection_id=rejection_id,
                    request_id=identifier,
                    created_at=datetime.now(UTC),
                    reason=reason[:512],
                )
            )
            keep = (
                select(_rejections.c.rejection_id)
                .order_by(_rejections.c.created_at.desc(), _rejections.c.rejection_id)
                .limit(_REJECTION_LIMIT)
            )
            connection.execute(delete(_rejections).where(_rejections.c.rejection_id.not_in(keep)))
        raise AdmissionRejected(reason[:512], rejection_id)

    def list_rejections(self, *, limit: int = 100) -> list[dict[str, object]]:
        _limit(limit)
        with self._engine.connect() as connection:
            return [
                _json_row(row)
                for row in connection.execute(
                    select(_rejections).order_by(_rejections.c.created_at.desc()).limit(limit)
                ).mappings()
            ]

    def _source_row(self, source_id: UUID) -> dict[str, object]:
        if not isinstance(source_id, UUID):
            raise ValueError("source_id must be a UUID")
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(_sources).where(_sources.c.source_id == source_id))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError("source not found")
        return dict(row)

    def _verify_source(self, source: dict[str, object]) -> None:
        visited: set[UUID] = set()
        while True:
            source_id = cast(UUID, source["source_id"])
            if source_id in visited or len(visited) >= 32:
                raise ValueError("source transformation lineage is cyclic or exceeds 32 inputs")
            visited.add(source_id)
            if (
                source["allow_retention"] is not True
                or _digest(_source_evidence(source)) != source["evidence_hash"]
            ):
                raise ValueError("source archive permission or evidence has drifted")
            self._files.read(str(source["content_hash"]), cast(int, source["byte_count"]))
            upstream_id = source["upstream_source_id"]
            if upstream_id is None:
                return
            upstream = self._source_row(cast(UUID, upstream_id))
            if upstream["evidence_hash"] != source["upstream_evidence_hash"]:
                raise ValueError("upstream source archive evidence has drifted")
            source = upstream

    def _source_summary(self, source: dict[str, object]) -> dict[str, object]:
        result = _json_row(source)
        result["file_status"] = self._files.inspect(
            str(source["content_hash"]), cast(int, source["byte_count"])
        )
        result["evidence_status"] = (
            "VERIFIED"
            if _digest(_source_evidence(source)) == source["evidence_hash"]
            else "CORRUPT"
        )
        return result

    def list_sources(self, *, limit: int = 50) -> list[dict[str, object]]:
        _limit(limit)
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    select(_sources).order_by(_sources.c.received_at.desc()).limit(limit)
                )
                .mappings()
                .all()
            )
        return [self._source_summary(dict(row)) for row in rows]

    def source(self, source_id: UUID) -> dict[str, object]:
        result = self._source_summary(self._source_row(source_id))
        with self._engine.connect() as connection:
            attempts = (
                connection.execute(
                    select(_attempts)
                    .where(_attempts.c.source_id == source_id)
                    .order_by(_attempts.c.created_at.desc())
                    .limit(200)
                )
                .mappings()
                .all()
            )
            children = (
                connection.execute(
                    select(_sources)
                    .where(_sources.c.upstream_source_id == source_id)
                    .order_by(_sources.c.received_at.desc())
                    .limit(200)
                )
                .mappings()
                .all()
            )
            # Include snapshots pinning this source's original observations, not
            # merely the latest receipt that happened to trigger publication.
            products = (
                connection.execute(
                    text(
                        "WITH RECURSIVE descendants AS ("
                        "SELECT source_id, 0 AS depth FROM data_sources "
                        "WHERE source_id = :source_id "
                        "UNION ALL SELECT s.source_id, d.depth + 1 FROM data_sources s "
                        "JOIN descendants d ON s.upstream_source_id = d.source_id "
                        "WHERE d.depth < 31"
                        ") SELECT DISTINCT a.snapshot_id FROM data_processing_attempts a "
                        "JOIN dataset_snapshot_import_quality_pin p "
                        "ON p.manifest_id = a.snapshot_id "
                        "JOIN import_run i ON i.id = p.import_run_id "
                        "WHERE a.status = 'PUBLISHED' AND (a.source_id IN "
                        "(SELECT source_id FROM descendants) "
                        "OR i.mapping::jsonb -> 'archive' ->> 'source_id' IN "
                        "(SELECT source_id::text FROM descendants)) LIMIT 200"
                    ),
                    {"source_id": source_id},
                )
                .scalars()
                .all()
            )
        result.update(
            attempts=[_json_row(row) for row in attempts],
            products=[{"snapshot_id": str(item)} for item in products],
            usages=self._usages(products),
            derived_sources=[self._source_summary(dict(row)) for row in children],
        )
        return result

    def list_attempts(self, *, limit: int = 50) -> list[dict[str, object]]:
        _limit(limit)
        with self._engine.connect() as connection:
            return [
                _json_row(row)
                for row in connection.execute(
                    select(_attempts).order_by(_attempts.c.created_at.desc()).limit(limit)
                ).mappings()
            ]

    def attempt(self, attempt_id: UUID) -> dict[str, object]:
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(_attempts).where(_attempts.c.attempt_id == attempt_id))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError("processing attempt not found")
        result = _json_row(row)
        result["source"] = self._source_summary(self._source_row(row["source_id"]))
        return result

    def download(self, source_id: UUID) -> tuple[str, bytes]:
        source = self._source_row(source_id)
        if source["allow_download"] is not True:
            raise PermissionError("download is not permitted by this source's retained declaration")
        self._verify_source(source)
        return str(source["filename"]), self._files.read(
            str(source["content_hash"]), cast(int, source["byte_count"])
        )

    def _verify_dataset_sources(self, dataset: ResearchDataset) -> None:
        if dataset.details is None:
            raise ValueError("published data requires original source evidence")
        for source in dataset.details.sources:
            self._verify_source(self._source_row(source.source_id))

    def load_dataset(self, snapshot_id: UUID) -> ResearchDataset:
        with self._engine.connect() as connection:
            published = connection.scalar(
                select(_attempts.c.attempt_id)
                .where(_attempts.c.snapshot_id == snapshot_id, _attempts.c.status == "PUBLISHED")
                .limit(1)
            )
        if published is None:
            raise LookupError("published library dataset not found")
        dataset = _load_dataset(self._engine, snapshot_id)
        self._verify_dataset_sources(dataset)
        return dataset

    def describe_dataset(self, snapshot_id: UUID) -> DatasetDetails:
        details = self.load_dataset(snapshot_id).details
        assert details is not None
        return details

    def list_datasets(self, *, limit: int = 50) -> tuple[DatasetSummary, ...]:
        """Offer only confirmed publications whose pinned data and archives verify."""

        _limit(limit)
        with self._engine.connect() as connection:
            ids = (
                connection.execute(
                    select(_attempts.c.snapshot_id)
                    .where(_attempts.c.status == "PUBLISHED")
                    .group_by(_attempts.c.snapshot_id)
                    .order_by(text("max(created_at) DESC"))
                    .limit(limit)
                )
                .scalars()
                .all()
            )
        result = []
        for snapshot_id in ids:
            try:
                result.append(self.describe_dataset(snapshot_id).summary)
            except (ValueError, LookupError):
                # The failure stays visible on source/attempt details; damaged
                # archives are not offered as runnable published input.
                continue
        return tuple(result)

    def lineage(self, snapshot_id: UUID) -> dict[str, object]:
        """Inspect pinned database evidence even when its archive cannot be executed.

        Missing bytes must block a new run, not hide an already stored result.
        Dynamic file status is intentionally outside the immutable run identity.
        """

        dataset = _load_dataset(self._engine, snapshot_id)
        details = dataset.details
        assert details is not None
        with self._engine.connect() as connection:
            attempts = (
                connection.execute(
                    select(_attempts)
                    .where(_attempts.c.snapshot_id == snapshot_id)
                    .order_by(_attempts.c.created_at.desc())
                    .limit(200)
                )
                .mappings()
                .all()
            )
        sources: dict[UUID, dict[str, object]] = {}
        for source in details.sources:
            current = self._source_row(source.source_id)
            for _ in range(32):
                identifier = cast(UUID, current["source_id"])
                if identifier in sources:
                    break
                sources[identifier] = self._source_summary(current)
                if current["upstream_source_id"] is None:
                    break
                current = self._source_row(cast(UUID, current["upstream_source_id"]))
        return {
            "snapshot_id": str(snapshot_id),
            "sources": list(sources.values()),
            "attempts": [_json_row(row) for row in attempts],
            "usages": self._usages([snapshot_id]),
        }

    def _usages(self, snapshot_ids: Sequence[UUID]) -> list[dict[str, object]]:
        if not snapshot_ids:
            return []
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT 'RESEARCH' AS kind, run_id AS use_id, snapshot_id, created_at "
                        "FROM research_runs WHERE snapshot_id = ANY(:ids) "
                        "UNION ALL SELECT 'PAPER' AS kind, session_id::text AS use_id, "
                        "snapshot_id, created_at "
                        "FROM paper_sessions WHERE snapshot_id = ANY(:ids) "
                        "ORDER BY created_at DESC LIMIT 200"
                    ),
                    {"ids": snapshot_ids},
                )
                .mappings()
                .all()
            )
        return [_json_row(row) for row in rows]

    def _interrupt_unfinished(self) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                update(_attempts)
                .where(_attempts.c.status.in_(["PENDING", "RUNNING"]))
                .values(
                    status="FAILED",
                    updated_at=datetime.now(UTC),
                    error=(
                        "Processing interrupted before a confirmed outcome; "
                        "explicitly retry as a new attempt."
                    ),
                )
            )

    def reconcile(self) -> dict[str, object]:
        """Acquire write admission, mark interrupted work and inspect retained files."""

        with self._writer():
            with self._engine.connect() as connection:
                expected = manifest(connection)
            identities = {str(item["content_hash"]) for item in expected}
            return {
                "sources": [
                    {
                        **item,
                        "file_status": self._files.inspect(
                            str(item["content_hash"]), cast(int, item["byte_count"])
                        ),
                    }
                    for item in expected
                ],
                "unreferenced_objects": [
                    item.to_dict()
                    for item in self._files.inventory()
                    if item.content_hash not in identities
                ],
                "storage": self._files.health(),
            }


def _parameters(spec: dict[str, object]) -> dict[str, object]:
    if not isinstance(spec, dict):
        raise ValueError("spec must be a JSON object")
    try:
        encoded = json.dumps(spec, allow_nan=False, ensure_ascii=False)
    except (ValueError, TypeError, RecursionError) as error:
        raise ValueError("processing parameters must be finite JSON values") from error
    if len(encoded.encode()) > 32768:
        raise ValueError("processing parameters exceed 32 KiB")
    return cast(dict[str, object], json.loads(encoded))


def _request_id(value: object) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise ValueError("request_id must be a canonical UUID string")
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except ValueError as error:
        raise ValueError("request_id must be a canonical UUID string") from error
    return value


def _bounded_text(value: object, name: str, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{name} must be nonempty text of at most {maximum} characters")


def _limit(value: int) -> None:
    if type(value) is not int or not 1 <= value <= 200:
        raise ValueError("list limit must be between 1 and 200")


def _json_row(row: Mapping[str, object] | RowMapping) -> dict[str, object]:
    return {
        key: str(value)
        if isinstance(value, UUID)
        else _timestamp(value)
        if isinstance(value, datetime)
        else value
        for key, value in row.items()
    }


def _safe_admission_error(error: ValueError | OSError) -> str:
    return (
        str(error)[:512]
        if isinstance(error, ValueError)
        else "source storage is unavailable; nothing accepted"
    )

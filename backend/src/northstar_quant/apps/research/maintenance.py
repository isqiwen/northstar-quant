"""Research database backups pinned to the fixed market inputs and report files they use."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast
from uuid import UUID

from sqlalchemy import Engine, create_engine, inspect, text

from northstar_quant.apps.maintenance import _file_hash, _write_record
from northstar_quant.apps.storage import require_current_database
from northstar_quant.data_management.publications import PublishedDatasets
from northstar_quant.data_management.storage_identity import initialize
from northstar_quant.research.artifacts import ResearchArtifacts


def backup(engine: Engine, destination: Path) -> dict[str, object]:
    require_current_database(engine)
    market = PublishedDatasets.from_environment()
    reports = ResearchArtifacts.from_environment()
    if not destination.is_absolute() or destination.exists():
        raise ValueError("backup requires a new absolute directory")
    target = destination.resolve()
    for root in (market.root.resolve(), reports.root.resolve()):
        if target.is_relative_to(root) or root.is_relative_to(target):
            raise ValueError("backup and active research storage must be separate")
    target.mkdir(parents=True, mode=0o700)
    (target / "market").mkdir()
    (target / "research").mkdir()
    if engine.dialect.name != "sqlite" or not engine.url.database:
        raise ValueError("Research backup requires local SQLite")
    with (
        closing(sqlite3.connect(engine.url.database)) as source_db,
        closing(sqlite3.connect(target / "database.sqlite3")) as copy_db,
    ):
        source_db.backup(copy_db)
        copy_db.execute("PRAGMA journal_mode=DELETE")
        if copy_db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("Research backup database integrity failure")
    frozen = create_engine("sqlite+pysqlite:///" + str(target / "database.sqlite3"))
    try:
        with frozen.connect() as connection:
            ids = (
                connection.execute(
                    text(
                        "SELECT snapshot_id FROM research_jobs "
                        "UNION SELECT snapshot_id FROM research_runs "
                        "UNION SELECT snapshot_id FROM paper_sessions "
                        "UNION SELECT snapshot_id FROM factor_runs WHERE status='SUCCEEDED'"
                    )
                )
                .scalars()
                .all()
            )
            for identifier in ids:
                identifier = UUID(str(identifier))
                market.load_dataset(identifier)
                source = market.root / f"{identifier}.json"
                shutil.copyfile(source, target / "market" / source.name)
                manifest = market.manifest(identifier)
                for entry in manifest["files"]:
                    source = market.root / entry["path"]
                    shutil.copyfile(source, target / "market" / source.name)
            for identity in connection.execute(text("SELECT run_id FROM research_runs")).scalars():
                reports.verify_backtest(identity)
            # Include immutable reports and receipts. Extra concurrent immutable files
            # are harmless; every file is checked and pinned in the backup manifest.
            for source in reports.root.glob("*.json"):
                reports._read(source)
                shutil.copyfile(source, target / "research" / source.name)
    finally:
        frozen.dispose()
    manifest = {str(p.relative_to(target)): _file_hash(p) for p in target.rglob("*") if p.is_file()}
    document: dict[str, object] = {"owner": "research", "files": manifest}
    for path in manifest:
        with (target / path).open("rb") as stream:
            os.fsync(stream.fileno())
    from northstar_quant.data_management.files import SourceFiles

    for root in (target / "market", target / "research", target):
        SourceFiles._sync(root)
    _write_record(target / "manifest.json", json.dumps(document, sort_keys=True).encode())
    return document


def restore(engine: Engine, destination: Path) -> dict[str, object]:
    if not destination.is_absolute() or destination.is_symlink():
        raise ValueError("restore requires a trusted absolute backup directory")
    document = json.loads((destination / "manifest.json").read_text())
    if document.get("owner") != "research" or not isinstance(document.get("files"), dict):
        raise ValueError("not a Research backup")
    for name, digest in document["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or len(relative.parts) not in {1, 2}:
            raise ValueError("invalid backup file path")
        if relative.parts[0] not in {"database.sqlite3", "market", "research"}:
            raise ValueError("invalid Research backup component")
        if _file_hash(destination / relative) != digest:
            raise ValueError("backup checksum mismatch")
    if "database.sqlite3" not in document["files"]:
        raise ValueError("missing database dump")
    with engine.connect() as connection:
        if (
            set(inspect(connection).get_schema_names()) - {"main", "temp"}
            or inspect(connection).get_table_names()
        ):
            raise ValueError("restore target database must be empty")
    roots = {
        name: Path(os.environ[f"NORTHSTAR_{name.upper()}_DIR"]) for name in ("market", "research")
    }
    if any(root.exists() or root.is_symlink() or not root.is_absolute() for root in roots.values()):
        raise ValueError("restore requires new market and research directories")
    for name, root in roots.items():
        root.mkdir(parents=True, mode=0o700)
        initialize(root, os.environ[f"NORTHSTAR_{name.upper()}_STORAGE_ID"])
        _write_record(root / ".restore-incomplete", b"Restore has not passed validation.\n")
    for name in document["files"]:
        parts = Path(name).parts
        if len(parts) == 2:
            _write_record(roots[parts[0]] / parts[1], (destination / name).read_bytes())
    if engine.dialect.name != "sqlite" or not engine.url.database:
        raise ValueError("Research restore requires local SQLite")
    engine.dispose()
    with (
        closing(sqlite3.connect(destination / "database.sqlite3")) as source_db,
        closing(sqlite3.connect(engine.url.database)) as target_db,
    ):
        if source_db.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("Research backup database integrity failure")
        source_db.backup(target_db)
    require_current_database(engine)
    market = PublishedDatasets(roots["market"])
    from northstar_quant.research.paper import PaperStore
    from northstar_quant.research.runs import RunStore

    runs = RunStore(engine)
    reports = ResearchArtifacts(roots["research"])
    with engine.connect() as connection:
        run_ids = connection.execute(text("SELECT run_id FROM research_runs")).scalars().all()
        paper_ids = (
            connection.execute(text("SELECT session_id FROM paper_sessions")).scalars().all()
        )
        factor_ids = (
            connection.execute(text("SELECT attempt_id FROM factor_runs WHERE status='SUCCEEDED'"))
            .scalars()
            .all()
        )
    for identity in run_ids:
        saved = runs.get(identity)
        market.load_dataset(UUID(str(cast(dict[str, object], saved["snapshot"])["id"])))
        reports.verify_backtest(identity)
    paper = PaperStore(engine, market)
    for identity in paper_ids:
        paper.get(UUID(str(identity)))
    from northstar_quant.research.factor_catalog import FactorCatalog

    factors = FactorCatalog(engine, market)
    for identity in factor_ids:
        factors.get(UUID(str(identity)))
    for root in roots.values():
        (root / ".restore-incomplete").unlink()
        from northstar_quant.data_management.files import SourceFiles

        SourceFiles._sync(root)
    return {"status": "restored", "owner": "research"}

"""Run inside the disposable NAS acceptance container, never against an operator database."""

import os
from pathlib import Path
from uuid import uuid4

import psycopg
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError

from northstar_quant.apps.research.maintenance import backup, restore
from northstar_quant.live.archive import accept


def engine_for(owner, *, admin=False, database=None):
    return create_engine(
        URL.create(
            "postgresql+psycopg",
            username="northstar_admin" if admin else f"northstar_{owner}_app",
            password=os.environ["NORTHSTAR_NAS_ADMIN_PASSWORD"]
            if admin
            else os.environ[f"NORTHSTAR_{owner.upper()}_DATABASE_PASSWORD"],
            host="postgres",
            database=database or f"northstar_{owner}",
        )
    )


def main():
    for owner in ("data_hub", "research", "live"):
        engine = engine_for(owner)
        try:
            with engine.connect() as connection:
                flags = connection.execute(
                    text(
                        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
                        "FROM pg_roles WHERE rolname=current_user"
                    )
                ).one()
                assert not any(flags)
                tables = set(inspect(connection).get_table_names())
                assert not any(t.startswith("broker_") for t in tables)
                if owner != "research":
                    assert "research_runs" not in tables
                if owner != "data_hub":
                    assert "data_sources" not in tables
            for statement in (
                "CREATE TABLE forbidden (id int)",
                "UPDATE northstar_store SET owner='all'",
            ):
                try:
                    with engine.begin() as connection:
                        connection.execute(text(statement))
                except SQLAlchemyError:
                    pass
                else:
                    raise AssertionError("runtime identity gained management authority")
            for other in {"data_hub", "research", "live"} - {owner}:
                cross = engine_for(owner, database=f"northstar_{other}")
                try:
                    try:
                        with cross.connect():
                            raise AssertionError("cross-database access was accepted")
                    except SQLAlchemyError:
                        pass
                finally:
                    cross.dispose()
            if owner == "live":
                writer = uuid4()
                digest = accept(engine, writer, 1, {"fact": "synthetic archive acceptance"})
                assert accept(engine, writer, 1, {"fact": "synthetic archive acceptance"}) == digest
                try:
                    accept(engine, writer, 1, {"fact": "conflict"})
                except ValueError:
                    pass
                else:
                    raise AssertionError("conflicting archive fact was accepted")
        finally:
            engine.dispose()
    print(
        "Three runtime roles: cross-database/DDL rejection and archive deduplication passed",
        flush=True,
    )
    engine = engine_for("research")
    target = Path(os.environ["NORTHSTAR_BACKUP_DIR"]) / "research-acceptance"
    backup(engine, target)
    with engine.connect() as connection:
        count = connection.execute(text("SELECT count(*) FROM research_runs")).scalar_one()
        assert count > 0
    engine.dispose()
    name = "northstar_restore_check_" + uuid4().hex
    with psycopg.connect(
        host="postgres",
        dbname="postgres",
        user="northstar_admin",
        password=os.environ["NORTHSTAR_NAS_ADMIN_PASSWORD"],
        autocommit=True,
    ) as admin:
        admin.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(name)))
        recovered = engine_for("research", admin=True, database=name)
        try:
            for share in ("MARKET", "RESEARCH"):
                os.environ[f"NORTHSTAR_{share}_DIR"] = str(
                    target.parent / ("restored-" + share.lower())
                )
                os.environ[f"NORTHSTAR_{share}_STORAGE_ID"] = str(uuid4())
            assert restore(recovered, target)["status"] == "restored"
            with recovered.connect() as connection:
                assert (
                    connection.execute(text("SELECT count(*) FROM research_runs")).scalar_one()
                    == count
                )
        finally:
            recovered.dispose()
            admin.execute(psycopg.sql.SQL("DROP DATABASE {}").format(psycopg.sql.Identifier(name)))
    print(
        "Research backup restores database, fixed inputs and artifacts into empty storage",
        flush=True,
    )


if __name__ == "__main__":
    main()

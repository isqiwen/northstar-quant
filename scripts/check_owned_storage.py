"""Verify the disposable core database role, never an operator database."""

import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError


def engine_for(owner, *, admin=False, database=None):
    return create_engine(
        URL.create(
            "postgresql+psycopg",
            username="northstar_admin" if admin else f"northstar_{owner}_app",
            password=os.environ["NORTHSTAR_DATABASE_ADMIN_PASSWORD"]
            if admin
            else os.environ[f"NORTHSTAR_{owner.upper()}_DATABASE_PASSWORD"],
            host="postgres",
            database=database or f"northstar_{owner}",
        )
    )


def main():
    for owner in (os.environ["NORTHSTAR_DATABASE_OWNER"],):
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
        finally:
            engine.dispose()
    print(
        "Core Data Hub role: cross-database and DDL rejection passed",
        flush=True,
    )


if __name__ == "__main__":
    main()

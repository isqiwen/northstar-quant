"""Explicit application-owned database and share initialization using the administrator identity."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from northstar_quant.apps.storage import initialize_database
from northstar_quant.data_management.storage_identity import initialize, require_identity

OWNERS = ("data_hub",)


def provision() -> None:
    password = os.environ["NORTHSTAR_DATABASE_ADMIN_PASSWORD"]
    host = os.environ.get("NORTHSTAR_DATABASE_HOST", "postgres")
    port = int(os.environ.get("NORTHSTAR_DATABASE_PORT", "5432"))
    owner = os.environ["NORTHSTAR_DATABASE_OWNER"]
    if owner not in OWNERS:
        raise ValueError("invalid provisioned database owner")
    owners = (owner,)
    app_passwords = {
        owner: os.environ[f"NORTHSTAR_{owner.upper()}_DATABASE_PASSWORD"] for owner in owners
    }
    if any(len(value) < 16 for value in [password, *app_passwords.values()]):
        raise ValueError("Use independent database passwords with at least 16 characters")
    if len({password, *app_passwords.values()}) != 2:
        raise ValueError("Database identities require distinct passwords")
    directories = [
        (Path(os.environ[f"NORTHSTAR_{name}_DIR"]), os.environ[f"NORTHSTAR_{name}_STORAGE_ID"])
        for name in ("SOURCE", "MARKET", "RESEARCH", "BACKUP")
        if os.environ.get(f"NORTHSTAR_{name}_DIR")
    ]
    with psycopg.connect(
        host=host,
        port=port,
        dbname="postgres",
        user="northstar_admin",
        password=password,
        autocommit=True,
    ) as connection:
        existing = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (f"northstar_{owner}",)
        ).fetchone()
        for root, identity in directories:
            if existing:
                require_identity(root, identity)
            else:
                initialize(root, identity)
        for owner in owners:
            database = f"northstar_{owner}"
            role = f"{database}_app"
            administrator = f"{database}_owner"
            for name, login in [(administrator, False), (role, True)]:
                exists = connection.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname=%s", (name,)
                ).fetchone()
                if not exists:
                    connection.execute(
                        sql.SQL(
                            "CREATE ROLE {} {} NOSUPERUSER NOCREATEDB NOCREATEROLE "
                            "NOREPLICATION NOBYPASSRLS"
                        ).format(sql.Identifier(name), sql.SQL("LOGIN" if login else "NOLOGIN"))
                    )
            for name in (administrator, role):
                flags = connection.execute(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
                    "FROM pg_roles WHERE rolname=%s",
                    (name,),
                ).fetchone()
                login_flags = connection.execute(
                    "SELECT rolcanlogin FROM pg_roles WHERE rolname=%s", (name,)
                ).fetchone()
                if flags is None or any(flags) or login_flags != (name == role,):
                    raise ValueError(
                        "Managed application role has unexpected privileged attributes"
                    )
                if connection.execute(
                    "SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member "
                    "WHERE r.rolname=%s",
                    (name,),
                ).fetchone():
                    raise ValueError("Managed application role has unexpected inherited authority")
            # This explicit maintenance operation sets the configured current password.
            connection.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(app_passwords[owner])
                )
            )
            exists = connection.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (database,)
            ).fetchone()
            if not exists:
                connection.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(database), sql.Identifier(administrator)
                    )
                )
            actual_owner = connection.execute(
                "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s", (database,)
            ).fetchone()
            if actual_owner != (administrator,):
                raise ValueError("Database belongs to an unexpected owner; do not adopt it")
            connection.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(database))
            )
            connection.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database), sql.Identifier(role)
                )
            )
            engine = create_engine(
                URL.create(
                    "postgresql+psycopg",
                    username="northstar_admin",
                    password=password,
                    host=host,
                    port=port,
                    database=database,
                ),
                connect_args={"options": f"-c role={administrator}"},
            )
            try:
                initialize_database(engine, owner=owner)
                with engine.begin() as writer:
                    writer.exec_driver_sql("REVOKE ALL ON SCHEMA public FROM PUBLIC")
                    writer.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {role}")
                    writer.exec_driver_sql(
                        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                        f"IN SCHEMA public TO {role}"
                    )
                    writer.exec_driver_sql(
                        f"REVOKE INSERT, UPDATE, DELETE ON northstar_store FROM {role}"
                    )
                    if owner == "data_hub":
                        writer.exec_driver_sql(
                            f"REVOKE INSERT, UPDATE, DELETE ON alembic_version FROM {role}"
                        )
                    writer.exec_driver_sql(
                        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}"
                    )
                    writer.exec_driver_sql(
                        "REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC"
                    )
                    writer.exec_driver_sql(
                        f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {role}"
                    )
            finally:
                engine.dispose()
    print("Application database and configured share identities initialized")


if __name__ == "__main__":
    provision()

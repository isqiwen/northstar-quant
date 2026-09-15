"""An existing Data Hub must never silently reuse or rewrite an older schema."""

import pytest

from northstar_quant.data_management.db.store import initialize, require_current


def test_old_baseline_is_rejected_without_conversion_or_catalog_loss(
    postgres_engine, clean_database
):
    require_current(postgres_engine)
    with postgres_engine.begin() as connection:
        revision = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()
        connection.exec_driver_sql("UPDATE alembic_version SET version_num='20260911_02'")
        tables = (
            connection.exec_driver_sql(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
            )
            .scalars()
            .all()
        )
    try:
        for operation in (require_current, initialize):
            with pytest.raises(ValueError, match="current baseline"):
                operation(postgres_engine)
        with postgres_engine.connect() as connection:
            assert (
                connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
                == "20260911_02"
            )
            assert (
                connection.exec_driver_sql(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
                )
                .scalars()
                .all()
                == tables
            )
    finally:
        with postgres_engine.begin() as connection:
            connection.exec_driver_sql("UPDATE alembic_version SET version_num=%s", (revision,))
    require_current(postgres_engine)

from sqlalchemy import create_engine, inspect, text
from tests.support.postgresql import postgresql_test_url

from northstar_quant.config.settings import get_settings
from northstar_quant.db import models  # noqa: F401
from northstar_quant.db.base import Base
from northstar_quant.db.init_db import _redact_database_url, init_db


def test_redact_database_url_hides_password():
    redacted = _redact_database_url(
        "postgresql+psycopg://northstar:super-secret@db.example.com:5432/northstar"
    )

    assert "super-secret" not in redacted
    assert "***" in redacted
    assert "db.example.com:5432/northstar" in redacted


def test_init_db_uses_alembic_head_and_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "northstar.db"
    storage_dir = tmp_path / "storage"
    database_url = postgresql_test_url(db_path)

    monkeypatch.setenv("NORTHSTAR_DATABASE_URL", database_url)
    monkeypatch.setenv("NORTHSTAR_STORAGE_DIR", str(storage_dir))
    get_settings.cache_clear()

    try:
        init_db()
        init_db()

        engine = create_engine(database_url, future=True)
        inspector = inspect(engine)
        business_tables = set(inspector.get_table_names()) - {"alembic_version"}
        assert business_tables == set(Base.metadata.tables)

        with engine.connect() as connection:
            revision = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
        assert revision == "0001_initial_schema"
    finally:
        get_settings.cache_clear()

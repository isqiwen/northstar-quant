from sqlalchemy import create_engine, inspect, text

import northstar_quant.platform.config.settings as settings_module
import northstar_quant.platform.db.init_db as init_db_module
from northstar_quant.platform.config.settings import Settings, get_settings
from northstar_quant.platform.db import models  # noqa: F401
from northstar_quant.platform.db.base import Base
from northstar_quant.platform.db.init_db import _redact_database_url, init_db
from tests.helpers.postgresql import postgresql_test_url


def test_redact_database_url_hides_password():
    redacted = _redact_database_url(
        "postgresql+psycopg://northstar:super-secret@db.example.com:5432/northstar"  # secret-scan: allow; reason: disposable test fixture
    )

    assert "super-secret" not in redacted
    assert "***" in redacted
    assert "db.example.com:5432/northstar" in redacted


def test_init_db_uses_alembic_head_and_is_idempotent(tmp_path, monkeypatch):
    db_path = tmp_path / "northstar.db"
    storage_dir = tmp_path / "storage"
    database_url = postgresql_test_url(db_path)

    settings = Settings(
        _env_file=None,
        database_url=database_url,
        storage_dir=storage_dir,
        downloads_dir=storage_dir / "downloads",
        reports_dir=tmp_path / "reports",
        log_dir=tmp_path / "logs",
    )
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)
    monkeypatch.setattr(init_db_module, "get_settings", lambda: settings)

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
        assert revision == "0010_agent_run_audit_hardening"
    finally:
        get_settings.cache_clear()

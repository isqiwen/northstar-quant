"""全局设置的失败关闭解析测试。"""

import pytest
from pydantic import ValidationError

from northstar_quant.config.settings import Settings


def test_alert_mode_rejects_unknown_delivery_channel():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, alert_mode="typo")


def test_database_defaults_to_postgresql_psycopg():
    settings = Settings(_env_file=None)

    assert settings.database_url.startswith("postgresql+psycopg://")


def test_database_rejects_sqlite_url():
    with pytest.raises(ValidationError, match="不再支持 SQLite"):
        Settings(
            _env_file=None,
            database_url="sqlite:///storage/northstar.db",
        )


def test_empty_optional_integer_environment_value_is_ignored(monkeypatch):
    monkeypatch.setenv("NORTHSTAR_TELEGRAM_MESSAGE_THREAD_ID", "")

    settings = Settings(_env_file=None)

    assert settings.telegram_message_thread_id is None

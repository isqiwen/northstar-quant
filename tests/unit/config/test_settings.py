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


def test_runtime_paths_derive_from_storage_unless_explicit(tmp_path):
    derived = Settings(
        _env_file=None,
        project_root=tmp_path,
        storage_dir="runtime/storage",
        paper_account="paper-research",
        ctp_sim_account="ctp-sim-research",
    )
    explicit = Settings(
        _env_file=None,
        project_root=tmp_path,
        storage_dir="runtime/storage",
        downloads_dir="runtime/download-cache",
    )

    assert derived.storage_dir == tmp_path / "runtime/storage"
    assert derived.downloads_dir == tmp_path / "runtime/storage/downloads"
    assert derived.paper_state_path == (
        tmp_path / "runtime/storage/brokers/paper/paper-research/state.json"
    )
    assert derived.ctp_sim_state_path == (
        tmp_path / "runtime/storage/brokers/ctp_sim/ctp-sim-research/state.json"
    )
    assert explicit.downloads_dir == tmp_path / "runtime/download-cache"


def test_downloads_dir_environment_override_takes_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("NORTHSTAR_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setenv("NORTHSTAR_STORAGE_DIR", "runtime/storage")
    monkeypatch.setenv("NORTHSTAR_DOWNLOADS_DIR", "runtime/independent-downloads")

    settings = Settings(_env_file=None)

    assert settings.storage_dir == tmp_path / "runtime/storage"
    assert settings.downloads_dir == tmp_path / "runtime/independent-downloads"


@pytest.mark.parametrize("field", ["paper_state_path", "ctp_sim_state_path"])
def test_local_state_path_must_remain_inside_storage_dir(tmp_path, field):
    with pytest.raises(ValidationError, match="必须位于 NORTHSTAR_STORAGE_DIR 内"):
        Settings(
            _env_file=None,
            project_root=tmp_path,
            storage_dir="runtime/storage",
            **{field: "runtime/other-state.json"},
        )


@pytest.mark.parametrize("field", ["paper_account", "ctp_sim_account"])
@pytest.mark.parametrize("account", ["../other-account", "paper/account", "paper account"])
def test_local_state_account_rejects_path_like_or_ambiguous_identifiers(field, account):
    with pytest.raises(ValidationError, match="本地 Paper/CTP 模拟账户"):
        Settings(_env_file=None, **{field: account})

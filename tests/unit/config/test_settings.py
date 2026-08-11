"""全局设置的失败关闭解析测试。"""

from pathlib import Path

import pytest
from pydantic import ValidationError
import yaml

from northstar_quant.config.settings import (
    ENV_DISABLED_FIELDS,
    LEGACY_RUNTIME_PATH_ENV_VARS,
    Settings,
)


def _write_runtime_config(
    project_root: Path,
    *,
    storage_dir: str = "storage",
    downloads_dir: str | None = None,
    reports_dir: str = "reports",
    log_dir: str = "logs",
) -> None:
    config_dir = project_root / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "app.yaml").write_text(
        yaml.safe_dump(
            {
                "runtime": {
                    "storage_dir": storage_dir,
                    "downloads_dir": downloads_dir,
                    "reports_dir": reports_dir,
                    "log_dir": log_dir,
                }
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )


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
    _write_runtime_config(
        tmp_path,
        storage_dir="runtime/storage",
        reports_dir="runtime/reports",
        log_dir="runtime/logs",
    )
    derived = Settings(
        _env_file=None,
        project_root=tmp_path,
        paper_account="paper-research",
        ctp_sim_account="ctp-sim-research",
    )
    explicit = Settings(
        _env_file=None,
        project_root=tmp_path,
        storage_dir="test-runtime/storage",
        downloads_dir="runtime/download-cache",
        reports_dir="test-runtime/reports",
        log_dir="test-runtime/logs",
    )

    assert derived.storage_dir == tmp_path / "runtime/storage"
    assert derived.downloads_dir == tmp_path / "runtime/storage/downloads"
    assert derived.reports_dir == tmp_path / "runtime/reports"
    assert derived.log_dir == tmp_path / "runtime/logs"
    assert derived.paper_state_path == (
        tmp_path / "runtime/storage/brokers/paper/paper-research/state.json"
    )
    assert derived.ctp_sim_state_path == (
        tmp_path / "runtime/storage/brokers/ctp_sim/ctp-sim-research/state.json"
    )
    assert explicit.storage_dir == tmp_path / "test-runtime/storage"
    assert explicit.downloads_dir == tmp_path / "runtime/download-cache"
    assert explicit.reports_dir == tmp_path / "test-runtime/reports"
    assert explicit.log_dir == tmp_path / "test-runtime/logs"


def test_runtime_path_environment_variables_are_rejected(monkeypatch):
    monkeypatch.setenv("NORTHSTAR_STORAGE_DIR", "runtime/storage")

    with pytest.raises(ValueError, match="不再接受运行输出路径环境变量"):
        Settings(_env_file=None)


def test_runtime_path_variables_in_dotenv_are_rejected(tmp_path):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("NORTHSTAR_REPORTS_DIR=legacy-reports\n", encoding="utf-8")

    with pytest.raises(ValueError, match="NORTHSTAR_REPORTS_DIR"):
        Settings(_env_file=dotenv_path)


def test_runtime_paths_are_only_disabled_environment_fields():
    assert ENV_DISABLED_FIELDS == {"storage_dir", "downloads_dir", "reports_dir", "log_dir"}
    assert LEGACY_RUNTIME_PATH_ENV_VARS == {
        "NORTHSTAR_STORAGE_DIR",
        "NORTHSTAR_DOWNLOADS_DIR",
        "NORTHSTAR_REPORTS_DIR",
        "NORTHSTAR_LOG_DIR",
    }


@pytest.mark.parametrize("field", ["paper_state_path", "ctp_sim_state_path"])
def test_local_state_path_must_remain_inside_storage_dir(tmp_path, field):
    _write_runtime_config(tmp_path, storage_dir="runtime/storage")
    with pytest.raises(ValidationError, match="必须位于 runtime.storage_dir 内"):
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

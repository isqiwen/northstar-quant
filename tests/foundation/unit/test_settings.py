"""全局设置的失败关闭解析测试。"""

from pathlib import Path

import pytest
from pydantic import ValidationError
import yaml

from northstar_quant.foundation.config.environment_file import ActiveEnvironmentFileError
from northstar_quant.foundation.config.settings import (
    ENV_DISABLED_FIELDS,
    LEGACY_RUNTIME_PATH_ENV_VARS,
    Settings,
    active_environment_file_keys,
    load_settings,
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
                },
                "logging": {
                    "level": "INFO",
                    "console_enabled": True,
                    "file_enabled": True,
                    "filename": "northstar.log",
                    "when": "midnight",
                    "interval": 1,
                    "backup_count": 14,
                    "encoding": "utf-8",
                    "format": "%(asctime)s | %(levelname)s | %(message)s",
                },
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


def test_core_database_rejects_sqlite_url():
    with pytest.raises(ValidationError, match="核心运行数据库"):
        Settings(
            _env_file=None,
            database_url="sqlite:///storage/northstar.db",
        )


def test_dashboard_host_only_allows_ipv4_loopback():
    settings = Settings(_env_file=None, dashboard_host=" 127.0.0.1 ")

    assert settings.dashboard_host == "127.0.0.1"


@pytest.mark.parametrize("host", ["0.0.0.0", "::1", "localhost", "192.168.1.10"])
def test_dashboard_host_rejects_non_ipv4_loopback_values(host):
    with pytest.raises(ValidationError, match="NORTHSTAR_DASHBOARD_HOST"):
        Settings(_env_file=None, dashboard_host=host)


def test_empty_optional_ntfy_environment_values_are_ignored(monkeypatch):
    monkeypatch.setenv("NORTHSTAR_NTFY_BASE_URL", "")
    monkeypatch.setenv("NORTHSTAR_NTFY_TOPIC", "")
    monkeypatch.setenv("NORTHSTAR_NTFY_TOKEN", "")

    settings = Settings(_env_file=None)

    assert settings.ntfy_base_url is None
    assert settings.ntfy_topic is None
    assert settings.ntfy_token is None


def test_ntfy_configuration_normalizes_private_service_values():
    settings = Settings(
        _env_file=None,
        alert_mode="ntfy",
        ntfy_base_url="https://ntfy.example.test/",
        ntfy_topic="northstar_alerts",
        ntfy_token=" tk_12345678901234567890123456789 ",
    )

    assert settings.alert_mode == "ntfy"
    assert settings.ntfy_base_url == "https://ntfy.example.test"
    assert settings.ntfy_topic == "northstar_alerts"
    assert settings.ntfy_token == "tk_12345678901234567890123456789"
    assert settings.ntfy_timeout_seconds == 10.0


def test_ntfy_timeout_must_be_positive():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ntfy_timeout_seconds=0)


def test_ntfy_rejects_non_ntfy_access_token():
    with pytest.raises(ValidationError, match="32 位 tk_ 访问令牌"):
        Settings(_env_file=None, ntfy_token="not-a-token")  # secret-scan: allow; reason: disposable test fixture


@pytest.mark.parametrize(
    "base_url",
    [
        "http://ntfy.example.test",
        "ftp://ntfy.example.test",
        "https://user:password@ntfy.example.test",  # secret-scan: allow; reason: disposable test fixture
        "https://ntfy.example.test/?token=must-not-be-here",  # secret-scan: allow; reason: disposable test fixture
        "https://ntfy.sh",
        "https://demo.ntfy.sh",
    ],
)
def test_ntfy_rejects_insecure_public_or_credential_bearing_service_urls(base_url):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ntfy_base_url=base_url)


def test_ntfy_allows_loopback_http_for_local_development():
    settings = Settings(_env_file=None, ntfy_base_url="http://127.0.0.1:2586")

    assert settings.ntfy_base_url == "http://127.0.0.1:2586"


@pytest.mark.parametrize("topic", ["../orders", "order/topic", "contains space"])
def test_ntfy_rejects_unsafe_topic(topic):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ntfy_topic=topic)


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
    assert derived.local_tools_dir == tmp_path / "runtime/storage/local-tools"
    assert derived.reports_dir == tmp_path / "runtime/reports"
    assert derived.log_dir == tmp_path / "runtime/logs"
    assert "paper_state_path" not in Settings.model_fields
    assert "ctp_sim_state_path" not in Settings.model_fields
    assert explicit.storage_dir == tmp_path / "test-runtime/storage"
    assert explicit.downloads_dir == tmp_path / "runtime/download-cache"
    assert explicit.local_tools_dir == tmp_path / "test-runtime/storage/local-tools"
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


def test_load_settings_requires_a_complete_active_environment_file(tmp_path, monkeypatch):
    _write_runtime_config(tmp_path)
    monkeypatch.setenv("NORTHSTAR_PROJECT_ROOT", str(tmp_path))

    with pytest.raises(ActiveEnvironmentFileError, match="请先复制 .env.example 为 .env"):
        load_settings()

    (tmp_path / ".env").write_text("NORTHSTAR_ENV=dev\n", encoding="utf-8")
    with pytest.raises(ActiveEnvironmentFileError, match="缺少字段"):
        load_settings()


def test_load_settings_accepts_complete_active_environment_file(tmp_path, monkeypatch):
    _write_runtime_config(tmp_path)
    monkeypatch.setenv("NORTHSTAR_PROJECT_ROOT", str(tmp_path))
    (tmp_path / ".env").write_text(
        "\n".join(f"{key}=" for key in sorted(active_environment_file_keys())) + "\n",
        encoding="utf-8",
    )

    settings = load_settings()

    assert settings.project_root == tmp_path


def test_load_settings_explicit_release_root_wins_over_process_root(tmp_path, monkeypatch):
    installed_package_root = tmp_path / "installed-package-root"
    release_root = tmp_path / "release-20260823"
    _write_runtime_config(installed_package_root)
    _write_runtime_config(release_root)
    for root in (installed_package_root, release_root):
        (root / ".env").write_text(
            "\n".join(f"{key}=" for key in sorted(active_environment_file_keys())) + "\n",
            encoding="utf-8",
        )
    monkeypatch.setenv("NORTHSTAR_PROJECT_ROOT", str(installed_package_root))

    settings = load_settings(project_root=release_root)

    assert settings.project_root == release_root


def test_explicit_runtime_paths_do_not_bypass_active_config_validation(tmp_path):
    with pytest.raises(ValueError, match="活动应用配置不存在"):
        Settings(
            _env_file=None,
            project_root=tmp_path,
            storage_dir="explicit/storage",
            downloads_dir="explicit/downloads",
            reports_dir="explicit/reports",
            log_dir="explicit/logs",
        )

    _write_runtime_config(tmp_path)
    (tmp_path / "configs" / "app.local.yaml").write_text("runtime: {}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="已废弃的 configs/app.local.yaml"):
        Settings(
            _env_file=None,
            project_root=tmp_path,
            storage_dir="explicit/storage",
            downloads_dir="explicit/downloads",
            reports_dir="explicit/reports",
            log_dir="explicit/logs",
        )


@pytest.mark.parametrize("field", ["paper_state_path", "ctp_sim_state_path"])
def test_retired_simulator_state_path_settings_are_rejected(tmp_path, field):
    _write_runtime_config(tmp_path, storage_dir="runtime/storage")
    with pytest.raises(ValidationError, match="已移除模拟柜台 JSON 状态配置"):
        Settings(
            _env_file=None,
            project_root=tmp_path,
            storage_dir="runtime/storage",
            **{field: "runtime/other-state.json"},
        )


@pytest.mark.parametrize(
    "retired_key",
    ("NORTHSTAR_PAPER_STATE_PATH", "NORTHSTAR_CTP_SIM_STATE_PATH"),
)
def test_active_environment_file_rejects_retired_simulator_state_paths(
    tmp_path,
    monkeypatch,
    retired_key,
):
    _write_runtime_config(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                *(f"{key}=" for key in sorted(active_environment_file_keys())),
                f"{retired_key}=storage/legacy-state.json",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NORTHSTAR_PROJECT_ROOT", str(tmp_path))

    with pytest.raises(ActiveEnvironmentFileError, match=f"已废弃字段：{retired_key}"):
        load_settings()


@pytest.mark.parametrize("field", ["paper_account", "ctp_sim_account"])
@pytest.mark.parametrize("account", ["../other-account", "paper/account", "paper account"])
def test_local_state_account_rejects_path_like_or_ambiguous_identifiers(field, account):
    with pytest.raises(ValidationError, match="Paper/CTP 模拟账户"):
        Settings(_env_file=None, **{field: account})

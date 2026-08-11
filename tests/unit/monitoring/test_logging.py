import json
from pathlib import Path

import pytest
import yaml

from northstar_quant.config.settings import active_environment_file_keys, get_settings
from northstar_quant.logging_.logger import _load_logging_config, _rotation_namer, get_logger, setup_logging


_DEFAULT_LOGGING = {
    "level": "INFO",
    "console_enabled": True,
    "file_enabled": True,
    "filename": "northstar.log",
    "when": "midnight",
    "interval": 1,
    "backup_count": 14,
    "encoding": "utf-8",
    "format": "%(asctime)s | %(levelname)-8s | %(filename)s:%(lineno)d | %(message)s",
}


def _write_app_config(
    project_root: Path,
    *,
    log_dir: str = "logs",
    logging: dict[str, object] | None = None,
) -> None:
    config_dir = project_root / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "app.yaml").write_text(
        yaml.safe_dump(
            {
                "runtime": {
                    "storage_dir": "storage",
                    "downloads_dir": None,
                    "reports_dir": "reports",
                    "log_dir": log_dir,
                },
                "logging": {**_DEFAULT_LOGGING, **(logging or {})},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (project_root / ".env").write_text(
        "\n".join(f"{key}=" for key in sorted(active_environment_file_keys())) + "\n",
        encoding="utf-8",
    )


def test_load_logging_config_from_app_yaml(tmp_path, monkeypatch):
    _write_app_config(
        tmp_path,
        log_dir="logs/custom",
        logging={
            "level": "DEBUG",
            "console_enabled": False,
            "file_enabled": True,
            "filename": "app.log",
            "backup_count": 3,
        },
    )

    monkeypatch.setenv("NORTHSTAR_PROJECT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    try:
        config = _load_logging_config()
    finally:
        get_settings.cache_clear()

    assert config["level"] == "DEBUG"
    assert config["console_enabled"] is False
    assert config["file_enabled"] is True
    assert config["directory"] == str(tmp_path / "logs/custom")
    assert config["filename"] == "app.log"
    assert config["backup_count"] == 3


def test_setup_logging_creates_log_file(tmp_path, monkeypatch):
    _write_app_config(
        tmp_path,
        logging={
            "console_enabled": False,
            "file_enabled": True,
            "filename": "northstar.log",
        },
    )

    monkeypatch.setenv("NORTHSTAR_PROJECT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    try:
        setup_logging()
        assert (tmp_path / "logs/northstar.log").exists()
    finally:
        get_settings.cache_clear()


def test_rotation_namer_uses_date_before_log_suffix():
    rotated = _rotation_namer("logs/northstar.log.2026-04-04")

    assert Path(rotated) == Path("logs/northstar-2026-04-04.log")


def test_setup_logging_writes_json_lines_with_top_level_fields(tmp_path, monkeypatch):
    _write_app_config(
        tmp_path,
        logging={
            "console_enabled": False,
            "file_enabled": True,
            "filename": "northstar.log",
        },
    )

    monkeypatch.setenv("NORTHSTAR_PROJECT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    try:
        setup_logging()
        get_logger("test_logger", command="init-db", strategy="momentum", symbol="RB2405").info("log test")

        line = (tmp_path / "logs/northstar.log").read_text(encoding="utf-8").strip().splitlines()[-1]
        payload = json.loads(line)
    finally:
        get_settings.cache_clear()

    assert line.startswith('{"timestamp":')
    assert '"level":' in line
    assert line.index('"file":') < line.index('"line":') < line.index('"msg":') < line.index('"command":')
    assert payload["msg"] == "log test"
    assert payload["file"] == "test_logging.py"
    assert payload["line"] > 0
    assert payload["command"] == "init-db"
    assert payload["strategy"] == "momentum"
    assert payload["symbol"] == "RB2405"
    assert "logger" not in payload
    assert "context" not in payload
    assert "message" not in payload


def test_runtime_log_dir_preserves_yaml_rotation_rules(tmp_path, monkeypatch):
    log_dir = "runtime/audit-logs"
    _write_app_config(
        tmp_path,
        log_dir=log_dir,
        logging={
            "console_enabled": False,
            "file_enabled": True,
            "filename": "audit.log",
            "when": "H",
            "interval": 6,
            "backup_count": 9,
        },
    )

    monkeypatch.setenv("NORTHSTAR_PROJECT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    try:
        config = _load_logging_config()
        setup_logging()
    finally:
        get_settings.cache_clear()

    assert config["directory"] == str(tmp_path / log_dir)
    assert config["filename"] == "audit.log"
    assert config["when"] == "H"
    assert config["interval"] == 6
    assert config["backup_count"] == 9
    assert (tmp_path / log_dir / "audit.log").exists()


def test_logging_unknown_key_is_rejected(tmp_path, monkeypatch):
    _write_app_config(
        tmp_path,
        logging={"directory": "legacy-logs"},
    )
    monkeypatch.setenv("NORTHSTAR_PROJECT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    try:
        with pytest.raises(ValueError, match="logging 必须完整包含且只包含"):
            _load_logging_config()
    finally:
        get_settings.cache_clear()


def test_default_logging_rules_use_runtime_log_dir(tmp_path, monkeypatch):
    _write_app_config(tmp_path, log_dir="runtime/default-logs")
    monkeypatch.setenv("NORTHSTAR_PROJECT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    try:
        config = _load_logging_config()
    finally:
        get_settings.cache_clear()

    assert config["directory"] == str(tmp_path / "runtime/default-logs")
    assert config["filename"] == "northstar.log"
    assert config["backup_count"] == 14

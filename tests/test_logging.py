import json
from pathlib import Path

from northstar_quant.config.settings import get_settings
from northstar_quant.logging_.logger import _load_logging_config, _rotation_namer, get_logger, setup_logging


def test_load_logging_config_from_app_yaml(tmp_path, monkeypatch):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "app.yaml").write_text(
        "\n".join(
            [
                "logging:",
                "  level: DEBUG",
                "  console_enabled: false",
                "  file_enabled: true",
                "  directory: storage/custom_logs",
                "  filename: app.log",
                "  backup_count: 3",
            ]
        ),
        encoding="utf-8",
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
    assert config["directory"] == "storage/custom_logs"
    assert config["filename"] == "app.log"
    assert config["backup_count"] == 3


def test_setup_logging_creates_log_file(tmp_path, monkeypatch):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "app.yaml").write_text(
        "\n".join(
            [
                "logging:",
                "  console_enabled: false",
                "  file_enabled: true",
                "  directory: storage/logs",
                "  filename: northstar.log",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("NORTHSTAR_PROJECT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    try:
        setup_logging()
        assert (tmp_path / "storage/logs/northstar.log").exists()
    finally:
        get_settings.cache_clear()


def test_rotation_namer_uses_date_before_log_suffix():
    rotated = _rotation_namer("storage/logs/northstar.log.2026-04-04")

    assert rotated == "storage\\logs\\northstar-2026-04-04.log"


def test_setup_logging_writes_json_lines_with_top_level_fields(tmp_path, monkeypatch):
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    (config_dir / "app.yaml").write_text(
        "\n".join(
            [
                "logging:",
                "  console_enabled: false",
                "  file_enabled: true",
                "  directory: storage/logs",
                "  filename: northstar.log",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("NORTHSTAR_PROJECT_ROOT", str(tmp_path))
    get_settings.cache_clear()

    try:
        setup_logging()
        get_logger("test_logger", command="init-db", strategy="momentum", symbol="SPY").info("log test")

        line = (tmp_path / "storage/logs/northstar.log").read_text(encoding="utf-8").strip().splitlines()[-1]
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
    assert payload["symbol"] == "SPY"
    assert "logger" not in payload
    assert "context" not in payload
    assert "message" not in payload

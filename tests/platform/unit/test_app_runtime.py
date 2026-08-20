"""唯一活动应用 YAML 配置的严格加载测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from northstar_quant.platform.config.app_runtime import (
    AppConfigError,
    load_app_config,
    load_app_runtime_paths,
)


def _write_yaml(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _runtime_block(
    *,
    storage_dir: str = "storage",
    downloads_dir: str | None = None,
    reports_dir: str = "reports",
    log_dir: str = "logs",
) -> dict[str, str | None]:
    return {
        "storage_dir": storage_dir,
        "downloads_dir": downloads_dir,
        "reports_dir": reports_dir,
        "log_dir": log_dir,
    }


def _logging_block(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "level": "INFO",
        "console_enabled": True,
        "file_enabled": True,
        "filename": "northstar.log",
        "when": "midnight",
        "interval": 1,
        "backup_count": 14,
        "encoding": "utf-8",
        "format": "%(asctime)s | %(levelname)s | %(message)s",
    }
    values.update(overrides)
    return values


def _active_payload(
    *,
    runtime: dict[str, object] | None = None,
    logging: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "runtime": _runtime_block() if runtime is None else runtime,
        "logging": _logging_block() if logging is None else logging,
    }


def _write_active_config(
    project_root: Path,
    *,
    runtime: dict[str, object] | None = None,
    logging: dict[str, object] | None = None,
) -> Path:
    path = project_root / "configs" / "app.yaml"
    _write_yaml(path, _active_payload(runtime=runtime, logging=logging))
    return path


def test_loads_the_only_complete_active_configuration(tmp_path: Path) -> None:
    _write_active_config(
        tmp_path,
        runtime=_runtime_block(
            storage_dir="runtime/storage",
            downloads_dir="runtime/downloads",
            reports_dir="runtime/reports",
            log_dir="runtime/logs",
        ),
        logging=_logging_block(level="debug", filename="audit.log", when="W2"),
    )

    config = load_app_config(tmp_path)
    runtime = load_app_runtime_paths(tmp_path)

    assert config.runtime == runtime
    assert runtime.storage_dir == tmp_path / "runtime/storage"
    assert runtime.downloads_dir == tmp_path / "runtime/downloads"
    assert runtime.reports_dir == tmp_path / "runtime/reports"
    assert runtime.log_dir == tmp_path / "runtime/logs"
    assert config.logging.level == "DEBUG"
    assert config.logging.filename == "audit.log"
    assert config.logging.when == "W2"


def test_missing_active_configuration_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(AppConfigError, match="活动应用配置不存在.*app.example.yaml"):
        load_app_config(tmp_path)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"runtime": _runtime_block()}, "顶层配置段"),
        ({"runtime": _runtime_block(), "logging": _logging_block(), "extra": {}}, "顶层配置段"),
        (_active_payload(runtime={"storage_dir": "storage"}), "runtime 必须完整包含且只包含"),
        (
            _active_payload(runtime={**_runtime_block(), "extra": "value"}),
            "runtime 必须完整包含且只包含",
        ),
        (_active_payload(logging={"level": "INFO"}), "logging 必须完整包含且只包含"),
        (
            _active_payload(logging={**_logging_block(), "directory": "logs"}),
            "logging 必须完整包含且只包含",
        ),
    ],
)
def test_active_configuration_requires_the_complete_schema(
    tmp_path: Path,
    payload: dict[str, object],
    message: str,
) -> None:
    _write_yaml(tmp_path / "configs" / "app.yaml", payload)

    with pytest.raises(AppConfigError, match=message):
        load_app_config(tmp_path)


@pytest.mark.parametrize(
    ("runtime", "message"),
    [
        (_runtime_block(reports_dir=""), "runtime.reports_dir"),
        (_runtime_block(log_dir=None), "runtime.log_dir"),
        (_runtime_block(storage_dir="a/.."), "不能指向项目根目录"),
        (_runtime_block(downloads_dir="cache/.."), "不能指向项目根目录"),
    ],
)
def test_runtime_values_are_strict_and_cannot_target_the_project_root(
    tmp_path: Path,
    runtime: dict[str, object],
    message: str,
) -> None:
    _write_active_config(tmp_path, runtime=runtime)

    with pytest.raises(AppConfigError, match=message):
        load_app_config(tmp_path)


@pytest.mark.parametrize(
    ("logging", "message"),
    [
        (_logging_block(level="verbose"), "logging.level"),
        (_logging_block(console_enabled="true"), "logging.console_enabled"),
        (_logging_block(filename="../northstar.log"), "logging.filename"),
        (_logging_block(when="weekly"), "logging.when"),
        (_logging_block(interval=0), "logging.interval"),
        (_logging_block(backup_count=-1), "logging.backup_count"),
        (_logging_block(encoding="not-a-real-codec"), "logging.encoding"),
    ],
)
def test_logging_values_are_strict_and_safe(
    tmp_path: Path,
    logging: dict[str, object],
    message: str,
) -> None:
    _write_active_config(tmp_path, logging=logging)

    with pytest.raises(AppConfigError, match=message):
        load_app_config(tmp_path)


def test_downloads_null_is_preserved_for_settings_to_derive(tmp_path: Path) -> None:
    _write_active_config(
        tmp_path,
        runtime=_runtime_block(storage_dir="runtime/storage", downloads_dir=None),
    )

    config = load_app_config(tmp_path)

    assert config.runtime.storage_dir == tmp_path / "runtime/storage"
    assert config.runtime.downloads_dir is None


def test_legacy_local_configuration_is_always_rejected(tmp_path: Path) -> None:
    _write_active_config(tmp_path)
    _write_yaml(tmp_path / "configs" / "app.local.yaml", {"runtime": _runtime_block()})

    with pytest.raises(AppConfigError, match="已废弃的 configs/app.local.yaml"):
        load_app_config(tmp_path)


def test_broken_legacy_local_configuration_symlink_is_rejected(tmp_path: Path) -> None:
    _write_active_config(tmp_path)
    local_path = tmp_path / "configs" / "app.local.yaml"
    try:
        local_path.symlink_to(tmp_path / "missing-app-local.yaml")
    except OSError:
        pytest.skip("当前文件系统不允许创建测试符号链接")

    with pytest.raises(AppConfigError, match="已废弃的 configs/app.local.yaml"):
        load_app_config(tmp_path)

"""应用运行输出路径 YAML 配置测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from northstar_quant.config.app_runtime import (
    AppRuntimeConfigError,
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


def test_runtime_paths_load_from_base_then_full_local_overlay(tmp_path: Path):
    _write_yaml(
        tmp_path / "configs" / "app.yaml",
        {"runtime": _runtime_block(storage_dir="base-storage")},
    )
    _write_yaml(
        tmp_path / "configs" / "app.local.yaml",
        {
            "runtime": _runtime_block(
                storage_dir="local-storage",
                downloads_dir="local-downloads",
                reports_dir="local-reports",
                log_dir="local-logs",
            )
        },
    )

    runtime = load_app_runtime_paths(tmp_path)

    assert runtime.storage_dir == tmp_path / "local-storage"
    assert runtime.downloads_dir == tmp_path / "local-downloads"
    assert runtime.reports_dir == tmp_path / "local-reports"
    assert runtime.log_dir == tmp_path / "local-logs"


@pytest.mark.parametrize(
    "local_payload",
    [
        {"runtime": _runtime_block(), "logging": {}},
        {"runtime": {"storage_dir": "storage"}},
        {"runtime": {**_runtime_block(), "unexpected": "value"}},
    ],
)
def test_local_runtime_overlay_must_be_runtime_only_and_complete(
    tmp_path: Path,
    local_payload: dict[str, object],
):
    _write_yaml(tmp_path / "configs" / "app.yaml", {"runtime": _runtime_block()})
    _write_yaml(tmp_path / "configs" / "app.local.yaml", local_payload)

    with pytest.raises(AppRuntimeConfigError, match="只能包含完整的 runtime|完整包含且只包含"):
        load_app_runtime_paths(tmp_path)


@pytest.mark.parametrize(
    "runtime",
    [
        {"storage_dir": "storage"},
        {**_runtime_block(), "unexpected": "value"},
        _runtime_block(reports_dir=""),
        _runtime_block(log_dir=None),
        _runtime_block(storage_dir="a/.."),
        _runtime_block(downloads_dir="cache/.."),
    ],
)
def test_base_runtime_block_is_strict(tmp_path: Path, runtime: dict[str, object]):
    _write_yaml(tmp_path / "configs" / "app.yaml", {"runtime": runtime})

    with pytest.raises(
        AppRuntimeConfigError,
        match="完整包含且只包含|必须是非空路径字符串|不能指向项目根目录",
    ):
        load_app_runtime_paths(tmp_path)


def test_downloads_null_is_preserved_for_settings_to_derive(tmp_path: Path):
    _write_yaml(
        tmp_path / "configs" / "app.yaml",
        {"runtime": _runtime_block(storage_dir="runtime/storage", downloads_dir=None)},
    )

    runtime = load_app_runtime_paths(tmp_path)

    assert runtime.storage_dir == tmp_path / "runtime/storage"
    assert runtime.downloads_dir is None


def test_broken_local_runtime_symlink_is_rejected(tmp_path: Path):
    _write_yaml(tmp_path / "configs" / "app.yaml", {"runtime": _runtime_block()})
    local_path = tmp_path / "configs" / "app.local.yaml"
    try:
        local_path.symlink_to(tmp_path / "missing-app-local.yaml")
    except OSError:
        pytest.skip("当前文件系统不允许创建测试符号链接")

    with pytest.raises(AppRuntimeConfigError, match="断开的符号链接"):
        load_app_runtime_paths(tmp_path)

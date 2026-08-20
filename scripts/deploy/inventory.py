#!/usr/bin/env python3
"""跨平台读取并校验非机密 Linux 部署清单。

部署清单只描述目标主机和运行时路径，不能承载数据库密码、令牌或其他机密。
机密仍只可通过受权限保护的活动 ``.env`` 在显式上传时传递。
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final


class InventoryError(ValueError):
    """部署清单不符合安全约束。"""


_KEY_PATTERN: Final = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SAFE_NAME_PATTERN: Final = re.compile(r"^[A-Za-z0-9._-]+$")
_DEPLOY_HOST_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@:\-\[\]]*$")
_PYTHON_VERSION_PATTERN: Final = re.compile(r"^3\.(?:1[1-9]|[2-9][0-9]*)$")
_NTFY_HOST_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$")
_EMAIL_PATTERN: Final = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_NTFY_IMAGE_PATTERN: Final = re.compile(r"^binwiederhier/ntfy:v\d+\.\d+\.\d+$")
_NTFY_CADDY_IMAGE_PATTERN: Final = re.compile(r"^caddy:\d+\.\d+\.\d+-alpine$")
_NTFY_CACHE_DURATION_PATTERN: Final = re.compile(r"^[1-9]\d*(?:s|m|h)$")

_ALLOWED_KEYS: Final = frozenset(
    {
        "DEPLOY_HOST",
        "APP_NAME",
        "SERVICE_USER",
        "SERVICE_HOME",
        "SYSTEMD_SERVICE_NAME",
        "SERVICE_MODE",
        "PYTHON_VERSION",
        "KEEP_RELEASES",
        "REMOTE_TMP",
        "RUNTIME_STORAGE_DIR",
        "RUNTIME_DOWNLOADS_DIR",
        "RUNTIME_REPORTS_DIR",
        "RUNTIME_LOG_DIR",
        "RUNTIME_CACHE_DIR",
        "RUNTIME_MATPLOTLIB_DIR",
        "DASHBOARD_DEPLOY_ENABLED",
        "NTFY_DEPLOY_ENABLED",
        "NTFY_PUBLIC_HOST",
        "NTFY_ACME_EMAIL",
        "NTFY_IMAGE",
        "NTFY_CADDY_IMAGE",
        "NTFY_CONFIG_DIR",
        "NTFY_DATA_DIR",
        "NTFY_CACHE_DURATION",
    }
)

_DEFAULTS: Final = {
    "APP_NAME": "northstar-quant",
    "SERVICE_USER": "northstar",
    "SERVICE_HOME": "/srv/northstar",
    "SYSTEMD_SERVICE_NAME": "northstar-quant",
    "SERVICE_MODE": "health",
    "PYTHON_VERSION": "3.12",
    "KEEP_RELEASES": "5",
    "REMOTE_TMP": "/tmp",
    "DASHBOARD_DEPLOY_ENABLED": "0",
    "NTFY_DEPLOY_ENABLED": "0",
    "NTFY_IMAGE": "binwiederhier/ntfy:v2.27.0",
    "NTFY_CADDY_IMAGE": "caddy:2.10.2-alpine",
    "NTFY_CONFIG_DIR": "/etc/northstar-ntfy",
    "NTFY_DATA_DIR": "/var/lib/northstar-ntfy",
    "NTFY_CACHE_DURATION": "24h",
}

_RUNTIME_PATH_KEYS: Final = (
    "RUNTIME_STORAGE_DIR",
    "RUNTIME_DOWNLOADS_DIR",
    "RUNTIME_REPORTS_DIR",
    "RUNTIME_LOG_DIR",
    "RUNTIME_CACHE_DIR",
    "RUNTIME_MATPLOTLIB_DIR",
)


def _read_key_value_file(path: Path) -> dict[str, str]:
    """读取受限的 ``KEY=VALUE`` 文件，不执行 shell 表达式。"""

    if not path.is_file():
        raise InventoryError(f"未找到部署清单：{path}")

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise InventoryError(f"部署清单第 {line_number} 行不是 KEY=VALUE：{raw_line}")

        key, raw_value = line.split("=", 1)
        key = key.strip()
        value = raw_value.strip()
        if not _KEY_PATTERN.fullmatch(key):
            raise InventoryError(f"部署清单第 {line_number} 行字段名无效：{key}")
        if key not in _ALLOWED_KEYS:
            raise InventoryError(f"部署清单包含不支持或机密字段：{key}")
        if key in values:
            raise InventoryError(f"部署清单重复定义字段：{key}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if "\x00" in value:
            raise InventoryError(f"部署清单第 {line_number} 行包含空字节：{key}")
        values[key] = value
    return values


def _require_safe_name(label: str, value: str) -> str:
    if not _SAFE_NAME_PATTERN.fullmatch(value):
        raise InventoryError(f"{label} 只能包含字母、数字、点、下划线和连字符。")
    return value


def _require_boolean(label: str, value: str) -> str:
    if value not in {"0", "1"}:
        raise InventoryError(f"{label} 只能是 0 或 1，当前值为：{value}")
    return value


def _require_linux_path(
    label: str,
    value: str,
    *,
    allowed_prefixes: tuple[str, ...],
    allow_empty: bool = False,
) -> str:
    if not value and allow_empty:
        return value
    if not value.startswith("/") or "/../" in f"/{value}/" or "/./" in f"/{value}/":
        raise InventoryError(f"{label} 必须是没有 . 或 .. 路径段的 Linux 绝对路径。")
    if not any(value == prefix.rstrip("/") or value.startswith(prefix) for prefix in allowed_prefixes):
        allowed = "、".join(prefix.rstrip("/") for prefix in allowed_prefixes)
        raise InventoryError(f"{label} 必须位于 {allowed} 下。")
    if not re.fullmatch(r"[A-Za-z0-9/._-]+", value):
        raise InventoryError(f"{label} 包含不允许的路径字符。")
    return value


@dataclass(frozen=True)
class DeploymentInventory:
    """经验证的、可安全转交给 Linux 后端的非机密部署参数。"""

    source: Path
    deploy_host: str
    app_name: str
    service_user: str
    service_home: str
    systemd_service_name: str
    service_mode: str
    python_version: str
    keep_releases: int
    remote_tmp: str
    dashboard_deploy_enabled: bool
    ntfy_deploy_enabled: bool
    values: dict[str, str]

    @property
    def app_root(self) -> str:
        return f"{self.service_home}/{self.app_name}"

    def legacy_environment(self) -> dict[str, str]:
        """返回可传给既有 Linux 部署后端的非机密环境变量。"""

        return dict(self.values)


def load_inventory(path: Path) -> DeploymentInventory:
    """加载部署清单，并在本地提前拒绝不安全的目标参数。"""

    supplied = _read_key_value_file(path)
    values = {**_DEFAULTS, **supplied}

    deploy_host = values.get("DEPLOY_HOST", "")
    if not _DEPLOY_HOST_PATTERN.fullmatch(deploy_host):
        raise InventoryError("DEPLOY_HOST 不能为空，且只能是 SSH 主机别名或 user@host。")

    app_name = _require_safe_name("APP_NAME", values["APP_NAME"])
    service_user = _require_safe_name("SERVICE_USER", values["SERVICE_USER"])
    systemd_service_name = _require_safe_name(
        "SYSTEMD_SERVICE_NAME", values["SYSTEMD_SERVICE_NAME"]
    )
    service_home = _require_linux_path(
        "SERVICE_HOME", values["SERVICE_HOME"], allowed_prefixes=("/srv/",)
    )
    if not service_home.startswith("/srv/"):
        raise InventoryError("SERVICE_HOME 必须位于 /srv 下。")
    remote_tmp = _require_linux_path(
        "REMOTE_TMP", values["REMOTE_TMP"], allowed_prefixes=("/tmp/", "/var/tmp/")
    )

    service_mode = values["SERVICE_MODE"]
    if service_mode not in {"health", "scheduler"}:
        raise InventoryError("SERVICE_MODE 只能是 health 或 scheduler。")
    python_version = values["PYTHON_VERSION"]
    if not _PYTHON_VERSION_PATTERN.fullmatch(python_version):
        raise InventoryError("PYTHON_VERSION 必须是受支持的 Python 3.11+ 小版本号。")
    try:
        keep_releases = int(values["KEEP_RELEASES"])
    except ValueError as exc:
        raise InventoryError("KEEP_RELEASES 必须是整数。") from exc
    if keep_releases < 2:
        raise InventoryError("KEEP_RELEASES 至少为 2。")

    for key in _RUNTIME_PATH_KEYS:
        _require_linux_path(
            key,
            values.get(key, ""),
            allowed_prefixes=("/srv/", "/var/lib/", "/var/cache/", "/var/log/", "/mnt/", "/data/"),
            allow_empty=True,
        )

    dashboard_enabled = _require_boolean(
        "DASHBOARD_DEPLOY_ENABLED", values["DASHBOARD_DEPLOY_ENABLED"]
    ) == "1"
    ntfy_enabled = _require_boolean("NTFY_DEPLOY_ENABLED", values["NTFY_DEPLOY_ENABLED"]) == "1"
    if ntfy_enabled:
        ntfy_public_host = values.get("NTFY_PUBLIC_HOST", "").lower()
        if (
            len(ntfy_public_host) > 253
            or not _NTFY_HOST_PATTERN.fullmatch(ntfy_public_host)
            or "." not in ntfy_public_host
            or any(segment in ntfy_public_host for segment in ("..", ".-", "-."))
        ):
            raise InventoryError(
                "NTFY_PUBLIC_HOST 必须是用于公开 HTTPS 服务的合法 FQDN，不能带协议、端口或路径。"
            )
        values["NTFY_PUBLIC_HOST"] = ntfy_public_host
        ntfy_email = values.get("NTFY_ACME_EMAIL", "")
        if not _EMAIL_PATTERN.fullmatch(ntfy_email) or any(
            character in ntfy_email for character in "\"';{}[]\\"
        ):
            raise InventoryError("NTFY_ACME_EMAIL 必须是有效的联系邮箱。")
        if not _NTFY_IMAGE_PATTERN.fullmatch(values["NTFY_IMAGE"]):
            raise InventoryError("NTFY_IMAGE 必须是明确的 binwiederhier/ntfy vX.Y.Z 标签。")
        if not _NTFY_CADDY_IMAGE_PATTERN.fullmatch(values["NTFY_CADDY_IMAGE"]):
            raise InventoryError("NTFY_CADDY_IMAGE 必须是明确的 caddy X.Y.Z-alpine 标签。")
        _require_linux_path(
            "NTFY_CONFIG_DIR", values["NTFY_CONFIG_DIR"], allowed_prefixes=("/etc/",)
        )
        ntfy_data_dir = _require_linux_path(
            "NTFY_DATA_DIR",
            values["NTFY_DATA_DIR"],
            allowed_prefixes=("/var/lib/", "/srv/", "/mnt/", "/data/"),
        )
        if any(segment in f"/{ntfy_data_dir}/" for segment in ("/releases/", "/current/")):
            raise InventoryError("NTFY_DATA_DIR 不能位于 releases 或 current 路径段。")
        if not _NTFY_CACHE_DURATION_PATTERN.fullmatch(values["NTFY_CACHE_DURATION"]):
            raise InventoryError("NTFY_CACHE_DURATION 必须是正整数加 s、m 或 h，例如 24h。")

    return DeploymentInventory(
        source=path.resolve(),
        deploy_host=deploy_host,
        app_name=app_name,
        service_user=service_user,
        service_home=service_home,
        systemd_service_name=systemd_service_name,
        service_mode=service_mode,
        python_version=python_version,
        keep_releases=keep_releases,
        remote_tmp=remote_tmp,
        dashboard_deploy_enabled=dashboard_enabled,
        ntfy_deploy_enabled=ntfy_enabled,
        values=values,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="读取并校验 Northstar Quant 的 Linux 部署清单。")
    parser.add_argument(
        "--inventory",
        type=Path,
        required=True,
        help="非机密部署清单（通常为 deploy.env）。",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        inventory = load_inventory(args.inventory)
    except InventoryError as exc:
        print(f"部署清单校验失败：{exc}")
        return 1

    print("部署清单校验通过")
    print(f"host={inventory.deploy_host}")
    print(f"mode={inventory.service_mode}")
    print(f"app_root={inventory.app_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

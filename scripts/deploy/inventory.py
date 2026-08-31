#!/usr/bin/env python3
"""读取并校验 Linux x86_64 控制端的非机密部署清单。

部署清单只描述目标主机和运行时路径，不能承载数据库密码、令牌或其他机密。
机密仍只可通过受权限保护的活动 ``.env`` 在显式上传时传递。
"""

from __future__ import annotations

import argparse
import ipaddress
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

try:  # Allow direct-script execution as well as package imports.
    from .platform_support import PlatformSupportError, require_linux_x86_64
except ImportError:  # pragma: no cover - direct-script invocation path.
    from platform_support import PlatformSupportError, require_linux_x86_64


class InventoryError(ValueError):
    """部署清单不符合安全约束。"""


def _require_linux_x86_64_host() -> None:
    """Reject unsupported controllers before reading a deployment inventory."""

    try:
        require_linux_x86_64()
    except PlatformSupportError as exc:
        raise InventoryError(str(exc)) from exc


_KEY_PATTERN: Final = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SAFE_NAME_PATTERN: Final = re.compile(r"^[A-Za-z0-9._-]+$")
_SSH_USER_PATTERN: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,31}$")
_SSH_ALIAS_PATTERN: Final = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9])?$")
_DNS_LABEL_PATTERN: Final = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_PYTHON_VERSION_PATTERN: Final = re.compile(r"^3\.(?:1[1-9]|[2-9][0-9]*)$")
_NTFY_HOST_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$")
_EMAIL_PATTERN: Final = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_NTFY_IMAGE_PATTERN: Final = re.compile(r"^binwiederhier/ntfy:v\d+\.\d+\.\d+$")
_NTFY_CADDY_IMAGE_PATTERN: Final = re.compile(r"^caddy:\d+\.\d+\.\d+-alpine$")
_NTFY_CACHE_DURATION_PATTERN: Final = re.compile(r"^[1-9]\d*(?:s|m|h)$")
_NTFY_CONFIG_DIR: Final = "/etc/northstar-ntfy"
_NTFY_DATA_DIR: Final = "/var/lib/northstar-ntfy"

_ALLOWED_KEYS: Final = frozenset(
    {
        "DEPLOY_HOST",
        "APP_NAME",
        "SERVICE_USER",
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
    "SYSTEMD_SERVICE_NAME": "northstar-quant",
    "SERVICE_MODE": "health",
    "PYTHON_VERSION": "3.12",
    "KEEP_RELEASES": "5",
    "REMOTE_TMP": "/tmp",
    "DASHBOARD_DEPLOY_ENABLED": "0",
    "NTFY_DEPLOY_ENABLED": "0",
    "NTFY_IMAGE": "binwiederhier/ntfy:v2.27.0",
    "NTFY_CADDY_IMAGE": "caddy:2.10.2-alpine",
    "NTFY_CONFIG_DIR": _NTFY_CONFIG_DIR,
    "NTFY_DATA_DIR": _NTFY_DATA_DIR,
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
_RUNTIME_PARENT_DIRECTORIES: Final = (
    "/var/lib/northstar",
    "/var/cache/northstar",
    "/var/log/northstar",
    "/mnt/northstar-quant",
    "/data/northstar-quant",
)


@dataclass(frozen=True)
class LinuxProductionLayout:
    """The only supported Linux production filesystem boundary.

    Release code is intentionally separate from service-owned state.  The
    deployment controller never accepts these paths from ``deploy.env``:
    changing them would make the ownership and systemd sandbox contracts
    impossible to reason about.
    """

    app_root: str = "/opt/northstar"
    config_dir: str = "/etc/northstar"
    state_dir: str = "/var/lib/northstar"
    cache_dir: str = "/var/cache/northstar"
    log_dir: str = "/var/log/northstar"

    def environment_file(self, app_name: str) -> str:
        return f"{self.config_dir}/{app_name}.env"


_LINUX_PRODUCTION_LAYOUT: Final = LinuxProductionLayout()
_NORTHSTAR_PROTECTED_ROOTS: Final = (
    _LINUX_PRODUCTION_LAYOUT.app_root,
    _LINUX_PRODUCTION_LAYOUT.config_dir,
    _LINUX_PRODUCTION_LAYOUT.state_dir,
    _LINUX_PRODUCTION_LAYOUT.cache_dir,
    _LINUX_PRODUCTION_LAYOUT.log_dir,
)
_RUNTIME_RESERVED_LEAVES: Final = (
    f"{_LINUX_PRODUCTION_LAYOUT.cache_dir}/dashboard",
    f"{_LINUX_PRODUCTION_LAYOUT.cache_dir}/venv-build",
    f"{_LINUX_PRODUCTION_LAYOUT.cache_dir}/uv-cache",
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
    if (
        not value.startswith("/")
        or value.startswith("//")
        or "/../" in f"/{value}/"
        or "/./" in f"/{value}/"
    ):
        raise InventoryError(f"{label} 必须是没有 . 或 .. 路径段的 Linux 绝对路径。")
    if not re.fullmatch(r"[A-Za-z0-9/._-]+", value):
        raise InventoryError(f"{label} 包含不允许的路径字符。")
    normalized_value = posixpath.normpath(value)
    if value != normalized_value:
        raise InventoryError(
            f"{label} 必须是规范路径，不得包含重复斜杠、尾随斜杠或其他可规范化路径段。"
        )
    if not any(
        normalized_value == prefix.rstrip("/") or normalized_value.startswith(prefix)
        for prefix in allowed_prefixes
    ):
        allowed = "、".join(prefix.rstrip("/") for prefix in allowed_prefixes)
        raise InventoryError(f"{label} 必须位于 {allowed} 下。")
    return normalized_value


def _require_runtime_path(label: str, value: str) -> str:
    path = _require_linux_path(
        label,
        value,
        allowed_prefixes=tuple(f"{parent}/" for parent in _RUNTIME_PARENT_DIRECTORIES),
        allow_empty=True,
    )
    if path and posixpath.dirname(path) not in _RUNTIME_PARENT_DIRECTORIES:
        allowed = "、".join(_RUNTIME_PARENT_DIRECTORIES)
        raise InventoryError(
            f"{label} 必须是 {allowed} 下的直属专属叶子目录，不得嵌套于另一个服务可写目录。"
        )
    return path


def _paths_overlap(first_path: str, second_path: str) -> bool:
    return (
        first_path == second_path
        or first_path.startswith(f"{second_path}/")
        or second_path.startswith(f"{first_path}/")
    )


def _effective_runtime_paths(values: dict[str, str]) -> tuple[str, ...]:
    storage_dir = values["RUNTIME_STORAGE_DIR"] or (f"{_LINUX_PRODUCTION_LAYOUT.state_dir}/storage")
    downloads_dir = values["RUNTIME_DOWNLOADS_DIR"] or (
        f"{_LINUX_PRODUCTION_LAYOUT.state_dir}/downloads"
    )
    reports_dir = values["RUNTIME_REPORTS_DIR"] or (f"{_LINUX_PRODUCTION_LAYOUT.state_dir}/reports")
    log_dir = values["RUNTIME_LOG_DIR"] or f"{_LINUX_PRODUCTION_LAYOUT.log_dir}/app"
    cache_dir = values["RUNTIME_CACHE_DIR"] or (f"{_LINUX_PRODUCTION_LAYOUT.cache_dir}/runtime")
    matplotlib_dir = values["RUNTIME_MATPLOTLIB_DIR"] or (
        f"{_LINUX_PRODUCTION_LAYOUT.cache_dir}/matplotlib"
    )
    return (
        storage_dir,
        downloads_dir,
        reports_dir,
        log_dir,
        cache_dir,
        matplotlib_dir,
    )


def _require_non_overlapping_runtime_paths(values: dict[str, str]) -> tuple[str, ...]:
    effective_paths = _effective_runtime_paths(values)
    for first_index, first_path in enumerate(effective_paths):
        for second_index, second_path in enumerate(
            effective_paths[first_index + 1 :], first_index + 1
        ):
            if _paths_overlap(first_path, second_path):
                raise InventoryError(
                    f"{_RUNTIME_PATH_KEYS[first_index]} 不得与 "
                    f"{_RUNTIME_PATH_KEYS[second_index]} 重叠或嵌套。"
                )
    for path_index, runtime_path in enumerate(effective_paths):
        for reserved_leaf in _RUNTIME_RESERVED_LEAVES:
            if runtime_path == reserved_leaf:
                raise InventoryError(
                    f"{_RUNTIME_PATH_KEYS[path_index]} 不得占用受系统管理的运行时叶子："
                    f"{reserved_leaf}"
                )
    return effective_paths


def _require_ntfy_path_separate_from_northstar(
    label: str,
    ntfy_path: str,
    *,
    protected_paths: tuple[str, ...],
) -> None:
    for protected_path in protected_paths:
        if _paths_overlap(ntfy_path, protected_path):
            raise InventoryError(
                f"{label} 不得与 Northstar 受保护的代码、配置、状态或运行时目录重叠。"
            )


@dataclass(frozen=True)
class SshTarget:
    """经过严格验证、可同时传给 OpenSSH 与 SCP 的目标身份。"""

    authority: str
    host: str
    deploy_user: str | None


def _parse_deploy_host(value: str, *, service_user: str) -> SshTarget:
    """校验 SSH 目标，只接受别名、user@host 或 user@[IPv6]。

    端口和额外 SSH 选项必须由本机受管 SSH 配置提供，不能混入非机密部署清单。
    """

    if not value or len(value) > 320 or value != value.strip():
        raise InventoryError("DEPLOY_HOST 不能为空，且不能包含前后空白。")
    if value.count("@") > 1:
        raise InventoryError("DEPLOY_HOST 最多只能包含一个 SSH 用户分隔符 @。")

    deploy_user: str | None = None
    host = value
    if "@" in value:
        deploy_user, host = value.split("@", 1)
        if not _SSH_USER_PATTERN.fullmatch(deploy_user):
            raise InventoryError("DEPLOY_HOST 的 SSH 用户格式无效。")
        if deploy_user in {"root", service_user}:
            raise InventoryError(
                "DEPLOY_HOST 的 SSH 部署用户不得为 root 或 SERVICE_USER；部署与服务身份必须分离。"
            )

    if host.startswith("[") or host.endswith("]"):
        if not (host.startswith("[") and host.endswith("]")):
            raise InventoryError("DEPLOY_HOST 的 IPv6 地址必须完整使用方括号。")
        literal = host[1:-1]
        try:
            canonical_host = str(ipaddress.IPv6Address(literal))
        except ValueError as exc:
            raise InventoryError("DEPLOY_HOST 的方括号内容必须是合法 IPv6 地址。") from exc
        host = f"[{canonical_host}]"
    else:
        if ":" in host:
            raise InventoryError("DEPLOY_HOST 不支持端口或未加方括号的 IPv6；请使用受管 SSH 配置。")
        if host.count(".") == 3 and all(part.isdigit() for part in host.split(".")):
            try:
                host = str(ipaddress.IPv4Address(host))
            except ValueError as exc:
                raise InventoryError("DEPLOY_HOST 的 IPv4 地址无效。") from exc
        elif "." in host:
            if len(host) > 253 or not all(
                _DNS_LABEL_PATTERN.fullmatch(label) for label in host.split(".")
            ):
                raise InventoryError("DEPLOY_HOST 的主机名格式无效。")
        elif not _SSH_ALIAS_PATTERN.fullmatch(host):
            raise InventoryError("DEPLOY_HOST 只能是安全 SSH 别名或主机名。")

    authority = f"{deploy_user}@{host}" if deploy_user else host
    return SshTarget(authority=authority, host=host, deploy_user=deploy_user)


@dataclass(frozen=True)
class DeploymentInventory:
    """经验证的、可安全转交给 Linux 后端的非机密部署参数。"""

    source: Path
    ssh_target: SshTarget
    app_name: str
    service_user: str
    systemd_service_name: str
    service_mode: str
    python_version: str
    keep_releases: int
    remote_tmp: str
    dashboard_deploy_enabled: bool
    ntfy_deploy_enabled: bool
    values: dict[str, str]
    layout: LinuxProductionLayout = _LINUX_PRODUCTION_LAYOUT

    @property
    def deploy_host(self) -> str:
        """OpenSSH/SCP 使用的唯一、已验证目标字符串。"""

        return self.ssh_target.authority

    @property
    def app_root(self) -> str:
        return self.layout.app_root

    @property
    def config_dir(self) -> str:
        return self.layout.config_dir

    @property
    def service_home(self) -> str:
        return self.layout.state_dir

    @property
    def state_dir(self) -> str:
        return self.layout.state_dir

    @property
    def cache_dir(self) -> str:
        return self.layout.cache_dir

    @property
    def log_dir(self) -> str:
        return self.layout.log_dir

    @property
    def environment_file(self) -> str:
        return self.layout.environment_file(self.app_name)


def load_inventory(path: Path) -> DeploymentInventory:
    """加载部署清单，并在本地提前拒绝不安全的目标参数。"""

    _require_linux_x86_64_host()
    supplied = _read_key_value_file(path)
    values = {**_DEFAULTS, **supplied}

    app_name = _require_safe_name("APP_NAME", values["APP_NAME"])
    service_user = _require_safe_name("SERVICE_USER", values["SERVICE_USER"])
    if app_name != "northstar-quant" or service_user != "northstar":
        raise InventoryError(
            "Linux 生产身份固定为 APP_NAME=northstar-quant 且 SERVICE_USER=northstar。"
        )
    ssh_target = _parse_deploy_host(values.get("DEPLOY_HOST", ""), service_user=service_user)
    systemd_service_name = _require_safe_name(
        "SYSTEMD_SERVICE_NAME", values["SYSTEMD_SERVICE_NAME"]
    )
    if systemd_service_name != "northstar-quant":
        raise InventoryError(
            "SYSTEMD_SERVICE_NAME 必须为 northstar-quant，禁止覆盖系统或第三方服务。"
        )
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
        values[key] = _require_runtime_path(key, values.get(key, ""))
    protected_ntfy_paths = _NORTHSTAR_PROTECTED_ROOTS + _require_non_overlapping_runtime_paths(
        values
    )

    dashboard_enabled = (
        _require_boolean("DASHBOARD_DEPLOY_ENABLED", values["DASHBOARD_DEPLOY_ENABLED"]) == "1"
    )
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
        ntfy_config_dir = _require_linux_path(
            "NTFY_CONFIG_DIR", values["NTFY_CONFIG_DIR"], allowed_prefixes=("/etc/",)
        )
        values["NTFY_CONFIG_DIR"] = ntfy_config_dir
        ntfy_data_dir = _require_linux_path(
            "NTFY_DATA_DIR",
            values["NTFY_DATA_DIR"],
            allowed_prefixes=("/var/lib/",),
        )
        values["NTFY_DATA_DIR"] = ntfy_data_dir
        if any(segment in f"/{ntfy_data_dir}/" for segment in ("/releases/", "/current/")):
            raise InventoryError("NTFY_DATA_DIR 不能位于 releases 或 current 路径段。")
        _require_ntfy_path_separate_from_northstar(
            "NTFY_CONFIG_DIR",
            ntfy_config_dir,
            protected_paths=protected_ntfy_paths,
        )
        _require_ntfy_path_separate_from_northstar(
            "NTFY_DATA_DIR",
            ntfy_data_dir,
            protected_paths=protected_ntfy_paths,
        )
        if ntfy_config_dir != _NTFY_CONFIG_DIR:
            raise InventoryError(
                f"NTFY_CONFIG_DIR 固定为 {_NTFY_CONFIG_DIR}，不允许接管任意主机目录。"
            )
        if ntfy_data_dir != _NTFY_DATA_DIR:
            raise InventoryError(f"NTFY_DATA_DIR 固定为 {_NTFY_DATA_DIR}，不允许接管任意主机目录。")
        if not _NTFY_CACHE_DURATION_PATTERN.fullmatch(values["NTFY_CACHE_DURATION"]):
            raise InventoryError("NTFY_CACHE_DURATION 必须是正整数加 s、m 或 h，例如 24h。")

    return DeploymentInventory(
        source=path.resolve(),
        ssh_target=ssh_target,
        app_name=app_name,
        service_user=service_user,
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
    print(f"env_file={inventory.environment_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

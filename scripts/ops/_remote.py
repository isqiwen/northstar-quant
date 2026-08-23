"""跨平台运维控制面的共享远程调用器。

这里刻意只接受经过 ``scripts/deploy/inventory.py`` 校验的非机密清单，并把固定的
Linux 脚本通过标准输入交给 SSH。不会拼接用户输入为本地或远端 Shell 字符串。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Final, Sequence


SCRIPT_ROOT: Final = Path(__file__).resolve().parents[1]
PROJECT_ROOT: Final = SCRIPT_ROOT.parent
DEPLOY_SCRIPT_ROOT: Final = SCRIPT_ROOT / "deploy"
REMOTE_LINUX_ROOT: Final = Path(__file__).resolve().parent / "remote" / "linux"
_REMOTE_ARGUMENT_PATTERN: Final = re.compile(r"^[A-Za-z0-9._/@:+=-]+$")
_SSH_OPTIONS: Final = (
    "-o",
    "BatchMode=yes",
    "-o",
    "StrictHostKeyChecking=yes",
    "-o",
    "ConnectTimeout=15",
    "-o",
    "ConnectionAttempts=1",
    "-o",
    "ServerAliveInterval=15",
    "-o",
    "ServerAliveCountMax=3",
)
_REMOTE_OPERATION_TIMEOUT_SECONDS: Final = 300
_REMOTE_SAFE_PATH: Final = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

if str(DEPLOY_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(DEPLOY_SCRIPT_ROOT))

from inventory import DeploymentInventory, InventoryError, load_inventory  # noqa: E402


class RemoteOperationError(RuntimeError):
    """无法安全执行 Linux 目标端运维操作。"""


def load_deployment_inventory(path: Path) -> DeploymentInventory:
    """读取同一份无秘密部署清单，统一部署与运维目标。"""

    try:
        return load_inventory(path)
    except InventoryError as exc:
        raise RemoteOperationError(f"部署清单校验失败：{exc}") from exc


def run_linux_operation(
    *,
    inventory: DeploymentInventory,
    operation: str,
    arguments: Sequence[str] = (),
    dry_run: bool = False,
) -> int:
    """在目标 Linux 主机运行固定脚本；``dry_run`` 永远不建立 SSH 连接。"""

    script_path = REMOTE_LINUX_ROOT / f"{operation}.sh"
    if not script_path.is_file():
        raise RemoteOperationError(f"未找到 Linux 运维脚本：{script_path}")
    # OpenSSH 会在远端将命令参数拼成 shell 命令。因此即便本地没有 shell，也必须把
    # 传入目标脚本的参数限制为严格的非解释字符集。
    if any(not _REMOTE_ARGUMENT_PATTERN.fullmatch(argument) for argument in arguments):
        raise RemoteOperationError("远端参数只能包含路径和标识符所需的安全字符。")

    if dry_run:
        print(
            "dry-run：未连接 Linux 服务器；"
            f"计划运行 {operation}.sh 到 {inventory.deploy_host}。"
        )
        return 0

    ssh = shutil.which("ssh")
    if ssh is None:
        raise RemoteOperationError("未找到 ssh；请安装 OpenSSH 客户端后再运行远程运维命令。")

    # 所有远端操作都以非交互 sudo 启动：权限未预配置时应失败，而非卡在密码提示。
    command = [
        ssh,
        *_SSH_OPTIONS,
        inventory.deploy_host,
        "sudo",
        "-n",
        "env",
        "-i",
        f"PATH={_REMOTE_SAFE_PATH}",
        "/bin/bash",
        "-p",
        "-s",
        "--",
        *arguments,
    ]
    print(f"连接 Linux 目标：{inventory.deploy_host}（{operation}）")
    try:
        result = subprocess.run(
            command,
            input=script_path.read_text(encoding="utf-8"),
            text=True,
            check=False,
            timeout=_REMOTE_OPERATION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RemoteOperationError("远程运维操作超时。") from exc
    return result.returncode

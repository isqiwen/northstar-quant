"""Root-only initialization of the fixed Northstar deployment account."""

from __future__ import annotations

import grp
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEPLOY_USER = "northstar"
DEPLOY_HOME = Path("/home/northstar")
SUDOERS = Path("/etc/sudoers.d/northstar-deploy")
RULE = b"northstar ALL=(ALL) NOPASSWD: ALL\n"


def write(path: Path, content: bytes, mode: int, uid: int, gid: int) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"拒绝替换非普通文件：{path}")
    descriptor, temporary = tempfile.mkstemp(prefix=".northstar-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            os.fchown(stream.fileno(), uid, gid)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def initialize(public_key: str) -> None:
    os.environ["PATH"] += os.pathsep + "/usr/sbin:/sbin"
    if os.geteuid() != 0:
        raise ValueError("init-host 必须以 root 执行")
    if len(public_key) > 16384 or len(public_key.splitlines()) != 1:
        raise ValueError("请提供单行 SSH 公钥")
    fields = public_key.split()
    if len(fields) < 2 or not fields[0].startswith(("ssh-", "ecdsa-", "sk-")):
        raise ValueError("无效的 SSH 公钥")
    with tempfile.TemporaryDirectory(prefix="northstar-account-") as temporary:
        key = Path(temporary) / "key.pub"
        key.write_text(public_key + "\n")
        subprocess.run(["ssh-keygen", "-lf", str(key)], check=True, stdout=subprocess.DEVNULL)
        if shutil.which("sudo") is None or shutil.which("visudo") is None:
            if shutil.which("apt-get") is None:
                raise ValueError("请先安装 sudo；自动安装仅支持 apt 主机")
            subprocess.run(["apt-get", "update"], check=True)
            subprocess.run(
                ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", "sudo"],
                check=True,
            )
        policy = Path(temporary) / "sudoers"
        policy.write_bytes(RULE)
        subprocess.run(["visudo", "-cf", str(policy)], check=True)
        try:
            account = pwd.getpwnam(DEPLOY_USER)
        except KeyError:
            subprocess.run(
                ["useradd", "--create-home", "--user-group", "--shell", "/bin/bash", DEPLOY_USER],
                check=True,
            )
            account = pwd.getpwnam(DEPLOY_USER)
        if (
            account.pw_uid < 1000
            or account.pw_dir != str(DEPLOY_HOME)
            or account.pw_shell != "/bin/bash"
        ):
            raise ValueError("已有 northstar 账号不是预期的普通部署账号，拒绝接管")
        ssh = DEPLOY_HOME / ".ssh"
        for directory in (DEPLOY_HOME, ssh):
            if directory.resolve() != directory or (directory.exists() and not directory.is_dir()):
                raise ValueError("部署用户目录不能使用符号链接或非目录文件")
        if not DEPLOY_HOME.is_dir():
            raise ValueError("已有 northstar 主目录缺失，拒绝创建替代目录")
        ssh.mkdir(mode=0o700, exist_ok=True)
        os.chown(ssh, account.pw_uid, account.pw_gid)
        ssh.chmod(0o700)
        keys = ssh / "authorized_keys"
        if keys.is_symlink() or (keys.exists() and not keys.is_file()):
            raise ValueError("authorized_keys 必须是普通文件")
        previous = keys.read_text() if keys.exists() else ""
        # Retain existing keys/options and do not add an unrestricted duplicate.
        content = previous
        if not any(fields[1] in line.split() for line in previous.splitlines()):
            content = previous.rstrip("\n") + ("\n" if previous else "") + public_key + "\n"
        write(keys, content.encode(), 0o600, account.pw_uid, account.pw_gid)
        if SUDOERS.parent.resolve() != SUDOERS.parent:
            raise ValueError("sudoers 目录不能使用符号链接")
        SUDOERS.parent.mkdir(mode=0o755, exist_ok=True)
        write(SUDOERS, RULE, 0o440, 0, 0)
        subprocess.run(["visudo", "-c"], check=True)
        try:
            grp.getgrnam("docker")
        except KeyError:
            pass  # First Docker installation adds the group during deploy.
        else:
            subprocess.run(["usermod", "--append", "--groups", "docker", DEPLOY_USER], check=True)
    print("northstar 账号、SSH 公钥和免密码 sudo 已准备", flush=True)


if __name__ == "__main__":
    try:
        initialize(json.loads(sys.argv[1])["public_key"])
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"主机初始化失败：{error}", file=sys.stderr)
        sys.exit(1)

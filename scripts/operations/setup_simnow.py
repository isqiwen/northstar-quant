"""Interactively save private SimNow credentials; never connect to a broker."""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import stat
import sys
import warnings
from pathlib import Path

from northstar_quant.broker.settings import Credentials, load_credentials

FIELDS = {
    "user_id": ("投资者代码", "NORTHSTAR_SIMNOW_USER_ID"),
    "app_id": ("AppID", "NORTHSTAR_SIMNOW_APP_ID"),
    "auth_code": ("AuthCode", "NORTHSTAR_SIMNOW_AUTH_CODE"),
    "password": ("交易密码", "NORTHSTAR_SIMNOW_PASSWORD"),
}


def save(path: Path, credentials: Credentials) -> None:
    """Replace all credentials atomically, without following a private-directory symlink."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = ".simnow-" + secrets.token_hex(16)
    try:
        if os.fstat(directory).st_uid != os.geteuid():
            raise ValueError("私密目录必须属于当前用户")
        try:
            existing = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(existing.st_mode) or existing.st_uid != os.geteuid():
                raise ValueError("凭据目标必须是当前用户的普通文件，不能是符号链接")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            for field, (_, key) in FIELDS.items():
                stream.write(f"{key}={getattr(credentials, field)}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


def main() -> int:
    parser = argparse.ArgumentParser(description="交互式保存本机 SimNow 凭据，不连接柜台")
    parser.add_argument(
        "--file",
        type=Path,
        default=Path("/opt/northstar/credentials/live/broker.env"),
        help="私密文件路径，默认 /opt/northstar/credentials/live/broker.env",
    )
    args = parser.parse_args()
    if not sys.stdin.isatty():
        parser.error("请在自己的交互式终端运行；不要通过命令行参数或管道传入凭据")
    # Do not resolve the final component: a symlink must be rejected, never followed.
    path = Path(os.path.abspath(args.file.expanduser()))
    try:
        if path.parent.is_symlink() or path.is_symlink():
            raise ValueError("私密目录和凭据文件不能是符号链接")
        previous = load_credentials(path) if path.exists() else None
        print("请在 SimNow 官网核对投资者代码、AppID 和 AuthCode：https://www.simnow.com.cn/")
        print("仅保存本机配置，不连接柜台。已有配置可按回车保留，原值不会显示。")
        values = {}
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            for field, (label, _) in FIELDS.items():
                prompt = label + ("（回车保留）" if previous else "") + "："
                value = (
                    getpass.getpass(prompt) if field in ("auth_code", "password") else input(prompt)
                )
                values[field] = value or (getattr(previous, field) if previous else "")
        credentials = Credentials(**values)
        if input(f"保存到 {path}？[y/N]：").strip().lower() != "y":
            print("已取消，配置未修改。")
            return 0
        save(path, credentials)
        print(f"已保存私密文件：{path}（权限 600）。")
        print("将 NORTHSTAR_LIVE_BROKER_CONFIG 指向该绝对路径；不要 source 或执行此文件。")
        print("deploy/live/compose.yaml 已将所属凭据目录挂载给 Live 内核；不需要补充 Compose。")
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。", file=sys.stderr)
        return 1
    except (OSError, ValueError, getpass.GetPassWarning):
        print("配置未完成：请检查输入、私密文件权限和终端隐藏输入能力。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Prepare application directories on the filesystem currently backing each path."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

ROOT = Path("/opt/northstar")
SHARES = {
    "database": ("source", "market", "research", "backup"),
    "data-hub": ("source", "market", "research", "backup"),
    "research": ("market", "research", "backup"),
    "live": (),
}


def inspect(path: Path) -> None:
    for item in (*reversed(path.parents), path):
        if item.is_symlink():
            raise ValueError(f"目录路径不接受符号链接：{item}")
        if item.exists() and not item.is_dir():
            raise ValueError(f"目录路径被文件占用：{item}")


def prepare(request: dict) -> None:
    os.umask(0o022)
    app = request["app"]
    uid, gid = request["uid"], request["gid"]
    if app not in SHARES or type(uid) is not int or type(gid) is not int or min(uid, gid) < 0:
        raise ValueError("无效的目录准备请求")
    # A mount is optional: missing file directories are created on the local filesystem.
    for share in SHARES[app]:
        path = ROOT / "files" / share
        inspect(path)

    owner = "data-hub" if app == "database" else app
    directories = {f"apps/{app}": 0o755, "config": 0o750, f"state/{owner}": 0o700}
    if app != "live":
        directories[f"state/{owner}/bindings"] = 0o700
    if app != "database":
        directories[f"logs/{app}"] = 0o750
    if app != "database":
        directories[f"credentials/{app}"] = 0o700
        directories[f"credentials/{app}/workspace"] = 0o700
    if app == "research":
        directories["work/research"] = 0o700
    if app == "database":
        directories[f"state/{owner}/postgresql"] = 0o700

    receipt = ROOT / "apps" / app / "prepared-directories.json"
    for relative in directories:
        inspect(ROOT / relative)
    if receipt.is_symlink() or (receipt.exists() and not receipt.is_file()):
        raise ValueError("目录准备记录必须是普通文件")
    if receipt.exists():
        saved = json.loads(receipt.read_text())
        if saved != {
            "app": app,
            "persistent": sorted(
                p for p in directories if p.startswith(("state/", "credentials/"))
            ),
        }:
            raise ValueError("目录准备记录不匹配；请检查部署状态")
        for relative in saved["persistent"]:
            if not (ROOT / relative).is_dir():
                raise ValueError(f"已部署的持久目录丢失，请恢复：{ROOT / relative}")

    private = ROOT / "config" / f"{app}.env"
    if private.is_symlink() or (private.exists() and not stat.S_ISREG(private.stat().st_mode)):
        raise ValueError("运行配置必须是普通文件")
    for share in SHARES[app]:
        path = ROOT / "files" / share
        if not path.exists():
            path.mkdir(parents=True, mode=0o750)
            os.chown(path, uid, gid)
            path.chmod(0o750)
    for relative, mode in directories.items():
        path = ROOT / relative
        existed = path.exists()
        # Parents contain no secrets; only explicitly managed leaves are made private.
        path.mkdir(parents=True, mode=mode, exist_ok=True)
        # PostgreSQL owns an initialized PGDATA. Never take it back or walk its contents.
        if relative.endswith("/postgresql") and existed:
            continue
        os.chown(path, uid, gid)
        path.chmod(mode)
    if private.exists():
        os.chown(private, uid, gid)
        private.chmod(0o600)
    if not receipt.exists():
        document = {
            "app": app,
            "persistent": sorted(
                p for p in directories if p.startswith(("state/", "credentials/"))
            ),
        }
        with receipt.open("x") as stream:
            json.dump(document, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(receipt, uid, gid)
        receipt.chmod(0o600)
        fd = os.open(receipt.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    print(f"{app} 目录及权限已准备；使用各路径当前所在的文件系统", flush=True)


if __name__ == "__main__":
    try:
        prepare(json.loads(sys.argv[1]))
    except (OSError, ValueError) as error:
        print(f"目录准备失败：{error}", file=sys.stderr)
        sys.exit(1)

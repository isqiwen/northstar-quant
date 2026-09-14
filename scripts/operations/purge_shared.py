"""Remote cleanup session: locks span stop, shared deletion and local purge."""

from __future__ import annotations

import json
import shutil
import socket
import sys
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from uuid import UUID

MARKET = Path("/opt/northstar/files/market")
DATA = {"objects", "staging", "tushare"}
MARKER = ".northstar-storage-id"


def inspect_share(host, server: str) -> str | None:
    for path in (MARKET, *MARKET.parents):
        if path.is_symlink():
            raise ValueError("共享路径不能是符号链接")
    mounts = json.loads(
        host["run"]("findmnt", "--json", "--list", "--output", "TARGET,SOURCE,FSTYPE,OPTIONS")
    ).get("filesystems", [])
    exact = [m for m in mounts if m["target"] == str(MARKET)]
    if len(exact) != 1:
        raise ValueError("共享挂载未就绪；不会清理本地同名目录")
    mount = exact[0]
    addresses = {server, *(item[4][0] for item in socket.getaddrinfo(server, None))}
    if mount["fstype"] not in {"nfs", "nfs4"} or mount["source"] not in {
        f"{address}:/quant" for address in addresses
    }:
        raise ValueError("共享挂载不是配置的 NFS /quant")
    if any(MARKET in Path(m["target"]).parents for m in mounts):
        raise ValueError("共享目录含嵌套挂载，拒绝清空")
    entries = list(MARKET.iterdir())
    for path in entries:
        if path.name not in DATA | {MARKER, ".DS_Store", "@Recently-Snapshot"}:
            raise ValueError(f"共享目录包含非项目内容：{path.name}")
        if path.name == "@Recently-Snapshot" and (path.is_symlink() or not path.is_dir()):
            raise ValueError("NAS 快照目录类型异常")
    marker = MARKET / MARKER
    if marker.is_symlink():
        raise ValueError("存储身份不能是符号链接")
    if marker.exists():
        return str(UUID(marker.read_text().strip()))
    if any(path.name in DATA for path in entries):
        raise ValueError("共享数据缺少存储身份，拒绝删除")
    return None


def clear_share(host, server, identity):
    if inspect_share(host, server) != identity:
        raise ValueError("共享存储身份发生变化，拒绝删除")
    if host["preflight"]():
        raise ValueError("Northstar 容器仍存在，拒绝清空共享")
    # rmtree unlinks symlinks without traversing their targets. Never enter NAS snapshots.
    for path in list(MARKET.iterdir()):
        if path.name not in DATA | {".DS_Store"}:
            continue
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
    (MARKET / MARKER).unlink(missing_ok=True)


def session(request):
    host = {"__name__": "purge_host"}
    exec(compile(request["host_program"], "purge_host.py", "exec"), host)
    with ExitStack() as stack:
        host["preflight"]()
        host["locks"](stack)
        identity = inspect_share(host, request["server"]) if request["client"] else None
        print(json.dumps({"phase": "ready", "identity": identity}), flush=True)
        stopped = False
        for line in sys.stdin:
            command = line.strip()
            with redirect_stdout(sys.stderr):
                if command == "stop" and not stopped:
                    host["stop"](host["preflight"]())
                    stopped = True
                    phase = "stopped"
                elif command == "clear" and stopped and request["writer"]:
                    clear_share(host, request["server"], identity)
                    phase = "cleared"
                elif command == "purge" and stopped:
                    host["purge"](locks_held=True)
                    phase = "purged"
                else:
                    raise ValueError("非法清空阶段")
            print(json.dumps({"phase": phase}), flush=True)
            if phase == "purged":
                return


if __name__ == "__main__":
    try:
        session(json.loads(sys.argv[1]))
    except Exception as error:
        sys.exit(f"清空未完成：{error}；已完成步骤不会回滚")

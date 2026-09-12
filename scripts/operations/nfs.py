"""Mount externally managed NFS market storage on application hosts."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

MARKET = Path("/opt/northstar/files/market")
STATE = Path("/opt/northstar/state/nfs")
UNITS = Path("/etc/systemd/system")
EXPORT = "/quant"
UNIT = "opt-northstar-files-market.mount"


def topology(settings: dict) -> dict | None:
    item = settings.get("nfs")
    if item is None:
        return None
    if not isinstance(item, dict) or set(item) - {"host"}:
        raise ValueError("nfs 只接受 host；服务端及 /quant 导出由外部准备")
    for name in ("nfs", "data_hub", "research"):
        if not isinstance(settings.get(name, {}).get("host"), str) or not settings[name]["host"]:
            raise ValueError(f"NFS 需要配置 {name}.host")
    if any(
        not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*", settings[name]["host"])
        for name in ("nfs", "data_hub", "research")
    ):
        raise ValueError("NFS 主机请填写有效主机名或 IPv4 地址")
    return {
        "server": item["host"],
        "writer": settings["data_hub"]["host"],
        "reader": settings["research"]["host"],
    }


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=None).strip()


def require_client() -> None:
    if not shutil.which("mount.nfs"):
        raise ValueError("请在应用主机预先安装 NFS 客户端（Ubuntu/Debian: nfs-common）")


def write(path: Path, content: str) -> None:
    if path.is_symlink():
        raise ValueError(f"配置路径不能是符号链接：{path}")
    if path.exists() and path.read_text() == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".northstar-")
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def mounted() -> dict | None:
    result = subprocess.run(
        ["findmnt", "--mountpoint", str(MARKET), "--json", "--output", "SOURCE,FSTYPE,OPTIONS"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 1:
        return None
    if result.returncode:
        raise ValueError("无法检查当前行情挂载")
    return json.loads(result.stdout)["filesystems"][0]


def check_client(current: dict | None, source: str, mode: str) -> None:
    if current is not None:
        options = current["options"].split(",")
        if (
            current["source"] != source
            or current["fstype"] not in {"nfs", "nfs4"}
            or mode not in options
            or "hard" not in options
            or not any(value == "vers=4" or value.startswith("vers=4.") for value in options)
        ):
            raise ValueError(
                "行情路径已有不同挂载；请停止使用者、迁移并验证原 UUID 后卸载旧挂载，再部署"
            )
    elif MARKET.exists() and any(MARKET.iterdir()):
        raise ValueError("行情本地目录非空；先迁移数据与 .northstar-storage-id，不能直接覆盖挂载")


def identity() -> str:
    marker = MARKET / ".northstar-storage-id"
    if marker.is_symlink():
        raise ValueError("行情存储身份不能是符号链接")
    value = marker.read_text().strip()
    if str(UUID(value)) != value:
        raise ValueError("行情存储 UUID 无效")
    return value


def prepare_client(request: dict) -> None:
    source = f"{request['server']}:{EXPORT}"
    mode = "rw" if request["host"] == request["writer"] else "ro"
    check_client(mounted(), source, mode)
    require_client()
    MARKET.mkdir(parents=True, exist_ok=True)
    unit = (
        "[Unit]\nDescription=Northstar market NFS\nWants=network-online.target\n"
        "After=network-online.target\nBefore=docker.service\n\n[Mount]\n"
        f"What={source}\nWhere={MARKET}\nType=nfs\nOptions=vers=4,{mode},hard,_netdev\n"
        "TimeoutSec=45\n\n[Install]\nWantedBy=multi-user.target\n"
    )
    write(UNITS / UNIT, unit)
    # A failed mount must not let Docker recreate a same-named local empty directory.
    write(
        UNITS / "docker.service.d/northstar-market.conf", f"[Unit]\nRequires={UNIT}\nAfter={UNIT}\n"
    )
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "--now", UNIT)
    current = mounted()
    if current is None:
        raise ValueError("NFS 挂载未就绪")
    check_client(current, source, mode)
    marker = MARKET / ".northstar-storage-id"
    saved = STATE / "client.json"
    if not marker.exists():
        if mode != "rw" or saved.exists() or any(MARKET.iterdir()):
            raise ValueError("共享缺少存储身份；首次请先部署 Data Hub，已有数据须恢复原身份")
        with marker.open("x") as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(str(uuid4()) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    value = identity()
    saved = STATE / "client.json"
    if saved.exists() and json.loads(saved.read_text())["identity"] != value:
        raise ValueError("NFS UUID 与之前挂载不符；必须保留数据身份")
    write(saved, json.dumps({"source": source, "mode": mode, "identity": value}) + "\n")
    if mode == "rw":
        fd, name = tempfile.mkstemp(prefix=".nfs-probe-", dir=MARKET)
        other = name + ".linked"
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(b"northstar\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.link(name, other)
            if Path(other).read_bytes() != b"northstar\n":
                raise ValueError("NFS 文件验证失败")
        finally:
            Path(other).unlink(missing_ok=True)
            Path(name).unlink(missing_ok=True)
    print(f"NFS 客户端就绪：{source} → {MARKET} ({mode})，UUID={value}", flush=True)


def verify_client() -> None:
    saved = STATE / "client.json"
    if not saved.exists():
        return
    request = json.loads(saved.read_text())
    current = mounted()
    if current is None:
        raise ValueError("已配置的 NFS 挂载未就绪；请重新 deploy，不能写入本地空目录")
    check_client(current, request["source"], request["mode"])
    if identity() != request["identity"]:
        raise ValueError("NFS 存储 UUID 不匹配")


def prepare(request: dict) -> None:
    if request.get("preflight"):
        source = f"{request['server']}:{EXPORT}"
        mode = "rw" if request["host"] == request["writer"] else "ro"
        check_client(mounted(), source, mode)
        return
    if os.geteuid() != 0:
        raise ValueError("NFS 主机准备需要 root 权限")
    for path in (*reversed(MARKET.parents), MARKET):
        if path.is_symlink():
            raise ValueError("行情路径不能是符号链接")
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "prepare.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prepare_client(request)


if __name__ == "__main__":
    try:
        prepare(json.loads(sys.argv[1]))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"NFS 准备失败：{error}")

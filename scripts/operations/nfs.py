"""Managed Linux NFS market storage; transported over SSH before app deployment."""

from __future__ import annotations

import fcntl
import json
import os
import pwd
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

MARKET = Path("/opt/northstar/files/market")
STATE = Path("/opt/northstar/state/nfs")
UNITS = Path("/etc/systemd/system")
EXPORTS = Path("/etc/exports.d/northstar.exports")
UNIT = "opt-northstar-files-market.mount"


def topology(settings: dict) -> dict | None:
    item = settings.get("nfs")
    if item is None:
        return None
    if not isinstance(item, dict) or set(item) - {"host", "user", "port"}:
        raise ValueError("nfs 只接受 host/user/port")
    for name in ("nfs", "data_hub", "research"):
        if not isinstance(settings.get(name, {}).get("host"), str) or not settings[name]["host"]:
            raise ValueError(f"NFS 需要配置 {name}.host")
    if any(":" in settings[name]["host"] for name in ("nfs", "data_hub", "research")):
        raise ValueError("NFS 主机请填写可解析为 IPv4 的主机名或 IPv4 地址")
    if settings.get("database", {}).get("host") != settings["data_hub"]["host"]:
        raise ValueError("database 与 Data Hub 必须部署到同一主机")
    return {
        "server": item["host"],
        "writer": settings["data_hub"]["host"],
        "reader": settings["research"]["host"],
    }


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True, stderr=None).strip()


def install(package: str) -> None:
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=${Status}", package], capture_output=True, text=True
    )
    if result.returncode == 0 and result.stdout.strip() == "install ok installed":
        return
    if not shutil.which("apt-get"):
        raise ValueError("自动配置 NFS 仅支持 Ubuntu/Debian Linux 服务端和客户端")
    subprocess.run(["apt-get", "update"], check=True)
    subprocess.run(
        [
            "env",
            "DEBIAN_FRONTEND=noninteractive",
            "apt-get",
            "install",
            "-y",
            "--no-upgrade",
            "--no-remove",
            package,
        ],
        check=True,
    )


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


def prepare_server(request: dict) -> None:
    current = mounted()
    if current and current["fstype"] in {"nfs", "nfs4", "autofs"}:
        raise ValueError("NFS 服务端不能重新导出旧客户端挂载；请先迁移至服务端本地目录")
    install("nfs-kernel-server")
    try:
        account = pwd.getpwnam("northstar-market")
    except KeyError:
        run(
            "useradd",
            "--system",
            "--no-create-home",
            "--shell",
            "/usr/sbin/nologin",
            "northstar-market",
        )
        account = pwd.getpwnam("northstar-market")
    MARKET.mkdir(parents=True, exist_ok=True)
    marker = MARKET / ".northstar-storage-id"
    if not marker.exists():
        if any(MARKET.iterdir()):
            raise ValueError("已有行情缺少存储 UUID，不能自动接管")
        write(marker, str(uuid4()) + "\n")
    identity()
    # Only the publication tree is shared. Never walk source, credentials or PGDATA.
    # Existing immutable publications become readable; temporary/lock files remain private.
    adopted = STATE / "server-storage-id"
    if adopted.exists() and adopted.read_text().strip() != identity():
        raise ValueError("服务端存储 UUID 改变，请恢复原数据与身份")
    for root, dirs, files in [] if adopted.exists() else os.walk(MARKET, followlinks=False):
        path = Path(root)
        if path.is_symlink():
            raise ValueError("行情目录不能包含符号链接")
        for name in dirs + files:
            item = path / name
            if item.is_symlink() or not (item.is_dir() or item.is_file()):
                raise ValueError("行情目录只能包含普通文件和目录")
        os.chown(path, account.pw_uid, account.pw_gid)
        path.chmod(0o700 if "staging" in path.relative_to(MARKET).parts else 0o755)
        for name in files:
            item = path / name
            os.chown(item, account.pw_uid, account.pw_gid)
            item.chmod(
                0o600
                if name.startswith(".") and name != marker.name or "staging" in item.parts
                else 0o644
            )
    write(adopted, identity() + "\n")
    clients = {}
    for host, access in ((request["reader"], "ro"), (request["writer"], "rw")):
        if host != request["server"]:
            address = socket.gethostbyname(host)
            clients[address] = access  # A co-located Data Hub remains the writer.
    options = [
        f"{ip}({mode},sync,no_subtree_check,all_squash,"
        f"anonuid={account.pw_uid if mode == 'rw' else 65534},"
        f"anongid={account.pw_gid if mode == 'rw' else 65534})"
        for ip, mode in sorted(clients.items())
    ]
    write(
        EXPORTS,
        f"{MARKET} " + " ".join(options) + "\n" if options else "",
    )
    run("systemctl", "enable", "--now", "nfs-server.service")
    run("exportfs", "-ra")
    if shutil.which("ufw") and "Status: active" in run("ufw", "status"):
        for ip in clients:
            run(
                "ufw",
                "allow",
                "from",
                ip,
                "to",
                "any",
                "port",
                "2049",
                "proto",
                "tcp",
                "comment",
                "northstar-nfs",
            )
    print(f"NFS 服务端就绪：{MARKET}，UUID={identity()}", flush=True)


def prepare_client(request: dict) -> None:
    source = f"{request['server']}:{MARKET}"
    mode = "rw" if request["host"] == request["writer"] else "ro"
    check_client(mounted(), source, mode)
    install("nfs-common")
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
    check_client(mounted(), source, mode)
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
        if request["host"] != request["server"]:
            source = f"{request['server']}:{MARKET}"
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
        if request["host"] == request["server"]:
            prepare_server(request)
        else:
            prepare_client(request)


def manage(request: dict) -> int:
    """Manage the host NFS service; never stop applications or remove market files."""
    action = request.get("action", "deploy")
    if action == "deploy":
        prepare(request)
        return 0
    if os.geteuid() != 0:
        raise ValueError("NFS 服务管理需要 root 权限")
    service = "nfs-server.service"
    if action == "logs":
        return subprocess.run(
            [
                "journalctl",
                "--unit",
                service,
                "--no-pager",
                "--lines",
                "100",
                *(["--follow"] if request.get("follow") else []),
            ],
            check=False,
        ).returncode
    if action == "status":
        return subprocess.run(
            ["systemctl", "status", "--no-pager", service], check=False
        ).returncode
    if action not in {"start", "restart", "stop"}:
        raise ValueError("不支持的 NFS 管理操作")
    if not STATE.is_dir():
        raise ValueError("NFS 尚未部署，请先执行 deploy nfs")
    with (STATE / "prepare.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if action in {"start", "restart"}:
            saved = STATE / "server-storage-id"
            if not saved.is_file() or not EXPORTS.is_file():
                raise ValueError("NFS 尚未部署，请先执行 deploy nfs")
            if saved.read_text().strip() != identity():
                raise ValueError("服务端存储 UUID 改变，请恢复原数据与身份")
        return subprocess.run(["systemctl", action, service], check=False).returncode


if __name__ == "__main__":
    try:
        sys.exit(manage(json.loads(sys.argv[1])))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"NFS 准备失败：{error}")

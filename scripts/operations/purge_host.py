"""Remove this host's Northstar deployment, never external mounts or shared tools."""

from __future__ import annotations

import fcntl
import json
import os
import shlex
import shutil
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path

ROOT = Path("/opt/northstar")
UNITS = Path("/etc/systemd/system")
APPS = ("database", "data-hub", "research", "live")
PROJECTS = {f"northstar-{app}" for app in APPS}
MOUNT = "opt-northstar-files-market.mount"
CHAINS = ("NS-DATA-WEB", "NS-RESEARCH-WEB", "NS-LIVE-WEB")


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def mounts() -> list[Path]:
    data = json.loads(run("findmnt", "--json", "--list", "--output", "TARGET"))
    return [Path(x["target"]) for x in data.get("filesystems", [])]


def inside(path: Path) -> bool:
    return path == ROOT or ROOT in path.parents


def containers(project: str | None = None) -> list[str]:
    filters = ["--filter", f"label=com.docker.compose.project={project}"] if project else []
    return run("docker", "ps", "-aq", *filters).splitlines()


def owned(kind: str) -> set[str]:
    result = set()
    for project in sorted(PROJECTS):
        result.update(
            run(
                "docker",
                kind,
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ).splitlines()
        )
    return result


def firewall() -> None:
    if not shutil.which("iptables"):
        return
    rules = run("iptables", "-w", "-S").splitlines()
    for line in rules:
        parts = shlex.split(line)
        if parts[:1] == ["-A"] and parts[-2:-1] == ["-j"] and parts[-1] in CHAINS:
            run("iptables", "-w", "-D", *parts[1:])
    for chain in CHAINS:
        if f"-N {chain}" in rules:
            run("iptables", "-w", "-F", chain)
            run("iptables", "-w", "-X", chain)


def purge() -> None:
    if os.geteuid() != 0:
        raise ValueError("整机卸载需要 sudo 权限")
    for path in (*ROOT.parents, ROOT):
        if path.is_symlink():
            raise ValueError(f"拒绝清理符号链接根目录：{path}")
    selected = {identifier for project in PROJECTS for identifier in containers(project)}
    for identifier in set(containers()) - selected:
        bindings = json.loads(run("docker", "inspect", "--format", "{{json .Mounts}}", identifier))
        if any(
            item.get("Type") == "bind"
            and (inside(Path(item["Source"])) or Path(item["Source"]) in ROOT.parents)
            for item in bindings
        ):
            raise ValueError("其他容器仍使用 Northstar 目录，拒绝清理")
    existing_mounts = [path for path in mounts() if inside(path)]
    market = ROOT / "files/market"
    if any(path != market for path in existing_mounts):
        raise ValueError("Northstar 下有其他挂载；请先由管理员卸载，不能递归删除挂载内容")
    with ExitStack() as stack:
        for app in APPS:
            directory = ROOT / "apps" / app
            if directory.is_symlink() or directory.parent.is_symlink():
                raise ValueError(f"部署目录不能是符号链接：{directory}")
            if directory.is_dir():
                lock_path = directory / ".deployment.lock"
                if lock_path.is_symlink():
                    raise ValueError("部署锁不能是符号链接")
                lock = stack.enter_context(lock_path.open("a"))
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Stop before removing: Docker gives the kernel a bounded graceful shutdown.
        if selected:
            run("docker", "stop", "--time", "60", *sorted(selected))
            run("docker", "rm", *sorted(selected))
        # Remove Requires=mount before stopping it, otherwise systemd can stop
        # the shared Docker daemon and unrelated applications with it.
        dropin = UNITS / "docker.service.d/northstar-market.conf"
        dropin.unlink(missing_ok=True)
        run("systemctl", "daemon-reload")
        for name in [*(f"northstar-{app}-firewall.service" for app in APPS), MOUNT]:
            unit = UNITS / name
            if unit.exists() or unit.is_symlink():
                run("systemctl", "disable", "--now", name)
                unit.unlink()
        if market in mounts():
            run("umount", str(market))
        if any(inside(path) for path in mounts()):
            raise ValueError("仍有挂载，已停止服务但拒绝删除文件；不使用强制或延迟卸载")
        firewall()
        run("systemctl", "daemon-reload")
        for kind in ("network", "volume"):
            for identifier in sorted(owned(kind)):
                if (
                    kind == "volume"
                    and run("docker", "volume", "inspect", "--format", "{{.Driver}}", identifier)
                    != "local"
                ):
                    raise ValueError("存在外部存储驱动卷，拒绝调用其删除操作")
                run("docker", kind, "rm", identifier)
        networks = run("docker", "network", "ls", "--format", "{{.Name}}").splitlines()
        if "northstar-data-storage" in networks:
            run("docker", "network", "rm", "northstar-data-storage")
        repositories = {
            f"northstar-{app}-{part}" for app in APPS for part in ("backend", "frontend")
        }
        repositories.add("northstar-quant")
        images = run("docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}").splitlines()
        for image in sorted(set(images)):
            repo, _, tag = image.rpartition(":")
            if repo in repositories and tag != "<none>":
                run("docker", "image", "rm", "--no-prune", image)
        # rmtree does not follow directory symlinks; mountpoints were explicitly checked above.
        if any(inside(path) for path in mounts()):
            raise ValueError("删除前发现新增挂载，拒绝删除文件")
        if ROOT.exists():
            shutil.rmtree(ROOT)
    print("本机 Northstar 容器、专属镜像/网络/卷、配置及本地数据已删除；外部共享和主机依赖保留")


if __name__ == "__main__":
    try:
        purge()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"卸载未完成：{error}；已完成步骤不会回滚，请检查后重试")

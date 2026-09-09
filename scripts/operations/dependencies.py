"""Bootstrap deployment tools over SSH before transferring application source."""

from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


def run(*args: str) -> None:
    subprocess.run(args, check=True, stdin=subprocess.DEVNULL)


def available(*args: str) -> bool:
    try:
        return (
            subprocess.run(
                args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            ).returncode
            == 0
        )
    except FileNotFoundError:
        return False


def admin(*args: str) -> None:
    run(*([] if os.geteuid() == 0 else ["sudo", "-n"]), *args)


def prepare(request: dict) -> None:
    private = Path(request["env_file"])
    if not private.is_file() or stat.S_IMODE(private.stat().st_mode) & 0o077:
        raise ValueError("目标 env_file 必须已存在且仅所属用户可访问（chmod 600）")
    os.environ["PATH"] += os.pathsep + str(Path.home() / ".local/bin") + ":/usr/sbin:/sbin"
    packages = []
    for binary, package in (("git", "git"), ("curl", "curl")):
        if shutil.which(binary) is None:
            packages.append(package)
    docker = available("docker", "--version")
    compose = available("docker", "compose", "version")
    buildx = available("docker", "buildx", "version")
    uv = available("uv", "--version")
    if packages or not (docker and compose and buildx and uv):
        release = Path("/etc/os-release")
        values = (
            dict(line.split("=", 1) for line in release.read_text().splitlines() if "=" in line)
            if release.exists()
            else {}
        )
        distro = values.get("ID", "").strip('"')
        codename = values.get("VERSION_CODENAME", "").strip('"')
        if distro not in ("ubuntu", "debian") or not re.fullmatch(r"[a-z]+", codename):
            raise ValueError("自动安装仅支持 Ubuntu/Debian；其他系统请先安装部署依赖")
        admin("true")
        print("准备部署依赖：检测缺项并安装", flush=True)
        if not (docker and compose and buildx):
            # Never replace a running distro engine or container runtime implicitly.
            for package in ("docker.io", "podman-docker", "containerd", "runc"):
                result = subprocess.run(
                    ["dpkg-query", "-W", "-f=${Status}", package],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.stdout.strip() == "install ok installed":
                    raise ValueError(
                        f"已有 {package}；请补齐其 Compose/Buildx，脚本不替换现有运行时"
                    )
        admin("apt-get", "update")
        admin(
            "env",
            "DEBIAN_FRONTEND=noninteractive",
            "apt-get",
            "install",
            "-y",
            "--no-upgrade",
            "ca-certificates",
            *packages,
            *(["python3-venv"] if not uv else []),
        )
        if not (docker and compose and buildx):
            arch = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
            if arch != "amd64":
                raise ValueError("当前应用镜像要求 Linux amd64 主机")
            sources = [Path("/etc/apt/sources.list")]
            sources += list(Path("/etc/apt/sources.list.d").glob("*.list"))
            sources += list(Path("/etc/apt/sources.list.d").glob("*.sources"))
            existing_source = any(
                path.is_file() and f"download.docker.com/linux/{distro}" in path.read_text()
                for path in sources
            )
            if not existing_source:
                with tempfile.TemporaryDirectory(prefix="northstar-dependencies-") as temporary:
                    key = Path(temporary) / "docker.asc"
                    run(
                        "curl",
                        "-fsSL",
                        f"https://download.docker.com/linux/{distro}/gpg",
                        "-o",
                        str(key),
                    )
                    source = Path(temporary) / "docker.sources"
                    source.write_text(
                        f"Types: deb\nURIs: https://download.docker.com/linux/{distro}\n"
                        f"Suites: {codename}\nComponents: stable\nArchitectures: {arch}\n"
                        "Signed-By: /etc/apt/keyrings/northstar-docker.asc\n"
                    )
                    admin("install", "-d", "-m", "0755", "/etc/apt/keyrings")
                    admin(
                        "install", "-m", "0644", str(key), "/etc/apt/keyrings/northstar-docker.asc"
                    )
                    admin(
                        "install",
                        "-m",
                        "0644",
                        str(source),
                        "/etc/apt/sources.list.d/northstar-docker.sources",
                    )
            admin("apt-get", "update")
            needed = [] if docker else ["docker-ce", "docker-ce-cli", "containerd.io"]
            needed += [] if compose else ["docker-compose-plugin"]
            needed += [] if buildx else ["docker-buildx-plugin"]
            admin(
                "env",
                "DEBIAN_FRONTEND=noninteractive",
                "apt-get",
                "install",
                "-y",
                "--no-upgrade",
                *needed,
            )
            if not docker:
                admin("systemctl", "enable", "--now", "docker")
        if not uv:
            tools = Path.home() / ".local/share/northstar/deployment-tools"
            run(sys.executable, "-m", "venv", str(tools))
            run(str(tools / "bin/python"), "-m", "pip", "install", "uv==0.11.6")
            binary = Path.home() / ".local/bin/uv"
            binary.parent.mkdir(parents=True, exist_ok=True)
            if binary.exists() or binary.is_symlink():
                raise ValueError(f"{binary} 已存在但不可用，请检查")
            binary.symlink_to(tools / "bin/uv")
    if not available("docker", "info"):
        # Newly installed Docker may need group membership. A fresh SSH session follows.
        if not docker and os.geteuid() != 0:
            admin("usermod", "-aG", "docker", pwd.getpwuid(os.getuid()).pw_name)
            admin("docker", "info")
        else:
            raise ValueError("Docker 不可访问，请检查服务状态和部署用户权限")
    for command in (
        ("git", "--version"),
        ("uv", "--version"),
        ("docker", "compose", "version"),
        ("docker", "buildx", "version"),
    ):
        if not available(*command):
            raise ValueError(f"依赖校验失败：{' '.join(command)}")
    root = Path(request["directory"])
    if not root.exists():
        try:
            root.mkdir(parents=True)
        except PermissionError:
            admin(
                "install",
                "-d",
                "-m",
                "0755",
                "-o",
                str(os.getuid()),
                "-g",
                str(os.getgid()),
                str(root),
            )
    if request["app"] != "live":
        owner = "research" if request["app"] == "research" else "data-hub"
        bindings = Path(f"/opt/northstar/state/{owner}/bindings")
        if not bindings.exists():
            try:
                bindings.mkdir(parents=True, mode=0o700)
            except PermissionError:
                admin(
                    "install",
                    "-d",
                    "-m",
                    "0700",
                    "-o",
                    str(os.getuid()),
                    "-g",
                    str(os.getgid()),
                    str(bindings),
                )
    print("部署依赖已就绪", flush=True)


if __name__ == "__main__":
    try:
        prepare(json.loads(sys.argv[1]))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"依赖准备失败：{error}", file=sys.stderr)
        sys.exit(1)

"""Merge and hot-reload the deployment host's Docker Hub mirrors."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MIRRORS = [
    "https://er6y16wc.mirror.aliyuncs.com",
    "https://docker.xuanyuan.me",
    "https://docker.1ms.run",
    "https://docker.m.daocloud.io",
]
CONFIG = Path("/etc/docker/daemon.json")


def configure(path: Path = CONFIG) -> None:
    if path.is_symlink():
        raise ValueError(f"Docker 配置不能是符号链接：{path}")
    original = path.read_text() if path.exists() else None
    values = json.loads(original) if original is not None else {}
    if not isinstance(values, dict):
        raise ValueError("Docker daemon.json 必须是 JSON 对象")
    if values.get("registry-mirrors") != MIRRORS:
        values["registry-mirrors"] = MIRRORS
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".northstar-docker-", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w") as file:
                json.dump(values, file, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            subprocess.run(["dockerd", "--validate", "--config-file", name], check=True)
            temporary.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    # Retry a previously failed reload even when the on-disk values already match.
    def applied() -> bool:
        output = subprocess.check_output(
            ["docker", "info", "--format", "{{json .RegistryConfig.Mirrors}}"], text=True
        )
        return [url.rstrip("/") for url in (json.loads(output) or [])] == MIRRORS

    if not applied():
        subprocess.run(["systemctl", "reload", "docker"], check=True)
        for _ in range(20):
            if applied():
                break
            time.sleep(0.25)
        else:
            raise ValueError("Docker 镜像源已写入但热加载未生效，请检查 Docker 服务日志")
    print("Docker 镜像源已配置并生效（未重启容器）", flush=True)


if __name__ == "__main__":
    try:
        configure()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Docker 配置失败：{error}", file=sys.stderr)
        sys.exit(1)

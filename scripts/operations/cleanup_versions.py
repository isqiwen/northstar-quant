"""Retain the verified deployment; never prune shared caches or persistent application state."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

SHA = re.compile(r"[0-9a-f]{40}\Z")


def output(*command: str) -> str:
    return subprocess.check_output(command, text=True).strip()


def cleanup(app: str, root: Path, revision: str) -> None:
    releases = root / "releases"
    current = releases / revision
    if not SHA.fullmatch(revision) or releases.is_symlink() or current.is_symlink():
        raise ValueError("Invalid release directory")
    if (root / "current").resolve(strict=True) != current.resolve(strict=True):
        raise ValueError("Current release changed; refusing cleanup")
    if (root / "successful-revision").read_text().strip() != revision:
        raise ValueError("Deployment is not verified; refusing cleanup")
    identifiers = output("docker", "ps", "-aq").splitlines()
    mounts = []
    for identifier in identifiers:
        mounts.extend(
            json.loads(output("docker", "inspect", "--format", "{{json .Mounts}}", identifier))
        )
    sources = [Path(mount["Source"]).resolve() for mount in mounts if mount.get("Type") == "bind"]
    retained = []
    for old in releases.iterdir():
        if old.name == revision or not SHA.fullmatch(old.name):
            continue
        if old.is_symlink() or not old.is_dir():
            raise ValueError(f"Unexpected old release path: {old}")
        if any(source == old or source.is_relative_to(old) for source in sources):
            retained.append(old.name)
            continue
        shutil.rmtree(old)
        print(f"已清理 {app} 旧程序版本：{old.name}", flush=True)
    # Remove only unused tags owned by this app. Never force removal or prune the daemon.
    used = set(output("docker", "ps", "-a", "--format", "{{.Image}}").splitlines())
    repositories = {f"northstar-{app}-backend", f"northstar-{app}-frontend"}
    images = output("docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}")
    for image in sorted(set(images.splitlines())):
        repository, _, tag = image.rpartition(":")
        if repository not in repositories or not SHA.fullmatch(tag) or tag == revision:
            continue
        if image in used:
            retained.append(image)
            continue
        subprocess.run(["docker", "image", "rm", "--no-prune", image], check=True)
    if retained:
        raise ValueError("旧版本仍被容器引用，已保留：" + ", ".join(retained))
    print(f"{app} 仅保留当前部署版本；配置、数据和构建缓存保留", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", choices=("database", "data-hub", "research", "live"))
    parser.add_argument("revision")
    args = parser.parse_args()
    try:
        cleanup(args.app, Path(f"/opt/northstar/apps/{args.app}"), args.revision)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"新版本已启动，但版本清理未完成：{error}\n")

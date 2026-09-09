"""Remote half of deploy.py, sent over verified SSH; uses only Python's standard library."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

TARGETS = {
    "database": ("database", "database", "northstar-database"),
    "data-hub": ("data_hub", "data", "northstar-data-hub"),
    "research": ("research", "research", "northstar-research"),
    "live": ("live", "live", "northstar-live"),
}


def run(*args: str, cwd: Path | None = None, capture: bool = False) -> str:
    result = subprocess.run(
        args, cwd=cwd, check=True, text=True, stdout=subprocess.PIPE if capture else None
    )
    return result.stdout.strip() if capture else ""


def image_environment(app: str, revision: str) -> None:
    # Separate tags prevent two applications building on one host from racing :local.
    backend = f"northstar-{app}-backend:{revision}"
    os.environ["NORTHSTAR_BACKEND_IMAGE"] = backend
    os.environ["NORTHSTAR_LIVE_IMAGE"] = backend
    key = {"data-hub": "DATA", "research": "RESEARCH", "live": "LIVE"}.get(app)
    if key:
        os.environ[f"NORTHSTAR_{key}_FRONTEND_IMAGE"] = f"northstar-{app}-frontend:{revision}"


def live_guard(project: str) -> None:
    # No deployment-owned check can atomically drain/authorize a trading session yet.
    # Refuse running, restarting and paused kernels; an absent/stopped kernel is never killed.
    ids = run(
        "docker",
        "ps",
        "-aq",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--filter",
        "label=com.docker.compose.service=live",
        capture=True,
    ).splitlines()
    for identity in ids:
        state = json.loads(
            run("docker", "inspect", "--format", "{{json .State}}", identity, capture=True)
        )
        if state.get("Running") or state.get("Restarting") or state.get("Paused"):
            raise ValueError(
                "Live 内核运行中：拒绝部署/停止。须先在主机完成会话核对和维护停机；无强制选项。"
            )


def execute(request: dict) -> None:
    app, action = request["app"], request["action"]
    folder, target, project = TARGETS[app]
    root = Path(request["directory"])
    env_file = Path(request["env_file"])
    if action == "deploy":
        root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ValueError("尚未部署该对象")
    # Lock every invocation, except following logs after command launch; no hidden retry.
    with (root / ".deployment.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("该对象已有部署/管理命令运行") from None
        if not env_file.is_file() or stat.S_IMODE(env_file.stat().st_mode) & 0o077:
            raise ValueError("目标 env_file 必须已存在且仅所属用户可访问（chmod 600）")
        # No ambient Compose or Make flags may redirect operations to a different project.
        for key in list(os.environ):
            if key.startswith(("NORTHSTAR_", "COMPOSE_")) or key in (
                "MAKEFLAGS",
                "MAKEOVERRIDES",
                "MFLAGS",
                "ENV_FILE",
            ):
                os.environ.pop(key)
        os.environ["COMPOSE_PROJECT_NAME"] = project
        run("docker", "info", capture=True)
        run("docker", "compose", "version", capture=True)
        if app == "live" and action in ("deploy", "stop"):
            live_guard(project)
        active = root / "current"
        if action == "deploy":
            for tool in ("git", "make", "uv"):
                if shutil.which(tool) is None:
                    raise ValueError(f"目标主机缺少 {tool}")
            releases = root / "releases"
            releases.mkdir(exist_ok=True)
            revision = request["revision"]
            release = releases / revision
            with tempfile.TemporaryDirectory(prefix=".upload-", dir=root) as temporary:
                bundle = Path(temporary) / "source.bundle"
                with bundle.open("wb") as destination:
                    shutil.copyfileobj(sys.stdin.buffer, destination)
                if not release.exists():
                    checkout = Path(temporary) / "checkout"
                    run("git", "clone", "--quiet", str(bundle), str(checkout))
                    run("git", "checkout", "--quiet", "--detach", revision, cwd=checkout)
                    checkout.rename(release)
            if run("git", "rev-parse", "HEAD", cwd=release, capture=True) != revision or run(
                "git", "status", "--porcelain", "--untracked-files=all", cwd=release, capture=True
            ):
                raise ValueError("目标版本目录身份不匹配或有修改，拒绝覆盖")
            compose = [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "-p",
                project,
                "-f",
                str(release / "deploy" / folder / "compose.yaml"),
            ]
            image_environment(app, revision)
            run(*compose, "config", "--quiet")
            if app == "live":
                live_guard(project)
            # Point management commands at the attempted release, even if up partially fails.
            # Never claim an atomic rollback of databases/containers.
            link = root / ".current-next"
            link.unlink(missing_ok=True)
            link.symlink_to(release)
            link.replace(active)
            run("make", f"up-{target}", f"ENV_FILE={env_file}", cwd=release)
            (root / "successful-revision").write_text(revision + "\n")
            print(f"部署完成：{app} {revision}", flush=True)
        else:
            if not active.is_symlink():
                raise ValueError("没有已记录的部署版本")
            release = active.resolve(strict=True)
            if release.parent != root.resolve() / "releases":
                raise ValueError("部署版本路径不属于该对象")
            compose = [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "-p",
                project,
                "-f",
                str(release / "deploy" / folder / "compose.yaml"),
            ]
            image_environment(app, release.name)
            print(f"当前配置版本：{release.name}", flush=True)
            if action == "status":
                successful = root / "successful-revision"
                print(
                    "最后成功版本："
                    + (successful.read_text().strip() if successful.exists() else "无"),
                    flush=True,
                )
                run(*compose, "ps", "--all")
            elif action == "logs":
                fcntl.flock(lock, fcntl.LOCK_UN)
                run(*compose, "logs", "--tail=100", *(["--follow"] if request["follow"] else []))
            elif action == "stop":
                run(*compose, "down")


if __name__ == "__main__":
    try:
        execute(json.loads(sys.argv[1]))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"远程操作失败：{error}", file=sys.stderr)
        sys.exit(1)

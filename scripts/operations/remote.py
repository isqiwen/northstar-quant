"""Remote half of northstarctl.py, sent over verified SSH; uses only Python's standard library."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

TARGETS = {
    "database": ("database", "northstar-database"),
    "data-hub": ("data_hub", "northstar-data-hub"),
    "research": ("research", "northstar-research"),
    "live": ("live", "northstar-live"),
}


def run(*args: str, cwd: Path | None = None, capture: bool = False) -> str:
    with subprocess.Popen(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        start_new_session=True,
    ) as process:
        try:
            output, _ = process.communicate()
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, args)
            return output.strip() if capture else ""
        except BaseException:
            # The process group contains this operation's clients, never Docker's containers.
            for signum in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(process.pid, signum)
                except ProcessLookupError:
                    break
                if signum == signal.SIGTERM:
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        pass
            process.wait()
            raise


def supervise_connection() -> None:
    """SSH without a PTY can leave the remote command orphaned after disconnect."""
    parent = os.getppid()

    def interrupted(signum, frame):
        # Do not interrupt cleanup with a second signal.
        for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            signal.signal(number, signal.SIG_IGN)
        raise SystemExit(128 + signum)

    for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(number, interrupted)

    def watch():
        interval = threading.Event()
        while not interval.wait(0.25):
            if parent == 1 or os.getppid() != parent:
                os.kill(os.getpid(), signal.SIGTERM)
                return

    threading.Thread(target=watch, daemon=True).start()


def image_environment(app: str, revision: str) -> None:
    # Separate tags prevent two applications building on one host from racing :local.
    backend = f"northstar-{app}-backend:{revision}"
    os.environ["NORTHSTAR_BACKEND_IMAGE"] = backend
    os.environ["NORTHSTAR_LIVE_IMAGE"] = backend
    key = {"data-hub": "DATA", "research": "RESEARCH", "live": "LIVE"}.get(app)
    if key:
        os.environ[f"NORTHSTAR_{key}_FRONTEND_IMAGE"] = f"northstar-{app}-frontend:{revision}"


def execute(request: dict) -> None:
    os.environ["PATH"] += os.pathsep + str(Path.home() / ".local/bin") + ":/usr/sbin:/sbin"
    app, action = request["app"], request["action"]
    folder, project = TARGETS[app]
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
        active = root / "current"
        if action == "deploy":
            for tool in ("git", "uv"):
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
            # Point management commands at the attempted release, even if up partially fails.
            # Never claim an atomic rollback of databases/containers.
            link = root / ".current-next"
            link.unlink(missing_ok=True)
            link.symlink_to(release)
            link.replace(active)
            run(
                sys.executable,
                "scripts/operations/compose.py",
                "deploy",
                app,
                cwd=release,
            )
            (root / "successful-revision").write_text(revision + "\n")
            print(f"部署完成：{app} {revision}", flush=True)
        else:
            if not active.is_symlink():
                raise ValueError("没有已记录的部署版本")
            release = active.resolve(strict=True)
            if release.parent != root.resolve() / "releases":
                raise ValueError("部署版本路径不属于该对象")
            image_environment(app, release.name)
            print(f"当前配置版本：{release.name}", flush=True)
            if action == "status":
                successful = root / "successful-revision"
                print(
                    "最后成功版本："
                    + (successful.read_text().strip() if successful.exists() else "无"),
                    flush=True,
                )
                run(
                    sys.executable,
                    "scripts/operations/compose.py",
                    "status",
                    app,
                    cwd=release,
                )
            elif action == "logs":
                fcntl.flock(lock, fcntl.LOCK_UN)
                run(
                    sys.executable,
                    "scripts/operations/compose.py",
                    "logs",
                    app,
                    *(["--follow"] if request["follow"] else []),
                    cwd=release,
                )
            elif action in ("start", "restart", "stop"):
                if action != "stop":
                    successful = root / "successful-revision"
                    if not successful.exists() or successful.read_text().strip() != release.name:
                        raise ValueError("当前版本尚未成功部署，请先重试 deploy")
                    if run(
                        "git", "rev-parse", "HEAD", cwd=release, capture=True
                    ) != release.name or run(
                        "git",
                        "status",
                        "--porcelain",
                        "--untracked-files=all",
                        cwd=release,
                        capture=True,
                    ):
                        raise ValueError("已部署版本有修改，拒绝启动；请通过 deploy 更新")
                run(
                    sys.executable,
                    "scripts/operations/compose.py",
                    action,
                    app,
                    cwd=release,
                )


if __name__ == "__main__":
    supervise_connection()
    try:
        execute(json.loads(sys.argv[1]))
    except subprocess.CalledProcessError as error:
        print(f"远程操作失败：命令返回 {error.returncode}，请查看上方错误。", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError) as error:
        print(f"远程操作失败：{error}", file=sys.stderr)
        sys.exit(1)

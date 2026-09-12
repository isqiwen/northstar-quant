"""Local Compose operations shared by Make shortcuts and remote management."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/src"))
FOLDERS = {
    "database": "database",
    "data-hub": "data_hub",
    "research": "research",
    "live": "live",
}


def run(*args: str, cwd: Path | None = None, capture: bool = False) -> str:
    result = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout.strip() if capture else ""


def lifecycle(
    compose: list[str],
    app: str,
    action: str,
    *,
    runner: Callable[..., str] = run,
) -> None:
    """Manage the complete selected deployment using its existing images."""
    services = ["postgres"] if app == "database" else []
    if action == "stop":
        runner(*compose, "down")
        return
    runner(
        *compose,
        "up",
        "--no-build",
        "--pull",
        "never",
        "-d",
        "--wait",
        "--wait-timeout",
        "180",
        "--force-recreate" if action == "restart" else "--no-recreate",
        *services,
    )


def reset_workspace(
    compose: list[str],
    app: str,
    *,
    credentials: Path = Path("/opt/northstar/credentials"),
    runner: Callable[..., str] = run,
) -> None:
    """Deploy revokes the old browser identity after stopping its only writer."""
    api = {"data-hub": "data-api", "research": "research-api", "live": "live-api"}[app]
    runner(*compose, "stop", api)
    account = credentials / app / "workspace" / ("northstar_" + app.replace("-", "_") + ".json")
    account.unlink(missing_ok=True)


def manage(app: str, action: str, *, follow: bool = False, instance: str | None = None) -> None:
    from contextlib import nullcontext

    from publication_network import publication_hosts

    folder = FOLDERS[app]
    base = [
        "docker",
        "compose",
        "--env-file",
        f"/opt/northstar/config/{app}.env",
        "-f",
        str(ROOT / "deploy" / folder / "compose.yaml"),
    ]
    if app == "live":
        from live_instances import prepare, topology

        config = json.loads(run(*base, "config", "--format", "json", capture=True))
        with topology(config) as (files, rendered):
            if action in {"deploy", "start", "restart"}:
                prepare(rendered)
            if instance is not None:
                if f"{instance}-live" not in rendered["services"]:
                    raise ValueError("Unknown configured Live instance")
                command = base[:-2] + files
                services = [f"{instance}-live"]
                if action == "status":
                    run(*command, "ps", "--all", *services)
                elif action == "logs":
                    run(
                        *command, "logs", "--tail=100", *(["--follow"] if follow else []), *services
                    )
                elif action == "stop":
                    run(*command, "stop", *services)
                else:
                    if action == "restart":
                        run(*command, "stop", *services)
                    run(*command, "up", "--no-build", "--pull", "never", "-d", "--wait", *services)
                return
            _manage(app, action, follow=follow, overrides=files)
        return
    context = nullcontext([])
    if app == "research" and action in {"deploy", "start", "restart", "backup"}:
        config = json.loads(run(*base, "config", "--format", "json", capture=True))
        context = publication_hosts(config)
    with context as overrides:
        _manage(app, action, follow=follow, overrides=overrides)


def _manage(app: str, action: str, *, follow: bool, overrides: list[str]) -> None:
    folder = FOLDERS[app]
    env_file = Path(f"/opt/northstar/config/{app}.env")
    compose = [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(ROOT / "deploy" / folder / "compose.yaml"),
    ]
    if app == "live" and overrides:
        compose = compose[:-2] + overrides
    else:
        compose.extend(overrides)
    if project := os.environ.get("COMPOSE_PROJECT_NAME"):
        compose[2:2] = ["-p", project]
    if app in {"data-hub", "research", "live"} and action in {
        "deploy",
        "start",
        "restart",
    }:
        config = json.loads(run(*compose, "config", "--format", "json", capture=True))
        service = "live-web" if app == "live" else app
        port = str(config["services"][service]["ports"][0]["published"])
        run(
            *([] if os.geteuid() == 0 else ["sudo", "-n", "--"]),
            sys.executable,
            "scripts/operations/web_firewall.py",
            "install",
            app,
            port,
            cwd=ROOT,
        )
    if action in ("deploy", "start", "restart", "backup"):
        bindings = run(
            sys.executable,
            "scripts/operations/check_storage.py",
            "--app",
            folder,
            cwd=ROOT,
            capture=True,
        )
        os.environ.update(json.loads(bindings))
    if action == "deploy":
        from northstar_quant import code_revision

        os.environ["NORTHSTAR_GIT_REVISION"] = code_revision()
        if app != "database":
            reset_workspace(compose, app)
        if app == "database":
            run(*compose, "build", "initialize")
            run(*compose, "up", "-d", "--wait", "--wait-timeout", "180", "postgres")
            run(*compose, "run", "--rm", "initialize")
            run(
                sys.executable,
                "scripts/operations/check_storage.py",
                "--app",
                "database",
                "--complete",
                cwd=ROOT,
                capture=True,
            )
        elif app in {"data-hub", "research"}:
            api = "data-api" if app == "data-hub" else "research-api"
            run(*compose, "build", api, app)
            run(
                *compose,
                "up",
                "--no-build",
                "--force-recreate",
                "-d",
                "--wait",
                "--wait-timeout",
                "180",
            )
        else:
            run(
                *compose,
                "up",
                "--build",
                "--remove-orphans",
                "-d",
                "--wait",
                "--wait-timeout",
                "180",
            )
    elif action in ("start", "restart", "stop"):
        if action != "stop" and app in ("data-hub", "research"):
            run(*compose, "run", "--rm", "--no-deps", "--pull", "never", "storage-check")
        lifecycle(compose, app, action)
    elif action == "status":
        run(*compose, "ps", "--all")
    elif action == "logs":
        run(*compose, "logs", "--tail=100", *(["--follow"] if follow else []))
    elif action == "backup":
        if app == "database":
            run(*compose, "run", "--rm", "--no-deps", "backup")
        else:
            run(
                *compose,
                "run",
                "--rm",
                "--no-deps",
                "maintenance",
                "northstar",
                "maintenance",
                "backup",
                f"/var/lib/northstar/backup/research-{uuid4()}",
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="在当前主机执行应用 Compose 操作")
    parser.add_argument(
        "action",
        choices=("deploy", "start", "restart", "stop", "status", "logs", "backup"),
    )
    parser.add_argument("app", choices=FOLDERS)
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("--instance", help="Only manage this configured Live instance")
    args = parser.parse_args()
    if args.instance and (args.app != "live" or args.action in {"deploy", "backup"}):
        parser.error("--instance only supports Live start/restart/stop/status/logs")
    if args.follow and args.action != "logs":
        parser.error("--follow 仅用于 logs")
    if args.action == "backup" and args.app not in ("database", "research"):
        parser.error("backup 仅用于 database 或 research")
    try:
        manage(args.app, args.action, follow=args.follow, instance=args.instance)
    except (OSError, ValueError, subprocess.CalledProcessError):
        print("本机操作失败，请检查上述错误与目标配置。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

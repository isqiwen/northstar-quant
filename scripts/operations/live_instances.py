"""Expand the owned Live Compose template into isolated account instances."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from northstar_quant.live.instances import configured_instances


def expand(config: dict) -> dict:
    instances = configured_instances(config.pop("x-instances", "sim:simnow_trading"))
    templates = config["services"]
    services = {k: v for k, v in templates.items() if k in {"live-api", "live-web"}}
    networks = {k: v for k, v in config["networks"].items() if k in {"frontend", "ingress"}}
    endpoints = []
    auth_volumes = []
    dependencies = {}
    for instance in instances:
        name = instance.identifier
        names = {key: f"{name}-{key}" for key in ("initialize", "live")}
        for network in ("management", "egress"):
            networks[f"{name}-{network}"] = {"internal": True} if network != "egress" else {}
        for key, service_name in names.items():
            service = copy.deepcopy(templates[key])
            service["networks"] = {f"{name}-{k}": v for k, v in service["networks"].items()}
            service["depends_on"] = {names[k]: v for k, v in service.get("depends_on", {}).items()}
            environment = service.get("environment", {})
            if key == "live":
                environment["NORTHSTAR_LIVE_INSTANCE"] = name
                environment["NORTHSTAR_LIVE_ENVIRONMENT"] = instance.environment
            for volume in service.get("volumes", []):
                if volume.get("type") == "bind":
                    volume["source"] = volume["source"].replace(
                        "/live", f"/live/instances/{name}", 1
                    )
            # Auth and source directories are host-owned. Run Python as that same
            # UID rather than weakening owner-only authentication checks.
            service["user"] = f"{os.getuid()}:{os.getgid()}"
            if key == "live":
                service["volumes"].append(
                    {
                        "type": "bind",
                        "source": "/opt/northstar/state/live/accounts",
                        "target": "/opt/northstar/state/live/accounts",
                        "read_only": False,
                        "bind": {"create_host_path": False},
                    }
                )
            services[service_name] = service
        endpoints.append(
            {
                "id": name,
                "environment": instance.environment,
                "url": f"http://{name}-live:18081",
                "auth": f"/var/lib/northstar/auth/{name}/live-web.toml",
            }
        )
        auth_volumes.append(
            {
                "type": "bind",
                "source": f"/opt/northstar/credentials/live/instances/{name}",
                "target": f"/var/lib/northstar/auth/{name}",
                "read_only": True,
                "bind": {"create_host_path": False},
            }
        )
        dependencies[f"{name}-initialize"] = {"condition": "service_completed_successfully"}
    api = services["live-api"]
    api["environment"] = {
        "NORTHSTAR_LOG_DIR": "/var/log/northstar",
        "NORTHSTAR_LIVE_ENDPOINTS": json.dumps(endpoints),
    }
    api["user"] = f"{os.getuid()}:{os.getgid()}"
    api["volumes"] = [
        v for v in api["volumes"] if v["target"] != "/var/lib/northstar/auth"
    ] + auth_volumes
    api["depends_on"] = dependencies
    api["networks"] = {"frontend": {}, **{f"{i.identifier}-management": {} for i in instances}}
    config["services"], config["networks"] = services, networks
    return config


def prepare(config: dict) -> None:
    """Create new instance leaves only; never rename or erase an old account store."""
    result = subprocess.run(
        [
            "docker",
            "ps",
            "--filter",
            "label=com.docker.compose.project=" + config["name"],
            "--format",
            '{{.Label "com.docker.compose.service"}}',
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if set(result.stdout.split()) - set(config["services"]):
        raise ValueError(
            "An instance removed from configuration is still running; stop it with "
            "its saved configuration first"
        )
    for service in config["services"].values():
        for volume in service.get("volumes", []):
            if volume.get("type") != "bind":
                continue
            path = Path(volume["source"])
            if not path.is_relative_to("/opt/northstar"):
                raise ValueError("Live bind escapes the owned deployment root")
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise ValueError("Live instance directories cannot be symlinks")
            if not path.exists():
                path.mkdir(parents=True, mode=0o700)


@contextmanager
def topology(config: dict):
    rendered = expand(config)
    with tempfile.TemporaryDirectory(prefix="northstar-live-compose-") as temporary:
        path = Path(temporary) / "compose.json"
        path.touch(mode=0o600)
        path.write_text(json.dumps(rendered))
        yield ["-f", str(path)], rendered

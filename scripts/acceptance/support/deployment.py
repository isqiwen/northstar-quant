"""Render production Compose into a private disposable filesystem for acceptance only."""

import hashlib
import ipaddress
import json
import os
import subprocess
from functools import cache
from pathlib import Path


@cache
def docker_host_gateway() -> str:
    # Some Engine builds render the host-gateway keyword as "invalid IP".
    # Resolve the actual default bridge gateway for this disposable host probe.
    result = subprocess.run(
        ["docker", "network", "inspect", "bridge"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    for config in json.loads(result.stdout)[0]["IPAM"]["Config"]:
        gateway = config.get("Gateway")
        if gateway and ipaddress.ip_address(gateway).version == 4:
            return gateway
    raise ValueError("Acceptance requires an IPv4 Docker host gateway")


def isolated_compose(
    source: Path,
    destination: Path,
    root: Path,
    environment: dict[str, str],
    bindings: dict[str, Path] | None = None,
) -> Path:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            os.devnull,
            "--profile",
            "*",
            "-f",
            str(source),
            "config",
            "--format",
            "json",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    config = json.loads(result.stdout)
    if source.parent.name == "live":
        import sys

        sys.path.insert(0, str(source.parents[2] / "scripts/operations"))
        from live_instances import expand

        config = expand(config)
    prefix = "northstar-check-" + hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]
    # `compose config` expands default network names before the caller's -p.
    # Rebind every name, retaining equal names only for intentionally shared
    # networks (for example the database/Data Hub storage network).
    for name, network in config.get("networks", {}).items():
        original = network.get("name", name)
        network["name"] = prefix + "-" + hashlib.sha256(original.encode()).hexdigest()[:12]
    for service in config["services"].values():
        if "extra_hosts" in service:
            service["extra_hosts"] = [
                entry.replace("host-gateway", docker_host_gateway())
                if entry.endswith(("=host-gateway", ":host-gateway"))
                else entry
                for entry in service["extra_hosts"]
            ]
        # Production ports are fixed; disposable concurrent runs need Docker to
        # allocate host ports, including APIs no longer controlled by env vars.
        for port in service.get("ports", []):
            port["published"] = "0"
        for volume in service.get("volumes", []):
            if volume.get("type") != "bind":
                continue
            original = Path(volume["source"])
            if not original.is_relative_to("/opt/northstar"):
                if not volume.get("read_only") or not original.is_file():
                    raise ValueError("Acceptance refuses a writable bind outside its private root")
                continue
            target = (bindings or {}).get(str(original)) or root / original.relative_to(
                "/opt/northstar"
            )
            if not target.resolve().is_relative_to(root.resolve()):
                raise ValueError("Acceptance bind escapes its private root")
            # Existing database directories may be unreadable to the host after PostgreSQL chown.
            if not target.exists():
                target.mkdir(parents=True)
            volume["source"] = str(target)
    destination.touch(mode=0o600, exist_ok=True)
    destination.write_text(json.dumps(config))
    return destination


def cleanup_files(root: Path, image: str) -> None:
    """Remove only this acceptance's generated directory after its containers stop."""
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--mount",
            f"type=bind,source={root},target=/cleanup",
            image,
            "python",
            "-c",
            "import pathlib,shutil; "
            "[shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink() "
            "for p in pathlib.Path('/cleanup').iterdir()]",
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )

"""Check application directories and identities; the host owns storage and mount configuration."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend/src"))

from northstar_quant.data_management.storage_identity import require_identity  # noqa: E402


def check_directories(
    config: dict, app: str, *, maintenance: bool = False, initializing: bool = False
) -> None:
    # Check every runtime bind, including credentials/logs/state, before creating containers.
    seen = set()
    for item in config["services"].values():
        for volume in item.get("volumes", []):
            if volume.get("type") != "bind":
                continue
            root = Path(volume["source"])
            if root in seen or (root.is_file() and volume.get("read_only")):
                continue
            require_directory(root)
            seen.add(root)
    if app == "live":
        return
    service = config["services"][
        "initialize" if app == "database" else "maintenance" if maintenance else "storage-check"
    ]
    roots: list[Path] = []
    first_install = initializing
    if app == "database":
        root = Path(config["services"]["postgres"]["volumes"][0]["source"])
        require_directory(root)
        roots.append(root.resolve())
        try:
            first_install = initializing or not any(root.iterdir())
        except PermissionError:
            # PostgreSQL owns PGDATA (usually mode 0700); do not inspect its contents.
            # Existing storage identities are still required in this case.
            first_install = initializing
    identities: set[str] = set()
    for volume in service["volumes"]:
        if volume.get("type") != "bind":
            continue
        root = Path(volume["source"])
        require_directory(root)
        if any(root.is_relative_to(other) or other.is_relative_to(root) for other in roots):
            raise ValueError("Storage directories must be distinct and non-overlapping")
        roots.append(root)
        share = Path(volume["target"]).name.upper()
        identity = service["environment"].get(f"NORTHSTAR_{share}_STORAGE_ID")
        if identity is None:
            continue
        if str(UUID(identity)) != identity or identity in identities:
            raise ValueError("Each storage directory requires a distinct canonical UUID")
        identities.add(identity)
        # A genuinely empty first installation is initialized by database provisioning.
        # Never recreate identity on redeploy or on a nonempty unrecognized directory.
        if not (first_install and not any(root.iterdir())):
            require_identity(root, identity)
        if (root / ".restore-incomplete").exists():
            raise ValueError("Storage restore is incomplete")
        print(f"Storage directory verified for {app}: {root}", file=sys.stderr)


def require_directory(root: Path) -> None:
    if (
        not root.is_absolute()
        or not root.is_dir()
        or root.resolve() != root
        or root in {Path("/"), Path.home()}
    ):
        raise ValueError(
            "Storage requires an existing dedicated absolute directory without symlinks"
        )


def main() -> None:
    from nfs import verify_client
    from storage_bindings import bind

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--app", required=True, choices=["database", "data_hub", "research", "live"]
    )
    parser.add_argument("--maintenance", action="store_true")
    parser.add_argument("--complete", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    env_file = f"/opt/northstar/config/{args.app.replace('_', '-')}.env"
    command = ["docker", "compose", "--profile", "*", "--env-file", env_file]
    command.extend(
        ["-f", str(ROOT / "deploy" / args.app / "compose.yaml"), "config", "--format", "json"]
    )
    try:
        if args.app != "live":
            verify_client()
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        config = json.loads(result.stdout)
        environment, pending = {}, False
        if args.app != "live":
            owner = "data-hub" if args.app in {"data_hub", "database"} else "research"
            path = Path(f"/opt/northstar/state/{owner}/bindings/storage.json")
            environment, pending = bind(config, args.app, path, complete=args.complete)
            for service in config["services"].values():
                service.setdefault("environment", {}).update(environment)
        check_directories(config, args.app, maintenance=args.maintenance, initializing=pending)
        print(json.dumps(environment))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(
            2, f"Storage not ready: {error}\nNothing started. Prepare directories explicitly.\n"
        )


if __name__ == "__main__":
    main()

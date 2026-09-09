"""Check application directories and identities; the host owns storage and mount configuration."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from uuid import UUID

from northstar_quant.data_management.storage_identity import require_identity

ROOT = Path(__file__).resolve().parents[2]


def check_directories(config: dict, app: str, *, maintenance: bool = False) -> None:
    service = config["services"][
        "initialize" if app == "database" else "maintenance" if maintenance else "storage-check"
    ]
    roots: list[Path] = []
    first_install = False
    if app == "database":
        root = Path(config["services"]["postgres"]["volumes"][0]["source"])
        require_directory(root)
        roots.append(root.resolve())
        try:
            first_install = not any(root.iterdir())
        except PermissionError:
            # PostgreSQL owns PGDATA (usually mode 0700); do not inspect its contents.
            # Existing storage identities are still required in this case.
            first_install = False
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
        identity = service["environment"][f"NORTHSTAR_{share}_STORAGE_ID"]
        if str(UUID(identity)) != identity or identity in identities:
            raise ValueError("Each storage directory requires a distinct canonical UUID")
        identities.add(identity)
        # A genuinely empty first installation is initialized by database provisioning.
        # Never recreate identity on redeploy or on a nonempty unrecognized directory.
        if not (first_install and not any(root.iterdir())):
            require_identity(root, identity)
        if (root / ".restore-incomplete").exists():
            raise ValueError("Storage restore is incomplete")
        print(f"Storage directory verified for {app}: {root}")


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, choices=["database", "data_hub", "research"])
    parser.add_argument("--env-file")
    parser.add_argument("--maintenance", action="store_true")
    args = parser.parse_args()
    command = ["docker", "compose"]
    if args.env_file:
        command.extend(["--env-file", args.env_file])
    command.extend(
        ["-f", str(ROOT / "deploy" / args.app / "compose.yaml"), "config", "--format", "json"]
    )
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        check_directories(json.loads(result.stdout), args.app, maintenance=args.maintenance)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(
            2, f"Storage not ready: {error}\nNothing started. Prepare directories explicitly.\n"
        )


if __name__ == "__main__":
    main()

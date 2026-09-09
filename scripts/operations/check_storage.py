"""Check explicitly selected local or NFS storage before starting application containers."""

from __future__ import annotations

import argparse
import json
import shlex
import socket
import subprocess
from pathlib import Path
from uuid import UUID

from northstar_quant.data_management.storage_identity import verify_local_directory, verify_mount

ROOT = Path(__file__).resolve().parents[2]


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
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        parser.exit(
            2, "Compose parameters incomplete; fill in the private host environment file.\n"
        )
    service = json.loads(result.stdout)["services"][
        "initialize"
        if args.app == "database"
        else "maintenance"
        if args.maintenance
        else "storage-check"
    ]
    if args.app == "database":
        postgres = json.loads(result.stdout)["services"]["postgres"]
        path = postgres["volumes"][0]["source"]
        if not Path(path).is_dir() or Path(path).is_symlink():
            parser.exit(2, "Create a dedicated core local PGDATA directory first.\n")
        mount = subprocess.run(
            ["findmnt", "--json", "--target", path, "--output", "FSTYPE"],
            capture_output=True,
            text=True,
            check=True,
        )
        kind = json.loads(mount.stdout)["filesystems"][0]["fstype"]
        if kind not in {"ext4", "xfs", "btrfs", "zfs"}:
            parser.exit(
                2, "PGDATA requires a verified local persistent filesystem, not NFS/SMB/tmpfs.\n"
            )
    env = service["environment"]
    mode = env.get("NORTHSTAR_STORAGE_MODE", "nfs")
    if mode not in {"local", "nfs"}:
        parser.exit(2, "NORTHSTAR_STORAGE_MODE must be local or nfs.\n")
    roots = [Path(path).resolve()] if args.app == "database" else []
    identities = set()
    for volume in service["volumes"]:
        if volume.get("type") == "bind":
            root = Path(volume["source"])
            if any(
                root.resolve().is_relative_to(other) or other.is_relative_to(root.resolve())
                for other in roots
            ):
                parser.exit(2, "Storage directories must be distinct and non-overlapping.\n")
            roots.append(root.resolve())
    for volume in service["volumes"]:
        if volume.get("type") != "bind":
            continue
        mount = volume["source"]
        share = Path(volume["target"]).name.upper()
        identity = env[f"NORTHSTAR_{share}_STORAGE_ID"]
        if identity in identities:
            parser.exit(2, "Each storage directory requires a distinct UUID.\n")
        identities.add(identity)
        if str(UUID(identity)) != identity:
            parser.exit(2, "Storage identity must be a canonical UUID.\n")
        if mode == "local":
            result = subprocess.run(
                ["findmnt", "--json", "--target", mount, "--output", "FSTYPE,OPTIONS"],
                capture_output=True,
                text=True,
                check=False,
            )
            rows = (
                json.loads(result.stdout).get("filesystems", []) if result.returncode == 0 else []
            )
            verify_local_directory(
                Path(mount),
                rows[0] if len(rows) == 1 else {},
                writable=not volume.get("read_only", False),
            )
            print(f"Local storage verified for {args.app}: {mount}")
            continue
        expected = {
            "server": socket.gethostbyname(env["NORTHSTAR_NAS_ADDRESS"]),
            "hostname": env["NORTHSTAR_NAS_ADDRESS"],
            "export": env[f"NORTHSTAR_{share}_NFS_EXPORT"],
            "version": env["NORTHSTAR_NFS_VERSION"],
            "mount": mount,
            "mode": "ro" if volume.get("read_only") else "rw",
        }
        try:
            result = subprocess.run(
                [
                    "findmnt",
                    "--json",
                    "--mountpoint",
                    mount,
                    "--output",
                    "TARGET,SOURCE,FSTYPE,OPTIONS",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            rows = (
                json.loads(result.stdout).get("filesystems", []) if result.returncode == 0 else []
            )
            verify_mount(expected, rows[0] if len(rows) == 1 else {})
        except (OSError, ValueError) as error:
            suggestion = shlex.join(
                [
                    "sudo",
                    "mount",
                    "-t",
                    "nfs",
                    "-o",
                    f"vers={expected['version']},hard,{expected['mode']}",
                    f"{expected['server']}:{expected['export']}",
                    mount,
                ]
            )
            parser.exit(
                2,
                f"{error}\nNothing started. Verify QNAP export and prepare the mount explicitly:\n"
                f"{suggestion}\n",
            )
        print(f"NFS mount verified for {args.app}: {mount} ({expected['mode']})")


if __name__ == "__main__":
    main()

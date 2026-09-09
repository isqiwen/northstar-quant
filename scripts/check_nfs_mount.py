"""Check an existing Linux NFS mount before an application may start; never mount implicitly."""

from __future__ import annotations

import argparse
import json
import shlex
import socket
import subprocess
from pathlib import Path

from northstar_quant.data_management.storage_identity import verify_mount

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, choices=["data_hub", "research"])
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
        "maintenance" if args.maintenance else "storage-check"
    ]
    env = service["environment"]
    for volume in service["volumes"]:
        if volume.get("type") != "bind":
            continue
        mount = volume["source"]
        share = Path(volume["target"]).name.upper()
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

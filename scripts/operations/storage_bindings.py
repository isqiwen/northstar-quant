"""Automatic, durable host bindings for application storage; never operator configuration."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

from northstar_quant.data_management.storage_identity import (
    initialize,
    read_identity,
    require_identity,
)

SHARES = ("SOURCE", "MARKET", "RESEARCH", "BACKUP")


def directory_map(config: dict, app: str) -> dict[str, Path]:
    service = config["services"]["initialize" if app == "database" else "maintenance"]
    result = {}
    for volume in service["volumes"]:
        share = Path(volume["target"]).name.upper()
        if volume.get("type") == "bind" and share in SHARES:
            result[share] = Path(volume["source"])
    return result


def bind(
    config: dict, app: str, path: Path, *, complete: bool = False
) -> tuple[dict[str, str], bool]:
    """Pin logical storage identities, including newly empty local fallback directories."""
    roots = directory_map(config, app)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (path.parent / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.is_symlink():
            raise ValueError("Storage bindings must not be a symbolic link")
        if path.exists():
            document = json.loads(path.read_text())
            identities = document["identities"]
            if (
                document.get("version") != 1
                or type(document.get("initialized")) is not bool
                or not isinstance(identities, dict)
                or not set(roots) <= identities.keys()
            ):
                raise ValueError("Invalid persisted storage bindings")
            if any(str(UUID(value)) != value for value in identities.values()) or len(
                set(identities.values())
            ) != len(identities):
                raise ValueError("Invalid or duplicate bound storage identity")
        else:
            if app == "data_hub" or complete:
                raise ValueError(
                    "Storage bindings missing; initialize database first or restore state"
                )
            state = (
                config["services"]["postgres"]["volumes"][0]["source"]
                if app == "database"
                else None
            )
            if state is not None:
                try:
                    if any(Path(state).iterdir()):
                        raise ValueError(
                            "Existing database requires its persisted storage bindings"
                        )
                except PermissionError:
                    raise ValueError(
                        "Existing database requires its persisted storage bindings"
                    ) from None
            elif (path.parent.parent / "research.sqlite3").exists():
                raise ValueError(
                    "Existing Research database requires its persisted storage bindings"
                )
            identities = {}
            for share, root in roots.items():
                if not root.is_dir() or root.is_symlink():
                    raise ValueError("Storage directory missing")
                if (root / ".northstar-storage-id").exists():
                    identities[share] = read_identity(root)
                elif app == "database" and not any(root.iterdir()):
                    identities[share] = str(uuid4())
                else:
                    raise ValueError("Storage not initialized; prepare published storage first")
            if len(set(identities.values())) != len(identities):
                raise ValueError("Storage identities must be distinct")
            document = {"version": 1, "identities": identities, "initialized": app != "database"}
        pending = not document["initialized"] and app == "database" and not complete
        for share, root in roots.items():
            if not root.is_absolute() or root.resolve() != root:
                raise ValueError("Storage requires an absolute directory without symlinks")
            root.mkdir(mode=0o750, parents=True, exist_ok=True)
            if not pending and not any(root.iterdir()):
                # The path may now use local disk. Reuse the logical identity; retained
                # manifests still verify every referenced file before data can be read.
                initialize(root, identities[share])
            if pending and root.is_dir() and not any(root.iterdir()):
                continue
            require_identity(root, identities[share])
        if complete:
            document["initialized"] = True
        if not path.exists() or complete:
            descriptor, temporary = tempfile.mkstemp(prefix=".bindings-", dir=path.parent)
            try:
                with os.fdopen(descriptor, "w") as stream:
                    json.dump(document, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                Path(temporary).unlink(missing_ok=True)
        environment = {
            f"NORTHSTAR_{share}_STORAGE_ID": value for share, value in identities.items()
        }
        if "SOURCE" in identities:
            environment["NORTHSTAR_STORAGE_ID"] = identities["SOURCE"]
        return environment, pending

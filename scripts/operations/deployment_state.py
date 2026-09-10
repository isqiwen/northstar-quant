"""Durable deployment outcome and observed container identities, never configuration values."""

import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def save(path: Path, record: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".deployment-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            json.dump(record, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def containers(project: str) -> list[dict]:
    ids = subprocess.check_output(
        ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + project],
        text=True,
        timeout=20,
    ).split()
    rows = []
    for identity in ids:
        data = json.loads(
            subprocess.check_output(["docker", "inspect", identity], text=True, timeout=20)
        )[0]
        rows.append(
            {
                "id": data["Id"],
                "service": data["Config"]["Labels"].get("com.docker.compose.service"),
                "image": data["Config"]["Image"],
                "image_id": data["Image"],
                "state": data["State"]["Status"],
                "health": data["State"].get("Health", {}).get("Status"),
            }
        )
    return rows


def record(path: Path, revision: str, digest: str, phase: str, project: str) -> None:
    previous = json.loads(path.read_text()) if path.exists() else {}
    value = {
        "revision": revision,
        "configuration_sha256": digest,
        "phase": phase,
        "observed_at": datetime.now(UTC).isoformat(),
        "last_success": previous.get("last_success"),
    }
    if phase in ("verified", "complete"):
        value["last_success"] = {"revision": revision, "configuration_sha256": digest}
    try:
        value["containers"] = containers(project)
    except (OSError, ValueError, subprocess.SubprocessError):
        value["observation_error"] = "无法查询容器身份；不能据此确认当前运行版本"
    save(path, value)

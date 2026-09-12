"""Data Hub-owned scheduled backup entry; no application startup or trading authority."""

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from northstar_quant.data_management.backup import backup
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.storage_identity import require_identity
from northstar_quant.persistence.backup_files import file_hash, write_record


def run() -> None:
    root = Path(os.environ["NORTHSTAR_BACKUP_DIR"])
    require_identity(root, os.environ["NORTHSTAR_BACKUP_STORAGE_ID"])
    target = root / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex)
    target.mkdir(mode=0o700)
    source = Path(os.environ["NORTHSTAR_SOURCE_DIR"])
    require_identity(source, os.environ["NORTHSTAR_SOURCE_STORAGE_ID"])
    host = os.environ.get("NORTHSTAR_DATABASE_HOST", "postgres")
    for owner in ("data_hub",):
        engine = create_engine(
            URL.create(
                "postgresql+psycopg",
                username=f"northstar_{owner}_app",
                password=os.environ[f"NORTHSTAR_{owner.upper()}_DATABASE_PASSWORD"],
                host=host,
                database=f"northstar_{owner}",
            )
        )
        try:
            if owner == "data_hub":
                backup(engine, SourceFiles(source), target / "data_hub")
        finally:
            engine.dispose()
    binary = shutil.which("pg_dumpall")
    if binary is None:
        raise ValueError("pg_dumpall is required to preserve database roles")
    environment = dict(os.environ, PGPASSWORD=os.environ["NORTHSTAR_DATABASE_ADMIN_PASSWORD"])
    try:
        subprocess.run(
            [
                binary,
                "--host=" + host,
                "--username=northstar_admin",
                "--no-password",
                "--globals-only",
                f"--file={target / 'roles.sql'}",
            ],
            env=environment,
            check=True,
            capture_output=True,
            timeout=300,
        )
    except subprocess.CalledProcessError:
        raise ValueError("role backup failed; no complete backup is declared") from None
    (target / "roles.sql").chmod(0o600)
    identities = {
        name: os.environ[f"NORTHSTAR_{name}_STORAGE_ID"]
        for name in ("SOURCE", "MARKET", "RESEARCH", "BACKUP")
    }
    write_record(
        target / "storage-bindings.json",
        json.dumps(
            {
                "version": 1,
                "initialized": True,
                "identities": identities,
            },
            sort_keys=True,
        ).encode(),
    )
    document = {
        str(path.relative_to(target)): file_hash(path)
        for path in target.rglob("*")
        if path.is_file()
    }
    for name in document:
        with (target / name).open("rb") as stream:
            os.fsync(stream.fileno())
    for path in target.rglob("*"):
        if path.is_dir():
            SourceFiles._sync(path)
    write_record(target / "complete.json", json.dumps(document, sort_keys=True).encode())
    print(json.dumps({"status": "complete", "directory": str(target)}, ensure_ascii=False))


if __name__ == "__main__":
    run()

"""Local DuckDB reads only verified immutable files named by a publication."""

import hashlib
import re
from pathlib import Path
from typing import Any

import duckdb


def read_rows(root: Path, entry: dict[str, Any]) -> list[dict[str, Any]]:
    name = entry["path"]
    if not isinstance(name, str) or not re.fullmatch(r"[0-9a-f]{64}\.parquet", name):
        raise ValueError("invalid publication file path")
    path = root / name
    if path.is_symlink() or not path.is_file():
        raise ValueError("published market file unavailable")
    if not 0 < entry["bytes"] <= 32 * 1024 * 1024 or path.stat().st_size != entry["bytes"]:
        raise ValueError("publication parquet checksum or size mismatch")
    raw = path.read_bytes()
    if len(raw) != entry["bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
        raise ValueError("publication parquet checksum mismatch")
    # No shared .duckdb database, arbitrary SQL, extension downloads or remote SQL execution.
    with duckdb.connect(
        config={
            "enable_external_access": "true",
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
        }
    ) as connection:
        rows = connection.execute("SELECT * FROM read_parquet(?)", [str(path)])
        names = [column[0] for column in rows.description]
        result = [dict(zip(names, row, strict=True)) for row in rows.fetchall()]
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("publication changed during query")
        return result

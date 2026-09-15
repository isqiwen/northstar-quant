"""Pin and restore series manifests and exact normalized bytes in joint backups."""

from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Connection, text

from ..catalog.snapshots import verify, write
from ..files import SourceFiles


def references(c: Connection, content_hashes: list[str] | None = None) -> list[dict[str, object]]:
    if content_hashes == []:
        return []
    query = "SELECT * FROM data_series_publications"
    if content_hashes is not None:
        query += """ WHERE manifest_hash=ANY(:hashes) OR EXISTS
            (SELECT 1 FROM jsonb_array_elements(manifest->'files') f
             WHERE f->>'sha256'=ANY(:hashes))"""
    result = []
    for row in c.execute(text(query), dict(hashes=content_hashes)).mappings():
        for entry in [
            *row["manifest"]["files"],
            dict(path=row["path"], sha256=row["manifest_hash"], bytes=row["manifest_bytes"]),
        ]:
            if content_hashes is None or entry["sha256"] in content_hashes:
                result.append(
                    dict(
                        source_id=str(
                            uuid5(NAMESPACE_URL, f"{row['publication_id']}/{entry['path']}")
                        ),
                        content_hash=entry["sha256"],
                        byte_count=entry["bytes"],
                    )
                )
    return result


def verify_all(c: Connection, root: Path) -> None:
    for row in c.execute(text("SELECT * FROM data_series_publications")).mappings():
        verify(root, row)


def restore(c: Connection, root: Path, source: SourceFiles) -> None:
    for row in c.execute(text("SELECT * FROM data_series_publications")).mappings():
        artifact = write(root, row["manifest"], source)
        if (artifact["sha256"], artifact["bytes"], artifact["path"]) != (
            row["manifest_hash"],
            row["manifest_bytes"],
            row["path"],
        ):
            raise ValueError("恢复的研究序列快照与固定身份不一致")

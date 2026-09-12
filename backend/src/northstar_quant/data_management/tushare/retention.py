"""Pin publication files in the existing joint source/database backup operation."""

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Connection, Engine, text

from ..files import SourceFiles
from . import publication


def references(connection: Connection) -> list[dict[str, object]]:
    from ..compaction import references as compacted_references

    result: list[dict[str, object]] = compacted_references(connection)
    for row in connection.execute(text("SELECT * FROM data_sync_receipts")).mappings():
        for role in ("manifest", "parquet"):
            result.append(
                {
                    "source_id": str(uuid5(NAMESPACE_URL, f"{row['receipt_id']}/{role}")),
                    "content_hash": row[f"{role}_hash"],
                    "byte_count": row[f"{role}_bytes"],
                }
            )
    return result


def restore_publications(engine: Engine, files: SourceFiles) -> None:
    with engine.connect() as connection:
        from ..compaction import references as compacted_references

        for item in compacted_references(connection):
            target = publication.storage()
            target.store(files.read(str(item["content_hash"]), int(item["byte_count"])))
        for row in connection.execute(text("SELECT * FROM data_sync_receipts")).mappings():
            target = publication.storage()
            for role in ("parquet", "manifest"):
                target.store(files.read(row[f"{role}_hash"], row[f"{role}_bytes"]))

"""Pin publication files in the existing joint source/database backup operation."""

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import Connection, Engine, text

from ..files import SourceFiles
from . import publication


def references(
    connection: Connection, *, content_hashes: list[str] | None = None
) -> list[dict[str, object]]:
    from ..compaction import references as compacted_references

    result: list[dict[str, object]] = compacted_references(
        connection, content_hashes=content_hashes
    )
    query = "SELECT * FROM data_sync_receipts"
    if content_hashes is not None:
        query += " WHERE manifest_hash=ANY(:hashes) OR parquet_hash=ANY(:hashes)"
    for row in connection.execute(text(query), {"hashes": content_hashes}).mappings():
        for role in ("manifest", "parquet"):
            if content_hashes is not None and row[f"{role}_hash"] not in content_hashes:
                continue
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
        from ..contract_data.packages import restore_packages
        from ..publications import PublishedDatasets

        if connection.scalar(text("SELECT EXISTS(SELECT 1 FROM data_contract_publications)")):
            restore_packages(connection, PublishedDatasets.from_environment().root, files)
        from ..compaction import references as compacted_references

        for item in compacted_references(connection):
            target = publication.storage()
            target.store(files.read(str(item["content_hash"]), int(item["byte_count"])))
        for row in connection.execute(text("SELECT * FROM data_sync_receipts")).mappings():
            target = publication.storage()
            for role in ("parquet", "manifest"):
                target.store(files.read(row[f"{role}_hash"], row[f"{role}_bytes"]))

"""Storage constraints for immutable normalized observations and publications."""

from sqlalchemy import Connection

IMMUTABLE_TABLES = (
    "canonical_bar",
    "quality_evaluation",
    "quality_finding",
    "import_quality_evaluation",
    "import_quality_finding",
    "dataset_snapshot_manifest",
    "dataset_snapshot_partition",
    "dataset_snapshot_member",
    "dataset_snapshot_import_quality_pin",
    "dataset_snapshot_series_quality_pin",
)

CAPACITY_RULES = (
    (
        "quality_finding",
        "quality_evaluation",
        "NEW.quality_evaluation_id",
        "quality_evaluation_id = NEW.quality_evaluation_id",
        "finding_count",
    ),
    (
        "import_quality_finding",
        "import_quality_evaluation",
        "NEW.import_quality_evaluation_id",
        "import_quality_evaluation_id = NEW.import_quality_evaluation_id",
        "finding_count",
    ),
    (
        "dataset_snapshot_partition",
        "dataset_snapshot_manifest",
        "NEW.manifest_id",
        "manifest_id = NEW.manifest_id",
        "partition_count",
    ),
    (
        "dataset_snapshot_import_quality_pin",
        "dataset_snapshot_manifest",
        "NEW.manifest_id",
        "manifest_id = NEW.manifest_id",
        "import_quality_pin_count",
    ),
    (
        "dataset_snapshot_member",
        "dataset_snapshot_manifest",
        "(SELECT manifest_id FROM dataset_snapshot_partition WHERE id = NEW.partition_id)",
        "partition_id IN (SELECT id FROM dataset_snapshot_partition WHERE manifest_id = "
        "(SELECT manifest_id FROM dataset_snapshot_partition WHERE id = NEW.partition_id))",
        "member_count",
    ),
    (
        "dataset_snapshot_series_quality_pin",
        "dataset_snapshot_manifest",
        "(SELECT manifest_id FROM dataset_snapshot_partition WHERE id = NEW.partition_id)",
        "partition_id IN (SELECT id FROM dataset_snapshot_partition WHERE manifest_id = "
        "(SELECT manifest_id FROM dataset_snapshot_partition WHERE id = NEW.partition_id))",
        "series_quality_pin_count",
    ),
)


def initialize_sqlite(connection: Connection) -> None:
    for table in IMMUTABLE_TABLES:
        for action in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"""CREATE TRIGGER IF NOT EXISTS immutable_{table}_{action}
                BEFORE {action} ON {table}
                BEGIN SELECT RAISE(ABORT, 'Immutable archive evidence'); END"""
            )
    for child, parent, parent_id, predicate, count in CAPACITY_RULES:
        connection.exec_driver_sql(
            f"""CREATE TRIGGER IF NOT EXISTS capacity_{child} BEFORE INSERT ON {child}
                WHEN (SELECT count(*) FROM {child} WHERE {predicate}) >=
                     (SELECT {count} FROM {parent} WHERE id={parent_id})
                BEGIN SELECT RAISE(ABORT, 'Archive count exceeded'); END"""
        )
    for action in ("INSERT", "UPDATE"):
        connection.exec_driver_sql(
            f"""CREATE TRIGGER IF NOT EXISTS overlap_session_{action}
                BEFORE {action} ON trading_session WHEN EXISTS (
                    SELECT 1 FROM trading_session s WHERE s.calendar_id=NEW.calendar_id
                    AND s.id<>NEW.id AND s.opens_at<NEW.closes_at AND NEW.opens_at<s.closes_at)
                BEGIN SELECT RAISE(ABORT, 'Trading sessions overlap'); END"""
        )
    connection.exec_driver_sql("""
        CREATE TRIGGER IF NOT EXISTS revision_reference BEFORE INSERT ON canonical_bar
        WHEN NEW.revision_number > 1 AND (
            NOT EXISTS (SELECT 1 FROM canonical_bar p
                WHERE p.id=NEW.supersedes_canonical_bar_id AND p.series_id=NEW.series_id
                AND p.event_time=NEW.event_time AND p.trading_day=NEW.trading_day
                AND p.revision_number+1=NEW.revision_number AND p.available_at<NEW.available_at)
            OR NOT EXISTS (SELECT 1 FROM import_record r
                WHERE r.id=NEW.revision_source_import_record_id
                AND r.import_run_id=NEW.import_run_id

                AND r.disposition='INSERTED'
                AND r.normalized_payload_hash=NEW.normalized_payload_hash
                AND r.event_time=NEW.event_time)
            OR NOT EXISTS (SELECT 1 FROM import_record r
                WHERE r.id=NEW.supersession_evidence_import_record_id AND r.disposition='CONFLICT'
                AND r.conflicting_bar_id=NEW.supersedes_canonical_bar_id

                AND r.normalized_payload_hash=NEW.normalized_payload_hash
                AND r.event_time=NEW.event_time)
        ) BEGIN SELECT RAISE(ABORT, 'Archive revision reference mismatch'); END
    """)

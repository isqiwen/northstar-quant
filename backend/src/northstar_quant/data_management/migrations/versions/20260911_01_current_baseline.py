"""Create the only current Data Hub authority-store shape.

Revision ID: 20260911_01
Revises: none
Create Date: 2026-09-11
"""

from collections.abc import Sequence

from alembic import context, op

from northstar_quant.data_management.catalog import models as _models  # noqa: F401
from northstar_quant.data_management.catalog.integrity import (
    CAPACITY_RULES as _CAPACITY_RULES,
)
from northstar_quant.data_management.catalog.integrity import (
    IMMUTABLE_TABLES as _IMMUTABLE_TABLES,
)
from northstar_quant.data_management.db.base import Base

revision: str = "20260911_01"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the exact current schema; no historical database is converted."""

    if context.is_offline_mode():
        raise RuntimeError("the current baseline requires a live empty database")
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("Data Hub requires PostgreSQL")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    Base.metadata.create_all(bind=bind, checkfirst=False)
    _create_postgres_guards()


def downgrade() -> None:
    """There is no retired database form to downgrade to."""

    raise RuntimeError("the current Data Hub baseline has no downgrade path; rebuild the database")


def _create_postgres_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION qdh_reject_immutable_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is immutable', TG_TABLE_NAME USING ERRCODE = '23514';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in _IMMUTABLE_TABLES:
        for operation in ("UPDATE", "DELETE", "TRUNCATE"):
            level = "STATEMENT" if operation == "TRUNCATE" else "ROW"
            op.execute(
                f"""
                CREATE TRIGGER trg_{table_name}_no_{operation.lower()}
                BEFORE {operation} ON {table_name}
                FOR EACH {level} EXECUTE FUNCTION qdh_reject_immutable_mutation()
                """
            )
    for child_table, parent_table, parent_id, child_filter, count_column in _CAPACITY_RULES:
        function_name = f"qdh_enforce_{child_table}_count"
        op.execute(
            f"""
            CREATE FUNCTION {function_name}() RETURNS trigger AS $$
            BEGIN
                IF (SELECT COUNT(*) FROM {child_table} WHERE {child_filter}) >=
                   (SELECT {count_column} FROM {parent_table} WHERE id = {parent_id}) THEN
                    RAISE EXCEPTION '{child_table} exceeds its recorded count'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER trg_{child_table}_count_cap
            BEFORE INSERT ON {child_table}
            FOR EACH ROW EXECUTE FUNCTION {function_name}()
            """
        )
    _create_postgres_revision_reference_guard()


def _create_postgres_revision_reference_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION qdh_validate_observation_revision() RETURNS trigger AS $$
        BEGIN
            IF NEW.revision_number > 1 AND NOT EXISTS (
                SELECT 1 FROM canonical_bar parent
                WHERE parent.id = NEW.supersedes_canonical_bar_id
                  AND parent.series_id = NEW.series_id
                  AND parent.event_time = NEW.event_time
                  AND parent.trading_day = NEW.trading_day
                  AND parent.revision_number + 1 = NEW.revision_number
                  AND parent.available_at < NEW.available_at
            ) THEN
                RAISE EXCEPTION 'observation revision predecessor mismatch';
            END IF;
            IF NEW.revision_number > 1 AND NOT EXISTS (
                SELECT 1 FROM import_record source_record
                WHERE source_record.id = NEW.revision_source_import_record_id
                  AND source_record.import_run_id = NEW.import_run_id
                  AND source_record.disposition = 'INSERTED'
                  AND source_record.normalized_payload_hash = NEW.normalized_payload_hash
                  AND source_record.event_time = NEW.event_time
            ) THEN
                RAISE EXCEPTION 'observation revision source pin mismatch';
            END IF;
            IF NEW.revision_number > 1 AND NOT EXISTS (
                SELECT 1 FROM import_record conflict_record
                WHERE conflict_record.id = NEW.supersession_evidence_import_record_id
                  AND conflict_record.disposition = 'CONFLICT'
                  AND conflict_record.conflicting_bar_id = NEW.supersedes_canonical_bar_id
                  AND conflict_record.normalized_payload_hash = NEW.normalized_payload_hash
                  AND conflict_record.event_time = NEW.event_time
            ) THEN
                RAISE EXCEPTION 'observation supersession evidence mismatch';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_canonical_bar_revision_reference
        BEFORE INSERT ON canonical_bar
        FOR EACH ROW EXECUTE FUNCTION qdh_validate_observation_revision()
        """
    )

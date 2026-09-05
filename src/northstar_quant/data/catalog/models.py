"""Authoritative catalog, ingestion, quality, and snapshot persistence models."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from northstar_quant.data.db.base import Base

Uuid = sa.Uuid(as_uuid=True)
# The catalog exposes scales up to 12, so every canonical numeric column must
# retain at least 12 fractional digits. Do not lower these scales without a
# coordinated storage update: PostgreSQL rounds excess fractional precision
# while coercing NUMERIC values.
Price = sa.Numeric(24, 12, asdecimal=True)
Quantity = sa.Numeric(28, 12, asdecimal=True)
Turnover = sa.Numeric(32, 12, asdecimal=True)

# These values pin the single manually operated SHFE daily path. They are not a
# provider registry and do not introduce configurable provider policy.
SHFE_DAILY_SOURCE_NAME = "SHFE_OFFICIAL_DAILY"
SHFE_DAILY_ADAPTER_NAME = "shfe_official_daily_json"
SHFE_DAILY_ADAPTER_VERSION = "1.0.0"
SHFE_DAILY_MAPPING_VERSION = "shfe_official_daily_json/1.0.0"
SHFE_DAILY_ENDPOINT_ID = "shfe_daily_data_v1"
SHFE_DAILY_SOURCE_ADMISSION_REVIEW_STATUSES = (
    "APPROVED",
    "RESTRICTED",
    "BLOCKED",
    "UNKNOWN",
)
# Source-use policy is a deliberately closed, source-receipt-level decision.
# Adapters never accept operator-supplied free-text policy.
SOURCE_RECEIPT_ACQUISITION_USES = (
    "UNKNOWN",
    "PRIVATE_RESEARCH_ONLY",
    "SYNTHETIC_TEST_ONLY",
)
SOURCE_RECEIPT_REDISTRIBUTION_POLICIES = (
    "UNKNOWN",
    "PROHIBITED",
    "PERMITTED",
)
SOURCE_RECEIPT_DEFAULT_ACQUISITION_USE = "UNKNOWN"
SOURCE_RECEIPT_DEFAULT_REDISTRIBUTION_POLICY = "UNKNOWN"

SHFE_DAILY_ACQUISITION_USE = "PRIVATE_RESEARCH_ONLY"
SHFE_DAILY_RETENTION_POLICY = "TRANSIENT_ONLY"
SHFE_DAILY_REDISTRIBUTION_POLICY = "PROHIBITED"
SHFE_DAILY_AVAILABLE_AT_BASIS = "OPERATOR_ATTESTED"
# The command boundary owns the exact opaque-identifier allowlist. The database
# keeps a shallow backstop against source-like content.
_OPAQUE_IDENTIFIER_FORBIDDEN_CHARACTERS = (" ", "/", "\\", ":", "?", "#", "@", "&", "=", "%")
_LOWER_HEX_CHARACTERS = "0123456789abcdef"


def _strip_allowed_characters_sql(column_name: str, allowed_characters: str) -> str:
    """Return SQL that leaves only characters outside an allowlist."""

    expression = column_name
    for character in allowed_characters:
        expression = f"replace({expression}, '{character}', '')"
    return expression


def _opaque_identifier_constraint(
    column_name: str,
    *,
    constraint_name: str,
    nullable: bool = False,
) -> sa.CheckConstraint:
    """Return one portable bounded backstop for an opaque identifier.

    Exact ``[A-Za-z0-9][A-Za-z0-9._-]{0,127}`` validation is performed at the
    command boundary. The physical check rejects whitespace and URL/path-like
    delimiters from a direct SQL write.
    """

    null_prefix = f"{column_name} IS NULL OR " if nullable else ""
    forbidden_checks = " AND ".join(
        f"length(replace({column_name}, '{character}', '')) = length({column_name})"
        for character in _OPAQUE_IDENTIFIER_FORBIDDEN_CHARACTERS
    )
    return sa.CheckConstraint(
        f"{null_prefix}(length({column_name}) BETWEEN 1 AND 128 AND {forbidden_checks})",
        name=constraint_name,
    )


def _lower_hex_sha256_constraint(
    column_name: str,
    *,
    constraint_name: str,
    nullable: bool = False,
) -> sa.CheckConstraint:
    """Require one exactly lowercase hexadecimal SHA-256 digest portably."""

    stripped = _strip_allowed_characters_sql(column_name, _LOWER_HEX_CHARACTERS)
    null_prefix = f"{column_name} IS NULL OR " if nullable else ""
    return sa.CheckConstraint(
        f"{null_prefix}(length({column_name}) = 64 AND length({stripped}) = 0)",
        name=constraint_name,
    )


class CreatedAtMixin:
    """Durable creation evidence for records that form operational history."""

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


class Exchange(CreatedAtMixin, Base):
    """An exchange namespace and its canonical IANA time zone."""

    __tablename__ = "exchange"
    __table_args__ = (sa.CheckConstraint("code = upper(code)", name="exchange_code_uppercase"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(sa.String(16), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    timezone_name: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    country_code: Mapped[str] = mapped_column(sa.String(2), nullable=False, server_default="CN")
    active: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, server_default=sa.true())

    products: Mapped[list[FuturesProduct]] = relationship(back_populates="exchange")
    calendars: Mapped[list[TradingCalendar]] = relationship(back_populates="exchange")


class FuturesProduct(CreatedAtMixin, Base):
    """A tradable commodity-futures product within one exchange namespace."""

    __tablename__ = "futures_product"
    __table_args__ = (
        sa.UniqueConstraint("exchange_id", "code", name="product_exchange_code"),
        sa.CheckConstraint("code = upper(code)", name="product_code_uppercase"),
        sa.CheckConstraint("price_tick > 0", name="product_price_tick_positive"),
        sa.CheckConstraint("contract_multiplier > 0", name="product_multiplier_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    exchange_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey("exchange.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    price_tick: Mapped[Decimal] = mapped_column(Price, nullable=False)
    contract_multiplier: Mapped[Decimal] = mapped_column(Price, nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), nullable=False, server_default="CNY")
    quantity_unit: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    active: Mapped[bool] = mapped_column(sa.Boolean(), nullable=False, server_default=sa.true())

    exchange: Mapped[Exchange] = relationship(back_populates="products")
    contracts: Mapped[list[FuturesContract]] = relationship(back_populates="product")


class FuturesContract(CreatedAtMixin, Base):
    """An exchange-listed, non-continuous futures contract."""

    __tablename__ = "futures_contract"
    __table_args__ = (
        sa.UniqueConstraint("product_id", "contract_code", name="contract_product_code"),
        sa.CheckConstraint("contract_code = upper(contract_code)", name="contract_code_uppercase"),
        sa.CheckConstraint(
            "last_trade_date IS NULL OR listed_on IS NULL OR last_trade_date >= listed_on",
            name="contract_dates_ordered",
        ),
        sa.CheckConstraint("status IN ('LISTED', 'EXPIRED', 'SUSPENDED')", name="contract_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey("futures_product.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    contract_code: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    listed_on: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    last_trade_date: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, server_default="LISTED")

    product: Mapped[FuturesProduct] = relationship(back_populates="contracts")
    data_series: Mapped[list[DataSeries]] = relationship(back_populates="contract")


class TradingCalendar(CreatedAtMixin, Base):
    """An immutable-versioned exchange trading calendar."""

    __tablename__ = "trading_calendar"
    __table_args__ = (
        sa.UniqueConstraint(
            "exchange_id", "code", "revision", name="calendar_exchange_code_revision"
        ),
        sa.CheckConstraint("code = upper(code)", name="calendar_code_uppercase"),
        sa.CheckConstraint("revision > 0", name="calendar_revision_positive"),
        sa.CheckConstraint("status IN ('ACTIVE', 'RETIRED')", name="calendar_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    exchange_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey("exchange.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    revision: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    timezone_name: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, server_default="ACTIVE")

    exchange: Mapped[Exchange] = relationship(back_populates="calendars")
    trading_days: Mapped[list[CalendarTradingDay]] = relationship(
        back_populates="calendar", cascade="all, delete-orphan"
    )
    data_series: Mapped[list[DataSeries]] = relationship(back_populates="calendar")


class CalendarTradingDay(Base):
    """An explicit OPEN or CLOSED exchange trading day in one calendar revision."""

    __tablename__ = "calendar_trading_day"
    __table_args__ = (
        sa.CheckConstraint("status IN ('OPEN', 'CLOSED')", name="calendar_day_status"),
    )

    calendar_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey("trading_calendar.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    trading_day: Mapped[date] = mapped_column(sa.Date(), primary_key=True)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(sa.String(256), nullable=True)

    calendar: Mapped[TradingCalendar] = relationship(back_populates="trading_days")
    sessions: Mapped[list[TradingSession]] = relationship(
        back_populates="trading_day_record", cascade="all, delete-orphan"
    )


class TradingSession(CreatedAtMixin, Base):
    """A materialized session interval with an explicit business trading day.

    ``opens_at`` / ``closes_at`` use half-open ``[open, close)`` semantics.  The
    explicit ``trading_day`` handles nights, holidays, and exceptional schedules
    without guessing from the civil date of an event timestamp.
    """

    __tablename__ = "trading_session"
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["calendar_id", "trading_day"],
            ["calendar_trading_day.calendar_id", "calendar_trading_day.trading_day"],
            name="session_calendar_trading_day",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "calendar_id", "trading_day", "sequence", name="session_calendar_day_sequence"
        ),
        sa.CheckConstraint("opens_at < closes_at", name="session_interval_ordered"),
        sa.CheckConstraint("kind IN ('NIGHT', 'DAY', 'AUCTION')", name="session_kind"),
        sa.CheckConstraint("sequence >= 0", name="session_sequence_nonnegative"),
        ExcludeConstraint(
            ("calendar_id", "="),
            (sa.text("tstzrange(opens_at, closes_at, '[)')"), "&&"),
            name="session_no_overlap",
        ),
        sa.Index("ix_trading_session_calendar_id_trading_day", "calendar_id", "trading_day"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    calendar_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    trading_day: Mapped[date] = mapped_column(sa.Date(), nullable=False)
    sequence: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    kind: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    opens_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    closes_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)

    trading_day_record: Mapped[CalendarTradingDay] = relationship(back_populates="sessions")


class DataSeries(CreatedAtMixin, Base):
    """A contract-bound canonical OHLCV series pinned to one calendar revision."""

    __tablename__ = "data_series"
    __table_args__ = (
        sa.UniqueConstraint(
            "contract_id",
            "calendar_id",
            "kind",
            "interval",
            "adjustment",
            name="series_contract_calendar_kind_interval_adjustment",
        ),
        sa.CheckConstraint("kind = 'OHLCV'", name="series_kind"),
        sa.CheckConstraint("interval IN ('1m', '1d')", name="series_interval"),
        sa.CheckConstraint("adjustment = 'RAW'", name="series_adjustment"),
        sa.CheckConstraint(
            "timestamp_convention = 'BAR_START'", name="series_timestamp_convention"
        ),
        sa.CheckConstraint("price_scale BETWEEN 0 AND 12", name="series_price_scale"),
        sa.CheckConstraint("quantity_scale BETWEEN 0 AND 12", name="series_quantity_scale"),
        sa.CheckConstraint("length(volume_unit) > 0", name="series_volume_unit_present"),
        sa.CheckConstraint("length(turnover_currency) = 3", name="series_turnover_currency_length"),
        sa.CheckConstraint("status IN ('ACTIVE', 'RETIRED')", name="series_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    contract_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey("futures_contract.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    calendar_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey("trading_calendar.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(sa.String(16), nullable=False, server_default="OHLCV")
    interval: Mapped[str] = mapped_column(sa.String(8), nullable=False)
    adjustment: Mapped[str] = mapped_column(sa.String(16), nullable=False, server_default="RAW")
    timestamp_convention: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default="BAR_START"
    )
    price_scale: Mapped[int] = mapped_column(sa.SmallInteger(), nullable=False)
    quantity_scale: Mapped[int] = mapped_column(sa.SmallInteger(), nullable=False)
    volume_unit: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    turnover_currency: Mapped[str] = mapped_column(sa.String(3), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, server_default="ACTIVE")

    contract: Mapped[FuturesContract] = relationship(back_populates="data_series")
    calendar: Mapped[TradingCalendar] = relationship(back_populates="data_series")
    bars: Mapped[list[CanonicalBar]] = relationship(back_populates="series")
    provider_retrievals: Mapped[list[ProviderRetrieval]] = relationship(back_populates="series")
    quality_evaluations: Mapped[list[QualityEvaluation]] = relationship(back_populates="series")
    snapshot_partitions: Mapped[list[DatasetSnapshotPartition]] = relationship(
        back_populates="series"
    )


class CanonicalBar(CreatedAtMixin, Base):
    """One immutable revision of a canonical OHLCV observation.

    Revision ``1`` is the original observation. A correction is a new row with
    an explicit predecessor and source/import evidence; it never replaces the
    prior row.
    """

    __tablename__ = "canonical_bar"
    __table_args__ = (
        sa.UniqueConstraint(
            "series_id",
            "event_time",
            "revision_number",
            name="bar_series_event_time_revision",
        ),
        sa.UniqueConstraint(
            "supersedes_canonical_bar_id",
            name="bar_supersedes_once",
        ),
        sa.UniqueConstraint(
            "revision_source_import_record_id",
            name="bar_revision_source_record_once",
        ),
        sa.UniqueConstraint(
            "supersession_evidence_import_record_id",
            name="bar_supersession_evidence_once",
        ),
        sa.UniqueConstraint(
            "revision_idempotency_key",
            name="bar_revision_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_canonical_bar_id"],
            ["canonical_bar.id"],
            name="bar_supersedes_canonical_bar",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["revision_source_import_record_id"],
            ["import_record.id"],
            name="bar_revision_source_import_record",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.ForeignKeyConstraint(
            ["supersession_evidence_import_record_id"],
            ["import_record.id"],
            name="bar_supersession_evidence_import_record",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        sa.CheckConstraint("high_price >= low_price", name="bar_high_not_below_low"),
        sa.CheckConstraint(
            "open_price >= low_price AND open_price <= high_price",
            name="bar_open_in_range",
        ),
        sa.CheckConstraint(
            "close_price >= low_price AND close_price <= high_price",
            name="bar_close_in_range",
        ),
        sa.CheckConstraint("volume >= 0", name="bar_volume_nonnegative"),
        sa.CheckConstraint("turnover IS NULL OR turnover >= 0", name="bar_turnover_nonnegative"),
        sa.CheckConstraint(
            "open_interest IS NULL OR open_interest >= 0",
            name="bar_open_interest_nonnegative",
        ),
        sa.CheckConstraint("available_at >= event_time", name="bar_available_after_event"),
        sa.CheckConstraint("length(source_timezone_name) > 0", name="bar_source_timezone_present"),
        sa.CheckConstraint("length(source_content_hash) = 64", name="bar_source_hash_length"),
        sa.CheckConstraint(
            "length(normalized_payload_hash) = 64", name="bar_normalized_hash_length"
        ),
        sa.CheckConstraint("revision_number > 0", name="bar_revision_positive"),
        sa.CheckConstraint(
            "(revision_number = 1 "
            "AND supersedes_canonical_bar_id IS NULL "
            "AND revision_source_import_record_id IS NULL "
            "AND supersession_evidence_import_record_id IS NULL "
            "AND supersession_reason IS NULL "
            "AND revision_idempotency_key IS NULL "
            "AND revision_request_fingerprint IS NULL "
            "AND revision_correlation_id IS NULL "
            "AND revision_causation_id IS NULL) "
            "OR (revision_number > 1 "
            "AND supersedes_canonical_bar_id IS NOT NULL "
            "AND revision_source_import_record_id IS NOT NULL "
            "AND supersession_evidence_import_record_id IS NOT NULL "
            "AND supersession_reason IN ('SOURCE_CORRECTION', 'PROVIDER_RESTATEMENT') "
            "AND revision_idempotency_key IS NOT NULL "
            "AND revision_request_fingerprint IS NOT NULL "
            "AND revision_correlation_id IS NOT NULL)",
            name="bar_revision_shape",
        ),
        _lower_hex_sha256_constraint(
            "revision_request_fingerprint",
            constraint_name="bar_revision_request_fingerprint",
            nullable=True,
        ),
        _opaque_identifier_constraint(
            "revision_idempotency_key",
            constraint_name="bar_revision_idempotency_key",
            nullable=True,
        ),
        _opaque_identifier_constraint(
            "revision_correlation_id",
            constraint_name="bar_revision_correlation_id",
            nullable=True,
        ),
        _opaque_identifier_constraint(
            "revision_causation_id",
            constraint_name="bar_revision_causation_id",
            nullable=True,
        ),
        sa.Index("ix_canonical_bar_series_id_trading_day", "series_id", "trading_day"),
        sa.Index(
            "ix_canonical_bar_series_event_revision",
            "series_id",
            "event_time",
            "revision_number",
        ),
        sa.Index(
            "ix_canonical_bar_import_run_event_time_id",
            "import_run_id",
            "event_time",
            "id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    series_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, sa.ForeignKey("data_series.id", ondelete="RESTRICT"), nullable=False
    )
    import_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        sa.ForeignKey("import_run.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    event_time: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    trading_day: Mapped[date] = mapped_column(sa.Date(), nullable=False)
    available_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )
    source_timezone_name: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    source_name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    source_record_id: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    source_content_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    normalized_payload_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    revision_number: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default="1")
    supersedes_canonical_bar_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
    )
    revision_source_import_record_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
    )
    supersession_evidence_import_record_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
    )
    supersession_reason: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    revision_idempotency_key: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    revision_request_fingerprint: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    revision_correlation_id: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    revision_causation_id: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    open_price: Mapped[Decimal] = mapped_column(Price, nullable=False)
    high_price: Mapped[Decimal] = mapped_column(Price, nullable=False)
    low_price: Mapped[Decimal] = mapped_column(Price, nullable=False)
    close_price: Mapped[Decimal] = mapped_column(Price, nullable=False)
    volume: Mapped[Decimal] = mapped_column(Quantity, nullable=False)
    turnover: Mapped[Decimal | None] = mapped_column(Turnover, nullable=True)
    open_interest: Mapped[Decimal | None] = mapped_column(Quantity, nullable=True)

    series: Mapped[DataSeries] = relationship(back_populates="bars")
    import_run: Mapped[ImportRun | None] = relationship(back_populates="bars")
    snapshot_members: Mapped[list[DatasetSnapshotMember]] = relationship(
        back_populates="canonical_bar"
    )


class JobRun(CreatedAtMixin, Base):
    """Persisted job identity reserved for later catalog, import, and quality work."""

    __tablename__ = "job_run"
    __table_args__ = (
        sa.UniqueConstraint("job_kind", "idempotency_key", name="job_kind_idempotency"),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')",
            name="job_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_kind: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    correlation_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    causation_id: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, server_default="QUEUED")
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)

    import_run: Mapped[ImportRun | None] = relationship(back_populates="job_run")
    provider_retrieval: Mapped[ProviderRetrieval | None] = relationship(back_populates="job_run")


class SourceReceipt(CreatedAtMixin, Base):
    """Canonical ingestion evidence, distinct from managed source bytes.

    Current library imports pin the controlled archive identity and evidence hash
    inside ImportRun.mapping in the same transaction as canonical observations.
    This receipt identifies ingestion; DataLibrary owns retention and file access.
    """

    __tablename__ = "source_receipt"
    __table_args__ = (
        sa.UniqueConstraint(
            "source_name",
            "content_hash",
            "source_timezone_name",
            name="receipt_source_content_timezone",
        ),
        sa.CheckConstraint("length(content_hash) = 64", name="receipt_hash_length"),
        sa.CheckConstraint("byte_count >= 0", name="receipt_byte_count_nonnegative"),
        sa.CheckConstraint("length(media_type) > 0", name="receipt_media_type_present"),
        sa.CheckConstraint("length(input_kind) > 0", name="receipt_input_kind_present"),
        sa.CheckConstraint(
            "length(source_timezone_name) > 0", name="receipt_source_timezone_present"
        ),
        sa.CheckConstraint(
            "retention_policy IN ('TRANSIENT', 'CONTROLLED')",
            name="receipt_retention_policy",
        ),
        sa.CheckConstraint(
            "acquisition_use IN ('UNKNOWN', 'PRIVATE_RESEARCH_ONLY', 'SYNTHETIC_TEST_ONLY')",
            name="receipt_acquisition_use",
        ),
        sa.CheckConstraint(
            "redistribution_policy IN ('UNKNOWN', 'PROHIBITED', 'PERMITTED')",
            name="receipt_redistribution_policy",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source_name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    content_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    byte_count: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    input_kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    source_timezone_name: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    retention_policy: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default="TRANSIENT"
    )
    acquisition_use: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        server_default=SOURCE_RECEIPT_DEFAULT_ACQUISITION_USE,
    )
    redistribution_policy: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        server_default=SOURCE_RECEIPT_DEFAULT_REDISTRIBUTION_POLICY,
    )
    received_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )

    import_runs: Mapped[list[ImportRun]] = relationship(back_populates="source_receipt")
    provider_retrievals: Mapped[list[ProviderRetrieval]] = relationship(
        back_populates="source_receipt"
    )


class ImportRun(CreatedAtMixin, Base):
    """One auditable import attempt before a later snapshot-publication decision."""

    __tablename__ = "import_run"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'QUARANTINED')",
            name="import_status",
        ),
        sa.CheckConstraint(
            "effect IS NULL OR effect IN ('APPLIED', 'NOOP', 'REJECTED')",
            name="import_effect",
        ),
        sa.CheckConstraint(
            "mapping_hash IS NULL OR length(mapping_hash) = 64",
            name="import_mapping_hash_length",
        ),
        sa.CheckConstraint("rows_read >= 0", name="import_rows_read_nonnegative"),
        sa.CheckConstraint("rows_accepted >= 0", name="import_rows_accepted_nonnegative"),
        sa.CheckConstraint("rows_rejected >= 0", name="import_rows_rejected_nonnegative"),
        sa.CheckConstraint("rows_inserted >= 0", name="import_rows_inserted_nonnegative"),
        sa.CheckConstraint(
            "rows_duplicate_identical >= 0", name="import_rows_duplicate_identical_nonnegative"
        ),
        sa.CheckConstraint("rows_conflicted >= 0", name="import_rows_conflicted_nonnegative"),
        sa.CheckConstraint(
            "error_detail IS NULL OR length(error_detail) <= 1024",
            name="import_error_detail_bounded",
        ),
        sa.Index(
            "uq_import_run_request_fingerprint",
            "request_fingerprint",
            unique=True,
            postgresql_where=sa.text("source_receipt_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        sa.ForeignKey("job_run.id", ondelete="RESTRICT"),
        unique=True,
        nullable=True,
    )
    series_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        sa.ForeignKey("data_series.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    source_receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        sa.ForeignKey("source_receipt.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    source_name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    mapping_version: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    mapping_hash: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    mapping: Mapped[dict[str, object] | None] = mapped_column(sa.JSON(), nullable=True)
    source_timezone_name: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, server_default="PENDING")
    effect: Mapped[str | None] = mapped_column(sa.String(16), nullable=True)
    rows_read: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default="0")
    rows_accepted: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default="0")
    rows_rejected: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default="0")
    rows_inserted: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default="0")
    rows_duplicate_identical: Mapped[int] = mapped_column(
        sa.Integer(), nullable=False, server_default="0"
    )
    rows_conflicted: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default="0")
    event_time_from: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    event_time_to: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    trading_day_from: Mapped[date | None] = mapped_column(sa.Date())
    trading_day_to: Mapped[date | None] = mapped_column(sa.Date())
    available_at_from: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    available_at_to: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(sa.String(1024), nullable=True)
    replayed_import_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        sa.ForeignKey("import_run.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    job_run: Mapped[JobRun | None] = relationship(back_populates="import_run")
    series: Mapped[DataSeries | None] = relationship()
    source_receipt: Mapped[SourceReceipt | None] = relationship(back_populates="import_runs")
    bars: Mapped[list[CanonicalBar]] = relationship(back_populates="import_run")
    replayed_import_run: Mapped[ImportRun | None] = relationship(remote_side=[id])
    records: Mapped[list[ImportRecord]] = relationship(
        back_populates="import_run", cascade="all, delete-orphan"
    )
    quality_findings: Mapped[list[QualityFinding]] = relationship(back_populates="import_run")
    import_quality_evaluations: Mapped[list[ImportQualityEvaluation]] = relationship(
        back_populates="import_run"
    )
    snapshot_import_quality_pins: Mapped[list[DatasetSnapshotImportQualityPin]] = relationship(
        back_populates="import_run"
    )
    provider_retrievals: Mapped[list[ProviderRetrieval]] = relationship(back_populates="import_run")


class ProviderRetrieval(CreatedAtMixin, Base):
    """Durable, non-secret evidence for one provider retrieval request.

    This model deliberately records request identity and bounded retrieval
    metadata before any provider adapter is implemented.  ``request_descriptor``
    may contain only canonical, non-secret request parameters; credentials,
    signed URLs, and request headers never belong in this table.

    A provider retrieval is distinct from both a ``SourceReceipt`` (the bytes
    received) and an ``ImportRun`` (canonical normalization/application).  It
    owns one durable request intent and outcome; a later manual recovery may
    terminalize an abandoned active reservation but must append separate audit
    evidence instead of replacing the original request identity.
    """

    __tablename__ = "provider_retrieval"
    __table_args__ = (
        sa.UniqueConstraint("job_run_id", name="provider_retrieval_job_run"),
        sa.UniqueConstraint("request_fingerprint", name="provider_retrieval_request_fingerprint"),
        sa.UniqueConstraint(
            "recovery_of_provider_retrieval_id",
            name="provider_retrieval_recovery_parent",
        ),
        sa.CheckConstraint("length(source_name) > 0", name="source_name_present"),
        sa.CheckConstraint("length(adapter_name) > 0", name="adapter_name_present"),
        sa.CheckConstraint("length(adapter_version) > 0", name="adapter_version_present"),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64",
            name="request_fingerprint_length",
        ),
        sa.CheckConstraint(
            "length(source_timezone_name) > 0",
            name="source_timezone_present",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'QUARANTINED', 'STALE')",
            name="status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        sa.CheckConstraint(
            "response_http_status IS NULL OR response_http_status BETWEEN 100 AND 599",
            name="response_http_status",
        ),
        sa.CheckConstraint(
            "response_etag IS NULL OR length(response_etag) <= 512",
            name="response_etag_bounded",
        ),
        sa.CheckConstraint(
            "response_last_modified IS NULL OR length(response_last_modified) <= 128",
            name="response_last_modified_bounded",
        ),
        sa.CheckConstraint(
            "provider_request_id IS NULL OR length(provider_request_id) <= 256",
            name="provider_request_id_bounded",
        ),
        sa.CheckConstraint(
            "error_detail IS NULL OR length(error_detail) <= 1024",
            name="error_detail_bounded",
        ),
        sa.CheckConstraint(
            "response_content_type IS NULL OR length(response_content_type) <= 128",
            name="response_content_type_bounded",
        ),
        sa.CheckConstraint(
            "status <> 'SUCCEEDED' OR source_receipt_id IS NOT NULL",
            name="success_requires_receipt",
        ),
        sa.CheckConstraint(
            "import_run_id IS NULL OR source_receipt_id IS NOT NULL",
            name="import_requires_receipt",
        ),
        sa.Index("ix_provider_retrieval_series_id", "series_id"),
        sa.Index("ix_provider_retrieval_import_run_id", "import_run_id"),
        sa.Index("ix_provider_retrieval_source_receipt_id", "source_receipt_id"),
        sa.Index("ix_provider_retrieval_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey("job_run.id", ondelete="RESTRICT"),
        nullable=False,
    )
    series_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey("data_series.id", ondelete="RESTRICT"),
        nullable=False,
    )
    import_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        sa.ForeignKey("import_run.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        sa.ForeignKey("source_receipt.id", ondelete="RESTRICT"),
        nullable=True,
    )
    recovery_of_provider_retrieval_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        sa.ForeignKey(
            "provider_retrieval.id",
            name="fk_retrieval_recovery_parent",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    source_name: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    adapter_name: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    adapter_version: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    request_descriptor: Mapped[dict[str, object]] = mapped_column(sa.JSON(), nullable=False)
    source_timezone_name: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, server_default="PENDING")
    attempt_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default="0")
    response_http_status: Mapped[int | None] = mapped_column(sa.SmallInteger(), nullable=True)
    response_content_type: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    response_etag: Mapped[str | None] = mapped_column(sa.String(512), nullable=True)
    response_last_modified: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(sa.String(256), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(sa.String(1024), nullable=True)
    error_retryable: Mapped[bool | None] = mapped_column(sa.Boolean(), nullable=True)

    job_run: Mapped[JobRun] = relationship(back_populates="provider_retrieval")
    series: Mapped[DataSeries] = relationship(back_populates="provider_retrievals")
    import_run: Mapped[ImportRun | None] = relationship(back_populates="provider_retrievals")
    source_receipt: Mapped[SourceReceipt | None] = relationship(
        back_populates="provider_retrievals"
    )
    recovery_parent: Mapped[ProviderRetrieval | None] = relationship(
        back_populates="recovery_successor",
        foreign_keys=[recovery_of_provider_retrieval_id],
        remote_side=[id],
    )
    recovery_successor: Mapped[ProviderRetrieval | None] = relationship(
        back_populates="recovery_parent",
        foreign_keys=[recovery_of_provider_retrieval_id],
        uselist=False,
    )
    recovery_events: Mapped[list[ProviderRetrievalRecovery]] = relationship(
        back_populates="provider_retrieval"
    )
    source_admission_review_link: Mapped[ShfeDailyRetrievalSourceAdmissionReview | None] = (
        relationship(
            back_populates="provider_retrieval",
            uselist=False,
            cascade="all, delete-orphan",
        )
    )


class ProviderRetrievalRecovery(CreatedAtMixin, Base):
    """Append-only operator evidence for controlled stale-retrieval recovery.

    Recovery does not retry a provider request. It records the active
    reservation's pre-recovery state and the accountable operator action, then
    either terminalizes an abandoned reservation or reconnects a terminal inner
    import that committed before its parent could be finalized.
    """

    __tablename__ = "provider_retrieval_recovery"
    __table_args__ = (
        sa.UniqueConstraint(
            "provider_retrieval_id",
            name="provider_retrieval_recovery_one_per_parent",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="provider_retrieval_recovery_idempotency_key",
        ),
        sa.CheckConstraint("action IN ('TERMINALIZED', 'RECONCILED_IMPORT')", name="action"),
        sa.CheckConstraint("prior_status IN ('PENDING', 'RUNNING')", name="prior_status"),
        sa.CheckConstraint("prior_attempt_count >= 0", name="prior_attempt_count"),
        sa.CheckConstraint("length(operator_id) BETWEEN 1 AND 128", name="operator_id_bounded"),
        sa.CheckConstraint("length(reason) BETWEEN 1 AND 1024", name="reason_bounded"),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 128",
            name="idempotency_key_bounded",
        ),
        sa.CheckConstraint(
            "length(correlation_id) BETWEEN 1 AND 128", name="correlation_id_bounded"
        ),
        sa.CheckConstraint(
            "causation_id IS NULL OR length(causation_id) BETWEEN 1 AND 128",
            name="causation_id_bounded",
        ),
        sa.CheckConstraint(
            "prior_response_http_status IS NULL OR prior_response_http_status BETWEEN 100 AND 599",
            name="prior_response_http_status",
        ),
        sa.CheckConstraint(
            "prior_error_detail IS NULL OR length(prior_error_detail) <= 1024",
            name="prior_error_detail_bounded",
        ),
        sa.CheckConstraint(
            "prior_error_code IS NULL OR length(prior_error_code) <= 64",
            name="prior_error_code_bounded",
        ),
        sa.Index("ix_provider_retrieval_recovery_provider_retrieval_id", "provider_retrieval_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider_retrieval_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey(
            "provider_retrieval.id",
            name="fk_retrieval_recovery_event_parent",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    prior_status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    prior_attempt_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    prior_started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    prior_finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    prior_response_http_status: Mapped[int | None] = mapped_column(sa.SmallInteger())
    prior_error_code: Mapped[str | None] = mapped_column(sa.String(64))
    prior_error_detail: Mapped[str | None] = mapped_column(sa.String(1024))
    operator_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    reason: Mapped[str] = mapped_column(sa.String(1024), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    correlation_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    causation_id: Mapped[str | None] = mapped_column(sa.String(128))

    provider_retrieval: Mapped[ProviderRetrieval] = relationship(back_populates="recovery_events")


class ShfeDailySourceAdmissionReview(CreatedAtMixin, Base):
    """Append-only, non-secret review evidence for the one SHFE daily source.

    The record deliberately captures a bounded evidence reference/digest and a
    fixed set of operational conclusions.  It never stores source terms, an
    endpoint URL, request headers, response bytes, or an unconstrained operator
    note.  A later review is a new record; it does not rewrite prior evidence.
    """

    __tablename__ = "shfe_daily_source_admission_review"
    __table_args__ = (
        sa.UniqueConstraint(
            "idempotency_key",
            name="shfe_daily_source_admission_review_idempotency_key",
        ),
        sa.UniqueConstraint(
            "review_sequence",
            name="shfe_daily_source_admission_review_sequence",
        ),
        sa.CheckConstraint(
            f"source_name = '{SHFE_DAILY_SOURCE_NAME}'",
            name="source_name_fixed",
        ),
        sa.CheckConstraint(
            f"adapter_name = '{SHFE_DAILY_ADAPTER_NAME}'",
            name="adapter_name_fixed",
        ),
        sa.CheckConstraint(
            f"adapter_version = '{SHFE_DAILY_ADAPTER_VERSION}'",
            name="adapter_version_fixed",
        ),
        sa.CheckConstraint(
            f"mapping_version = '{SHFE_DAILY_MAPPING_VERSION}'",
            name="mapping_version_fixed",
        ),
        sa.CheckConstraint(
            f"endpoint_id = '{SHFE_DAILY_ENDPOINT_ID}'",
            name="endpoint_id_fixed",
        ),
        sa.CheckConstraint(
            "status IN ('APPROVED', 'RESTRICTED', 'BLOCKED', 'UNKNOWN')",
            name="status",
        ),
        sa.CheckConstraint(
            f"acquisition_use = '{SHFE_DAILY_ACQUISITION_USE}'",
            name="acquisition_use_fixed",
        ),
        sa.CheckConstraint(
            f"retention_policy = '{SHFE_DAILY_RETENTION_POLICY}'",
            name="retention_policy_fixed",
        ),
        sa.CheckConstraint(
            f"redistribution_policy = '{SHFE_DAILY_REDISTRIBUTION_POLICY}'",
            name="redistribution_fixed",
        ),
        sa.CheckConstraint(
            f"available_at_basis = '{SHFE_DAILY_AVAILABLE_AT_BASIS}'",
            name="available_at_basis_fixed",
        ),
        sa.CheckConstraint(
            "valid_until > created_at",
            name="valid_until_after_created",
        ),
        sa.CheckConstraint(
            "review_sequence > 0",
            name="review_sequence_positive",
        ),
        _lower_hex_sha256_constraint(
            "evidence_sha256",
            constraint_name="evidence_sha256_lower_hex",
        ),
        _opaque_identifier_constraint(
            "evidence_ref",
            constraint_name="evidence_ref_opaque",
        ),
        _opaque_identifier_constraint(
            "reviewer_id",
            constraint_name="reviewer_id_opaque",
        ),
        _opaque_identifier_constraint(
            "idempotency_key",
            constraint_name="idempotency_key_opaque",
        ),
        _opaque_identifier_constraint(
            "correlation_id",
            constraint_name="correlation_id_opaque",
        ),
        _opaque_identifier_constraint(
            "causation_id",
            constraint_name="causation_id_opaque",
            nullable=True,
        ),
        sa.Index("ix_shfe_daily_source_admission_review_status", "status"),
        sa.Index("ix_shfe_daily_source_admission_review_valid_until", "valid_until"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    review_sequence: Mapped[int] = mapped_column(sa.BigInteger(), nullable=False)
    source_name: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        server_default=SHFE_DAILY_SOURCE_NAME,
    )
    adapter_name: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
        server_default=SHFE_DAILY_ADAPTER_NAME,
    )
    adapter_version: Mapped[str] = mapped_column(
        sa.String(16),
        nullable=False,
        server_default=SHFE_DAILY_ADAPTER_VERSION,
    )
    mapping_version: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
        server_default=SHFE_DAILY_MAPPING_VERSION,
    )
    endpoint_id: Mapped[str] = mapped_column(
        sa.String(64),
        nullable=False,
        server_default=SHFE_DAILY_ENDPOINT_ID,
    )
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    acquisition_use: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        server_default=SHFE_DAILY_ACQUISITION_USE,
    )
    retention_policy: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        server_default=SHFE_DAILY_RETENTION_POLICY,
    )
    redistribution_policy: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        server_default=SHFE_DAILY_REDISTRIBUTION_POLICY,
    )
    available_at_basis: Mapped[str] = mapped_column(
        sa.String(32),
        nullable=False,
        server_default=SHFE_DAILY_AVAILABLE_AT_BASIS,
    )
    evidence_ref: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    reviewer_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    valid_until: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    correlation_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    causation_id: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)

    retrieval_links: Mapped[list[ShfeDailyRetrievalSourceAdmissionReview]] = relationship(
        back_populates="source_admission_review"
    )


class ShfeDailyRetrievalSourceAdmissionReview(CreatedAtMixin, Base):
    """The source-admission evidence that authorized one SHFE retrieval.

    A review may serve multiple matching retrievals, but each retrieval has one
    durable review association.  The service enforces matching retrieval
    semantics before inserting this relation; the database enforces one
    non-null relation and RESTRICT foreign keys.  The application exposes no
    reassignment or deletion command, and production database roles must keep
    that evidence plane append-only.
    """

    __tablename__ = "provider_retrieval_source_admission_review"
    __table_args__ = (
        sa.UniqueConstraint(
            "provider_retrieval_id",
            name="retrieval_source_admission_review_one_per_retrieval",
        ),
        sa.Index(
            "ix_retrieval_source_admission_review_review_id",
            "source_admission_review_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider_retrieval_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey(
            "provider_retrieval.id",
            name="fk_retrieval_source_admission_review_retrieval",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    source_admission_review_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey(
            "shfe_daily_source_admission_review.id",
            name="fk_retrieval_source_admission_review_review",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )

    provider_retrieval: Mapped[ProviderRetrieval] = relationship(
        back_populates="source_admission_review_link"
    )
    source_admission_review: Mapped[ShfeDailySourceAdmissionReview] = relationship(
        back_populates="retrieval_links"
    )


class ImportRecord(CreatedAtMixin, Base):
    """Per-row disposition evidence for an import attempt.

    A row can be accepted as a newly inserted canonical bar, matched to an
    identical existing bar, or retained as conflict/rejection evidence.  It is
    not a second canonical observation store.
    """

    __tablename__ = "import_record"
    __table_args__ = (
        sa.UniqueConstraint("import_run_id", "source_row_number", name="record_import_row"),
        sa.CheckConstraint("source_row_number >= 1", name="record_row_number_positive"),
        sa.CheckConstraint("length(normalized_payload_hash) = 64", name="record_hash_length"),
        sa.CheckConstraint(
            "disposition IN ('INSERTED', 'DUPLICATE_IDENTICAL', 'CONFLICT', 'REJECTED')",
            name="record_disposition",
        ),
        sa.CheckConstraint(
            "evidence IS NULL OR length(evidence) <= 512", name="record_evidence_bounded"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    import_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey("import_run.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    source_row_number: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    source_record_id: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    normalized_payload_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    event_time: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    disposition: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    canonical_bar_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        sa.ForeignKey("canonical_bar.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    conflicting_bar_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        sa.ForeignKey("canonical_bar.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    error_code: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    evidence: Mapped[str | None] = mapped_column(sa.String(512), nullable=True)

    import_run: Mapped[ImportRun] = relationship(back_populates="records")
    canonical_bar: Mapped[CanonicalBar | None] = relationship(foreign_keys=[canonical_bar_id])
    conflicting_bar: Mapped[CanonicalBar | None] = relationship(foreign_keys=[conflicting_bar_id])


class ImportQualityEvaluation(CreatedAtMixin, Base):
    """One immutable import-integrity conclusion over one terminal ``ImportRun``."""

    __tablename__ = "import_quality_evaluation"
    __table_args__ = (
        sa.UniqueConstraint("idempotency_key", name="import_quality_evaluation_idempotency"),
        sa.CheckConstraint(
            "rule_set_name = 'import_integrity_quality'",
            name="iqe_rule_set",
        ),
        sa.CheckConstraint(
            "rule_set_version = '2.0.0'",
            name="iqe_rule_version",
        ),
        _lower_hex_sha256_constraint("input_fingerprint", constraint_name="iqe_fingerprint"),
        sa.CheckConstraint(
            "observed_status IN ('SUCCEEDED', 'FAILED', 'QUARANTINED')",
            name="iqe_terminal_status",
        ),
        sa.CheckConstraint(
            "observed_effect IN ('APPLIED', 'NOOP', 'REJECTED')",
            name="iqe_terminal_effect",
        ),
        sa.CheckConstraint(
            "outcome IN ('PASS', 'WARN', 'FAIL', 'UNKNOWN')",
            name="iqe_outcome",
        ),
        sa.CheckConstraint(
            "delivery_gate IN ('ELIGIBLE', 'BLOCKED')",
            name="iqe_delivery_gate",
        ),
        sa.CheckConstraint(
            "(outcome IN ('PASS', 'WARN') AND delivery_gate = 'ELIGIBLE') "
            "OR (outcome IN ('FAIL', 'UNKNOWN') AND delivery_gate = 'BLOCKED')",
            name="iqe_outcome_gate",
        ),
        sa.CheckConstraint("rows_read >= 0", name="iqe_rows_read"),
        sa.CheckConstraint("rows_accepted >= 0", name="iqe_rows_accepted"),
        sa.CheckConstraint("rows_rejected >= 0", name="iqe_rows_rejected"),
        sa.CheckConstraint("rows_inserted >= 0", name="iqe_rows_inserted"),
        sa.CheckConstraint(
            "rows_duplicate_identical >= 0",
            name="iqe_rows_duplicates",
        ),
        sa.CheckConstraint("rows_conflicted >= 0", name="iqe_rows_conflicted"),
        sa.CheckConstraint("record_count >= 0", name="iqe_record_count"),
        sa.CheckConstraint("finding_count >= 0", name="iqe_finding_count"),
        _opaque_identifier_constraint("idempotency_key", constraint_name="iqe_idempotency_bounded"),
        _opaque_identifier_constraint("correlation_id", constraint_name="iqe_correlation_bounded"),
        _opaque_identifier_constraint(
            "causation_id",
            constraint_name="iqe_causation_bounded",
            nullable=True,
        ),
        sa.Index("ix_import_quality_evaluation_import_run", "import_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    import_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey(
            "import_run.id",
            name="import_quality_evaluation_import_run",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    rule_set_name: Mapped[str] = mapped_column(
        sa.String(64), nullable=False, server_default="import_integrity_quality"
    )
    rule_set_version: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default="2.0.0"
    )
    input_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    observed_status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    observed_effect: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    delivery_gate: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    rows_read: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default="0")
    rows_accepted: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default="0")
    rows_rejected: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default="0")
    rows_inserted: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default="0")
    rows_duplicate_identical: Mapped[int] = mapped_column(
        sa.Integer(), nullable=False, server_default="0"
    )
    rows_conflicted: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default="0")
    record_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default="0")
    finding_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default="0")
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    correlation_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    causation_id: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)

    import_run: Mapped[ImportRun] = relationship(back_populates="import_quality_evaluations")
    findings: Mapped[list[ImportQualityFinding]] = relationship(back_populates="evaluation")
    snapshot_pins: Mapped[list[DatasetSnapshotImportQualityPin]] = relationship(
        back_populates="import_quality_evaluation"
    )


class ImportQualityFinding(CreatedAtMixin, Base):
    """One bounded, immutable aggregate finding for an import-quality conclusion."""

    __tablename__ = "import_quality_finding"
    __table_args__ = (
        sa.UniqueConstraint(
            "import_quality_evaluation_id",
            "rule_code",
            name="import_quality_finding_evaluation_rule",
        ),
        sa.CheckConstraint(
            "outcome IN ('PASS', 'WARN', 'FAIL', 'UNKNOWN')",
            name="iqf_outcome",
        ),
        sa.CheckConstraint(
            "severity IN ('INFO', 'WARNING', 'ERROR')",
            name="iqf_severity",
        ),
        sa.CheckConstraint("occurrence_count >= 0", name="iqf_occurrence_count"),
        sa.CheckConstraint("length(evidence) <= 2048", name="iqf_evidence_bounded"),
        sa.Index("ix_import_quality_finding_evaluation_id", "import_quality_evaluation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    import_quality_evaluation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey(
            "import_quality_evaluation.id",
            name="import_quality_finding_evaluation",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    rule_code: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    outcome: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    severity: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    evidence: Mapped[str] = mapped_column(sa.Text(), nullable=False)

    evaluation: Mapped[ImportQualityEvaluation] = relationship(back_populates="findings")


class DatasetSnapshotManifest(CreatedAtMixin, Base):
    """One published, content-addressed logical ``futures.ohlcv`` snapshot.

    The manifest is an immutable publication decision, not a mutable ``latest``
    pointer and not a copy of canonical observations. Its child rows pin the
    exact selected canonical members and quality evidence that authorized delivery.
    """

    __tablename__ = "dataset_snapshot_manifest"
    __table_args__ = (
        sa.UniqueConstraint("idempotency_key", name="snapshot_manifest_idempotency"),
        sa.CheckConstraint(
            "manifest_schema_version = '2.0.0'",
            name="manifest_schema",
        ),
        sa.CheckConstraint("dataset_kind = 'FUTURES_OHLCV'", name="dataset_kind"),
        sa.CheckConstraint(
            "canonical_schema_version = 'canonical_ohlcv/1.0.0'",
            name="canonical_schema",
        ),
        _lower_hex_sha256_constraint("request_fingerprint", constraint_name="request_fp"),
        _lower_hex_sha256_constraint("content_hash", constraint_name="content_hash"),
        sa.CheckConstraint("partition_count > 0", name="partition_count"),
        sa.CheckConstraint("member_count > 0", name="member_count"),
        sa.CheckConstraint("import_quality_pin_count > 0", name="import_pin_count"),
        sa.CheckConstraint(
            "series_quality_pin_count = partition_count",
            name="series_pin_count",
        ),
        _opaque_identifier_constraint("idempotency_key", constraint_name="idempotency_syntax"),
        _opaque_identifier_constraint("correlation_id", constraint_name="correlation_syntax"),
        _opaque_identifier_constraint(
            "causation_id",
            constraint_name="causation_syntax",
            nullable=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    manifest_schema_version: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default="2.0.0"
    )
    dataset_kind: Mapped[str] = mapped_column(
        sa.String(32), nullable=False, server_default="FUTURES_OHLCV"
    )
    canonical_schema_version: Mapped[str] = mapped_column(
        sa.String(32), nullable=False, server_default="canonical_ohlcv/1.0.0"
    )
    available_at_cutoff: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )
    request_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    partition_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    member_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    import_quality_pin_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    series_quality_pin_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    correlation_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    causation_id: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)

    partitions: Mapped[list[DatasetSnapshotPartition]] = relationship(back_populates="manifest")
    import_quality_pins: Mapped[list[DatasetSnapshotImportQualityPin]] = relationship(
        back_populates="manifest"
    )


class DatasetSnapshotPartition(CreatedAtMixin, Base):
    """One immutable series/trading-day selection within a snapshot manifest.

    These rows deliberately retain the full frozen metadata needed to
    interpret their members.  A future read/export path must not substitute
    mutable ``DataSeries``, contract, exchange, or calendar fields for this
    stored publication-time meaning.
    """

    __tablename__ = "dataset_snapshot_partition"
    __table_args__ = (
        sa.UniqueConstraint("manifest_id", "series_id", name="snapshot_partition_manifest_series"),
        sa.CheckConstraint(
            "length(contract_code) BETWEEN 1 AND 32 AND contract_code = upper(contract_code)",
            name="contract_code",
        ),
        sa.CheckConstraint(
            "length(product_code) BETWEEN 1 AND 24 AND product_code = upper(product_code)",
            name="product_code",
        ),
        sa.CheckConstraint(
            "length(exchange_code) BETWEEN 1 AND 16 AND exchange_code = upper(exchange_code)",
            name="exchange_code",
        ),
        sa.CheckConstraint(
            "length(exchange_timezone_name) BETWEEN 1 AND 64",
            name="exchange_timezone",
        ),
        sa.CheckConstraint(
            "length(calendar_code) BETWEEN 1 AND 32 AND calendar_code = upper(calendar_code)",
            name="calendar_code",
        ),
        sa.CheckConstraint("calendar_revision > 0", name="calendar_revision"),
        sa.CheckConstraint(
            "length(calendar_timezone_name) BETWEEN 1 AND 64",
            name="calendar_timezone",
        ),
        sa.CheckConstraint(
            "exchange_timezone_name = calendar_timezone_name",
            name="timezone_match",
        ),
        sa.CheckConstraint("series_kind = 'OHLCV'", name="series_kind"),
        sa.CheckConstraint("interval IN ('1m', '1d')", name="interval"),
        sa.CheckConstraint("adjustment = 'RAW'", name="adjustment"),
        sa.CheckConstraint("timestamp_convention = 'BAR_START'", name="timestamp_convention"),
        sa.CheckConstraint("price_scale BETWEEN 0 AND 12", name="price_scale"),
        sa.CheckConstraint("quantity_scale BETWEEN 0 AND 12", name="quantity_scale"),
        sa.CheckConstraint(
            "length(price_currency) = 3 AND price_currency = upper(price_currency)",
            name="price_currency",
        ),
        sa.CheckConstraint("price_tick > 0", name="price_tick_positive"),
        sa.CheckConstraint("contract_multiplier > 0", name="multiplier_positive"),
        sa.CheckConstraint(
            "length(quantity_unit) BETWEEN 1 AND 32 AND quantity_unit = upper(quantity_unit)",
            name="quantity_unit",
        ),
        sa.CheckConstraint(
            "length(volume_unit) BETWEEN 1 AND 32 AND volume_unit = upper(volume_unit)",
            name="volume_unit",
        ),
        sa.CheckConstraint(
            "length(turnover_currency) = 3 AND turnover_currency = upper(turnover_currency)",
            name="turnover_currency",
        ),
        sa.CheckConstraint("trading_day_from <= trading_day_to", name="day_range"),
        sa.CheckConstraint("event_time_from <= event_time_to", name="time_range"),
        sa.CheckConstraint("row_count > 0", name="row_count"),
        _lower_hex_sha256_constraint("membership_hash", constraint_name="membership_hash"),
        _lower_hex_sha256_constraint("content_hash", constraint_name="content_hash"),
        sa.Index("ix_snapshot_partition_manifest", "manifest_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    manifest_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey(
            "dataset_snapshot_manifest.id",
            name="snapshot_partition_manifest",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    series_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey("data_series.id", name="snapshot_partition_series", ondelete="RESTRICT"),
        nullable=False,
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey(
            "futures_contract.id", name="snapshot_partition_contract", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    contract_code: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    product_code: Mapped[str] = mapped_column(sa.String(24), nullable=False)
    exchange_code: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    exchange_timezone_name: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    calendar_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey(
            "trading_calendar.id", name="snapshot_partition_calendar", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    calendar_code: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    calendar_revision: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    calendar_timezone_name: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    series_kind: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    interval: Mapped[str] = mapped_column(sa.String(8), nullable=False)
    adjustment: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    timestamp_convention: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    price_scale: Mapped[int] = mapped_column(sa.SmallInteger(), nullable=False)
    quantity_scale: Mapped[int] = mapped_column(sa.SmallInteger(), nullable=False)
    price_currency: Mapped[str] = mapped_column(sa.String(3), nullable=False)
    price_tick: Mapped[Decimal] = mapped_column(Price, nullable=False)
    contract_multiplier: Mapped[Decimal] = mapped_column(Price, nullable=False)
    quantity_unit: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    volume_unit: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    turnover_currency: Mapped[str] = mapped_column(sa.String(3), nullable=False)
    trading_day_from: Mapped[date] = mapped_column(sa.Date(), nullable=False)
    trading_day_to: Mapped[date] = mapped_column(sa.Date(), nullable=False)
    event_time_from: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    event_time_to: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    row_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    membership_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    content_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)

    manifest: Mapped[DatasetSnapshotManifest] = relationship(back_populates="partitions")
    series: Mapped[DataSeries] = relationship(back_populates="snapshot_partitions")
    members: Mapped[list[DatasetSnapshotMember]] = relationship(back_populates="partition")
    series_quality_pin: Mapped[DatasetSnapshotSeriesQualityPin | None] = relationship(
        back_populates="partition"
    )


class DatasetSnapshotMember(CreatedAtMixin, Base):
    """One explicit canonical-bar membership pin with its semantic fingerprint."""

    __tablename__ = "dataset_snapshot_member"
    __table_args__ = (
        sa.UniqueConstraint(
            "partition_id", "canonical_bar_id", name="snapshot_member_partition_bar"
        ),
        sa.UniqueConstraint("partition_id", "ordinal", name="snapshot_member_partition_ordinal"),
        sa.CheckConstraint("ordinal >= 0", name="ordinal"),
        _lower_hex_sha256_constraint("canonical_bar_fingerprint", constraint_name="bar_fp"),
        sa.Index("ix_snapshot_member_partition", "partition_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    partition_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey(
            "dataset_snapshot_partition.id", name="snapshot_member_partition", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    canonical_bar_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey("canonical_bar.id", name="snapshot_member_bar", ondelete="RESTRICT"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    event_time: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    trading_day: Mapped[date] = mapped_column(sa.Date(), nullable=False)
    available_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    canonical_bar_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False)

    partition: Mapped[DatasetSnapshotPartition] = relationship(back_populates="members")
    canonical_bar: Mapped[CanonicalBar] = relationship(back_populates="snapshot_members")


class DatasetSnapshotImportQualityPin(CreatedAtMixin, Base):
    """The exact eligible import-quality conclusion for one included run."""

    __tablename__ = "dataset_snapshot_import_quality_pin"
    __table_args__ = (
        sa.UniqueConstraint(
            "manifest_id", "import_run_id", name="snapshot_import_pin_manifest_run"
        ),
        sa.UniqueConstraint(
            "manifest_id", "import_quality_evaluation_id", name="snapshot_import_pin_manifest_eval"
        ),
        sa.CheckConstraint("rule_set_name = 'import_integrity_quality'", name="rule_set"),
        sa.CheckConstraint("rule_set_version = '2.0.0'", name="rule_version"),
        _lower_hex_sha256_constraint("input_fingerprint", constraint_name="fingerprint"),
        sa.CheckConstraint("outcome IN ('PASS', 'WARN')", name="outcome"),
        sa.CheckConstraint("delivery_gate = 'ELIGIBLE'", name="delivery_gate"),
        sa.Index("ix_snapshot_import_pin_manifest", "manifest_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    manifest_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey(
            "dataset_snapshot_manifest.id", name="snapshot_import_pin_manifest", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    import_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey("import_run.id", name="snapshot_import_pin_run", ondelete="RESTRICT"),
        nullable=False,
    )
    import_quality_evaluation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey(
            "import_quality_evaluation.id",
            name="snapshot_import_pin_evaluation",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    rule_set_name: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    rule_set_version: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    delivery_gate: Mapped[str] = mapped_column(sa.String(16), nullable=False)

    manifest: Mapped[DatasetSnapshotManifest] = relationship(back_populates="import_quality_pins")
    import_run: Mapped[ImportRun] = relationship(back_populates="snapshot_import_quality_pins")
    import_quality_evaluation: Mapped[ImportQualityEvaluation] = relationship(
        back_populates="snapshot_pins"
    )


class DatasetSnapshotSeriesQualityPin(CreatedAtMixin, Base):
    """The exact eligible series-quality conclusion for one snapshot partition."""

    __tablename__ = "dataset_snapshot_series_quality_pin"
    __table_args__ = (
        sa.UniqueConstraint("partition_id", name="snapshot_series_pin_partition"),
        sa.UniqueConstraint("quality_evaluation_id", name="snapshot_series_pin_quality_evaluation"),
        sa.CheckConstraint(
            "(evaluation_scope = 'DAILY_COVERAGE' "
            "AND rule_set_name = 'daily_coverage_quality' "
            "AND rule_set_version = '1.0.0') "
            "OR (evaluation_scope = 'MINUTE_SESSION_COVERAGE' "
            "AND rule_set_name = 'minute_session_coverage_quality' "
            "AND rule_set_version = '1.0.0')",
            name="rule_scope",
        ),
        sa.CheckConstraint("trading_day_from <= trading_day_to", name="day_range"),
        sa.CheckConstraint("calendar_revision > 0", name="calendar_revision"),
        _lower_hex_sha256_constraint("input_fingerprint", constraint_name="fingerprint"),
        sa.CheckConstraint("outcome IN ('PASS', 'WARN')", name="outcome"),
        sa.CheckConstraint("delivery_gate = 'ELIGIBLE'", name="delivery_gate"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    partition_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey(
            "dataset_snapshot_partition.id",
            name="snapshot_series_pin_partition_fk",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    quality_evaluation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey(
            "quality_evaluation.id", name="snapshot_series_pin_evaluation", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    calendar_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey(
            "trading_calendar.id", name="snapshot_series_pin_calendar", ondelete="RESTRICT"
        ),
        nullable=False,
    )
    calendar_revision: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    evaluation_scope: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    rule_set_name: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    rule_set_version: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    trading_day_from: Mapped[date] = mapped_column(sa.Date(), nullable=False)
    trading_day_to: Mapped[date] = mapped_column(sa.Date(), nullable=False)
    as_of: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    delivery_gate: Mapped[str] = mapped_column(sa.String(16), nullable=False)

    partition: Mapped[DatasetSnapshotPartition] = relationship(back_populates="series_quality_pin")
    quality_evaluation: Mapped[QualityEvaluation] = relationship(
        back_populates="snapshot_series_quality_pins"
    )


class QualityEvaluation(CreatedAtMixin, Base):
    """One immutable series-window quality conclusion.

    An evaluation is deliberately separate from an ``ImportRun``.  Import runs
    record whether a source payload was normalized and applied; this evidence
    records what committed canonical observations and a pinned calendar mean at
    one explicit ``as_of`` instant.  ``evaluation_scope`` makes the supported
    daily and minute-session policies non-interchangeable.  ``delivery_gate``
    is consumed only by explicit snapshot publication; the evaluation itself
    does not expose data to a research client.
    """

    __tablename__ = "quality_evaluation"
    __table_args__ = (
        sa.UniqueConstraint("idempotency_key", name="quality_evaluation_idempotency"),
        sa.CheckConstraint(
            "(evaluation_scope = 'DAILY_COVERAGE' "
            "AND rule_set_name = 'daily_coverage_quality' "
            "AND rule_set_version = '1.0.0') "
            "OR (evaluation_scope = 'MINUTE_SESSION_COVERAGE' "
            "AND rule_set_name = 'minute_session_coverage_quality' "
            "AND rule_set_version = '1.0.0')",
            name="quality_evaluation_rule_scope",
        ),
        sa.CheckConstraint(
            "trading_day_from <= trading_day_to", name="quality_evaluation_day_range"
        ),
        _lower_hex_sha256_constraint(
            "input_fingerprint", constraint_name="quality_evaluation_fingerprint"
        ),
        sa.CheckConstraint(
            "outcome IN ('PASS', 'WARN', 'FAIL', 'UNKNOWN')",
            name="quality_evaluation_outcome",
        ),
        sa.CheckConstraint(
            "delivery_gate IN ('ELIGIBLE', 'BLOCKED')",
            name="quality_evaluation_delivery_gate",
        ),
        sa.CheckConstraint(
            "(outcome IN ('PASS', 'WARN') AND delivery_gate = 'ELIGIBLE') "
            "OR (outcome IN ('FAIL', 'UNKNOWN') AND delivery_gate = 'BLOCKED')",
            name="quality_evaluation_outcome_gate",
        ),
        sa.CheckConstraint(
            "expected_observation_count >= 0", name="quality_evaluation_expected_count"
        ),
        sa.CheckConstraint(
            "covered_observation_count >= 0", name="quality_evaluation_covered_count"
        ),
        sa.CheckConstraint(
            "missing_observation_count >= 0", name="quality_evaluation_missing_count"
        ),
        sa.CheckConstraint("unknown_day_count >= 0", name="quality_evaluation_unknown_count"),
        sa.CheckConstraint("finding_count >= 0", name="quality_evaluation_finding_count"),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 1 AND 128",
            name="quality_evaluation_idempotency_bounded",
        ),
        sa.CheckConstraint(
            "length(correlation_id) BETWEEN 1 AND 128",
            name="quality_evaluation_correlation_bounded",
        ),
        sa.CheckConstraint(
            "causation_id IS NULL OR length(causation_id) BETWEEN 1 AND 128",
            name="quality_evaluation_causation_bounded",
        ),
        sa.Index(
            "ix_quality_evaluation_series_range",
            "series_id",
            "trading_day_from",
            "trading_day_to",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    series_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey(
            "data_series.id",
            name="quality_evaluation_series",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    calendar_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey(
            "trading_calendar.id",
            name="quality_evaluation_calendar",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    calendar_revision: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    evaluation_scope: Mapped[str] = mapped_column(
        sa.String(32), nullable=False, server_default="DAILY_COVERAGE"
    )
    rule_set_name: Mapped[str] = mapped_column(
        sa.String(64), nullable=False, server_default="daily_coverage_quality"
    )
    rule_set_version: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default="1.0.0"
    )
    trading_day_from: Mapped[date] = mapped_column(sa.Date(), nullable=False)
    trading_day_to: Mapped[date] = mapped_column(sa.Date(), nullable=False)
    as_of: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    delivery_gate: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    expected_observation_count: Mapped[int] = mapped_column(
        sa.Integer(), nullable=False, server_default="0"
    )
    covered_observation_count: Mapped[int] = mapped_column(
        sa.Integer(), nullable=False, server_default="0"
    )
    missing_observation_count: Mapped[int] = mapped_column(
        sa.Integer(), nullable=False, server_default="0"
    )
    unknown_day_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default="0")
    finding_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False, server_default="0")
    idempotency_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    correlation_id: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    causation_id: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)

    series: Mapped[DataSeries] = relationship(back_populates="quality_evaluations")
    findings: Mapped[list[QualityFinding]] = relationship(back_populates="quality_evaluation")
    snapshot_series_quality_pins: Mapped[list[DatasetSnapshotSeriesQualityPin]] = relationship(
        back_populates="quality_evaluation"
    )


class QualityFinding(CreatedAtMixin, Base):
    """One immutable bounded rule result under a concrete quality evaluation."""

    __tablename__ = "quality_finding"
    __table_args__ = (
        sa.CheckConstraint(
            "outcome IN ('PASS', 'WARN', 'FAIL', 'UNKNOWN')", name="quality_outcome"
        ),
        sa.CheckConstraint("severity IN ('INFO', 'WARNING', 'ERROR')", name="quality_severity"),
        sa.UniqueConstraint(
            "quality_evaluation_id",
            "rule_code",
            name="quality_finding_evaluation_rule",
        ),
        sa.CheckConstraint(
            "trading_day_from IS NULL OR trading_day_to IS NULL "
            "OR trading_day_from <= trading_day_to",
            name="quality_finding_day_range",
        ),
        sa.CheckConstraint("occurrence_count >= 0", name="quality_finding_occurrence_count"),
        sa.CheckConstraint("length(evidence) <= 2048", name="quality_finding_evidence_bounded"),
        sa.Index("ix_quality_finding_evaluation_id", "quality_evaluation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    import_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        sa.ForeignKey("import_run.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    series_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey("data_series.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quality_evaluation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        sa.ForeignKey(
            "quality_evaluation.id",
            name="quality_finding_evaluation",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    rule_code: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    outcome: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    severity: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    trading_day_from: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    trading_day_to: Mapped[date | None] = mapped_column(sa.Date(), nullable=True)
    occurrence_count: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    evidence: Mapped[str] = mapped_column(sa.Text(), nullable=False)

    import_run: Mapped[ImportRun | None] = relationship(back_populates="quality_findings")
    series: Mapped[DataSeries] = relationship()
    quality_evaluation: Mapped[QualityEvaluation] = relationship(back_populates="findings")

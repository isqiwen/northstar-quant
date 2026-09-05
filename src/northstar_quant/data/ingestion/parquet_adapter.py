"""The strict, bounded ``canonical_ohlcv_parquet/1.0.0`` source adapter.

This module deliberately decodes only a small canonical Parquet envelope.  It
does not make Parquet a stored dataset format: callers pass its transient bytes
through the same ``SourcePayload -> ParsedOhlcvRows`` boundary used by CSV
ingestion, so the existing canonical writer remains the only persistence path.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from northstar_quant.data.catalog.models import (
    SOURCE_RECEIPT_DEFAULT_ACQUISITION_USE,
    SOURCE_RECEIPT_DEFAULT_REDISTRIBUTION_POLICY,
)
from northstar_quant.data.ingestion.imports import (
    PARQUET_AVAILABLE_AT_POLICY,
    PARQUET_MAPPING_VERSION,
    PARQUET_PROFILE_NAME,
    PARQUET_PROFILE_VERSION,
    PARQUET_UNIT_POLICY,
    OhlcvImportError,
    ParsedOhlcvRows,
    RawOhlcvRow,
    SourcePayload,
)

REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "symbol",
        "interval",
        "trading_day",
        "available_at",
        "source_record_id",
        "price_currency",
        "volume_unit",
        "open_interest_unit",
        "turnover_currency",
        "turnover_multiplier",
        "open_price",
        "high_price",
        "low_price",
        "close_price",
        "volume",
    }
)
OPTIONAL_COLUMNS: Final[frozenset[str]] = frozenset({"event_time", "turnover", "open_interest"})

_PROFILE_METADATA: Final[dict[bytes, bytes]] = {
    b"northstar_quant.data.profile": PARQUET_PROFILE_NAME.encode("ascii"),
    b"northstar_quant.data.profile_version": PARQUET_PROFILE_VERSION.encode("ascii"),
}
_TEXT_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "symbol",
        "interval",
        "source_record_id",
        "price_currency",
        "volume_unit",
        "open_interest_unit",
        "turnover_currency",
    }
)
_DECIMAL_LIMITS: Final[dict[str, tuple[int, int]]] = {
    "turnover_multiplier": (32, 12),
    "open_price": (24, 12),
    "high_price": (24, 12),
    "low_price": (24, 12),
    "close_price": (24, 12),
    "volume": (28, 12),
    "turnover": (32, 12),
    "open_interest": (28, 12),
}
_ROW_BATCH_SIZE: Final[int] = 8_192


class CanonicalOhlcvParquetAdapter:
    """Decode one exact, bounded canonical OHLCV Parquet profile.

    The profile rejects unknown columns, binary floating point, timestamp values
    without an IANA timezone, and schema coercion.  Decimal scale and the final
    session and availability rules still flow through the canonical writer.
    """

    media_type = "application/vnd.apache.parquet"
    mapping_version = PARQUET_MAPPING_VERSION
    job_kind = "PARQUET_IMPORT_V1"
    input_kind = "OPERATOR_FILE"
    retention_policy = "TRANSIENT"
    acquisition_use = SOURCE_RECEIPT_DEFAULT_ACQUISITION_USE
    redistribution_policy = SOURCE_RECEIPT_DEFAULT_REDISTRIBUTION_POLICY

    def __init__(
        self,
        *,
        max_bytes: int,
        max_rows: int,
        max_field_bytes: int,
        max_uncompressed_bytes: int | None = None,
    ) -> None:
        if max_bytes <= 0 or max_rows <= 0 or max_field_bytes <= 0:
            raise ValueError("Parquet adapter limits must be positive")
        effective_uncompressed_limit = max_uncompressed_bytes or max_bytes
        if effective_uncompressed_limit <= 0:
            raise ValueError("max_uncompressed_bytes must be positive")
        self._max_bytes = max_bytes
        self._max_rows = max_rows
        self._max_field_bytes = max_field_bytes
        self._max_uncompressed_bytes = effective_uncompressed_limit

    def load(self, file_path: Path) -> SourcePayload:
        """Read one bounded transient Parquet input and hash its exact bytes."""

        try:
            with file_path.open("rb") as source_file:
                content = source_file.read(self._max_bytes + 1)
        except (OSError, ValueError) as error:
            raise OhlcvImportError(
                "INPUT_UNREADABLE",
                "the input file could not be read by this operator command",
            ) from error
        if len(content) > self._max_bytes:
            raise OhlcvImportError(
                "INPUT_TOO_LARGE",
                "the input exceeds the configured Parquet byte limit",
            )
        if not content:
            raise OhlcvImportError("EMPTY_INPUT", "the input Parquet file is empty")
        return SourcePayload(
            content=content,
            content_hash=hashlib.sha256(content).hexdigest(),
            byte_count=len(content),
        )

    def parse(
        self,
        payload: SourcePayload,
        *,
        source_timezone_name: str | None = None,
    ) -> ParsedOhlcvRows:
        """Return canonical raw rows without accepting implicit Arrow coercions.

        When the caller declares a source timezone, it must exactly match the
        Parquet timestamp schema's IANA name.  Matching only an observed UTC
        offset would make the durable receipt/mapping evidence ambiguous.
        """

        try:
            parquet_file = pq.ParquetFile(pa.BufferReader(payload.content))
            metadata = parquet_file.metadata
            if metadata is None:
                raise OhlcvImportError("INVALID_PARQUET", "the Parquet file has no metadata")
            if metadata.num_rows > self._max_rows:
                raise OhlcvImportError(
                    "ROW_LIMIT_EXCEEDED",
                    "the input exceeds the configured Parquet row limit",
                    rows_read=metadata.num_rows,
                    rows_rejected=1,
                )
            if self._uncompressed_size(metadata) > self._max_uncompressed_bytes:
                raise OhlcvImportError(
                    "PARQUET_UNCOMPRESSED_LIMIT_EXCEEDED",
                    "the input exceeds the configured decoded Parquet byte limit",
                )
            schema = parquet_file.schema_arrow
            schema_timezone_name = self._validate_schema(schema)
            if source_timezone_name is not None:
                self._validate_declared_source_timezone(schema_timezone_name, source_timezone_name)
            return self._parse_batches(parquet_file, schema, schema_timezone_name)
        except OhlcvImportError:
            raise
        except (OSError, OverflowError, ValueError, pa.ArrowException) as error:
            raise OhlcvImportError(
                "INVALID_PARQUET", "the input is not a valid Parquet file"
            ) from error

    def mapping_metadata(self, *, source_timezone_name: str) -> dict[str, object]:
        """Describe the exact profile, units, and row locator policy in use."""

        return {
            "profile": {"name": PARQUET_PROFILE_NAME, "version": PARQUET_PROFILE_VERSION},
            "mapping_version": PARQUET_MAPPING_VERSION,
            "available_at_policy": PARQUET_AVAILABLE_AT_POLICY,
            "unit_policy": PARQUET_UNIT_POLICY,
            "source_timezone_name": source_timezone_name,
            "source_row_number_policy": "parquet_physical_row_ordinal_plus_two_v1",
            "columns": {
                "symbol": "catalog.contract.contract_code",
                "interval": "data_series.interval",
                "event_time": "canonical_bar.event_time (required for 1m; derived for 1d)",
                "trading_day": "canonical_bar.trading_day",
                "available_at": "canonical_bar.available_at",
                "source_record_id": "canonical_bar.source_record_id",
                "open_price": "canonical_bar.open_price",
                "high_price": "canonical_bar.high_price",
                "low_price": "canonical_bar.low_price",
                "close_price": "canonical_bar.close_price",
                "volume": "canonical_bar.volume",
                "turnover": "canonical_bar.turnover",
                "open_interest": "canonical_bar.open_interest",
            },
        }

    def request_fingerprint_metadata(self, *, source_timezone_name: str) -> dict[str, object]:
        """All Parquet request facts are already in its exact hashed bytes."""

        del source_timezone_name
        return {}

    def _uncompressed_size(self, metadata: pq.FileMetaData) -> int:
        total = 0
        for row_group_index in range(metadata.num_row_groups):
            row_group = metadata.row_group(row_group_index)
            for column_index in range(row_group.num_columns):
                column = row_group.column(column_index)
                uncompressed_size = int(column.total_uncompressed_size)
                if uncompressed_size < 0:
                    raise OhlcvImportError(
                        "INVALID_PARQUET", "the Parquet file has an invalid chunk size"
                    )
                total += uncompressed_size
                if total > self._max_uncompressed_bytes:
                    return total
        return total

    def _validate_schema(self, schema: pa.Schema) -> str:
        metadata = schema.metadata or {}
        if any(metadata.get(key) != value for key, value in _PROFILE_METADATA.items()):
            raise OhlcvImportError(
                "UNSUPPORTED_PARQUET_PROFILE",
                "the Parquet schema does not declare canonical_ohlcv_parquet/1.0.0",
            )

        names = tuple(schema.names)
        if len(set(names)) != len(names):
            raise OhlcvImportError(
                "DUPLICATE_HEADER", "the input Parquet schema has duplicate field names"
            )
        name_set = frozenset(names)
        missing = sorted(REQUIRED_COLUMNS - name_set)
        if missing:
            raise OhlcvImportError(
                "MISSING_REQUIRED_FIELD",
                f"the input Parquet schema is missing required field '{missing[0]}'",
            )
        unexpected = sorted(name_set - REQUIRED_COLUMNS - OPTIONAL_COLUMNS)
        if unexpected:
            raise OhlcvImportError(
                "UNSUPPORTED_FIELD",
                f"the input Parquet schema declares unsupported field '{unexpected[0]}'",
            )

        for field_name in _TEXT_COLUMNS:
            if not pa.types.is_string(schema.field(field_name).type):
                self._raise_type_error(field_name, "UTF-8 string")
        if not pa.types.is_date32(schema.field("trading_day").type):
            self._raise_type_error("trading_day", "date32")

        available_timezone = self._validate_timestamp_field(schema.field("available_at"))
        if "event_time" in name_set:
            event_timezone = self._validate_timestamp_field(schema.field("event_time"))
            if event_timezone != available_timezone:
                raise OhlcvImportError(
                    "TIMESTAMP_TIMEZONE_MISMATCH",
                    "event_time and available_at must use the same source timezone",
                )

        for field_name, (max_precision, max_scale) in _DECIMAL_LIMITS.items():
            if field_name not in name_set:
                continue
            data_type = schema.field(field_name).type
            if pa.types.is_floating(data_type):
                raise OhlcvImportError(
                    "BINARY_FLOAT_UNSUPPORTED",
                    f"Parquet field '{field_name}' must use decimal128, not binary float",
                )
            if not pa.types.is_decimal128(data_type):
                self._raise_type_error(field_name, "decimal128")
            precision = getattr(data_type, "precision")
            scale = getattr(data_type, "scale")
            if not isinstance(precision, int) or not isinstance(scale, int):
                self._raise_type_error(field_name, "decimal128")
            if precision > max_precision or scale < 0 or scale > max_scale:
                raise OhlcvImportError(
                    "DECIMAL_SCHEMA_OUT_OF_RANGE",
                    f"Parquet field '{field_name}' exceeds the canonical decimal domain",
                )
        return available_timezone

    def _validate_timestamp_field(self, field: pa.Field) -> str:
        data_type = field.type
        if not pa.types.is_timestamp(data_type):
            self._raise_type_error(field.name, "timestamp with IANA timezone")
        timezone_name = getattr(data_type, "tz")
        if not isinstance(timezone_name, str) or not timezone_name:
            raise OhlcvImportError(
                "MISSING_TIMEZONE_OFFSET",
                f"Parquet field '{field.name}' must declare an IANA timezone",
            )
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise OhlcvImportError(
                "UNKNOWN_SOURCE_TIMEZONE",
                f"Parquet field '{field.name}' must declare a known IANA timezone",
            ) from error
        return timezone_name

    def _validate_declared_source_timezone(
        self, schema_timezone_name: str, declared_source_timezone_name: str
    ) -> None:
        normalized = declared_source_timezone_name.strip()
        try:
            ZoneInfo(normalized)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise OhlcvImportError(
                "UNKNOWN_SOURCE_TIMEZONE",
                "source_timezone_name must be a known IANA timezone",
            ) from error
        if normalized != schema_timezone_name:
            raise OhlcvImportError(
                "SOURCE_TIMEZONE_DECLARATION_MISMATCH",
                "the declared source timezone must exactly match the Parquet timestamp schema",
            )

    def _raise_type_error(self, field_name: str, expected: str) -> None:
        raise OhlcvImportError(
            "INVALID_PARQUET_TYPE",
            f"Parquet field '{field_name}' must use {expected}",
        )

    def _parse_batches(
        self,
        parquet_file: pq.ParquetFile,
        schema: pa.Schema,
        source_timezone_name: str,
    ) -> ParsedOhlcvRows:
        rows: list[RawOhlcvRow] = []
        source_record_ids: set[str] = set()
        names = tuple(schema.names)
        try:
            batches = parquet_file.iter_batches(
                batch_size=min(_ROW_BATCH_SIZE, self._max_rows), columns=list(names)
            )
            for batch in batches:
                values_by_name = {
                    name: batch.column(index).to_pylist() for index, name in enumerate(names)
                }
                for batch_index in range(batch.num_rows):
                    rows_read = len(rows) + 1
                    if rows_read > self._max_rows:
                        raise OhlcvImportError(
                            "ROW_LIMIT_EXCEEDED",
                            "the input exceeds the configured Parquet row limit",
                            rows_read=rows_read,
                            rows_rejected=1,
                        )
                    row = {name: values_by_name[name][batch_index] for name in names}
                    parsed = self._parse_row(
                        row,
                        source_row_number=rows_read + 1,
                        rows_read=rows_read,
                    )
                    if parsed.source_record_id in source_record_ids:
                        raise OhlcvImportError(
                            "DUPLICATE_SOURCE_RECORD_ID",
                            f"row {parsed.source_row_number} repeats a source record identifier",
                            rows_read=rows_read,
                            rows_rejected=1,
                            quarantined=True,
                        )
                    source_record_ids.add(parsed.source_record_id)
                    rows.append(parsed)
        except OhlcvImportError:
            raise
        except (OSError, OverflowError, ValueError, pa.ArrowException) as error:
            raise OhlcvImportError(
                "INVALID_PARQUET", "the input could not be decoded safely"
            ) from error
        if not rows:
            raise OhlcvImportError("EMPTY_INPUT", "the input Parquet file contains no data rows")
        return ParsedOhlcvRows(
            rows=tuple(rows),
            mapping=self.mapping_metadata(source_timezone_name=source_timezone_name),
        )

    def _parse_row(
        self,
        row: dict[str, object],
        *,
        source_row_number: int,
        rows_read: int,
    ) -> RawOhlcvRow:
        def required_text(field_name: str) -> str:
            value = row[field_name]
            if not isinstance(value, str):
                raise self._row_error(
                    "MISSING_FIELD_VALUE",
                    field_name,
                    source_row_number=source_row_number,
                    rows_read=rows_read,
                )
            normalized = value.strip()
            if not normalized:
                raise self._row_error(
                    "MISSING_FIELD_VALUE",
                    field_name,
                    source_row_number=source_row_number,
                    rows_read=rows_read,
                )
            if "\x00" in normalized or len(normalized.encode("utf-8")) > self._max_field_bytes:
                raise self._row_error(
                    "INVALID_PARQUET_FIELD_VALUE",
                    field_name,
                    source_row_number=source_row_number,
                    rows_read=rows_read,
                )
            return normalized

        def required_decimal(field_name: str) -> Decimal:
            value = row[field_name]
            if not isinstance(value, Decimal) or not value.is_finite():
                raise self._row_error(
                    "INVALID_DECIMAL",
                    field_name,
                    source_row_number=source_row_number,
                    rows_read=rows_read,
                )
            return value

        def optional_decimal(field_name: str) -> Decimal | None:
            if field_name not in row or row[field_name] is None:
                return None
            return required_decimal(field_name)

        def optional_timestamp(field_name: str) -> datetime | None:
            if field_name not in row or row[field_name] is None:
                return None
            value = row[field_name]
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise self._row_error(
                    "MISSING_TIMEZONE_OFFSET",
                    field_name,
                    source_row_number=source_row_number,
                    rows_read=rows_read,
                )
            return value

        trading_day = row["trading_day"]
        if not isinstance(trading_day, date) or isinstance(trading_day, datetime):
            raise self._row_error(
                "INVALID_TRADING_DAY",
                "trading_day",
                source_row_number=source_row_number,
                rows_read=rows_read,
            )
        available_at = optional_timestamp("available_at")
        if available_at is None:
            raise self._row_error(
                "MISSING_FIELD_VALUE",
                "available_at",
                source_row_number=source_row_number,
                rows_read=rows_read,
            )

        return RawOhlcvRow(
            source_row_number=source_row_number,
            symbol=required_text("symbol"),
            interval=required_text("interval"),
            event_time=optional_timestamp("event_time"),
            trading_day=trading_day,
            available_at=available_at,
            source_record_id=required_text("source_record_id"),
            price_currency=required_text("price_currency"),
            volume_unit=required_text("volume_unit"),
            open_interest_unit=required_text("open_interest_unit"),
            turnover_currency=required_text("turnover_currency"),
            turnover_multiplier=required_decimal("turnover_multiplier"),
            open_price=required_decimal("open_price"),
            high_price=required_decimal("high_price"),
            low_price=required_decimal("low_price"),
            close_price=required_decimal("close_price"),
            volume=required_decimal("volume"),
            turnover=optional_decimal("turnover"),
            open_interest=optional_decimal("open_interest"),
        )

    def _row_error(
        self,
        code: str,
        field_name: str,
        *,
        source_row_number: int,
        rows_read: int,
    ) -> OhlcvImportError:
        return OhlcvImportError(
            code,
            f"row {source_row_number} field '{field_name}' is invalid for the Parquet profile",
            rows_read=rows_read,
            rows_rejected=1,
        )

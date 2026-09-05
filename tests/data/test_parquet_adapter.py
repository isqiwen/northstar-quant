"""Behavior tests for the strict Parquet input adapter."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from northstar_quant.data.ingestion.imports import (
    PARQUET_MAPPING_VERSION,
    PARQUET_PROFILE_NAME,
    PARQUET_PROFILE_VERSION,
    OhlcvImportError,
    ParsedOhlcvRows,
    RawOhlcvRow,
)
from northstar_quant.data.ingestion.parquet_adapter import CanonicalOhlcvParquetAdapter

_SOURCE_TIMEZONE = ZoneInfo("Asia/Shanghai")
_PROFILE_METADATA = {
    b"northstar_quant.data.profile": PARQUET_PROFILE_NAME.encode("ascii"),
    b"northstar_quant.data.profile_version": PARQUET_PROFILE_VERSION.encode("ascii"),
}
_FIELD_TYPES: dict[str, pa.DataType] = {
    "symbol": pa.string(),
    "interval": pa.string(),
    "event_time": pa.timestamp("us", tz="Asia/Shanghai"),
    "trading_day": pa.date32(),
    "available_at": pa.timestamp("us", tz="Asia/Shanghai"),
    "source_record_id": pa.string(),
    "price_currency": pa.string(),
    "volume_unit": pa.string(),
    "open_interest_unit": pa.string(),
    "turnover_currency": pa.string(),
    "turnover_multiplier": pa.decimal128(32, 0),
    "open_price": pa.decimal128(24, 2),
    "high_price": pa.decimal128(24, 2),
    "low_price": pa.decimal128(24, 2),
    "close_price": pa.decimal128(24, 2),
    "volume": pa.decimal128(28, 0),
    "turnover": pa.decimal128(32, 2),
    "open_interest": pa.decimal128(28, 0),
}
_VALUES: dict[str, list[object | None]] = {
    "symbol": ["RB2605", "RB2605"],
    "interval": ["1m", "1m"],
    "event_time": [
        datetime(2026, 1, 6, 21, 0, tzinfo=_SOURCE_TIMEZONE),
        datetime(2026, 1, 6, 21, 1, tzinfo=_SOURCE_TIMEZONE),
    ],
    "trading_day": [date(2026, 1, 7), date(2026, 1, 7)],
    "available_at": [
        datetime(2026, 1, 6, 21, 1, tzinfo=_SOURCE_TIMEZONE),
        datetime(2026, 1, 6, 21, 2, tzinfo=_SOURCE_TIMEZONE),
    ],
    "source_record_id": ["parquet-0001", "parquet-0002"],
    "price_currency": ["CNY", "CNY"],
    "volume_unit": ["LOT", "LOT"],
    "open_interest_unit": ["LOT", "LOT"],
    "turnover_currency": ["CNY", "CNY"],
    "turnover_multiplier": [Decimal("1"), Decimal("1")],
    "open_price": [Decimal("3500.10"), Decimal("3500.80")],
    "high_price": [Decimal("3501.20"), Decimal("3501.00")],
    "low_price": [Decimal("3499.80"), Decimal("3500.50")],
    "close_price": [Decimal("3500.80"), Decimal("3500.70")],
    "volume": [Decimal("10"), Decimal("15")],
    "turnover": [Decimal("35008.00"), Decimal("52510.50")],
    "open_interest": [Decimal("120000"), Decimal("120010")],
}


def _adapter(
    *, max_rows: int = 100, max_uncompressed_bytes: int = 1_000_000
) -> CanonicalOhlcvParquetAdapter:
    return CanonicalOhlcvParquetAdapter(
        max_bytes=1_000_000,
        max_rows=max_rows,
        max_field_bytes=128,
        max_uncompressed_bytes=max_uncompressed_bytes,
    )


def _write_parquet(
    path: Path,
    *,
    type_overrides: dict[str, pa.DataType] | None = None,
    value_overrides: dict[str, list[object | None]] | None = None,
    omit: frozenset[str] = frozenset(),
    extra_field: tuple[str, pa.DataType, list[object | None]] | None = None,
    metadata: dict[bytes, bytes] | None = _PROFILE_METADATA,
) -> None:
    types = {**_FIELD_TYPES, **(type_overrides or {})}
    values = {**_VALUES, **(value_overrides or {})}
    fields: list[pa.Field] = []
    arrays: list[pa.Array] = []
    for name in _FIELD_TYPES:
        if name in omit:
            continue
        data_type = types[name]
        current_values = values[name]
        if pa.types.is_timestamp(data_type) and getattr(data_type, "tz") is None:
            current_values = [
                value.replace(tzinfo=None) if isinstance(value, datetime) else value
                for value in current_values
            ]
        if pa.types.is_floating(data_type):
            current_values = [
                float(value) if isinstance(value, Decimal) else value for value in current_values
            ]
        fields.append(pa.field(name, data_type))
        arrays.append(pa.array(current_values, type=data_type))
    if extra_field is not None:
        name, data_type, current_values = extra_field
        fields.append(pa.field(name, data_type))
        arrays.append(pa.array(current_values, type=data_type))
    schema = pa.schema(fields, metadata=metadata)
    pq.write_table(pa.Table.from_arrays(arrays, schema=schema), path)


def _parse(
    path: Path,
    *,
    adapter: CanonicalOhlcvParquetAdapter | None = None,
    source_timezone_name: str | None = None,
) -> ParsedOhlcvRows:
    active_adapter = adapter or _adapter()
    return active_adapter.parse(
        active_adapter.load(path), source_timezone_name=source_timezone_name
    )


def test_parquet_adapter_returns_raw_rows_without_float_conversion(
    tmp_path: Path,
) -> None:
    path = tmp_path / "canonical-bars.parquet"
    _write_parquet(path)

    parsed = _parse(path)

    assert len(parsed.rows) == 2
    assert isinstance(parsed.rows[0], RawOhlcvRow)
    assert parsed.rows[0].source_row_number == 2
    assert parsed.rows[0].event_time == datetime(2026, 1, 6, 21, tzinfo=_SOURCE_TIMEZONE)
    assert parsed.rows[0].open_price == Decimal("3500.10")
    assert parsed.rows[0].volume == Decimal("10")
    assert parsed.mapping["mapping_version"] == PARQUET_MAPPING_VERSION
    assert parsed.mapping["profile"] == {
        "name": PARQUET_PROFILE_NAME,
        "version": PARQUET_PROFILE_VERSION,
    }
    assert parsed.mapping["source_timezone_name"] == "Asia/Shanghai"


def test_parquet_adapter_rejects_a_declared_timezone_that_only_matches_by_offset(
    tmp_path: Path,
) -> None:
    path = tmp_path / "timezone-name-mismatch.parquet"
    _write_parquet(
        path,
        type_overrides={
            "event_time": pa.timestamp("us", tz="Etc/GMT-8"),
            "available_at": pa.timestamp("us", tz="Etc/GMT-8"),
        },
    )

    with pytest.raises(OhlcvImportError) as error:
        _parse(path, source_timezone_name="Asia/Shanghai")

    assert error.value.code == "SOURCE_TIMEZONE_DECLARATION_MISMATCH"


@pytest.mark.parametrize(
    ("type_overrides", "extra_field", "expected_code"),
    [
        (
            {"open_price": pa.float64()},
            None,
            "BINARY_FLOAT_UNSUPPORTED",
        ),
        (
            None,
            ("unrecognized_vendor_field", pa.string(), ["x", "y"]),
            "UNSUPPORTED_FIELD",
        ),
        (
            {"available_at": pa.timestamp("us")},
            None,
            "MISSING_TIMEZONE_OFFSET",
        ),
        (
            {"event_time": pa.timestamp("us")},
            None,
            "MISSING_TIMEZONE_OFFSET",
        ),
    ],
)
def test_parquet_adapter_rejects_unsafe_or_noncanonical_schema(
    tmp_path: Path,
    type_overrides: dict[str, pa.DataType] | None,
    extra_field: tuple[str, pa.DataType, list[object | None]] | None,
    expected_code: str,
) -> None:
    path = tmp_path / "invalid-schema.parquet"
    _write_parquet(path, type_overrides=type_overrides, extra_field=extra_field)

    with pytest.raises(OhlcvImportError) as error:
        _parse(path)

    assert error.value.code == expected_code


def test_parquet_adapter_requires_its_explicit_versioned_profile_metadata(tmp_path: Path) -> None:
    path = tmp_path / "missing-profile.parquet"
    _write_parquet(path, metadata={})

    with pytest.raises(OhlcvImportError) as error:
        _parse(path)

    assert error.value.code == "UNSUPPORTED_PARQUET_PROFILE"


def test_parquet_adapter_rejects_duplicate_source_record_ids_as_a_quarantine(
    tmp_path: Path,
) -> None:
    path = tmp_path / "duplicate-source-record-id.parquet"
    _write_parquet(path, value_overrides={"source_record_id": ["same", "same"]})

    with pytest.raises(OhlcvImportError) as error:
        _parse(path)

    assert error.value.code == "DUPLICATE_SOURCE_RECORD_ID"
    assert error.value.quarantined


def test_parquet_adapter_rejects_a_file_before_decoding_more_rows_than_allowed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "too-many-rows.parquet"
    _write_parquet(path)

    with pytest.raises(OhlcvImportError) as error:
        _parse(path, adapter=_adapter(max_rows=1))

    assert error.value.code == "ROW_LIMIT_EXCEEDED"


def test_parquet_adapter_rejects_a_compressed_input_with_an_excessive_decoded_size(
    tmp_path: Path,
) -> None:
    path = tmp_path / "decoded-size-limit.parquet"
    _write_parquet(path)

    with pytest.raises(OhlcvImportError) as error:
        _parse(path, adapter=_adapter(max_uncompressed_bytes=1))

    assert error.value.code == "PARQUET_UNCOMPRESSED_LIMIT_EXCEEDED"


def test_parquet_adapter_contains_out_of_range_physical_date32_values(tmp_path: Path) -> None:
    path = tmp_path / "date32-overflow.parquet"
    _write_parquet(
        path,
        value_overrides={"trading_day": [2_147_483_647, 2_147_483_647]},
    )

    with pytest.raises(OhlcvImportError) as error:
        _parse(path)

    assert error.value.code == "INVALID_PARQUET"


def test_parquet_adapter_rejects_a_schema_decimal_domain_larger_than_canonical_storage(
    tmp_path: Path,
) -> None:
    path = tmp_path / "large-decimal-domain.parquet"
    _write_parquet(type_overrides={"turnover": pa.decimal128(38, 18)}, path=path)

    with pytest.raises(OhlcvImportError) as error:
        _parse(path)

    assert error.value.code == "DECIMAL_SCHEMA_OUT_OF_RANGE"

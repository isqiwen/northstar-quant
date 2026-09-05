"""Strict decoder for one SHFE official daily-data response.

The network client owns retrieval.  This module only converts the exact response
bytes into the same provider-neutral rows used by the canonical write path.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

from northstar_quant.data.catalog.models import (
    SHFE_DAILY_ACQUISITION_USE,
    SHFE_DAILY_REDISTRIBUTION_POLICY,
)
from northstar_quant.data.ingestion.imports import (
    AVAILABLE_AT_POLICY,
    UNIT_POLICY,
    OhlcvImportError,
    ParsedOhlcvRows,
    RawOhlcvRow,
    SourcePayload,
)

SHFE_DAILY_PROFILE_NAME: Final = "shfe_official_daily_json"
SHFE_DAILY_PROFILE_VERSION: Final = "1.0.0"
SHFE_DAILY_MAPPING_VERSION: Final = f"{SHFE_DAILY_PROFILE_NAME}/{SHFE_DAILY_PROFILE_VERSION}"
SHFE_DAILY_ENDPOINT: Final = "https://www.shfe.com.cn/data/dailydata/kx/pm{trading_day}.dat"
SHFE_DAILY_TERMS_URL: Final = "https://www.shfe.com.cn/eng/reports/"


class ShfeDailyJsonAdapter:
    """Decode one exact official daily-data response for one named contract.

    ``available_at`` is deliberately supplied by the invoking command as a
    source-attested publication time. It is never inferred from request time.
    """

    media_type = "application/json"
    mapping_version = SHFE_DAILY_MAPPING_VERSION
    job_kind = "SHFE_DAILY_IMPORT_V1"
    input_kind = "PROVIDER_RESPONSE"
    retention_policy = "TRANSIENT"
    acquisition_use = SHFE_DAILY_ACQUISITION_USE
    redistribution_policy = SHFE_DAILY_REDISTRIBUTION_POLICY

    def __init__(
        self,
        *,
        trading_day: date,
        source_symbol: str,
        available_at: datetime,
        max_bytes: int,
        max_rows: int,
    ) -> None:
        self._trading_day = trading_day
        self._source_symbol = source_symbol.strip().upper()
        self._available_at = available_at
        self._max_bytes = max_bytes
        self._max_rows = max_rows

    def load(self, file_path: Path) -> SourcePayload:
        """Read staged response bytes once without retaining their file path."""

        try:
            content = file_path.read_bytes()
        except OSError as error:
            raise OhlcvImportError(
                "INPUT_UNREADABLE", "the provider response could not be staged"
            ) from error
        if not content:
            raise OhlcvImportError(
                "EMPTY_PROVIDER_RESPONSE", "the provider returned an empty response"
            )
        if len(content) > self._max_bytes:
            raise OhlcvImportError(
                "PROVIDER_RESPONSE_TOO_LARGE",
                "the provider response exceeds the configured byte limit",
            )
        return SourcePayload(
            content=content,
            content_hash=hashlib.sha256(content).hexdigest(),
            byte_count=len(content),
        )

    def parse(self, payload: SourcePayload, *, source_timezone_name: str) -> ParsedOhlcvRows:
        """Decode only the documented instrument array and reject loose shapes."""

        if source_timezone_name != "Asia/Shanghai":
            raise OhlcvImportError(
                "SOURCE_TIMEZONE_MISMATCH",
                "the SHFE daily adapter requires the Asia/Shanghai source timezone",
            )

        try:
            decoded = json.loads(
                payload.content.decode("utf-8"),
                parse_float=Decimal,
                object_pairs_hook=_strict_json_object,
            )
        except _DuplicateJsonKeyError as error:
            raise OhlcvImportError(
                "DUPLICATE_PROVIDER_FIELD",
                "the provider response contains an ambiguous duplicate JSON field",
            ) from error
        except (RecursionError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            raise OhlcvImportError(
                "INVALID_PROVIDER_RESPONSE", "the provider response is not valid UTF-8 JSON"
            ) from error
        if not isinstance(decoded, dict):
            raise OhlcvImportError(
                "INVALID_PROVIDER_RESPONSE", "the provider response must be a JSON object"
            )
        instruments = decoded.get("o_curinstrument")
        if not isinstance(instruments, list):
            raise OhlcvImportError(
                "UNSUPPORTED_PROVIDER_SCHEMA", "the provider response lacks o_curinstrument"
            )
        if len(instruments) > self._max_rows:
            raise OhlcvImportError(
                "ROW_LIMIT_EXCEEDED", "the provider response exceeds the row limit"
            )
        matches = [
            row
            for row in instruments
            if isinstance(row, dict) and self._symbol(row) == self._source_symbol
        ]
        if len(matches) != 1:
            raise OhlcvImportError(
                "PROVIDER_SYMBOL_NOT_UNIQUE",
                "the official response must contain exactly one requested contract row",
                rows_read=len(instruments),
                rows_rejected=len(instruments),
            )
        return ParsedOhlcvRows(
            rows=(self._parse_row(matches[0]),),
            mapping=self.mapping_metadata(source_timezone_name="Asia/Shanghai"),
        )

    def mapping_metadata(self, *, source_timezone_name: str) -> dict[str, object]:
        return {
            "profile": {"name": SHFE_DAILY_PROFILE_NAME, "version": SHFE_DAILY_PROFILE_VERSION},
            "mapping_version": SHFE_DAILY_MAPPING_VERSION,
            "provider": {
                "name": "SHFE_OFFICIAL_DAILY",
                "endpoint_template": "shfe_daily_data_v1",
                "terms_url": SHFE_DAILY_TERMS_URL,
                "license_review": "operator-must-verify-current-terms-before-production-use",
            },
            "requested_trading_day": self._trading_day.isoformat(),
            "source_symbol": self._source_symbol,
            "source_publication_at": self._available_at.isoformat(),
            "available_at_policy": AVAILABLE_AT_POLICY,
            "unit_policy": UNIT_POLICY,
            "source_timezone_name": source_timezone_name,
            "columns": {
                "DELIVERYMONTH": "catalog.contract.contract_code",
                "OPENPRICE": "canonical_bar.open_price",
                "HIGHESTPRICE": "canonical_bar.high_price",
                "LOWESTPRICE": "canonical_bar.low_price",
                "CLOSEPRICE": "canonical_bar.close_price",
                "VOLUME": "canonical_bar.volume",
                "TURNOVER": "canonical_bar.turnover",
                "OPENINTEREST": "canonical_bar.open_interest",
            },
        }

    def request_fingerprint_metadata(self, *, source_timezone_name: str) -> dict[str, object]:
        """Bind source facts that do not necessarily change response bytes."""

        return {
            "requested_trading_day": self._trading_day.isoformat(),
            "source_symbol": self._source_symbol,
            "source_publication_at": self._available_at.astimezone(UTC).isoformat(),
            "source_timezone_name": source_timezone_name,
        }

    def _parse_row(self, row: dict[str, object]) -> RawOhlcvRow:
        symbol = self._symbol(row)
        assert symbol is not None
        return RawOhlcvRow(
            source_row_number=2,
            symbol=symbol,
            interval="1d",
            event_time=None,
            trading_day=self._trading_day,
            available_at=self._available_at,
            source_record_id=f"SHFE:{self._trading_day:%Y%m%d}:{symbol}",
            price_currency="CNY",
            volume_unit="LOT",
            open_interest_unit="LOT",
            turnover_currency="CNY",
            turnover_multiplier=Decimal("1"),
            open_price=self._decimal(row, "OPENPRICE"),
            high_price=self._decimal(row, "HIGHESTPRICE"),
            low_price=self._decimal(row, "LOWESTPRICE"),
            close_price=self._decimal(row, "CLOSEPRICE"),
            volume=self._decimal(row, "VOLUME"),
            turnover=self._optional_decimal(row, "TURNOVER"),
            open_interest=self._optional_decimal(row, "OPENINTEREST"),
        )

    @staticmethod
    def _symbol(row: dict[str, object]) -> str | None:
        value = row.get("DELIVERYMONTH")
        return value.strip().upper() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _decimal(row: dict[str, object], field_name: str) -> Decimal:
        value = row.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise OhlcvImportError(
                "MISSING_PROVIDER_FIELD", f"the provider response lacks {field_name}"
            )
        return ShfeDailyJsonAdapter._coerce_decimal(value, field_name)

    @staticmethod
    def _optional_decimal(row: dict[str, object], field_name: str) -> Decimal | None:
        value = row.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return ShfeDailyJsonAdapter._coerce_decimal(value, field_name)

    @staticmethod
    def _coerce_decimal(value: object, field_name: str) -> Decimal:
        if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
            raise OhlcvImportError(
                "INVALID_PROVIDER_FIELD",
                f"the provider field {field_name} must be an exact decimal",
            )
        try:
            parsed = Decimal(str(value))
        except InvalidOperation as error:
            raise OhlcvImportError(
                "INVALID_PROVIDER_FIELD", f"the provider field {field_name} is not an exact decimal"
            ) from error
        if not parsed.is_finite():
            raise OhlcvImportError(
                "INVALID_PROVIDER_FIELD", f"the provider field {field_name} must be finite"
            )
        return parsed


class _DuplicateJsonKeyError(ValueError):
    """Raised while parsing an otherwise valid JSON object with duplicate keys."""


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject ambiguous JSON objects instead of silently taking the last key."""

    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(key)
        result[key] = value
    return result

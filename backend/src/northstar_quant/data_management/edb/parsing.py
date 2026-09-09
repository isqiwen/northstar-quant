"""Map retained EDB bytes while preserving the original payload identity."""

import csv
import hashlib
import io
import re
from datetime import UTC, datetime, timedelta

from ..ingestion.imports import ParsedOhlcvRows, SourcePayload
from ..research import _ResearchCsv, _timestamp
from .acquisition import request_parameters


class EdbCsv(_ResearchCsv):
    mapping_version = "edb-minute-csv/1"

    def parse(self, payload: SourcePayload, *, source_timezone_name: str) -> ParsedOhlcvRows:
        try:
            return self._parse_edb(payload, source_timezone_name=source_timezone_name)
        except csv.Error as error:
            raise ValueError("EDB response contains malformed CSV") from error

    def _parse_edb(self, payload: SourcePayload, *, source_timezone_name: str) -> ParsedOhlcvRows:
        request_parameters(self.spec)
        reader = csv.DictReader(io.StringIO(payload.content.decode("utf-8")), strict=True)
        expected = [
            "datetime_nano",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "open_oi",
            "close_oi",
        ]
        if reader.fieldnames != expected:
            raise ValueError("EDB response must contain the documented eight CSV columns")
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(
            [
                "event_time",
                "available_at",
                "source_record_id",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        )
        previous = -1
        for index, row in enumerate(reader):
            if (
                index >= 120
                or set(row) != set(expected)
                or any(
                    not isinstance(value, str) or not value or len(value) > 128
                    for value in row.values()
                )
            ):
                raise ValueError("EDB response has oversized, missing or extra fields/rows")
            stamp = row["datetime_nano"]
            if re.fullmatch(r"[0-9]{18,19}", stamp) is None:
                raise ValueError("EDB datetime_nano must be an integer Unix nanosecond timestamp")
            nano = int(stamp)
            if nano % 60_000_000_000 or nano <= previous:
                raise ValueError("EDB bars must be minute aligned, ordered and unique")
            previous = nano
            start = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=nano // 1_000_000_000)
            for name in ("volume", "open_oi", "close_oi"):
                if re.fullmatch(r"(?:0|[1-9][0-9]{0,15})", row[name]) is None:
                    raise ValueError(f"EDB {name} must be nonnegative integer lots")
            writer.writerow(
                [
                    _timestamp(start),
                    _timestamp(start + timedelta(minutes=1)),
                    f"EDB:{self.spec.symbol}:{stamp}",
                    *[row[name] for name in ("open", "high", "low", "close", "volume")],
                ]
            )
        converted = output.getvalue().encode()
        parsed = _ResearchCsv(self.spec, payload=payload, archive=self.archive).parse(
            SourcePayload(converted, hashlib.sha256(converted).hexdigest(), len(converted)),
            source_timezone_name=source_timezone_name,
        )
        # The import service loads self.payload (unaltered EDB bytes), not this temporary CSV.
        return ParsedOhlcvRows(
            parsed.rows, self.mapping_metadata(source_timezone_name=source_timezone_name)
        )

"""Parse retained Tushare JSON using Decimal and explicit bar-end clock semantics."""

import csv
import hashlib
import io
import json
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from ..ingestion.imports import ParsedOhlcvRows, SourcePayload
from ..research import _ResearchCsv, _timestamp
from .request import FIELDS, parameters


class TushareMinutes(_ResearchCsv):
    mapping_version = "tushare-minute-end/1"
    input_kind = "PROVIDER_RESPONSE"
    media_type = "application/json"

    def parse(self, payload: SourcePayload, *, source_timezone_name: str) -> ParsedOhlcvRows:
        request = parameters(self.spec)
        document = json.loads(payload.content, parse_float=Decimal)
        if (
            not isinstance(document, dict)
            or type(document.get("code")) is not int
            or document["code"] != 0
        ):
            raise ValueError("Tushare source must contain a successful result")
        data = document.get("data")
        if not isinstance(data, dict) or data.get("fields") != FIELDS.split(","):
            raise ValueError("Tushare source has unexpected fields")
        items = data.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= 120:
            raise ValueError("Tushare source must contain 1..120 bars for the bounded segment")
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
        for item in items:
            if not isinstance(item, list) or len(item) != 9:
                raise ValueError("Tushare row has missing or extra fields")
            row = dict(zip(FIELDS.split(","), item, strict=True))
            if row["ts_code"] != request["ts_code"] or not isinstance(row["trade_time"], str):
                raise ValueError("Tushare row contract or time is invalid")
            try:
                end = datetime.strptime(row["trade_time"], "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=ZoneInfo("Asia/Shanghai")
                )
            except ValueError:
                raise ValueError(
                    "Tushare trade_time must be a local second-resolution timestamp"
                ) from None
            if end.second or end.microsecond:
                raise ValueError("Tushare trade_time must be minute aligned")
            numbers = {}
            for name in ("open", "high", "low", "close", "vol", "amount", "oi"):
                value = row[name]
                if type(value) not in {int, Decimal}:
                    raise ValueError("Tushare financial fields must be JSON numbers")
                value = Decimal(value)
                if not value.is_finite() or value < 0 or value >= Decimal("1e16"):
                    raise ValueError("Tushare financial value is invalid or oversized")
                if name in {"vol", "oi"} and value != value.to_integral_value():
                    raise ValueError("Tushare quantities must be integer lots")
                numbers[name] = format(value, "f")
            writer.writerow(
                [
                    _timestamp(end - timedelta(minutes=1)),
                    _timestamp(end),
                    f"TUSHARE:{row['ts_code']}:{row['trade_time']}",
                    *[numbers[name] for name in ("open", "high", "low", "close", "vol")],
                ]
            )
        converted = output.getvalue().encode()
        parsed = _ResearchCsv(self.spec, payload=payload, archive=self.archive).parse(
            SourcePayload(converted, hashlib.sha256(converted).hexdigest(), len(converted)),
            source_timezone_name=source_timezone_name,
        )
        return ParsedOhlcvRows(
            parsed.rows, self.mapping_metadata(source_timezone_name=source_timezone_name)
        )

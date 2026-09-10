"""Supplier shape and time failures must never become accepted market observations."""

import json

import pytest

from northstar_quant.data_management.tushare.acquisition import DownloadError, decode
from northstar_quant.data_management.tushare.quality import Empty, InvalidResponse, normalize


def market_response(dataset):
    row = {
        "ts_code": "RB2610.SHF",
        "trade_date": "20260901",
        "open": "3100.1",
        "high": "3100.2",
        "low": "3100.0",
        "close": "3100.1",
        "vol": 2,
        "amount": None,
        "oi": None,
    }
    if dataset.endswith("min"):
        row.pop("trade_date")
        row["trade_time"] = "2026-09-01 21:01:00"
    if dataset in ("week", "month"):
        row["trade_date"] = "20260904" if dataset == "week" else "20260930"
        row.update(end_date="20260901", freq=dataset)
    if dataset == "index":
        row["ts_code"] = "RB.NH"
        row.pop("oi")  # The index API does not declare open interest.
    job = {
        "dataset": dataset,
        "parameters": {"ts_code": row["ts_code"]},
        "start_at": "2026-09-01",
        "end_at": "2026-09-01",
    }
    return row, job


def encoded(row):
    return json.dumps(
        {"code": 0, "data": {"fields": list(row), "items": [list(row.values())]}}
    ).encode()


@pytest.mark.parametrize(
    "dataset",
    ["1min", "5min", "15min", "30min", "60min", "daily", "week", "month", "adjusted", "index"],
)
def test_missing_ohlcv_cannot_skip_market_validation(dataset):
    row, job = market_response(dataset)
    accepted, evidence = normalize(encoded(row), job)
    assert accepted[0]["close"] == "3100.1"
    assert accepted[0]["low"] == "3100"
    assert accepted[0]["vol"] == "2"
    assert accepted[0].get("oi") is None
    assert evidence["availability_basis"] == "FINAL_REVISED"
    for field in ("open", "high", "low", "close", "vol"):
        incomplete = {key: value for key, value in row.items() if key != field}
        with pytest.raises(InvalidResponse, match=field):
            normalize(encoded(incomplete), job)


@pytest.mark.parametrize(
    "field,value",
    [
        ("open", None),
        ("close", "NaN"),
        ("close", "Infinity"),
        ("low", "-1"),
        ("low", "3101"),
        ("vol", None),
        ("vol", -1),
        ("vol", True),
        ("amount", "-0.1"),
        ("oi", "invalid"),
    ],
)
def test_invalid_market_values_are_rejected_without_filling_or_leaking_values(field, value):
    row, job = market_response("1min")
    row[field] = value
    with pytest.raises(InvalidResponse):
        normalize(encoded(row), job)


@pytest.mark.parametrize(
    "value",
    [
        "2026-09-01",
        "2026-09-01 untrusted-value",
        "2026-09-01 25:00:00",
        "2026-09-01 21:01:01",
        "2026-09-01 21:01:00+08:00",
        "2026-09-01 21:01:00 trailing-value",
    ],
)
def test_minute_label_must_be_complete_without_inventing_a_trading_day(value):
    row, job = market_response("1min")
    row["trade_time"] = value
    with pytest.raises(InvalidResponse) as caught:
        normalize(encoded(row), job)
    assert value not in str(caught.value)


def test_weekly_label_and_actual_cutoff_are_kept_distinct():
    row, job = market_response("week")
    accepted, _ = normalize(encoded(row), job)
    assert accepted[0]["trade_date"] == "20260904"
    assert accepted[0]["end_date"] == "20260901"
    row["freq"] = "month"
    with pytest.raises(InvalidResponse, match="频率"):
        normalize(encoded(row), job)
    row["freq"] = "week"
    row["end_date"] = "20260905"
    with pytest.raises(InvalidResponse, match="截至日期"):
        normalize(encoded(row), job)


def test_metadata_response_does_not_require_market_fields():
    row = {"ts_code": "RB2610.SHF", "exchange": "SHFE", "fut_code": "RB"}
    rows, _ = normalize(
        encoded(row), {"dataset": "contracts", "parameters": {}, "start_at": "", "end_at": ""}
    )
    assert rows == [row]


@pytest.mark.parametrize("fields", [[], ["ts_code", "trade_time"]])
def test_empty_response_waits_for_coverage_instead_of_becoming_a_schema_failure(fields):
    _, job = market_response("1min")
    content = json.dumps({"code": 0, "data": {"fields": fields, "items": []}}).encode()
    with pytest.raises(Empty):
        normalize(content, job)


def test_rows_without_field_names_remain_invalid():
    for items in ([[]], [["RB2610.SHF"]], None):
        content = json.dumps({"code": 0, "data": {"fields": [], "items": items}}).encode()
        with pytest.raises(DownloadError):
            decode(content)


def test_equivalent_number_spellings_deduplicate_without_decimal_context_rounding():
    from decimal import localcontext

    row, job = market_response("daily")
    row.update(
        open="100",
        high="100." + "0" * 50000,
        low="1e2",
        close="100.0",
        vol="2.000",
        amount="123456789012345.123456789012",
    )
    other = row | {"open": 100, "high": "1E2", "vol": 2}
    content = json.dumps(
        {
            "code": 0,
            "data": {"fields": list(row), "items": [list(row.values()), list(other.values())]},
        }
    ).encode()
    with localcontext() as context:
        context.prec = 5
        accepted, evidence = normalize(content, job)
    single, single_evidence = normalize(encoded(other), job)
    assert accepted == single
    assert evidence["content_hash"] == single_evidence["content_hash"]
    assert evidence["duplicate_rows"] == 1
    assert accepted[0]["amount_cny"] == "1234567890123451234.56789012"
    assert {accepted[0][name] for name in ("open", "high", "low", "close")} == {"100"}
    assert accepted[0]["oi"] is None


@pytest.mark.parametrize("value", ["1e-13", "1e1000000", "1e26"])
def test_unrepresentable_price_is_rejected_before_formatting_or_rounding(value):
    row, job = market_response("daily")
    row.update({name: value for name in ("open", "high", "low", "close")})
    for content in (encoded(row), encoded(row).replace(json.dumps(value).encode(), value.encode())):
        with pytest.raises(InvalidResponse, match="精确范围"):
            normalize(content, job)


def test_amount_unit_overflow_cannot_be_silently_rounded():
    row, job = market_response("daily")
    row["amount"] = "99999999999999999999999999"
    with pytest.raises(InvalidResponse, match="精确范围"):
        normalize(encoded(row), job)

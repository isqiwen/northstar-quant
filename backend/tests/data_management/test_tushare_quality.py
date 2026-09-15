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
    "field,value",
    [
        ("settle", "0"),
        ("settle", "NaN"),
        ("settle", "-1"),
        ("trading_fee_rate", "Infinity"),
        ("trading_fee_rate", "-0.01"),
        ("offset_today_fee", True),
        ("offset_today_fee", "unknown"),
        ("long_margin_rate", "1e-13"),
        ("short_margin_rate", "1e30"),
    ],
)
def test_invalid_settlement_numbers_cannot_pass_response_quality(field, value):
    from northstar_quant.data_management.tushare.catalog import BY_KEY

    row = dict.fromkeys(BY_KEY["settlement"].fields)
    row.update(ts_code="RB2610.SHF", trade_date="20260901", settle="3100.125")
    row[field] = value
    job = {
        "dataset": "settlement",
        "parameters": {"ts_code": "RB2610.SHF"},
        "start_at": "2026-09-01",
        "end_at": "2026-09-01",
    }
    with pytest.raises(InvalidResponse) as caught:
        normalize(encoded(row), job)
    assert field in caught.value.report["issues"][0]["fields"]


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
    with pytest.raises(InvalidResponse, match="请求窗口"):
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


def test_row_diagnostics_are_bounded_and_do_not_echo_supplier_values():
    row, job = market_response("1min")
    rows = []
    for i in range(103):
        value = dict(row, vol="private-invalid-value")
        rows.append(list(value.values()))
    content = json.dumps({"code": 0, "data": {"fields": list(row), "items": rows}}).encode()
    with pytest.raises(InvalidResponse) as caught:
        normalize(content, job)
    report = caught.value.report
    assert report["issue_count"] == 103
    assert report["truncated"] and len(report["issues"]) == 100
    assert [i["row_number"] for i in report["issues"]] == list(range(1, 101))
    assert report["issues"][0]["fields"] == ["vol"]
    assert "private-invalid-value" not in json.dumps(report)


def test_conflicting_rows_report_both_raw_positions():
    row, job = market_response("1min")
    changed = dict(row, vol=3)
    content = json.dumps(
        {
            "code": 0,
            "data": {"fields": list(row), "items": [list(row.values()), list(changed.values())]},
        }
    ).encode()
    with pytest.raises(InvalidResponse) as caught:
        normalize(content, job)
    issue = caught.value.report["issues"][0]
    assert issue["row_number"] == 2
    assert issue["related_row_number"] == 1


@pytest.mark.parametrize("dataset", ["week", "month"])
def test_historical_period_recalculation_preserves_both_dates(dataset):
    import io

    import pyarrow as pa
    import pyarrow.parquet as pq

    from northstar_quant.data_management.exploration.parquet import ResponseScan

    row, job = market_response(dataset)
    row.update(trade_date="19950428", end_date="20260623")
    job.update(start_at="1995-04-17", end_at="1995-04-30")
    accepted, _ = normalize(encoded(row), job)
    assert accepted[0]["end_date"] == "20260623"
    raw = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(accepted), raw)
    scan = ResponseScan(
        raw.getvalue(),
        row_count=1,
        dataset=dataset,
        scope=row["ts_code"],
        start="1995-04-17",
        end="1995-04-30",
    )
    assert list(scan.rows())[0]["trade_date"] == "19950428"
    job.update(start_at="2026-06-01", end_at="2026-06-30")
    with pytest.raises(InvalidResponse, match="请求窗口"):
        normalize(encoded(row), job)


@pytest.mark.parametrize("dataset", ["daily", "adjusted", "week", "month"])
def test_zero_volume_reference_close_keeps_null_prices_and_exact_status(dataset):
    from northstar_quant.data_management.tushare.publication import response_table

    row, job = market_response(dataset)
    row.update(open=None, high=None, low=None, vol=0, amount=0, oi=60)
    accepted, evidence = normalize(encoded(row), job)
    assert accepted[0]["observation_status"] == "ZERO_VOLUME"
    assert all(accepted[0][f] is None for f in ("open", "high", "low"))
    assert accepted[0]["close"] == row["close"]
    assert evidence["zero_volume_rows"] == 1
    table = response_table(accepted, dataset)
    assert table["open"].to_pylist() == [None]
    assert table["observation_status"].to_pylist() == ["ZERO_VOLUME"]


@pytest.mark.parametrize(
    "change",
    [
        {"vol": 1},
        {"vol": None},
        {"amount": 1},
        {"open": "3100.1"},
        {"close": 0},
        {"close": None},
        {"close": "invalid"},
    ],
)
def test_missing_daily_prices_are_not_excused_by_inconsistent_zero_volume(change):
    row, job = market_response("daily")
    row.update(open=None, high=None, low=None, vol=0, amount=0)
    row.update(change)
    with pytest.raises(InvalidResponse):
        normalize(encoded(row), job)


def test_mixed_response_rejects_all_rows_with_original_failure_positions():
    row, job = market_response("daily")
    job["end_at"] = "2026-09-02"
    bad = dict(row, trade_date="20260902", close=None)
    data = {
        "code": 0,
        "data": {"fields": list(row), "items": [list(row.values()), list(bad.values())]},
    }
    with pytest.raises(InvalidResponse) as failure:
        normalize(json.dumps(data).encode(), job)
    assert failure.value.report["issue_count"] == 1
    assert failure.value.report["issues"][0]["row_number"] == 2


def test_settlement_only_is_not_an_invented_close():
    row, job = market_response("daily")
    row.update(open=None, high=None, low=None, close=None, vol=0, amount=0, settle="2480")
    accepted, _ = normalize(encoded(row), job)
    assert accepted[0]["observation_status"] == "SETTLEMENT_ONLY"
    assert accepted[0]["close"] is None
    assert accepted[0]["settle"] == "2480"
    row["vol"] = 600
    with pytest.raises(InvalidResponse):
        normalize(encoded(row), job)


def test_pooled_provider_session_keeps_request_credentials_scoped_and_bounds():
    import httpx2

    from northstar_quant.data_management.tushare import acquisition

    received = []

    def handle(request):
        received.append(json.loads(request.content))
        return httpx2.Response(200, json={"code": 0, "data": {"fields": [], "items": []}})

    with acquisition.open_client(transport=httpx2.MockTransport(handle)) as client:
        for token in ("a" * 40, "b" * 40):
            acquisition.fetch("fut_daily", {"ts_code": "RB2610.SHF"}, token, client=client)
            assert not client.is_closed
        assert [r["token"] for r in received] == ["a" * 40, "b" * 40]
    assert client.is_closed


def test_minute_zero_prices_preserve_supplier_values_in_returned_field_order():
    row, job = market_response("1min")
    row.update(open=0, high=0, low=0, close=0, vol=14, amount=661080)
    row["untrusted"] = "must not enter diagnostics"
    # Provider fields need not match request order.
    reordered = dict(reversed(list(row.items())))
    with pytest.raises(InvalidResponse) as caught:
        normalize(encoded(reordered), job)
    issue = caught.value.report["issues"][0]
    assert issue["row_number"] == 1
    assert issue["observed"]["trade_time"] == row["trade_time"]
    assert issue["observed"]["open"] == "0"
    assert issue["observed"]["vol"] == "14"
    assert issue["observed"]["amount"] == "661080"
    assert "untrusted" not in issue["observed"]
    assert "源响应价格" in issue["reason"]


def test_permission_code_does_not_depend_on_provider_message():
    with pytest.raises(DownloadError, match="权限不足") as caught:
        decode(json.dumps({"code": 2002, "msg": None}).encode())
    assert not caught.value.retry

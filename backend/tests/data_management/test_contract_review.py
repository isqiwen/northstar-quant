"""Whole-contract review must not turn partial downloads into admission/deletion."""

import json
from uuid import uuid4

import pytest
from sqlalchemy import text

from northstar_quant.data_management.tushare import acquisition, jobs
from northstar_quant.data_management.tushare.catalog import DATASETS
from northstar_quant.data_management.tushare.contract_review import review
from tests.data_management import test_tushare

automatic = test_tushare.automatic
pending = test_tushare.pending
response = test_tushare.response


def lifetime(library, start="20260901", end="20260902"):
    with library._engine.begin() as c:
        c.execute(
            text("UPDATE data_sync_contracts SET details=CAST(:details AS jsonb)"),
            {
                "details": json.dumps(
                    dict(
                        name="螺纹钢2610",
                        list_date=start,
                        delist_date=end,
                        last_ddate=end,
                        d_mode_desc="实物交割",
                    )
                )
            },
        )


def test_single_valid_dataset_does_not_admit_whole_contract(automatic, monkeypatch):
    lifetime(automatic, end="20260901")
    pending(automatic)
    monkeypatch.setattr(acquisition, "fetch", lambda *a: response())
    assert jobs.process_next(automatic)["status"] == "VALIDATED"
    result = review(automatic._engine, "RB2610.SHF")
    assert result["admitted"] is False
    assert result["required_end"] == "2026-09-01"
    assert {r["dataset"] for r in result["requirements"]} == {d.key for d in DATASETS}
    found = {r["dataset"]: r for r in result["requirements"]}
    assert found["daily"]["status"] == "VERIFIED"
    assert found["daily"]["evidence"]["actual_records"] == 1
    assert found["settlement"]["status"] == "COLLECTING"
    assert found["warehouse"]["scopes"] == ["SHFE:RB"]
    assert found["mapping"]["status"] == "RELATED"


def test_listing_before_old_history_floor_is_not_silently_shortened(automatic):
    lifetime(automatic, start="20080101")
    result = review(automatic._engine, "RB2610.SHF")
    assert result["listing_date"] == "2008-01-01"
    assert result["required_end"] is None
    assert result["status"] == "VERIFICATION_PENDING"
    assert any("交易日历" in reason for reason in result["reasons"])


def test_active_contract_is_excluded_even_when_some_days_are_complete(automatic):
    lifetime(automatic, end="20991031")
    result = review(automatic._engine, "RB2610.SHF")
    assert result["required_end"] is None
    assert result["status"] == "NOT_ELIGIBLE"
    assert result["admitted"] is False


def test_bad_response_marks_contract_invalid_but_network_failure_does_not(automatic, monkeypatch):
    lifetime(automatic)
    pending(automatic)
    data = json.loads(response())
    data["data"]["items"][0][5] = None
    monkeypatch.setattr(acquisition, "fetch", lambda *a: json.dumps(data).encode())
    assert jobs.process_next(automatic)["status"] == "BLOCKED"
    result = review(automatic._engine, "RB2610.SHF")
    assert result["status"] == "INVALID"
    assert result["admitted"] is False
    assert result["reasons"][0].startswith("日线：RB2610.SHF")
    assert "2026-09-01" in result["reasons"][0]
    assert "异常 1 行" in result["reasons"][0]
    from northstar_quant.data_management.tushare.store import job

    with automatic._engine.begin() as c:
        request = c.scalar(text("SELECT request_id FROM data_sync_jobs LIMIT 1"))
        generation = c.scalar(text("SELECT generation FROM data_sync_jobs LIMIT 1"))
        c.execute(
            text("""INSERT INTO data_contract_source_releases(source_id,scope,reason)
            VALUES(:g,'RB2610.SHF','已按拒绝合约清理')"""),
            {"g": generation},
        )
    detail = job(automatic._engine, request)
    assert detail["attempts_detail"][0]["source_release_reason"] == "已按拒绝合约清理"
    assert detail["attempts_detail"][0]["quality"]["issues"][0]["observed"]
    assert detail["reprocess_source"] is None
    with automatic._engine.begin() as c:
        # A subsequent network attempt has no quality evidence. Old failure does not
        # become evidence of the latest supplier response, nor proof of a missing day.
        generation = uuid4()
        request = c.scalar(text("SELECT request_id FROM data_sync_jobs LIMIT 1"))
        c.execute(
            text("""INSERT INTO data_sync_attempts(generation,request_id,outcome,error)
            VALUES(:g,:request,'BLOCKED','network')"""),
            dict(g=generation, request=request),
        )
        c.execute(text("UPDATE data_sync_jobs SET generation=:g"), {"g": generation})
    assert review(automatic._engine, "RB2610.SHF")["status"] == "VERIFICATION_PENDING"


def test_continuous_series_cannot_impersonate_real_contract(automatic):
    with automatic._engine.begin() as c:
        c.execute(text("UPDATE data_sync_contracts SET kind='2'"))
    with pytest.raises(ValueError, match="真实合约"):
        review(automatic._engine, "RB2610.SHF")


def test_contract_review_uses_owned_authenticated_protocol(automatic):
    from northstar_quant.apps.data_hub.application import create_app
    from tests.apps.browser import ProtocolClient

    lifetime(automatic)
    with ProtocolClient(
        create_app(automatic._engine, automatic), base_url="http://127.0.0.1"
    ) as client:
        csrf = test_tushare.login_response(client).json()["csrf"]
        client.headers.update({"x-northstar-csrf": csrf, "origin": "http://127.0.0.1"})
        result = client.post("/api/sync/contracts/review", json={"scope": "RB2610.SHF"})
        assert result.status_code == 200, result.text
        assert result.json()["admitted"] is False
        assert result.json()["listing_date"] == "2026-09-01"
        assert len(result.json()["requirements"]) == len(DATASETS)
        assert (
            client.post("/api/sync/contracts/review", json={"scope": "missing"}).status_code == 404
        )


def test_unowned_receipt_cannot_satisfy_contract_review(automatic, monkeypatch):
    lifetime(automatic, end="20260901")
    pending(automatic)
    monkeypatch.setattr(acquisition, "fetch", lambda *a: response())
    assert jobs.process_next(automatic)["status"] == "VALIDATED"
    with automatic._engine.begin() as c:
        c.execute(text("DELETE FROM data_contract_requests"))
    found = {r["dataset"]: r for r in review(automatic._engine, "RB2610.SHF")["requirements"]}
    assert found["daily"]["status"] == "COLLECTING"
    assert not found["daily"]["received"]


def test_calendar_revision_invalidates_previously_valid_daily_rows(automatic, monkeypatch):
    lifetime(automatic)
    with automatic._engine.begin() as c:
        c.execute(text("UPDATE data_sync_calendar SET is_open=false WHERE cal_date='2026-09-02'"))
    pending(automatic, end="2026-09-02")
    monkeypatch.setattr(acquisition, "fetch", lambda *a: response())
    assert jobs.process_next(automatic)["status"] == "VALIDATED"
    with automatic._engine.begin() as c:
        c.execute(text("UPDATE data_sync_calendar SET is_open=true WHERE cal_date='2026-09-02'"))
    result = review(automatic._engine, "RB2610.SHF")
    found = {r["dataset"]: r for r in result["requirements"]}
    assert found["daily"]["status"] == "INVALID"
    assert found["daily"]["evidence"]["missing_dates"] == ["2026-09-02"]
    assert not result["admitted"]


@pytest.mark.parametrize("dataset,label", [("week", "20260904"), ("month", "20260930")])
def test_native_period_including_partial_last_period_is_verified(
    automatic, monkeypatch, dataset, label
):
    from northstar_quant.data_management.tushare import planning

    lifetime(automatic)
    with automatic._engine.begin() as c:
        planning.enqueue(
            c,
            dataset,
            "RB2610.SHF",
            dict(ts_code="RB2610.SHF", freq=dataset, start_date="20260901", end_date="20260902"),
            "2026-09-01",
            "2026-09-02",
        )
    data = json.loads(response())
    data["data"]["fields"].extend(["freq", "end_date"])
    data["data"]["items"][0][1] = label
    data["data"]["items"][0].extend([dataset, "20260902"])
    monkeypatch.setattr(acquisition, "fetch", lambda *a: json.dumps(data).encode())
    assert jobs.process_next(automatic)["status"] == "VALIDATED"
    found = {r["dataset"]: r for r in review(automatic._engine, "RB2610.SHF")["requirements"]}
    assert found[dataset]["status"] == "VERIFIED", found[dataset]
    assert found[dataset]["evidence"]["expected_records"] == 1
    # A new calendar day inside the period is a required observation, even though
    # the supplier window and period label still appear complete.
    with automatic._engine.begin() as c:
        c.execute(
            text(
                "UPDATE data_sync_contracts SET details=details || "
                '\'{"delist_date":"20260903","last_ddate":"20260903"}\'::jsonb'
            )
        )
        c.execute(text("INSERT INTO data_sync_calendar VALUES('SHFE','2026-09-03',true)"))
    from datetime import date

    from northstar_quant.data_management.contract_data.record_review import verify

    with automatic._engine.connect() as c:
        result = verify(c, "RB2610.SHF", "SHFE", dataset, date(2026, 9, 1), date(2026, 9, 3))
    assert result["status"] == "INVALID"
    assert "未覆盖应有最后交易日" in result["reason"]


def test_last_month_request_includes_period_label_after_contract_expiry(automatic, monkeypatch):
    from northstar_quant.data_management.tushare import planning

    lifetime(automatic)
    with automatic._engine.begin() as c:
        c.execute(text("UPDATE data_sync_contracts SET planned_revision=0"))
    test_tushare.calendar_for_planning(automatic)
    planning.plan(automatic._engine)
    with automatic._engine.begin() as c:
        c.execute(text("UPDATE data_contract_collections SET discovery_complete=true"))
    planning.plan(automatic._engine)
    with automatic._engine.begin() as c:
        c.execute(
            text("UPDATE data_sync_jobs SET next_at=now()+interval '1 day' WHERE dataset<>'month'")
        )
    data = json.loads(response())
    data["data"]["fields"].extend(["freq", "end_date"])
    data["data"]["items"][0][1] = "20260930"
    data["data"]["items"][0].extend(["month", "20260930"])

    def fetch(api, parameters, token):
        assert parameters["end_date"] == "20260930"
        return json.dumps(data).encode()

    monkeypatch.setattr(acquisition, "fetch", fetch)
    assert jobs.process_next(automatic)["status"] == "VALIDATED"
    result = review(automatic._engine, "RB2610.SHF")
    found = {r["dataset"]: r for r in result["requirements"]}
    assert found["month"]["status"] == "VERIFIED"
    assert result["last_trade_date"] == "2026-09-02"


def test_missing_fixed_file_is_not_a_data_rejection_or_cleanup_permission(automatic, monkeypatch):
    lifetime(automatic, end="20260901")
    pending(automatic)
    monkeypatch.setattr(acquisition, "fetch", lambda *a: response())
    assert jobs.process_next(automatic)["status"] == "VALIDATED"
    with automatic._engine.connect() as c:
        digest = c.scalar(text("SELECT parquet_hash FROM data_sync_receipts LIMIT 1"))
    for path in automatic._files.root.rglob(digest):
        path.unlink()
    result = review(automatic._engine, "RB2610.SHF")
    found = {r["dataset"]: r for r in result["requirements"]}
    assert found["daily"]["status"] == "UNKNOWN"
    assert result["status"] == "VERIFICATION_PENDING"


@pytest.mark.parametrize(
    "dataset,field,value,valid",
    [
        ("limits", "m_ratio", 10, True),
        ("limits", "m_ratio", None, True),
        ("limits", "m_ratio", -1, False),
        ("limits", "up_limit", None, False),
        ("limits", "down_limit", None, False),
        ("settlement", "long_margin_rate", 0.1, True),
        ("settlement", "long_margin_rate", None, False),
        ("settlement", "short_margin_rate", None, False),
    ],
)
def test_complete_daily_terms_require_actual_values(
    automatic, monkeypatch, dataset, field, value, valid
):
    from northstar_quant.data_management.tushare import planning

    lifetime(automatic, end="20260901")
    with automatic._engine.begin() as c:
        planning.enqueue(
            c,
            dataset,
            "RB2610.SHF",
            dict(ts_code="RB2610.SHF", start_date="20260901", end_date="20260901"),
            "2026-09-01",
            "2026-09-01",
        )
    row = dict(ts_code="RB2610.SHF", trade_date="20260901")
    if dataset == "limits":
        row.update(up_limit=3500, down_limit=2800, m_ratio=10)
    else:
        from northstar_quant.data_management.tushare.catalog import BY_KEY

        row.update({field: None for field in BY_KEY[dataset].fields if field not in row})
        row.update(
            exchange="SHFE",
            settle=3100,
            trading_fee_rate=0.1,
            trading_fee=0,
            long_margin_rate=0.1,
            short_margin_rate=0.1,
        )
    row[field] = value
    payload = json.dumps(dict(code=0, data=dict(fields=list(row), items=[list(row.values())])))
    monkeypatch.setattr(acquisition, "fetch", lambda *a: payload.encode())
    assert jobs.process_next(automatic)["status"] == "VALIDATED"
    result = review(automatic._engine, "RB2610.SHF")
    found = {r["dataset"]: r for r in result["requirements"]}
    assert found[dataset]["status"] == ("VERIFIED" if valid else "INVALID")
    if dataset == "limits" and field == "m_ratio" and value is None:
        assert found[dataset]["evidence"]["optional_unknown_fields"]["m_ratio"]["count"] == 1
        from northstar_quant.data_management.contract_data.record_review import fixed_rows

        with automatic._engine.connect() as c:
            item = (
                c.execute(
                    text("""SELECT r.*,j.dataset,j.scope,j.parameters,j.start_at,j.end_at
                FROM data_sync_receipts r JOIN data_sync_jobs j ON r.receipt_id=j.receipt_id""")
                )
                .mappings()
                .one()
            )
            assert next(fixed_rows(automatic._files, dict(item)))["m_ratio"] is None
    assert not result["admitted"]


def historical_minutes(automatic, monkeypatch, case):
    # Synthetic historical evidence exercises review independently of product discovery.
    from northstar_quant.data_management.tushare import planning

    lifetime(automatic, start="20120118", end="20120119")
    with automatic._engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO data_sync_calendar VALUES('SHFE','2012-01-18',true),"
                "('SHFE','2012-01-19',true)"
            )
        )
    pending(automatic, start="2012-01-18", end="2012-01-19")
    payload = json.loads(response())
    first = payload["data"]["items"][0]
    first[1] = "20120118"
    payload["data"]["items"].append([*first[:1], "20120119", *first[2:]])
    monkeypatch.setattr(acquisition, "fetch", lambda *a: json.dumps(payload).encode())
    assert jobs.process_next(automatic)["status"] == "VALIDATED"
    with automatic._engine.begin() as c:
        c.execute(text("UPDATE data_sync_settings SET next_request_at=now(),api_next_at='{}'"))
        planning.enqueue(
            c,
            "30min",
            "RB2610.SHF",
            dict(
                ts_code="RB2610.SHF",
                freq="30min",
                start_date="2012-01-18 00:00:00",
                end_date="2012-01-19 23:59:59",
            ),
            "2012-01-18",
            "2012-01-19",
        )
    payload["data"]["fields"][1] = "trade_time"
    payload["data"]["items"][0][1] = "2012-01-18 15:00:00"
    payload["data"]["items"][1][1] = "2012-01-19 15:00:00"
    if case == "missing":
        payload["data"]["items"].pop()
    elif case == "different":
        payload["data"]["items"][0][6] = 3
    elif case == "zero":
        zero = list(payload["data"]["items"][0])
        zero[1], zero[6] = "2012-01-18 14:30:00", 0
        payload["data"]["items"].append(zero)
    assert jobs.process_next(automatic)["status"] == "VALIDATED"
    if case in {"overlap", "conflict"}:
        with automatic._engine.begin() as c:
            c.execute(text("UPDATE data_sync_settings SET next_request_at=now(),api_next_at='{}'"))
            planning.enqueue(
                c,
                "30min",
                "RB2610.SHF",
                dict(
                    ts_code="RB2610.SHF",
                    freq="30min",
                    start_date="2012-01-19 00:00:00",
                    end_date="2012-01-19 23:59:59",
                ),
                "2012-01-19",
                "2012-01-19",
            )
        payload["data"]["items"] = [payload["data"]["items"][1]]
        if case == "conflict":
            payload["data"]["items"][0][6] = 4
        assert jobs.process_next(automatic)["status"] == "VALIDATED"
    return review(automatic._engine, "RB2610.SHF")


@pytest.mark.parametrize("case", ["missing", "present", "different", "zero", "overlap", "conflict"])
def test_minute_diagnostics_distinguish_observation_from_admission(automatic, monkeypatch, case):
    result = historical_minutes(automatic, monkeypatch, case)
    item = next(r for r in result["requirements"] if r["dataset"] == "30min")
    expected = {
        "missing": ("INVALID", "RECORD_GAP"),
        "present": ("RECEIVED", "RULE_UNCONFIRMED"),
        "different": ("RECEIVED", "RECORD_DIFFERENCE"),
        "zero": ("RECEIVED", "RULE_UNCONFIRMED"),
        "overlap": ("RECEIVED", "RULE_UNCONFIRMED"),
        "conflict": ("UNKNOWN", "RECORD_CONFLICT"),
    }
    assert (item["status"], item["diagnosis"]["category"]) == expected[case]
    assert item["diagnosis"]["source_missing_confirmed"] is False
    evidence = item["evidence"]
    assert evidence["supplier_policy"]["timestamp_convention"] == "BAR_END"
    assert evidence["supplier_policy"]["available_at"] is None
    assert evidence["supplier_policy"]["historical_applicability_verified"] is False
    assert evidence["grid_verified"] is False
    assert evidence["missing_dates"] == (["2012-01-19"] if case == "missing" else [])
    if case == "different":
        assert evidence["volume_differences"] == [
            dict(date="2012-01-18", minute_volume="3", daily_volume="2")
        ]
    else:
        assert evidence["volume_differences"] == []
    assert evidence["zero_volume_records"] == (1 if case == "zero" else 0)
    assert evidence["duplicate_records"] == (1 if case in {"overlap", "conflict"} else 0)
    assert not result["admitted"]


def test_unverified_night_mapping_does_not_assign_minutes_to_natural_day(automatic, monkeypatch):
    historical_minutes(automatic, monkeypatch, "different")
    from datetime import date

    from northstar_quant.data_management.contract_data import minute_review

    with automatic._engine.connect() as c:
        result = minute_review.inspect(
            c, "RB2610.SHF", "SHFE", "30min", date(2012, 1, 18), date(2013, 7, 5)
        )
    assert result["status"] == "RECEIVED"
    assert result["evidence"]["classification"] == "RULE_UNCONFIRMED"
    assert result["evidence"]["volume_comparison"] == "NOT_COMPARED_TRADING_DAY_UNVERIFIED"
    assert result["evidence"]["volume_differences"] == []
    assert result["evidence"]["observed_records"] == 2
    assert result["evidence"]["observed_label_clocks"] == [dict(clock="15:00:00", records=2)]


def test_night_rule_unknown_does_not_hide_fixed_record_conflicts(automatic, monkeypatch):
    historical_minutes(automatic, monkeypatch, "conflict")
    from datetime import date

    from northstar_quant.data_management.contract_data import minute_review

    with automatic._engine.connect() as c:
        result = minute_review.inspect(
            c, "RB2610.SHF", "SHFE", "30min", date(2012, 1, 18), date(2013, 7, 5)
        )
    assert result["status"] == "UNKNOWN"
    assert result["evidence"]["classification"] == "RECORD_CONFLICT"
    assert result["evidence"]["conflicting_records"] == 1
    assert result["evidence"]["grid_verified"] is False

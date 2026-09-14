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
            {"details": json.dumps(dict(name="螺纹钢2610", list_date=start, delist_date=end))},
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
    assert found["daily"]["status"] == "RECEIVED"
    assert found["settlement"]["status"] == "COLLECTING"
    assert found["warehouse"]["scopes"] == ["SHFE:RB"]
    assert found["mapping"]["status"] == "UNKNOWN"


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

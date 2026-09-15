"""Core acceptance, auxiliary gaps and immutable available capabilities stay separate."""

import json
from uuid import UUID

import pytest
from sqlalchemy import text

from northstar_quant.data_management.contract_data import processing
from northstar_quant.data_management.contract_data.catalog import require_receipt
from northstar_quant.data_management.contract_data.snapshots import publish
from northstar_quant.data_management.exploration.discovery import available, open_published
from northstar_quant.data_management.tushare import acquisition, contract_review, jobs, planning
from northstar_quant.data_management.tushare.contract_review import review
from tests.data_management import test_tushare

automatic = test_tushare.automatic
SCOPE = "RB2610.SHF"


def collect(library, monkeypatch, dataset, rows, *, status="VALIDATED"):
    scope = "SHFE" if dataset in {"contracts", "calendar"} else SCOPE
    params = (
        dict(exchange="SHFE", fut_type="1")
        if dataset == "contracts"
        else dict(exchange="SHFE", start_date="20260901", end_date="20260902")
        if dataset == "calendar"
        else dict(
            ts_code=SCOPE,
            start_date="20260901",
            end_date="20260904" if dataset == "week" else "20260902",
        )
    )
    if dataset == "week":
        params["freq"] = "week"
    with library._engine.begin() as c:
        c.execute(text("UPDATE data_sync_settings SET api_next_at='{}',next_request_at=now()"))
        c.execute(
            text(
                "UPDATE data_sync_contracts "
                "SET planned_revision=(SELECT revision FROM data_sync_settings)"
            )
        )
        identity = planning.enqueue(
            c,
            dataset,
            scope,
            params,
            "" if dataset == "contracts" else "2026-09-01",
            "" if dataset == "contracts" else "2026-09-04" if dataset == "week" else "2026-09-02",
        )
        planning._link(c, SCOPE, identity)
    fields = list(rows[0])
    raw = json.dumps(
        dict(code=0, data=dict(fields=fields, items=[[r.get(f) for f in fields] for r in rows]))
    ).encode()
    monkeypatch.setattr(acquisition, "fetch", lambda *a: raw)
    result = jobs.process_next(library)
    assert result["status"] == status, result


def core(library, monkeypatch):
    with library._engine.begin() as c:
        c.execute(
            text(
                "UPDATE data_contract_collections SET start_date='2026-09-01',end_date='2026-09-02'"
            )
        )
    collect(
        library,
        monkeypatch,
        "contracts",
        [
            dict(
                ts_code=SCOPE,
                exchange="SHFE",
                fut_code="RB",
                name="螺纹钢2610",
                list_date="20260901",
                delist_date="20260902",
                last_ddate="20260903",
                d_mode_desc="实物交割",
                per_unit="10",
            )
        ],
    )
    collect(
        library,
        monkeypatch,
        "calendar",
        [dict(exchange="SHFE", cal_date=day, is_open=1) for day in ("20260901", "20260902")],
    )
    collect(
        library,
        monkeypatch,
        "daily",
        [
            dict(ts_code=SCOPE, trade_date=day, open=3100, high=3102, low=3099, close=3101, vol=2)
            for day in ("20260901", "20260902")
        ],
    )
    # Entire optional source columns may be absent without discarding valid core rows.
    collect(
        library,
        monkeypatch,
        "settlement",
        [
            dict(
                ts_code=SCOPE,
                trade_date=day,
                settle=3100,
                long_margin_rate="0.1",
                short_margin_rate="0.1",
                trading_fee=3,
            )
            for day in ("20260901", "20260902")
        ],
    )
    collect(
        library,
        monkeypatch,
        "limits",
        [
            dict(ts_code=SCOPE, trade_date=day, up_limit=3500, down_limit=2800, m_ratio=None)
            for day in ("20260901", "20260902")
        ],
    )


@pytest.fixture
def verified_minutes(monkeypatch):
    """Isolate policy composition; this is not supplier minute-grid acceptance."""
    original = contract_review._requirement

    def reviewed(c, dataset, *args):
        result = original(c, dataset, *args)
        if dataset.key.endswith("min"):
            result.update(status="VERIFIED", reason="Test-only independent minute verifier")
        return result

    monkeypatch.setattr(contract_review, "_requirement", reviewed)


def test_core_can_publish_and_browse_with_auxiliary_gaps(automatic, monkeypatch, verified_minutes):
    core(automatic, monkeypatch)
    result = review(automatic._engine, SCOPE)
    assert result["admitted"] is True
    assert result["reasons"] == []
    assert result["quality"]["status"] == "GAPS"
    assert result["completeness"]["core_verified"] == result["completeness"]["core_total"] == 10
    assert not result["completeness"]["fully_verified"]
    assert result["completeness"]["optional_unknown_fields"]["limits"]["m_ratio"]["count"] == 2
    artifact = publish(automatic._engine, SCOPE)
    assert {r["dataset"] for r in artifact["manifest"]["inputs"]} == {
        "contracts",
        "calendar",
        "daily",
        "settlement",
        "limits",
    }
    found = available(automatic._engine, "daily", "", "", "", 0)
    assert found["total"] == 1
    view = open_published(automatic._engine, UUID(found["rows"][0]["receipt_id"]))
    assert view
    assert not available(automatic._engine, "1min", "", "", "", 0)["rows"]


def test_auxiliary_error_does_not_reject_core_or_enter_snapshot(
    automatic, monkeypatch, verified_minutes
):
    core(automatic, monkeypatch)
    collect(
        automatic,
        monkeypatch,
        "week",
        [
            dict(
                ts_code=SCOPE,
                trade_date="20260904",
                end_date="20260904",
                freq="week",
                open=0,
                high=3100,
                low=3090,
                close=3100,
                vol=3,
            )
        ],
        status="BLOCKED",
    )
    result = review(automatic._engine, SCOPE)
    assert result["admitted"]
    assert any("周线" in r for r in result["quality"]["warnings"])
    artifact = publish(automatic._engine, SCOPE)
    assert "week" not in {r["dataset"] for r in artifact["manifest"]["inputs"]}


def test_core_missing_or_unpinned_reference_still_prevents_publication(automatic, monkeypatch):
    core(automatic, monkeypatch)
    with automatic._engine.begin() as c:
        c.execute(
            text(
                "DELETE FROM data_contract_requests WHERE request_id IN "
                "(SELECT request_id FROM data_sync_jobs WHERE dataset='settlement')"
            )
        )
    assert not review(automatic._engine, SCOPE)["admitted"]
    with pytest.raises(ValueError, match="禁止发布"):
        publish(automatic._engine, SCOPE)


def test_later_auxiliary_completion_creates_new_snapshot_without_changing_old(
    automatic, monkeypatch, verified_minutes
):
    core(automatic, monkeypatch)
    before = publish(automatic._engine, SCOPE)
    collect(
        automatic,
        monkeypatch,
        "week",
        [
            dict(
                ts_code=SCOPE,
                trade_date="20260904",
                end_date="20260904",
                freq="week",
                open=3100,
                high=3102,
                low=3099,
                close=3101,
                vol=4,
            )
        ],
    )
    with automatic._engine.begin() as c:
        c.execute(
            text("UPDATE data_contract_collections SET updated_at=now()-interval '2 minutes'")
        )
    assert processing.process_next(automatic._engine) == SCOPE
    with automatic._engine.connect() as c:
        assert c.scalar(text("SELECT count(*) FROM data_contract_publications")) == 2
        assert c.scalar(text("SELECT status FROM data_contract_collections")) == "PUBLISHED"
        row = c.execute(text("SELECT receipt_id FROM data_sync_jobs WHERE dataset='week'")).one()
        require_receipt(c, row.receipt_id)
    assert "week" not in {r["dataset"] for r in before["manifest"]["inputs"]}


@pytest.mark.parametrize("dataset", ["1min", "5min", "15min", "30min", "60min"])
def test_each_unverified_minute_interval_blocks_publication(
    automatic, monkeypatch, verified_minutes, dataset
):
    core(automatic, monkeypatch)
    original = contract_review._requirement

    def missing(c, definition, *args):
        result = original(c, definition, *args)
        if definition.key == dataset:
            result.update(status="UNKNOWN", reason="分钟完整性未确认")
        return result

    monkeypatch.setattr(contract_review, "_requirement", missing)
    result = review(automatic._engine, SCOPE)
    assert not result["admitted"]
    assert result["completeness"]["core_verified"] == 9
    with pytest.raises(ValueError, match="禁止发布"):
        publish(automatic._engine, SCOPE)


def test_actual_missing_minutes_block_even_when_daily_core_is_complete(automatic, monkeypatch):
    core(automatic, monkeypatch)
    result = review(automatic._engine, SCOPE)
    assert not result["admitted"]
    assert result["completeness"]["core_verified"] == 5
    assert result["completeness"]["core_total"] == 10
    with pytest.raises(ValueError, match="禁止发布"):
        publish(automatic._engine, SCOPE)


def test_auxiliary_rejection_never_authorizes_raw_cleanup(automatic, monkeypatch):
    from northstar_quant.data_management.contract_data.retention import release_rejected

    core(automatic, monkeypatch)
    collect(
        automatic,
        monkeypatch,
        "week",
        [
            dict(
                ts_code=SCOPE,
                trade_date="20260904",
                end_date="20260904",
                freq="week",
                open=0,
                high=3102,
                low=3099,
                close=3101,
                vol=2,
            )
        ],
        status="BLOCKED",
    )
    with automatic._engine.begin() as c:
        source = (
            c.execute(
                text("""SELECT s.* FROM data_sources s
            JOIN data_sync_attempts a ON a.generation=s.source_id
            JOIN data_sync_jobs j USING(request_id) WHERE j.dataset='week'""")
            )
            .mappings()
            .one()
        )
        c.execute(text("UPDATE data_contract_collections SET status='REJECTED'"))
    assert release_rejected(automatic._engine, automatic._files) == 0
    assert automatic._files.inspect(source["content_hash"], source["byte_count"]) == "AVAILABLE"


def test_unpinned_calendar_projection_cannot_count_as_verified(automatic, monkeypatch):
    core(automatic, monkeypatch)
    with automatic._engine.begin() as c:
        c.execute(text("UPDATE data_sync_calendar SET is_open=false WHERE cal_date='2026-09-01'"))
    result = review(automatic._engine, SCOPE)
    calendar = next(r for r in result["requirements"] if r["dataset"] == "calendar")
    assert calendar["status"] == "UNKNOWN"
    assert not result["admitted"]

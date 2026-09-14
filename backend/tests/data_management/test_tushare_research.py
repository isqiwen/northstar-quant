"""Fixed official-period receipts feed durable research without another download."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from northstar_quant.apps.data_hub import create_app
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.data_management.processing import process_attempt
from northstar_quant.data_management.publications import PublishedDatasets
from northstar_quant.data_management.research_input import ImportSpec
from northstar_quant.data_management.tushare import acquisition, jobs, planning
from northstar_quant.data_management.tushare.research import submit
from tests.apps.browser import ProtocolClient, _browser_session
from tests.data_management import test_tushare

automatic = test_tushare.automatic


def _published(library, monkeypatch, *, opening_record=False):
    with library._engine.begin() as connection:
        planning.enqueue(
            connection,
            "15min",
            "RB2610.SHF",
            {
                "ts_code": "RB2610.SHF",
                "freq": "15min",
                "start_date": "2026-09-01 09:00:00",
                "end_date": "2026-09-01 09:30:00",
            },
            "2026-09-01",
            "2026-09-01",
        )
    labels = ["09:15:00", "09:30:00"]
    if opening_record:
        labels.insert(0, "09:00:00")
    raw = json.dumps(
        {
            "code": 0,
            "data": {
                "fields": ["ts_code", "trade_time", "open", "high", "low", "close", "vol"],
                "items": [
                    ["RB2610.SHF", f"2026-09-01 {label}", "3100", "3101", "3099", "3100", 10]
                    for label in labels
                ],
            },
        }
    ).encode()
    monkeypatch.setattr(acquisition, "fetch", lambda *args: raw)
    processed = jobs.process_next(library)
    assert processed["status"] == "VALIDATED", processed
    monkeypatch.setattr(
        acquisition, "fetch", lambda *args: pytest.fail("research must not download")
    )
    with library._engine.connect() as connection:
        return connection.scalar(text("SELECT receipt_id FROM data_sync_receipts"))


def _spec():
    return ImportSpec(
        exchange="SHFE",
        symbol="RB2610",
        product="RB",
        timezone="Asia/Shanghai",
        currency="CNY",
        quantity_unit="TON",
        price_tick=Decimal(1),
        multiplier=Decimal(10),
        trading_day=date(2026, 9, 1),
        session_kind="DAY",
        session_open=datetime(2026, 9, 1, 1, tzinfo=UTC),
        session_close=datetime(2026, 9, 1, 1, 30, tzinfo=UTC),
        source_name="TUSHARE",
        source_reference="Synthetic provider response for integration",
        availability_basis="FINAL_REVISED",
        availability_note="Final values at assumed completion; not first-availability evidence.",
        interval="15m",
    )


def test_fixed_receipt_survives_api_restart_and_preserves_offline_provenance(
    automatic, monkeypatch
):
    library = automatic
    receipt = _published(library, monkeypatch)
    body = {
        "receipt_id": str(receipt),
        "request_id": str(uuid4()),
        "specification": _spec().to_mapping(),
        "label_convention": "BAR_END",
        "interpretation_reference": "Synthetic fixture explicitly labels complete bar ends",
    }
    with ProtocolClient(
        create_app(library._engine, library), base_url="http://127.0.0.1"
    ) as client:
        assert client.post("/api/research-inputs", json=body).status_code == 401
        _browser_session(client)
        response = client.post("/api/research-inputs", json=body)
        assert response.status_code == 202, response.text
        admitted = response.json()
        assert admitted["status"] == "PENDING"
        assert client.post("/api/research-inputs", json=body).json() == admitted
    reopened = DataLibrary(library._engine, library._files)
    outcome = process_attempt(reopened, UUID(admitted["attempt_id"]))
    assert outcome["status"] == "PUBLISHED", outcome
    dataset = reopened.load_dataset(UUID(str(outcome["snapshot_id"])))
    assert dataset.interval_seconds == 900
    assert len(dataset.bars) == 2
    assert dataset.bars[0].event_time == _spec().session_open
    assert dataset.bars[-1].completed_at == _spec().session_close
    assert dataset.details.import_specs[0].availability_basis == "FINAL_REVISED"
    assert str(receipt) in dataset.details.import_specs[0].source_reference
    assert (
        PublishedDatasets(reopened.publications.root).load_dataset(dataset.snapshot_id) == dataset
    )
    with library._engine.connect() as connection:
        derived = (
            connection.execute(text("SELECT * FROM data_sources WHERE input_kind='CONVERTED_CSV'"))
            .mappings()
            .one()
        )
        upstream = (
            connection.execute(
                text("SELECT * FROM data_sources WHERE source_id=:id"),
                {"id": derived["upstream_source_id"]},
            )
            .mappings()
            .one()
        )
    assert upstream["input_kind"] == "TUSHARE_RESPONSE"
    assert derived["upstream_evidence_hash"] == upstream["evidence_hash"]


@pytest.mark.parametrize("problem", ["frequency", "opening", "availability"])
def test_receipt_admission_rejects_unproven_meaning(automatic, monkeypatch, problem):
    receipt = _published(automatic, monkeypatch, opening_record=problem == "opening")
    spec = _spec()
    if problem == "frequency":
        spec = replace(spec, interval="5m")
    if problem == "availability":
        spec = replace(spec, availability_basis="SOURCE_DECLARED")
    with pytest.raises(ValueError):
        submit(
            automatic,
            receipt_id=receipt,
            request_id=uuid4(),
            specification=spec,
            label_convention="BAR_END",
            interpretation_reference="Synthetic integration label",
        )
    assert automatic.list_attempts() == []


def test_zero_volume_observation_cannot_become_executable_bar():
    from northstar_quant.data_management.tushare.research import _session_rows

    with pytest.raises(ValueError, match="not an executable"):
        _session_rows(
            [{"ts_code": "RB2610.SHF", "observation_status": "ZERO_VOLUME"}],
            _spec(),
            "RB2610.SHF",
            "BAR_END",
        )

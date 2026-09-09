"""Durable historical sync, receipt recovery, source clocks and credential isolation."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from sqlalchemy import Engine

from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.data_management.processing import process_attempt
from northstar_quant.data_management.research import ImportSpec
from northstar_quant.data_management.tushare import jobs
from northstar_quant.data_management.tushare.acquisition import fetch
from northstar_quant.data_management.tushare.request import FIELDS
from tests.data_management.test_library import _study

TOKEN = "test-only-tushare-token-never-persist"


def spec() -> ImportSpec:
    data = _study()[1]
    day = datetime.now(UTC).date() - timedelta(days=1)
    data.update(
        exchange="SHFE",
        symbol="RB2610",
        source_name="TUSHARE",
        trading_day=str(day),
        session_open=f"{day}T01:00:00Z",
        session_close=f"{day}T01:02:00Z",
        availability_basis="FINAL_REVISED",
    )
    return ImportSpec.from_mapping(data)


def document(source: ImportSpec) -> dict:
    # Provider example is reverse ordered; no silent assumption of ascending input.
    day = source.trading_day
    return {
        "code": 0,
        "msg": None,
        "data": {
            "fields": FIELDS.split(","),
            "items": [
                [
                    f"{source.symbol}.SHF",
                    f"{day} 09:02:00",
                    3100.1,
                    3100.1,
                    3101.1,
                    3099.1,
                    12.0,
                    100.0,
                    102.0,
                ],
                [
                    f"{source.symbol}.SHF",
                    f"{day} 09:01:00",
                    3100.1,
                    3100.1,
                    3101.1,
                    3099.1,
                    10.0,
                    100.0,
                    101.0,
                ],
            ],
        },
    }


def bind(monkeypatch, content: bytes):
    requests = []

    def network(request):
        requests.append(json.loads(request.content))
        assert str(request.url) == "https://api.tushare.pro"
        return httpx.Response(200, content=content)

    monkeypatch.setenv("NORTHSTAR_TUSHARE_TOKEN", TOKEN)
    monkeypatch.setattr(
        jobs,
        "fetch",
        lambda spec, token: fetch(spec, token, transport=httpx.MockTransport(network)),
    )
    return requests


def test_durable_sync_publishes_original_bytes_and_bar_end_clock(
    postgres_engine: Engine, clean_database: None, tmp_path: Path, monkeypatch
):
    source = replace(spec(), price_tick=Decimal("0.1"))
    content = json.dumps(document(source)).encode()
    requests = bind(monkeypatch, content)
    request_id = uuid4()
    first = jobs.submit(postgres_engine, source, request_id)
    assert first["status"] == "PENDING" and requests == []
    assert jobs.submit(postgres_engine, source, request_id) == first
    with pytest.raises(ValueError, match="different"):
        jobs.submit(postgres_engine, replace(source, multiplier=source.multiplier * 2), request_id)
    # New owner object models an unrelated submission process having exited.
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path))
    received = jobs.process_next(library)
    assert received["status"] == "RECEIVED"
    assert requests[0]["token"] == TOKEN and requests[0]["api_name"] == "ft_mins"
    assert requests[0]["params"]["start_date"].endswith("09:01:00")
    assert jobs.process_next(library) is None
    assert len(requests) == 1 and TOKEN not in json.dumps(jobs.get(postgres_engine, request_id))
    result = process_attempt(library)
    assert result["status"] == "PUBLISHED", result
    dataset = library.publications.load_dataset(UUID(result["snapshot_id"]))
    assert len(dataset.bars) == 2
    assert dataset.bars[0].close == Decimal("3100.1")
    assert dataset.bars[0].event_time == source.session_open
    assert dataset.bars[0].available_at == source.session_open + timedelta(minutes=1)
    assert dataset.details.sources[0].content_hash == hashlib.sha256(content).hexdigest()
    assert dataset.details.sources[0].input_kind == "TUSHARE_JSON"
    assert TOKEN not in json.dumps(result)


@pytest.mark.parametrize(
    "fault", ["gap", "duplicate", "contract", "fractional_volume", "null", "row_limit"]
)
def test_bad_source_retained_without_publication(
    postgres_engine, clean_database, tmp_path, monkeypatch, fault
):
    source = spec()
    value = document(source)
    rows = value["data"]["items"]
    if fault == "gap":
        rows.pop()
    elif fault == "duplicate":
        rows[-1] = rows[0]
    elif fault == "contract":
        rows[0][0] = "CU2610.SHF"
    elif fault == "fractional_volume":
        rows[0][6] = 0.5
    elif fault == "null":
        rows[0][2] = None
    else:
        value["data"]["items"] = rows * 61
    content = json.dumps(value).encode()
    bind(monkeypatch, content)
    jobs.submit(postgres_engine, source, uuid4())
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path))
    assert jobs.process_next(library)["status"] == "RECEIVED"
    assert process_attempt(library)["status"] == "FAILED"
    assert library.list_datasets() == ()
    assert library.list_sources()[0]["content_hash"] == hashlib.sha256(content).hexdigest()


@pytest.mark.parametrize(
    "fault", ["permission", "http", "large", "reflected", "missing_token", "network"]
)
def test_failed_download_does_not_leak_token_or_retry(
    postgres_engine, clean_database, tmp_path, monkeypatch, fault
):
    calls = []

    def network(request):
        calls.append(request)
        if fault == "network":
            raise httpx.ReadTimeout("upstream " + TOKEN)
        return httpx.Response(
            302 if fault == "http" else 200,
            content=(
                b"x" * 5242881
                if fault == "large"
                else json.dumps(
                    {"code": 2002, "msg": TOKEN if fault == "reflected" else "denied"}
                ).encode()
            ),
        )

    monkeypatch.setenv("NORTHSTAR_TUSHARE_TOKEN", "" if fault == "missing_token" else TOKEN)
    monkeypatch.setattr(
        jobs, "fetch", lambda s, t: fetch(s, t, transport=httpx.MockTransport(network))
    )
    request_id = uuid4()
    jobs.submit(postgres_engine, spec(), request_id)
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path))
    result = jobs.process_next(library)
    assert result["status"] == "FAILED" and TOKEN not in json.dumps(result)
    assert jobs.process_next(library) is None
    assert jobs.submit(postgres_engine, spec(), request_id) == result
    assert len(calls) == (0 if fault == "missing_token" else 1)
    assert library.list_sources() == []


@pytest.mark.parametrize("after_receipt", [False, True])
def test_interrupted_download_recovery_never_blindly_redownloads(
    postgres_engine, clean_database, tmp_path, monkeypatch, after_receipt
):
    bind(monkeypatch, json.dumps(document(spec())).encode())
    request_id = uuid4()
    jobs.submit(postgres_engine, spec(), request_id)
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path))
    with monkeypatch.context() as context:
        if after_receipt:
            original = library.submit

            def crash(*args, **kwargs):
                original(*args, **kwargs)
                raise KeyboardInterrupt()

            context.setattr(library, "submit", crash)
        else:

            def crash(*args, **kwargs):
                raise KeyboardInterrupt()

            context.setattr(jobs, "fetch", crash)
        with pytest.raises(KeyboardInterrupt):
            jobs.process_next(library)
    assert jobs.get(postgres_engine, request_id)["status"] == "RUNNING"

    def no_download(*args):
        raise AssertionError("must not repeat network access")

    monkeypatch.setattr(jobs, "fetch", no_download)
    assert jobs.process_next(DataLibrary(postgres_engine, SourceFiles(tmp_path))) is None
    result = jobs.get(postgres_engine, request_id)
    assert result["status"] == ("RECEIVED" if after_receipt else "FAILED")


def test_active_download_excludes_second_worker_and_keeps_submission_available(
    postgres_engine, clean_database, tmp_path, monkeypatch
):
    started, release = Event(), Event()
    content = json.dumps(document(spec())).encode()

    def hold(*args):
        started.set()
        assert release.wait(10)
        return content

    monkeypatch.setattr(jobs, "fetch", hold)
    jobs.submit(postgres_engine, spec(), uuid4())
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path))
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(jobs.process_next, library)
        try:
            assert started.wait(5)
            assert jobs.process_next(library) is None
            assert jobs.submit(postgres_engine, spec(), uuid4())["status"] == "PENDING"
        finally:
            release.set()
        assert worker.result()["status"] == "RECEIVED"


def test_sync_api_requires_csrf_and_returns_durable_job(postgres_engine, clean_database, tmp_path):
    from northstar_quant.apps.data_hub import create_app
    from tests.apps.browser import ProtocolClient, _browser_session

    library = DataLibrary(postgres_engine, SourceFiles(tmp_path))
    request_id = str(uuid4())
    payload = {"request_id": request_id, "spec": spec().to_mapping()}
    with ProtocolClient(
        create_app(postgres_engine, library), base_url="http://127.0.0.1"
    ) as client:
        assert client.post("/api/sync/tushare", json=payload).status_code == 403
        assert jobs.recent(postgres_engine) == []
        _browser_session(client)
        response = client.post("/api/sync/tushare", json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "PENDING"
        assert client.get(f"/api/sync/{request_id}").json() == response.json()
        assert client.get("/api/sync").json() == [response.json()]
        assert client.post("/api/sync/tushare", json=payload).json() == response.json()

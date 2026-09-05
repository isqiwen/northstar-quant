"""Synthetic saved callbacks to durable, reusable data; no external connection evidence."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from test_broker_streams import Clock, logins, prepare, start
from test_live import OPEN as LIVE_OPEN
from test_live import tick

from northstar_quant.broker import streams as stream_module
from northstar_quant.broker.streams import BrokerStreams, read_stream_archive
from northstar_quant.data.library import AdmissionRejected
from northstar_quant.research import ResearchConfig, run_research
from northstar_quant.runs import RunStore
from northstar_quant.sessions import SessionStore
from northstar_quant.web import create_app

OPEN = LIVE_OPEN - timedelta(days=3)  # Publication cannot claim future observations.
RANGE = {"session_open": "2026-09-04T01:01:00Z", "session_close": "2026-09-04T01:03:00Z"}


def test_paused_shadow_source_publishes_without_rewriting_decisions_and_reuses_after_restart(
    postgres_engine: Engine, clean_database: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del clean_database
    library, source, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    streams, identifier = BrokerStreams(postgres_engine, library), uuid4()
    request_id = uuid4()
    try:
        start(streams, source, configuration, identifier)
        assert calls["ready"].wait(3)
        logins(calls["accept"], at=OPEN)
        streams.control(identifier, "PAUSE", request_id=uuid4())
        for sequence, seconds in enumerate(range(0, 181, 4), 3):
            event = tick(sequence, OPEN + timedelta(seconds=seconds), volume=100 + sequence)
            Clock.at = datetime.fromisoformat(event.received_at)
            calls["accept"](event)
        assert streams.get(identifier)["steps"] == []
        prefix = read_stream_archive(postgres_engine, identifier, 48)
        before = streams.get(identifier)
        attempt = streams.archive(identifier, through_sequence=48, request_id=request_id, **RANGE)
        assert attempt["status"] == "PUBLISHED", attempt
        assert attempt["source"]["input_kind"] == "CTP_CALLBACK_SEGMENT"
        assert attempt["source"]["allow_download"] is False
        with pytest.raises(PermissionError):
            library.download(UUID(attempt["source_id"]))
        dataset = library.load_dataset(UUID(attempt["snapshot_id"]))
        assert len(dataset.bars) == 2
        assert [bar.volume for bar in dataset.bars] == [Decimal(15), Decimal(15)]
        assert dataset.bars[0].available_at == OPEN + timedelta(minutes=2, milliseconds=100)
        assert dataset.bars[1].available_at == OPEN + timedelta(minutes=3, milliseconds=100)
        assert dataset.details is not None
        assert dataset.details.import_spec.availability_basis == "LOCAL_CAPTURE_RECONSTRUCTED"
        assert dataset.details.sources[0].content_hash == attempt["source"]["content_hash"]
        # Later actual receipt and shadow controls cannot rebind an archived prefix.
        later = tick(49, OPEN + timedelta(seconds=184), volume=149)
        Clock.at = datetime.fromisoformat(later.received_at)
        calls["accept"](later)
        assert read_stream_archive(postgres_engine, identifier, 48) == prefix
        assert (
            streams.archive(identifier, through_sequence=48, request_id=request_id, **RANGE)
            == attempt
        )
        assert streams.get(identifier)["steps"] == before["steps"] == []
        assert len(streams.get(identifier)["archives"]) == 1
    finally:
        streams.close()
    monkeypatch.setattr(
        stream_module, "load_credentials", lambda: pytest.fail("local archive read credentials")
    )
    reopened = BrokerStreams(postgres_engine, library)
    assert (
        reopened.archive(identifier, through_sequence=48, request_id=request_id, **RANGE) == attempt
    )
    configuration_value = ResearchConfig()
    research = run_research(dataset, configuration_value)
    run_id = RunStore(postgres_engine).save(dataset, configuration_value, research)
    paper = SessionStore(postgres_engine, library)
    saved_configuration = paper.save_configuration("archived samples", configuration_value)
    paper_id = uuid4()
    paper.create(dataset.snapshot_id, saved_configuration["configuration_id"], request_id=paper_id)
    for _ in dataset.bars:
        paper.advance(paper_id, request_id=uuid4())
    assert paper.get(paper_id)["summary"] == research.to_dict()["summary"]
    assert {item["use_id"] for item in library.source(UUID(attempt["source_id"]))["usages"]} == {
        run_id,
        str(paper_id),
    }
    assert calls["count"] == 1


def test_incomplete_requested_range_stays_failed_then_reprocesses_same_original_bytes(
    postgres_engine: Engine, clean_database: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del clean_database
    library, source, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    streams, identifier = BrokerStreams(postgres_engine, library), uuid4()
    try:
        start(streams, source, configuration, identifier)
        assert calls["ready"].wait(3)
        logins(calls["accept"], at=OPEN)
        for sequence, seconds in enumerate(range(0, 181, 4), 3):
            event = tick(sequence, OPEN + timedelta(seconds=seconds), volume=100 + sequence)
            Clock.at = datetime.fromisoformat(event.received_at)
            calls["accept"](event)
    finally:
        streams.close()
    request_id = uuid4()
    incomplete = {**RANGE, "session_close": "2026-09-04T01:04:00Z"}
    failed = streams.archive(
        identifier, through_sequence=48, request_id=request_id, allow_download=True, **incomplete
    )
    assert failed["status"] == "FAILED" and failed["snapshot_id"] is None, failed
    assert not library.list_datasets()
    _, content = library.download(UUID(failed["source_id"]))
    assert content == read_stream_archive(postgres_engine, identifier, 48)
    assert json.loads(content)["events"][0]["event"]["received_at"] == "2026-09-04T01:00:00Z"
    assert (
        streams.archive(
            identifier,
            through_sequence=48,
            request_id=request_id,
            allow_download=True,
            **incomplete,
        )
        == failed
    )
    with pytest.raises(AdmissionRejected, match="request_id"):
        streams.archive(
            identifier, through_sequence=48, request_id=request_id, allow_download=True, **RANGE
        )
    parameters = {**failed["parameters"], **RANGE}
    reprocessed = library.reprocess(
        UUID(failed["source_id"]), spec=parameters, request_id=str(uuid4())
    )
    assert reprocessed["status"] == "PUBLISHED", reprocessed
    assert reprocessed["source_id"] == failed["source_id"]
    assert library.attempt(UUID(failed["attempt_id"])) == failed
    with pytest.raises(AdmissionRejected):
        library.reprocess(
            UUID(failed["source_id"]),
            spec={**parameters, "through_sequence": 47},
            request_id=str(uuid4()),
        )
    assert calls["count"] == 1


def test_archive_rejects_forged_permission_and_does_not_truncate_oversized_prefix(
    postgres_engine: Engine, clean_database: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del clean_database
    library, source, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    streams, identifier = BrokerStreams(postgres_engine, library), uuid4()
    try:
        start(streams, source, configuration, identifier)
        assert calls["ready"].wait(3)
        logins(calls["accept"], at=OPEN)
    finally:
        streams.close()
    with pytest.raises(ValueError, match="unreceived"):
        read_stream_archive(postgres_engine, identifier, 3)
    document = json.loads(read_stream_archive(postgres_engine, identifier, 2))
    document["binding"]["request"]["use_basis"] = "forged permission declaration"
    with pytest.raises(AdmissionRejected):
        library.receive_stream(json.dumps(document).encode(), request_id=str(uuid4()), **RANGE)
    assert not library.list_sources()
    monkeypatch.setattr(stream_module, "_ARCHIVE_BYTES", 1)
    with pytest.raises(ValueError, match="5 MiB"):
        streams.archive(identifier, through_sequence=2, request_id=uuid4(), **RANGE)
    assert not library.list_sources()


def test_browser_archive_requires_csrf_and_publishes_saved_prefix_without_connecting(
    postgres_engine: Engine, clean_database: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del clean_database
    library, source, configuration, calls = prepare(postgres_engine, tmp_path, monkeypatch)
    streams, identifier = BrokerStreams(postgres_engine, library), uuid4()
    try:
        start(streams, source, configuration, identifier)
        assert calls["ready"].wait(3)
        logins(calls["accept"], at=OPEN)
        for sequence, seconds in enumerate(range(0, 181, 4), 3):
            event = tick(sequence, OPEN + timedelta(seconds=seconds), volume=100 + sequence)
            Clock.at = datetime.fromisoformat(event.received_at)
            calls["accept"](event)
    finally:
        streams.close()
    monkeypatch.setenv("NORTHSTAR_DATA_DIR", str(tmp_path / "archive"))
    monkeypatch.setattr(stream_module, "load_credentials", lambda: pytest.fail("archive connected"))
    with TestClient(create_app(postgres_engine, library), base_url="http://127.0.0.1") as client:
        page = client.get(f"/streams/{identifier}")
        csrf = re.search(r'<meta name="northstar-csrf" content="([^"]+)"', page.text).group(1)
        payload = {
            "through_sequence": 48,
            **RANGE,
            "allow_download": False,
            "request_id": str(uuid4()),
        }
        assert client.post(f"/api/streams/{identifier}/archive", json=payload).status_code == 403
        headers = {"X-Northstar-CSRF": csrf}
        result = client.post(f"/api/streams/{identifier}/archive", headers=headers, json=payload)
        assert result.status_code == 200, result.text
        attempt = result.json()
        assert attempt["status"] == "PUBLISHED", attempt
        assert (
            client.post(f"/api/streams/{identifier}/archive", headers=headers, json=payload).json()
            == attempt
        )
        assert client.get(f"/attempts/{attempt['attempt_id']}").status_code == 200
        assert client.get(f"/datasets/{attempt['snapshot_id']}").status_code == 200
        assert len(client.get(f"/api/streams/{identifier}").json()["archives"]) == 1
        # A malformed processing request remains readable evidence, not a reason
        # for the whole reception/control page to crash.
        failure = client.post(
            f"/api/sources/{attempt['source_id']}/reprocess",
            headers=headers,
            json={
                "spec": {"stream_id": str(identifier), "through_sequence": 48},
                "request_id": str(uuid4()),
            },
        )
        assert failure.status_code == 200 and failure.json()["status"] == "FAILED"
        assert client.get(f"/streams/{identifier}").status_code == 200
        assert client.get(f"/attempts/{failure.json()['attempt_id']}").status_code == 200
        assert len(client.get(f"/api/streams/{identifier}").json()["archives"]) == 2
    assert calls["count"] == 1

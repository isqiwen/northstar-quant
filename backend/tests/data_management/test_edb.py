"""Free-source bounds and retained raw evidence through PostgreSQL publication."""

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx2 as httpx
import pytest
from sqlalchemy import Engine

from northstar_quant.data_management.edb import collect
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.data_management.processing import process_attempt
from northstar_quant.data_management.research import ImportSpec
from tests.data_management.test_library import _study


def spec() -> ImportSpec:
    data = _study()[1]
    day = datetime.now(UTC).date() - timedelta(days=1)
    data.update(
        exchange="SHFE",
        symbol="RB2610",
        source_name="SHINNY_EDB",
        trading_day=str(day),
        session_open=f"{day}T01:00:00Z",
        session_close=f"{day}T01:02:00Z",
        availability_basis="FINAL_REVISED",
    )
    return ImportSpec.from_mapping(data)


def response(source: ImportSpec) -> bytes:
    start = int(source.session_open.timestamp()) * 1_000_000_000
    return (
        "datetime_nano,open,high,low,close,volume,open_oi,close_oi\n"
        f"{start},3100,3101,3099,3100,10,100,101\n"
        f"{start + 60_000_000_000},3100,3101,3099,3100,12,101,102\n"
    ).encode()


def test_edb_retains_original_and_publishes_independently(
    postgres_engine: Engine, clean_database: None, tmp_path: Path
) -> None:
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path))
    source = spec()
    content = response(source)
    requests = []

    def fetch(request):
        requests.append(request)
        return httpx.Response(200, content=content)

    transport = httpx.MockTransport(fetch)
    request_id = uuid4()
    attempt = collect(library, source, request_id=request_id, transport=transport)
    assert collect(library, source, request_id=request_id, transport=transport) == attempt
    assert attempt["status"] == "PENDING"
    assert str(requests[0].url).startswith("https://edb.shinnytech.com/md/kline?")
    assert dict(requests[0].url.params)["symbol"] == "SHFE.rb2610"
    assert "token" not in requests[0].url.params
    retained = library.source(UUID(str(attempt["source_id"])))
    assert retained["content_hash"] == hashlib.sha256(content).hexdigest()
    worker = DataLibrary(postgres_engine, SourceFiles(tmp_path))
    done = process_attempt(worker)
    assert done["status"] == "PUBLISHED", done
    dataset = worker.load_dataset(UUID(str(done["snapshot_id"])))
    assert len(dataset.bars) == 2
    assert dataset.bars[0].available_at == source.session_open + timedelta(minutes=1)
    assert dataset.details.sources[0].content_hash == hashlib.sha256(content).hexdigest()
    assert dataset.details.sources[0].input_kind == "EDB_CSV"
    assert (
        worker.publications.load_dataset(dataset.snapshot_id).content_hash == dataset.content_hash
    )


@pytest.mark.parametrize("fault", ["gap", "duplicate", "unaligned", "html", "fractional_volume"])
def test_bad_edb_is_retained_but_never_published(
    postgres_engine: Engine, clean_database: None, tmp_path: Path, fault: str
) -> None:
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path))
    source = spec()
    rows = response(source).splitlines(keepends=True)
    if fault == "gap":
        rows.pop()
    elif fault == "duplicate":
        rows[-1] = rows[1]
    elif fault == "unaligned":
        stamp, rest = rows[1].split(b",", 1)
        rows[1] = str(int(stamp) + 1).encode() + b"," + rest
    elif fault == "fractional_volume":
        rows[1] = rows[1].replace(b",10,100,", b",1.5,100,")
    else:
        rows = [b"<html>upstream failure</html>"]
    content = b"".join(rows)
    attempt = collect(
        library,
        source,
        request_id=uuid4(),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content)),
    )
    assert process_attempt(library)["status"] == "FAILED"
    assert (
        library.source(UUID(str(attempt["source_id"])))["content_hash"]
        == hashlib.sha256(content).hexdigest()
    )
    assert library.list_datasets() == ()


@pytest.mark.parametrize(
    "fault", ["expired", "future", "continuous", "redirect", "limited", "large"]
)
def test_edb_refuses_unbounded_or_failed_acquisition(
    postgres_engine: Engine, clean_database: None, tmp_path: Path, fault: str
) -> None:
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path))
    source = spec()
    if fault in {"expired", "future"}:
        shift = timedelta(days=-366 if fault == "expired" else 2)
        source = replace(
            source,
            trading_day=source.trading_day + shift,
            session_open=source.session_open + shift,
            session_close=source.session_close + shift,
        )
    if fault == "continuous":
        source = replace(source, symbol="RB0")
    calls = []

    def fetch(request):
        calls.append(request)
        return httpx.Response(
            302 if fault == "redirect" else 429 if fault == "limited" else 200,
            content=b"x" * 5242881 if fault == "large" else b"error",
            headers={"location": "https://example.com/paid"},
        )

    with pytest.raises(ValueError):
        collect(library, source, request_id=uuid4(), transport=httpx.MockTransport(fetch))
    assert len(calls) == (0 if fault in {"expired", "future", "continuous"} else 1)
    assert library.list_sources() == []

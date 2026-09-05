"""Source retention, failed attempts, immutable provenance and publication recovery."""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

import northstar_quant.data.library as library_module
from northstar_quant.data.files import SourceFiles
from northstar_quant.data.library import AdmissionRejected, DataLibrary, manifest
from northstar_quant.data.research import ImportSpec, ResearchDataset
from northstar_quant.research import ResearchConfig, run_research
from northstar_quant.runs import RunStore
from northstar_quant.sessions import SessionStore


def _study() -> tuple[bytes, dict[str, object], ResearchConfig]:
    path = Path(__file__).resolve().parents[2] / "examples/intraday.toml"
    study = tomllib.loads(path.read_text())
    source = dict(study["source"])
    content = (path.parent / source.pop("file")).read_bytes()
    return content, source, ResearchConfig.from_mapping(study["research"])


def _receive(
    library: DataLibrary,
    content: bytes,
    spec: dict[str, object],
    *,
    request_id: str | None = None,
    allow_download: bool = True,
) -> dict[str, object]:
    return library.receive(
        content,
        filename="received.csv",
        source_name=str(spec["source_name"]),
        use_basis="Locally generated data; retention and private research permitted.",
        allow_retention=True,
        allow_download=allow_download,
        spec=spec,
        request_id=str(uuid4()) if request_id is None else request_id,
    )


def test_admission_refusal_does_not_store_content_but_bad_spec_has_a_durable_attempt(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
) -> None:
    del clean_database
    files = SourceFiles(tmp_path / "sources")
    library = DataLibrary(postgres_engine, files)
    content, spec, _ = _study()
    with pytest.raises(AdmissionRejected, match="retention") as rejection:
        library.receive(
            content,
            filename="private.csv",
            source_name="PRIVATE",
            use_basis="No retention grant",
            allow_retention=False,
            allow_download=False,
            spec=spec,
            request_id=str(uuid4()),
        )
    assert library.list_sources() == []
    assert files.inventory() == []
    assert library.list_rejections()[0]["rejection_id"] == rejection.value.rejection_id
    assert set(library.list_rejections()[0]) == {
        "rejection_id",
        "request_id",
        "created_at",
        "reason",
    }
    failed = library.receive(
        content,
        filename="actual.csv",
        source_name=str(spec["source_name"]),
        use_basis="Local synthetic private use",
        allow_retention=True,
        allow_download=False,
        spec={},
        request_id=str(uuid4()),
    )
    assert failed["status"] == "FAILED"
    assert failed["stage"] == "VALIDATING"
    assert failed["snapshot_id"] is None
    source_id = UUID(str(failed["source_id"]))
    assert library.source(source_id)["file_status"] == "AVAILABLE"
    with pytest.raises(PermissionError):
        library.download(source_id)
    repaired = library.reprocess(source_id, spec=spec, request_id=str(uuid4()))
    assert repaired["status"] == "PUBLISHED"
    assert repaired["attempt_id"] != failed["attempt_id"]
    assert len(library.source(source_id)["attempts"]) == 2
    assert len(files.inventory()) == 1
    assert repaired["retry_of"] == failed["attempt_id"]
    for statement in (
        "UPDATE data_sources SET allow_download = true",
        "UPDATE data_processing_attempts SET error = 'rewritten'",
        "DELETE FROM data_processing_attempts",
    ):
        with pytest.raises(DBAPIError, match="immutable"):
            with postgres_engine.begin() as connection:
                connection.execute(text(statement))


def test_request_identity_and_retries_keep_fixed_evidence_and_distinct_attempts(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
) -> None:
    del clean_database
    files = SourceFiles(tmp_path / "sources")
    library = DataLibrary(postgres_engine, files)
    content, spec, config = _study()
    request_id = str(uuid4())
    first = _receive(library, content, spec, request_id=request_id)
    assert first["status"] == "PUBLISHED"
    assert _receive(library, content, spec, request_id=request_id) == first
    with pytest.raises(AdmissionRejected, match="request_id"):
        _receive(library, content + b"\n", spec, request_id=request_id)
    assert len(library.list_sources()) == len(library.list_attempts()) == 1
    source_id = UUID(str(first["source_id"]))
    snapshot_id = UUID(str(first["snapshot_id"]))
    dataset = library.load_dataset(snapshot_id)
    result = run_research(dataset, config)
    run_id = RunStore(postgres_engine).save(dataset, config, result)
    second = library.reprocess(source_id, spec=spec, request_id=str(uuid4()))
    third = _receive(library, content, spec)
    assert second["reused_product"] is True
    assert third["reused_product"] is True
    assert first["snapshot_id"] == second["snapshot_id"] == third["snapshot_id"]
    assert len(library.list_attempts()) == 3
    assert library.load_dataset(snapshot_id) == dataset
    assert RunStore(postgres_engine).save(dataset, config, result) == run_id
    assert library.download(source_id) == ("received.csv", content)
    assert {item["kind"] for item in library.lineage(snapshot_id)["usages"]} == {"RESEARCH"}
    with postgres_engine.connect() as connection:
        assert len(manifest(connection)) == 2
        assert connection.scalar(text("SELECT count(*) FROM dataset_snapshot_manifest")) == 1
    assert len(files.inventory()) == 1


@pytest.mark.parametrize("failure", ["missing", "corrupt"])
def test_unavailable_archive_blocks_download_research_and_new_paper(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    failure: str,
) -> None:
    del clean_database
    files = SourceFiles(tmp_path / "sources")
    library = DataLibrary(postgres_engine, files)
    content, spec, config = _study()
    accepted = _receive(library, content, spec)
    source_id = UUID(str(accepted["source_id"]))
    snapshot_id = UUID(str(accepted["snapshot_id"]))
    dataset = library.load_dataset(snapshot_id)
    run_id = RunStore(postgres_engine).save(dataset, config, run_research(dataset, config))
    source = library.source(source_id)
    digest = str(source["content_hash"])
    stored = files.root / "objects" / digest[:2] / digest
    if failure == "missing":
        stored.unlink()
    else:
        stored.write_bytes(b"damaged")
    assert library.source(source_id)["file_status"] == failure.upper()
    assert library.lineage(snapshot_id)["sources"][0]["file_status"] == failure.upper()
    assert RunStore(postgres_engine).get(run_id)["run_id"] == run_id
    with pytest.raises(ValueError):
        library.download(source_id)
    with pytest.raises(ValueError):
        library.load_dataset(snapshot_id)
    assert library.list_datasets() == ()
    sessions = SessionStore(postgres_engine, library)
    saved = sessions.save_configuration("No damaged input", config)
    with pytest.raises(ValueError):
        sessions.create(snapshot_id, str(saved["configuration_id"]), request_id=uuid4())


def test_converted_input_tracks_actual_upstream_and_both_run_consumers(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
) -> None:
    del clean_database
    library = DataLibrary(postgres_engine, SourceFiles(tmp_path / "sources"))
    content, spec, config = _study()
    original = _receive(library, b"actual received provider-format bytes", spec)
    assert original["status"] == "FAILED"
    upstream_id = UUID(str(original["source_id"]))
    converted = library.receive(
        content,
        filename="converted.csv",
        source_name=str(spec["source_name"]),
        use_basis="Local engineering transformation with explicit permission to retain.",
        allow_retention=True,
        allow_download=True,
        spec=spec,
        request_id=str(uuid4()),
        input_kind="CONVERTED_CSV",
        upstream_source_id=upstream_id,
        transformation_note="Operator supplied this transformed CSV; converter was run externally.",
    )
    assert converted["status"] == "PUBLISHED"
    snapshot_id = UUID(str(converted["snapshot_id"]))
    dataset = library.load_dataset(snapshot_id)
    assert dataset.details is not None
    assert dataset.details.sources[0].upstream_source_id == upstream_id
    run_id = RunStore(postgres_engine).save(dataset, config, run_research(dataset, config))
    sessions = SessionStore(postgres_engine, library)
    saved = sessions.save_configuration("Fixed source", config)
    session_id = uuid4()
    sessions.create(snapshot_id, str(saved["configuration_id"]), request_id=session_id)
    lineage = library.lineage(snapshot_id)
    assert {source["source_id"] for source in lineage["sources"]} == {
        str(upstream_id),
        converted["source_id"],
    }
    assert {(use["kind"], use["use_id"]) for use in lineage["usages"]} == {
        ("RESEARCH", run_id),
        ("PAPER", str(session_id)),
    }
    assert library.source(upstream_id)["usages"] == lineage["usages"]
    assert library.source(upstream_id)["products"] == [{"snapshot_id": str(snapshot_id)}]


def test_interrupted_publication_is_not_offered_and_retry_recovers_without_duplicate_facts(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    files = SourceFiles(tmp_path / "sources")
    library = DataLibrary(postgres_engine, files)
    content, spec, _ = _study()
    original = library_module._import_csv

    def lose_completion(
        engine: Engine,
        content: bytes,
        spec: ImportSpec,
        *,
        archive: dict[str, object],
        processing_hash: str,
        stage: Callable[[str, dict[str, object]], None],
    ) -> ResearchDataset:
        original(
            engine, content, spec, archive=archive, processing_hash=processing_hash, stage=stage
        )
        raise KeyboardInterrupt("process terminated before library publication acknowledgement")

    with monkeypatch.context() as context:
        context.setattr(library_module, "_import_csv", lose_completion)
        with pytest.raises(KeyboardInterrupt):
            _receive(library, content, spec)
    interrupted = library.list_attempts()[0]
    assert interrupted["status"] == "RUNNING"
    assert library.list_datasets() == ()
    with postgres_engine.connect() as connection:
        before = connection.scalar(text("SELECT count(*) FROM canonical_bar"))
        assert connection.scalar(text("SELECT count(*) FROM dataset_snapshot_manifest")) == 1
    # A complete but unreferenced object is an audit result, never a published input.
    orphan = files.store(b"complete receipt whose database transaction did not commit")
    audit = library.reconcile()
    assert audit["unreferenced_objects"] == [orphan.to_dict()]
    assert library.attempt(UUID(str(interrupted["attempt_id"])))["status"] == "FAILED"
    retried = library.reprocess(
        UUID(str(interrupted["source_id"])), spec=spec, request_id=str(uuid4())
    )
    assert retried["status"] == "PUBLISHED"
    assert len(library.list_datasets()) == 1
    with postgres_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM canonical_bar")) == before
        assert connection.scalar(text("SELECT count(*) FROM dataset_snapshot_manifest")) == 1

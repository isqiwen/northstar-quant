"""Publication corruption and interrupted export must not produce false research inputs."""

import json
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import Engine

from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.data_management.processing import process_attempt
from northstar_quant.data_management.publications import PublishedDatasets
from tests.data_management.test_library import _receive, _study


def test_fixed_publication_survives_owner_offline_and_rejects_corruption(
    postgres_engine: Engine, clean_database: None, tmp_path: Path
):
    files = SourceFiles(tmp_path / "source")
    library = DataLibrary(postgres_engine, files)
    content, spec, _ = _study()
    attempt = _receive(library, content, spec)
    identifier = UUID(str(attempt["snapshot_id"]))
    expected = library.load_dataset(identifier)
    reader = PublishedDatasets(library.publications.root)
    assert reader.load_dataset(identifier) == expected
    # Reader needs neither the source archive nor a database connection.
    for path in files.root.glob("objects/**/*"):
        if path.is_file():
            path.unlink()
    assert reader.load_dataset(identifier) == expected
    path = reader.root / f"{identifier}.json"
    envelope = json.loads(path.read_text())
    envelope["payload"] = envelope["payload"].replace("SYNTHETIC", "FINAL_REVISED")
    path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError, match="checksum"):
        reader.load_dataset(identifier)


def test_committed_publication_export_is_repaired_without_reprocessing(
    postgres_engine: Engine, clean_database: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    files = SourceFiles(tmp_path / "source")
    library = DataLibrary(postgres_engine, files)
    content, spec, _ = _study()
    publish = library.publications.publish

    def unavailable(_):
        raise OSError("share unavailable")

    monkeypatch.setattr(library.publications, "publish", unavailable)
    with pytest.raises(OSError, match="share unavailable"):
        _receive(library, content, spec)
    attempts = library.list_attempts()
    assert len(attempts) == 1 and attempts[0]["status"] == "PUBLISHED"
    monkeypatch.setattr(library.publications, "publish", publish)
    assert process_attempt(library) is None
    assert len(library.list_attempts()) == 1
    assert library.publications.load_dataset(UUID(str(attempts[0]["snapshot_id"])))

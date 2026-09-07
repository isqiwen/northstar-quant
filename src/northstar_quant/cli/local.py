"""Current local Data, research and file-Paper commands."""

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import Engine


def _study(path: Path) -> tuple[Path, dict[str, object], dict[str, object], dict[str, object]]:
    if path.stat().st_size > 65_536:
        raise ValueError("study settings exceed 64 KiB")
    with path.open("rb") as stream:
        document = tomllib.load(stream)
    if not {"source", "research"} <= set(document) <= {"source", "research", "archive"}:
        raise ValueError("study requires [source], [research] and optional import-only [archive]")
    source = document["source"]
    research = document["research"]
    archive = document.get("archive", {})
    if (
        not isinstance(source, dict)
        or not isinstance(research, dict)
        or not isinstance(archive, dict)
    ):
        raise ValueError("source, research and archive must be TOML tables")
    source = dict(source)
    file_name = source.pop("file", None)
    if not isinstance(file_name, str) or not file_name.strip():
        raise ValueError("source.file must name a CSV file relative to the study")
    return (
        (path.parent / file_name).resolve(),
        cast(dict[str, object], source),
        cast(dict[str, object], research),
        cast(dict[str, object], archive),
    )


def execute(arguments: argparse.Namespace, engine: Engine) -> int:
    from northstar_quant.data.files import SourceFiles
    from northstar_quant.data.library import DataLibrary
    from northstar_quant.research import ResearchConfig, run_research
    from northstar_quant.runs import RunStore
    from northstar_quant.runtime import implementation_hash
    from northstar_quant.sessions import SessionStore

    store = RunStore(engine)
    files = SourceFiles.from_environment()
    library = DataLibrary(engine, files)
    paper = SessionStore(engine, library)
    if arguments.command == "configure":
        config = ResearchConfig.from_mapping(_study(arguments.study.resolve())[2])
        print(json.dumps(paper.save_configuration(arguments.name, config), ensure_ascii=False))
    elif arguments.command == "configurations":
        print(json.dumps(paper.list_configurations(), ensure_ascii=False))
    elif arguments.command == "paper-create":
        print(
            json.dumps(
                paper.create(
                    arguments.snapshot_id,
                    arguments.configuration_id,
                    request_id=arguments.request_id,
                ),
                ensure_ascii=False,
            )
        )
    elif arguments.command == "paper-list":
        print(json.dumps(paper.list(), ensure_ascii=False))
    elif arguments.command == "paper-show":
        print(json.dumps(paper.get(arguments.session_id), ensure_ascii=False))
    elif arguments.command == "paper-next":
        print(
            json.dumps(
                paper.advance(arguments.session_id, request_id=arguments.request_id),
                ensure_ascii=False,
            )
        )
    elif arguments.command in {"run", "import"}:
        csv_path, source, parameters, archive = _study(arguments.study.resolve())
        required = {"use_basis", "allow_retention", "allow_download", "input_kind"}
        if not required <= set(archive) <= required | {"upstream_source_id", "transformation_note"}:
            raise ValueError(
                "imports require explicit [archive] use_basis, allow_retention, "
                "allow_download and input_kind"
            )
        with csv_path.open("rb") as stream:
            content = stream.read(files.max_file_bytes + 1)
        upstream = archive.get("upstream_source_id")
        attempt = library.receive(
            content,
            filename=csv_path.name,
            source_name=cast(str, source.get("source_name")),
            use_basis=cast(str, archive["use_basis"]),
            allow_retention=cast(bool, archive["allow_retention"]),
            allow_download=cast(bool, archive["allow_download"]),
            input_kind=cast(str, archive["input_kind"]),
            upstream_source_id=None if upstream is None else UUID(str(upstream)),
            transformation_note=cast(str | None, archive.get("transformation_note")),
            spec=source,
            request_id=str(arguments.request_id or uuid4()),
        )
        if arguments.command == "import" or attempt["status"] != "PUBLISHED":
            print(json.dumps(attempt, ensure_ascii=False))
            return 0 if attempt["status"] == "PUBLISHED" else 2
        dataset = library.load_dataset(UUID(str(attempt["snapshot_id"])))
        config = ResearchConfig.from_mapping(parameters)
        result = run_research(dataset, config)
        run_id = store.save(dataset, config, result)
        print(json.dumps(store.get(run_id), ensure_ascii=False, sort_keys=True))
    elif arguments.command == "datasets":
        print(json.dumps([item.to_dict() for item in library.list_datasets()], ensure_ascii=False))
    elif arguments.command == "dataset":
        print(
            json.dumps(
                library.describe_dataset(arguments.snapshot_id).to_dict(), ensure_ascii=False
            )
        )
    elif arguments.command == "research":
        config = (
            ResearchConfig()
            if arguments.study is None
            else ResearchConfig.from_mapping(_study(arguments.study.resolve())[2])
        )
        dataset = library.load_dataset(arguments.snapshot_id)
        run_id = store.save(dataset, config, run_research(dataset, config))
        print(json.dumps(store.get(run_id), ensure_ascii=False, sort_keys=True))
    elif arguments.command == "replay":
        original = store.get(arguments.run_id)
        if original["implementation_hash"] != implementation_hash():
            raise ValueError("exact replay requires the saved implementation identity")
        snapshot = cast(dict[str, object], original["snapshot"])
        dataset = library.load_dataset(UUID(str(snapshot["id"])))
        if dataset.content_hash != snapshot["content_hash"]:
            raise ValueError("stored snapshot identity does not match the saved research")
        config = ResearchConfig.from_mapping(cast(dict[str, object], original["config"]))
        run_id = store.save(dataset, config, run_research(dataset, config))
        if run_id != arguments.run_id:
            raise ValueError("replay did not reproduce the saved result identity")
        print(json.dumps(store.get(run_id), ensure_ascii=False, sort_keys=True))
    elif arguments.command == "show":
        print(json.dumps(store.get(arguments.run_id), ensure_ascii=False, sort_keys=True))
    elif arguments.command == "list":
        print(json.dumps(store.list(), ensure_ascii=False, sort_keys=True))
    elif arguments.command == "sources":
        print(json.dumps(library.list_sources(), ensure_ascii=False))
    elif arguments.command == "source":
        print(json.dumps(library.source(arguments.source_id), ensure_ascii=False))
    elif arguments.command == "attempt":
        print(json.dumps(library.attempt(arguments.attempt_id), ensure_ascii=False))
    elif arguments.command == "reprocess":
        attempt = library.reprocess(
            arguments.source_id,
            spec=_study(arguments.study.resolve())[1],
            request_id=str(arguments.request_id),
        )
        print(json.dumps(attempt, ensure_ascii=False))
        return 0 if attempt["status"] == "PUBLISHED" else 2
    elif arguments.command == "download":
        filename, content = library.download(arguments.source_id)
        with arguments.destination.open("xb") as stream:
            stream.write(content)
        print(json.dumps({"filename": filename, "byte_count": len(content)}))
    elif arguments.command == "audit-data":
        print(json.dumps(library.reconcile(), ensure_ascii=False))
    elif arguments.command == "backup":
        from northstar_quant.data.maintenance import backup

        print(
            json.dumps(backup(engine, files, arguments.destination.resolve()), ensure_ascii=False)
        )
    return 0

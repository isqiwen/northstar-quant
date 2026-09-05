"""Supported user operations for the single personal application."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

from alembic.util.exc import CommandError
from sqlalchemy.exc import SQLAlchemyError

from northstar_quant.db import initialize_database, open_database, require_current_database


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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="northstar", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="initialize or verify the current PostgreSQL baseline")
    run = commands.add_parser("run", help="import CSV, evaluate research and save the result")
    run.add_argument("study", type=Path, help="TOML study with [source] and [research]")
    accept = commands.add_parser(
        "import", help="accept CSV into the data library without a research run"
    )
    accept.add_argument("study", type=Path, help="TOML study with [source] and [research]")
    for command in (run, accept):
        command.add_argument(
            "--request-id", type=UUID, help="reuse on retry; otherwise a new attempt"
        )
    commands.add_parser("sources", help="list retained source files and their availability")
    source_command = commands.add_parser("source", help="show processing and usage of one source")
    source_command.add_argument("source_id", type=UUID)
    attempt_command = commands.add_parser("attempt", help="show accepted processing or failure")
    attempt_command.add_argument("attempt_id", type=UUID)
    reprocess = commands.add_parser(
        "reprocess", help="process retained bytes with explicit parameters"
    )
    reprocess.add_argument("source_id", type=UUID)
    reprocess.add_argument("--study", required=True, type=Path)
    reprocess.add_argument("--request-id", required=True, type=UUID)
    download = commands.add_parser("download", help="export permitted original bytes to a new file")
    download.add_argument("source_id", type=UUID)
    download.add_argument("destination", type=Path)
    commands.add_parser(
        "audit-data", help="reconcile interrupted processing and inspect files; never delete"
    )
    backup_command = commands.add_parser(
        "backup", help="maintenance-window database and source backup"
    )
    backup_command.add_argument("destination", type=Path)
    restore_command = commands.add_parser(
        "restore", help="restore trusted backup into an empty database and new data directory"
    )
    restore_command.add_argument("backup", type=Path)
    commands.add_parser("datasets", help="list accepted datasets available for research")
    dataset_command = commands.add_parser(
        "dataset", help="show pinned source, quality and time evidence"
    )
    dataset_command.add_argument("snapshot_id", type=UUID)
    research = commands.add_parser(
        "research", help="research an accepted dataset without its source file"
    )
    research.add_argument("snapshot_id", type=UUID)
    research.add_argument(
        "--study",
        type=Path,
        help="use only this TOML study's [research] parameters; do not import CSV",
    )
    replay = commands.add_parser("replay", help="reproduce a saved run from its immutable snapshot")
    replay.add_argument("run_id")
    show = commands.add_parser("show", help="read one persisted research result")
    show.add_argument("run_id")
    commands.add_parser("list", help="list the latest stored research runs")
    configure = commands.add_parser("configure", help="save immutable strategy/Risk parameters")
    configure.add_argument("study", type=Path)
    configure.add_argument("--name", required=True, help="human-readable configuration label")
    commands.add_parser("configurations", help="list saved immutable configurations")
    paper_create = commands.add_parser(
        "paper-create", help="create a paused FILE_REPLAY Paper account"
    )
    paper_create.add_argument("snapshot_id", type=UUID)
    paper_create.add_argument("configuration_id")
    paper_create.add_argument(
        "--request-id", type=UUID, required=True, help="stable retry identity"
    )
    commands.add_parser("paper-list", help="list paused or completed Paper accounts")
    paper_show = commands.add_parser("paper-show", help="inspect committed Paper facts")
    paper_show.add_argument("session_id", type=UUID)
    paper_next = commands.add_parser("paper-next", help="reconcile and advance one file input only")
    paper_next.add_argument("session_id", type=UUID)
    paper_next.add_argument(
        "--request-id", type=UUID, required=True, help="reuse this UUID on retry"
    )
    serve = commands.add_parser("serve", help="serve the personal research workspace")
    serve.add_argument("--port", type=int, default=18080)
    commands.add_parser("broker-status", help="inspect SimNow setup without connecting")
    commands.add_parser("broker-sdk-check", help="load and release native SDK without network")
    commands.add_parser("broker-list", help="list persisted SimNow query evidence")
    broker_show = commands.add_parser("broker-show", help="read one saved broker query")
    broker_show.add_argument("batch_id", type=UUID)
    broker_query = commands.add_parser(
        "broker-query", help="explicit bounded SimNow read-only query"
    )
    broker_query.add_argument("profile", choices=("simnow_dev", "simnow_trading"))
    broker_query.add_argument("--instrument", required=True, help="one concrete futures instrument")
    broker_query.add_argument(
        "--request-id", type=UUID, required=True, help="reuse to read an uncertain response"
    )
    arguments = parser.parse_args(argv)

    engine = None
    try:
        if arguments.command == "broker-sdk-check":
            from northstar_quant.broker.ctp import sdk_self_check

            status = sdk_self_check()
            print(json.dumps(status, ensure_ascii=False))
            return 0 if status["native_verified"] else 2
        if arguments.command == "broker-status":
            from northstar_quant.broker.workspace import BrokerWorkspace

            print(json.dumps(BrokerWorkspace.status(), ensure_ascii=False))
            return 0
        engine = open_database()
        if arguments.command == "init-db":
            initialize_database(engine)
            print(json.dumps({"status": "ready"}))
            return 0

        if arguments.command == "restore":
            from northstar_quant.data.maintenance import restore

            data_root = os.environ.get("NORTHSTAR_DATA_DIR")
            if not data_root:
                raise ValueError("NORTHSTAR_DATA_DIR must be an explicitly prepared new directory")
            restored = restore(engine, Path(data_root), arguments.backup.resolve())
            print(json.dumps(restored, ensure_ascii=False))
            return 0

        require_current_database(engine)

        if arguments.command in {"broker-query", "broker-list", "broker-show"}:
            from northstar_quant.broker.workspace import BrokerWorkspace

            broker = BrokerWorkspace(engine)
            if arguments.command == "broker-query":
                broker_result = broker.query(
                    arguments.profile,
                    arguments.instrument,
                    request_id=arguments.request_id,
                )
                print(json.dumps(broker_result, ensure_ascii=False))
                return 0 if broker_result["status"] == "COMPLETE" else 2
            if arguments.command == "broker-list":
                print(json.dumps(broker.list(), ensure_ascii=False))
            else:
                print(json.dumps(broker.get(arguments.batch_id), ensure_ascii=False))
            return 0

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
            if (
                not required
                <= set(archive)
                <= required | {"upstream_source_id", "transformation_note"}
            ):
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
            print(
                json.dumps([item.to_dict() for item in library.list_datasets()], ensure_ascii=False)
            )
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
                json.dumps(
                    backup(engine, files, arguments.destination.resolve()), ensure_ascii=False
                )
            )
        elif arguments.command == "serve":
            if not 1024 <= arguments.port <= 65535:
                raise ValueError("port must be between 1024 and 65535")
            import uvicorn

            from northstar_quant.web import create_app

            uvicorn.run(create_app(engine, library), host="127.0.0.1", port=arguments.port)
        return 0
    except SQLAlchemyError:
        print(
            "northstar: PostgreSQL operation failed; check its availability and baseline",
            file=sys.stderr,
        )
        return 2
    except (OSError, ValueError, LookupError, CommandError) as exc:
        print(f"northstar: {exc}", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())

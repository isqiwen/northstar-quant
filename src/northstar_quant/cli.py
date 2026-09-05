"""Supported user operations for the single personal application."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import cast
from uuid import UUID

from alembic.util.exc import CommandError
from sqlalchemy.exc import SQLAlchemyError

from northstar_quant.db import initialize_database, open_database, require_current_database


def _study(path: Path) -> tuple[Path, dict[str, object], dict[str, object]]:
    if path.stat().st_size > 65_536:
        raise ValueError("study settings exceed 64 KiB")
    with path.open("rb") as stream:
        document = tomllib.load(stream)
    if set(document) != {"source", "research"}:
        raise ValueError("study requires exactly [source] and [research]")
    source = document["source"]
    research = document["research"]
    if not isinstance(source, dict) or not isinstance(research, dict):
        raise ValueError("source and research must be TOML tables")
    source = dict(source)
    file_name = source.pop("file", None)
    if not isinstance(file_name, str) or not file_name.strip():
        raise ValueError("source.file must name a CSV file relative to the study")
    return (
        (path.parent / file_name).resolve(),
        cast(dict[str, object], source),
        cast(dict[str, object], research),
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
    arguments = parser.parse_args(argv)

    engine = None
    try:
        engine = open_database()
        if arguments.command == "init-db":
            initialize_database(engine)
            print(json.dumps({"status": "ready"}))
            return 0

        require_current_database(engine)

        from northstar_quant.data.research import (
            ImportSpec,
            describe_dataset,
            import_csv,
            list_datasets,
            load_dataset,
        )
        from northstar_quant.research import ResearchConfig, run_research
        from northstar_quant.runs import RunStore, implementation_hash
        from northstar_quant.sessions import SessionStore

        store = RunStore(engine)
        paper = SessionStore(engine)
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
            csv_path, source, parameters = _study(arguments.study.resolve())
            config = ResearchConfig.from_mapping(parameters)
            dataset = import_csv(engine, csv_path, ImportSpec.from_mapping(source))
            if arguments.command == "import":
                print(
                    json.dumps(
                        describe_dataset(engine, dataset.snapshot_id).to_dict(), ensure_ascii=False
                    )
                )
                return 0
            result = run_research(dataset, config)
            run_id = store.save(dataset, config, result)
            print(json.dumps(store.get(run_id), ensure_ascii=False, sort_keys=True))
        elif arguments.command == "datasets":
            print(
                json.dumps([item.to_dict() for item in list_datasets(engine)], ensure_ascii=False)
            )
        elif arguments.command == "dataset":
            print(
                json.dumps(
                    describe_dataset(engine, arguments.snapshot_id).to_dict(), ensure_ascii=False
                )
            )
        elif arguments.command == "research":
            config = (
                ResearchConfig()
                if arguments.study is None
                else ResearchConfig.from_mapping(_study(arguments.study.resolve())[2])
            )
            dataset = load_dataset(engine, arguments.snapshot_id)
            run_id = store.save(dataset, config, run_research(dataset, config))
            print(json.dumps(store.get(run_id), ensure_ascii=False, sort_keys=True))
        elif arguments.command == "replay":
            original = store.get(arguments.run_id)
            if original["implementation_hash"] != implementation_hash():
                raise ValueError("exact replay requires the saved implementation identity")
            snapshot = cast(dict[str, object], original["snapshot"])
            dataset = load_dataset(engine, UUID(str(snapshot["id"])))
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
        elif arguments.command == "serve":
            if not 1024 <= arguments.port <= 65535:
                raise ValueError("port must be between 1024 and 65535")
            import uvicorn

            from northstar_quant.web import create_app

            uvicorn.run(create_app(engine), host="127.0.0.1", port=arguments.port)
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

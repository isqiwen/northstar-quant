"""Dispatch installed commands to local research or the owning Live runtime."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from alembic.util.exc import CommandError
from sqlalchemy.exc import SQLAlchemyError

from northstar_quant.cli.arguments import parse
from northstar_quant.db import initialize_database, open_database, require_current_database
from northstar_quant.live import CommandUnknown, LiveClient, RuntimeUnavailable


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse(argv)
    engine = None
    try:
        if arguments.command == "init-live-auth":
            from northstar_quant.cli.runtime import initialize_auth

            print(json.dumps(initialize_auth(arguments.directory), ensure_ascii=False))
            return 0
        if arguments.command in {"live", "live-web", "data-hub", "research-web"}:
            from northstar_quant.cli.runtime import serve

            serve(arguments.command, arguments.port)
            return 0
        if arguments.command == "broker-sdk-check":
            from northstar_quant.broker.ctp import sdk_self_check

            status = sdk_self_check()
            print(json.dumps(status, ensure_ascii=False))
            return 0 if status["native_verified"] else 2
        if arguments.command.startswith(("broker-", "stream-", "live-")):
            from northstar_quant.cli import broker, streams

            client = LiveClient.from_environment()
            try:
                if arguments.command == "live-status":
                    print(json.dumps(client.status(), ensure_ascii=False))
                elif arguments.command == "live-check":
                    observation = client.diagnostics()
                    print(json.dumps(observation, ensure_ascii=False))
                    return 0 if observation["status"] == "OK" else 2
                elif arguments.command == "live-command-show":
                    print(json.dumps(client.command(arguments.request_id), ensure_ascii=False))
                elif arguments.command == "broker-status":
                    print(json.dumps(client.broker.status(), ensure_ascii=False))
                elif arguments.command.startswith("broker-opening-budget"):
                    return broker.budget(arguments, client)
                elif (
                    arguments.command.startswith("stream-")
                    or arguments.command == "broker-catchup-stream"
                ):
                    return streams.execute(arguments, client)
                else:
                    return broker.execute(arguments, client)
                return 0
            finally:
                client.close()

        engine = open_database()
        if arguments.command == "init-db":
            initialize_database(engine)
            print(json.dumps({"status": "ready"}))
            return 0
        if arguments.command == "restore":
            from northstar_quant.data_management.maintenance import restore

            data_root = os.environ.get("NORTHSTAR_DATA_DIR")
            if not data_root:
                raise ValueError("NORTHSTAR_DATA_DIR must be an explicitly prepared new directory")
            result = restore(engine, Path(data_root), arguments.backup.resolve())
            print(json.dumps(result, ensure_ascii=False))
            return 0
        require_current_database(engine)
        from northstar_quant.cli.local import execute

        return execute(arguments, engine)
    except SQLAlchemyError:
        print(
            "northstar: PostgreSQL operation failed; check its availability and baseline",
            file=sys.stderr,
        )
        return 2
    except (
        OSError,
        ValueError,
        LookupError,
        CommandError,
        RuntimeUnavailable,
        CommandUnknown,
    ) as error:
        print(f"northstar: {error}", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()

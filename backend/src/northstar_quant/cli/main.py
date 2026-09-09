"""Dispatch installed commands to local research or the owning Live runtime."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from alembic.util.exc import CommandError
from sqlalchemy.exc import SQLAlchemyError

from northstar_quant.apps.storage import (
    initialize_database,
    open_database,
    require_current_database,
)
from northstar_quant.cli.arguments import parse
from northstar_quant.live import CommandUnknown, LiveClient, RuntimeUnavailable


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse(argv)
    engine = None
    try:
        if arguments.operation == "init-live-auth":
            from northstar_quant.apps.live.bootstrap import initialize_auth

            print(json.dumps(initialize_auth(arguments.directory), ensure_ascii=False))
            return 0
        if arguments.operation == "data-worker":
            from northstar_quant.apps.data_hub.worker import run

            run()
            return 0
        if arguments.scope == "serve":
            from northstar_quant.apps.launch import serve

            serve(arguments.operation, arguments.port)
            return 0
        if arguments.operation == "broker-sdk-check":
            from northstar_quant.broker.ctp import sdk_self_check

            status = sdk_self_check()
            print(json.dumps(status, ensure_ascii=False))
            return 0 if status["native_verified"] else 2
        if arguments.operation.startswith(("broker-", "stream-", "live-")):
            from northstar_quant.cli import broker_commands, stream_commands

            client = LiveClient.from_environment()
            try:
                if arguments.operation == "live-status":
                    print(json.dumps(client.status(), ensure_ascii=False))
                elif arguments.operation == "live-check":
                    observation = client.diagnostics()
                    print(json.dumps(observation, ensure_ascii=False))
                    return 0 if observation["status"] == "OK" else 2
                elif arguments.operation == "live-command-show":
                    print(json.dumps(client.command(arguments.request_id), ensure_ascii=False))
                elif arguments.operation == "broker-status":
                    print(json.dumps(client.broker.status(), ensure_ascii=False))
                elif arguments.operation.startswith("broker-opening-budget"):
                    return broker_commands.budget(arguments, client)
                elif (
                    arguments.operation.startswith("stream-")
                    or arguments.operation == "broker-catchup-stream"
                ):
                    return stream_commands.execute(arguments, client)
                else:
                    return broker_commands.execute(arguments, client)
                return 0
            finally:
                client.close()

        engine = open_database()
        if arguments.operation == "init-db":
            initialize_database(engine)
            print(json.dumps({"status": "ready"}))
            return 0
        if arguments.operation == "restore":
            from northstar_quant.apps.maintenance import restore

            data_root = os.environ.get("NORTHSTAR_DATA_DIR")
            if not data_root:
                raise ValueError("NORTHSTAR_DATA_DIR must be an explicitly prepared new directory")
            result = restore(
                engine,
                Path(data_root),
                arguments.backup.resolve(),
                storage_identity=os.environ.get("NORTHSTAR_STORAGE_ID"),
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0
        require_current_database(engine)
        from northstar_quant.cli import (
            data_commands,
            maintenance_commands,
            paper_commands,
            research_commands,
        )

        handlers = {
            "data": data_commands.execute,
            "research": research_commands.execute,
            "paper": paper_commands.execute,
            "maintenance": maintenance_commands.execute,
        }
        return handlers[arguments.scope](arguments, engine)
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

"""Bounded reception requests and observations; the CLI owns no receiving worker."""

from __future__ import annotations

import argparse
import json

from northstar_quant.live import LiveClient


def execute(arguments: argparse.Namespace, client: LiveClient) -> int:
    streams = client.streams
    if arguments.command == "stream-list":
        print(json.dumps(streams.list(), ensure_ascii=False))
    elif arguments.command == "stream-show":
        print(json.dumps(streams.get(arguments.stream_id), ensure_ascii=False))
    elif arguments.command == "stream-events":
        print(
            json.dumps(
                streams.events(arguments.stream_id, after=arguments.after),
                ensure_ascii=False,
            )
        )
    elif arguments.command == "broker-catchup-stream":
        progress = streams.catchup_account(
            arguments.stream_id,
            arguments.baseline_id,
            arguments.through_sequence,
            request_id=arguments.request_id,
        )
        print(json.dumps(progress, ensure_ascii=False))
        return 0 if progress["status"] == "READY" else 2
    elif arguments.command == "stream-archive":
        attempt = streams.archive(
            arguments.stream_id,
            through_sequence=arguments.through_sequence,
            session_open=arguments.session_open,
            session_close=arguments.session_close,
            request_id=arguments.request_id,
            allow_download=arguments.allow_download,
        )
        print(json.dumps(attempt, ensure_ascii=False))
        return 0 if attempt["status"] == "PUBLISHED" else 2
    elif arguments.command == "stream-control":
        print(
            json.dumps(
                streams.control(
                    arguments.stream_id, arguments.action, request_id=arguments.request_id
                ),
                ensure_ascii=False,
            )
        )
    else:
        result = streams.start(
            arguments.query_batch_id,
            arguments.configuration,
            request_id=arguments.request_id,
            duration_seconds=arguments.seconds,
            allow_retention=arguments.allow_retention,
            use_basis=arguments.use_basis,
        )
        print(json.dumps(result, ensure_ascii=False))
    return 0

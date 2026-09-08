"""User-facing command arguments, without runtime or SDK ownership."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID


def parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="northstar",
        description="Personal futures data, research and independently managed Live/Console",
    )
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
    serve = commands.add_parser("serve", help="serve the independent personal Console")
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
    broker_baseline = commands.add_parser(
        "broker-baseline", help="record one saved flat-account observation locally; no connection"
    )
    broker_baseline.add_argument("source_batch_id", type=UUID)
    broker_baseline.add_argument("--request-id", type=UUID, required=True)
    broker_compare = commands.add_parser(
        "broker-compare", help="compare an independent saved query; not ledger reconciliation"
    )
    broker_compare.add_argument("baseline_id", type=UUID)
    broker_compare.add_argument("query_batch_id", type=UUID)
    broker_compare.add_argument("--request-id", type=UUID, required=True)
    broker_context = commands.add_parser(
        "broker-baseline-context", help="read local baseline eligibility and saved comparisons"
    )
    broker_context.add_argument("query_batch_id", type=UUID)
    broker_ingest = commands.add_parser(
        "broker-ingest", help="apply saved broker fills to the local quantity ledger; no connection"
    )
    broker_ingest.add_argument("baseline_id", type=UUID)
    broker_ingest.add_argument("source_batch_id", type=UUID)
    broker_ingest.add_argument("--request-id", type=UUID, required=True)
    stream_ingest = commands.add_parser(
        "broker-ingest-stream",
        help="book confirmed trades from a saved stream prefix; no connection",
    )
    stream_ingest.add_argument("baseline_id", type=UUID)
    stream_ingest.add_argument("stream_id", type=UUID)
    stream_ingest.add_argument("--through-sequence", type=int, required=True)
    stream_ingest.add_argument("--request-id", type=UUID, required=True)
    stream_catchup = commands.add_parser(
        "broker-catchup-stream",
        help="process saved asynchronous account callbacks to a fixed bound; no connection",
    )
    stream_catchup.add_argument("baseline_id", type=UUID)
    stream_catchup.add_argument("stream_id", type=UUID)
    stream_catchup.add_argument("--through-sequence", type=int, required=True)
    funds_observe = commands.add_parser(
        "broker-funds", help="record cumulative account money from a saved query; no connection"
    )
    funds_observe.add_argument("baseline_id", type=UUID)
    funds_observe.add_argument("source_batch_id", type=UUID)
    funds_observe.add_argument("--request-id", type=UUID, required=True)
    funds_show = commands.add_parser("broker-funds-show", help="read a fixed account money entry")
    funds_show.add_argument("entry_id", type=UUID)
    broker_ledger = commands.add_parser(
        "broker-ledger", help="read local fill entries and independent position comparisons"
    )
    broker_ledger.add_argument("query_batch_id", type=UUID)
    broker_positions = commands.add_parser(
        "broker-positions", help="compare fixed ledger quantities with a later saved query"
    )
    broker_positions.add_argument("entry_id", type=UUID)
    broker_positions.add_argument("query_batch_id", type=UUID)
    broker_positions.add_argument("--request-id", type=UUID, required=True)
    broker_orders = commands.add_parser(
        "broker-orders", help="check saved order observations against recorded fills; no connection"
    )
    broker_orders.add_argument("position_check_id", type=UUID)
    broker_orders.add_argument("--request-id", type=UUID, required=True)
    opening_budget = commands.add_parser(
        "broker-opening-budget",
        help="budget one opening lot from a saved shadow target; never execution permission",
    )
    opening_budget.add_argument("stream_id", type=UUID)
    opening_budget.add_argument("--sequence", type=int, required=True)
    opening_budget.add_argument("--order-check", type=UUID, required=True)
    opening_budget.add_argument("--limit-price", required=True, help="exact decimal limit price")
    opening_budget.add_argument("--request-id", type=UUID, required=True)
    opening_budget_show = commands.add_parser(
        "broker-opening-budget-show", help="read one fixed historical opening budget; no connection"
    )
    opening_budget_show.add_argument("budget_id", type=UUID)
    commands.add_parser("stream-list", help="list saved continuous shadow sessions; no connection")
    stream_show = commands.add_parser("stream-show", help="read a saved continuous shadow session")
    stream_show.add_argument("stream_id", type=UUID)
    stream_events = commands.add_parser(
        "stream-events", help="read up to 100 original SDK callbacks"
    )
    stream_events.add_argument("stream_id", type=UUID)
    stream_events.add_argument("--after", type=int, default=0)
    stream_archive = commands.add_parser(
        "stream-archive",
        help="archive a fixed saved callback prefix and process minutes; no connection",
    )
    stream_archive.add_argument("stream_id", type=UUID)
    stream_archive.add_argument("--through-sequence", type=int, required=True)
    stream_archive.add_argument("--session-open", required=True, help="first minute start in UTC")
    stream_archive.add_argument("--session-close", required=True, help="last minute end in UTC")
    stream_archive.add_argument("--request-id", type=UUID, required=True)
    stream_archive.add_argument(
        "--allow-download",
        action="store_true",
        help="permit local download including account TD callbacks",
    )
    stream_start = commands.add_parser(
        "stream-start", help="start bounded reception in Live; command exit does not stop it"
    )
    stream_start.add_argument("query_batch_id", type=UUID)
    stream_start.add_argument("--configuration", required=True)
    stream_start.add_argument("--seconds", type=int, default=300)
    stream_start.add_argument("--allow-retention", action="store_true", required=True)
    stream_start.add_argument("--use-basis", required=True)
    stream_start.add_argument("--request-id", type=UUID, required=True)
    live = commands.add_parser("live", help="serve Live without connecting or arming at startup")
    live.add_argument("--port", type=int, default=18081)
    commands.add_parser("live-status", help="read Live identity, freshness and capabilities")
    commands.add_parser("live-check", help="read Live storage diagnostics; nonzero on degradation")
    receipt = commands.add_parser("live-command-show", help="query a fixed command; never resubmit")
    receipt.add_argument("request_id", type=UUID)
    setup = commands.add_parser(
        "init-live-auth", help="create private runtime authentication files"
    )
    setup.add_argument("directory", type=Path)
    control = commands.add_parser(
        "stream-control", help="PAUSE/RESUME shadow or request STOP; no orders"
    )
    control.add_argument("stream_id", type=UUID)
    control.add_argument("action", choices=("PAUSE", "RESUME", "STOP"))
    control.add_argument("--request-id", type=UUID, required=True)
    stream_catchup.add_argument("--request-id", type=UUID, required=True)
    return parser.parse_args(argv)

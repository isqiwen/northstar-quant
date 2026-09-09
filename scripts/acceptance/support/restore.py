"""Restore acceptance against a fresh database and its own installed Live owner."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import uuid4

from support.catalog import check_restored_catalog
from support.processes import InstalledApplication


def check_restore(
    executable,
    runtime,
    environment,
    parsed,
    run_id,
    snapshot_id,
    data,
    saved_queries,
    saved_streams,
    catalog_evidence,
):
    """Use a generated disposable restore database; never overwrite the source database."""

    target_name = "northstar_quant_restore_test_" + uuid4().hex[:12]
    pg_environment = dict(environment, PGPASSWORD=parsed.password or "")
    pg_arguments = [
        "--no-password",
        f"--host={parsed.hostname}",
        f"--port={parsed.port or 5432}",
        f"--username={parsed.username}",
        target_name,
    ]
    subprocess.run(
        ["createdb", *pg_arguments], env=pg_environment, check=True, capture_output=True, timeout=30
    )
    restored = dict(
        environment,
        NORTHSTAR_DATABASE_URL=environment["NORTHSTAR_DATABASE_URL"].rsplit("/", 1)[0]
        + "/"
        + target_name,
        NORTHSTAR_DATA_DIR=str(runtime / "restored-sources"),
    )

    application = InstalledApplication(executable, runtime, restored)
    application.environment["NORTHSTAR_LOG_DIR"] = str(runtime / "restored-logs")
    command = application.command

    try:
        manifest = json.loads((runtime / "backup/manifest.json").read_bytes())
        identity = manifest["sources"][0]["content_hash"]
        referenced = runtime / "backup/sources/objects" / identity[:2] / identity
        unavailable = runtime / "missing-source.saved"
        referenced.rename(unavailable)
        try:
            missing = subprocess.run(
                [executable, "restore", str(runtime / "backup")],
                env=restored,
                cwd=runtime,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert missing.returncode != 0, "missing referenced bytes must reject restoration"
            assert not Path(restored["NORTHSTAR_DATA_DIR"]).exists(), (
                "preflight must not activate partial restore"
            )
        finally:
            unavailable.rename(referenced)
        result = command("maintenance", "restore", str(runtime / "backup"))
        assert result["status"] == "restored" and result["execution"] == "PAUSED"
        evidence = result["evidence"]
        assert set(evidence) == {
            "query_batches_count",
            "pending_queries_count",
            "baselines_count",
            "checks_count",
            "position_entries_count",
            "position_checks_count",
            "order_checks_count",
            "streams_count",
        }
        assert all(type(count) is int and count >= 0 for count in evidence.values())
        assert evidence["query_batches_count"] >= len(saved_queries)
        assert evidence["streams_count"] >= len(saved_streams)
        if not saved_queries:
            assert evidence == {
                "query_batches_count": 0,
                "pending_queries_count": 0,
                "baselines_count": 0,
                "checks_count": 0,
                "position_entries_count": 0,
                "position_checks_count": 0,
                "order_checks_count": 0,
                "streams_count": 0,
            }
        # Remote broker reads must target this restored database, not the original Live.
        with application.live():
            assert command("advanced", "broker", "list") == saved_queries
            assert [
                item["stream_id"] for item in command("advanced", "stream", "list")
            ] == saved_streams
        application.environment["NORTHSTAR_RESEARCH_DATABASE"] = str(
            runtime / "restored-research.sqlite3"
        )
        for name in ("MARKET", "RESEARCH"):
            application.environment[f"NORTHSTAR_{name}_DIR"] = str(
                runtime / ("recovered-" + name.lower())
            )
            application.environment[f"NORTHSTAR_{name}_STORAGE_ID"] = str(uuid4())
        subprocess.run(
            [executable, "maintenance", "restore", str(runtime / "research-backup")],
            env=dict(application.environment, NORTHSTAR_DATABASE_OWNER="research"),
            cwd=runtime,
            check=True,
            capture_output=True,
            timeout=60,
        )
        with application.api("data-api"):
            check_restored_catalog(application, catalog_evidence)
        assert command("data", "dataset", snapshot_id) == data
        assert command("research", "replay", run_id)["run_id"] == run_id
        assert all(item["file_status"] == "AVAILABLE" for item in command("data", "sources"))
        print(
            "Joint restore: empty database, retained bytes, source/processing/publication "
            "and exact research reuse plus saved broker evidence verification passed",
            flush=True,
        )
    finally:
        subprocess.run(
            ["dropdb", *pg_arguments],
            env=pg_environment,
            check=True,
            capture_output=True,
            timeout=30,
        )

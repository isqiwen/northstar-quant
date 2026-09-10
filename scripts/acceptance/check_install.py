"""Run the installed CLI and HTTP workflow against an explicit disposable database.

Runs with the installed interpreter and launches the installed entrypoint from
an empty working directory; setup imports resolve from that installed package. The study is the
synthetic intraday example; no application database is reset or deleted.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from support.broker import check_broker_access
from support.catalog import check_catalog
from support.evidence import save as save_evidence
from support.processes import InstalledApplication
from support.restore import check_restore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path, help="path to backend/tests/data/intraday.toml")
    study = parser.parse_args().study.resolve()
    if sys.flags.optimize:
        parser.error("run without -O or PYTHONOPTIMIZE so acceptance assertions are enforced")
    database_url = os.environ.get("NORTHSTAR_TEST_DATABASE_URL", "")
    parsed = urlsplit(database_url)
    if (
        parsed.scheme != "postgresql+psycopg"
        or parsed.path != "/northstar_quant_test"
        or parsed.query
        or parsed.fragment
    ):
        parser.error("NORTHSTAR_TEST_DATABASE_URL must name disposable northstar_quant_test")

    environment = dict(os.environ, NORTHSTAR_DATABASE_URL=database_url)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    # Acceptance never inherits private operator credentials or connects to a broker.
    for key in tuple(environment):
        if key.startswith("NORTHSTAR_SIMNOW_"):
            environment.pop(key)
    environment["NORTHSTAR_LIVE_ENVIRONMENT"] = "simnow_dev"
    environment.pop("NORTHSTAR_LIVE_AUTH", None)
    environment.pop("NORTHSTAR_LIVE_URL", None)
    executable = str(Path(sys.executable).parent / "northstar")
    settings = tomllib.loads(study.read_text("utf-8"))
    source = dict(settings["source"])
    source_file = source.pop("file")
    csv = (study.parent / source_file).read_bytes()
    with tempfile.TemporaryDirectory(prefix="northstar-install-") as directory:
        runtime = Path(directory)
        environment["NORTHSTAR_DATA_DIR"] = str(runtime / "sources")
        from northstar_quant.data_management.storage_identity import initialize

        for share in ("MARKET", "RESEARCH"):
            root = runtime / share.lower()
            root.mkdir()
            identity = str(uuid4())
            initialize(root, identity)
            environment[f"NORTHSTAR_{share}_DIR"] = str(root)
            environment[f"NORTHSTAR_{share}_STORAGE_ID"] = identity

        research_study = runtime / "research-only.toml"
        research_study.write_text(study.read_text("utf-8"), encoding="utf-8")
        assert not (runtime / source_file).exists(), "reuse check must not have the source CSV"

        application = InstalledApplication(executable, runtime, environment)
        command, request = application.command, application.request

        outcome = "failed"
        try:
            assert command("maintenance", "init-db") == {"status": "ready"}
            assert command("maintenance", "init-db") == {"status": "ready"}
            authentication = command("maintenance", "init-auth", str(runtime / "auth"))
            auth_bytes = {
                name: Path(authentication[name]).read_bytes() for name in ("live_auth", "web_auth")
            }
            assert command("maintenance", "init-auth", str(runtime / "auth")) == authentication
            assert all(
                Path(authentication[name]).read_bytes() == value
                for name, value in auth_bytes.items()
            )
            application.environment["NORTHSTAR_LIVE_AUTH"] = authentication["web_auth"]
            with application.live() as live_process:
                live_status = command("status")
                application.assert_live(live_process, live_status)
                broker_setup = command("advanced", "broker", "status")
                assert not broker_setup["credentials"]["configured"], broker_setup
                assert broker_setup["execution"] == {
                    "order_sending": False,
                    "cancel_sending": False,
                }
                if broker_setup["sdk"]["supported"]:
                    checked_sdk = command("advanced", "broker", "sdk-check")
                    assert checked_sdk["native_verified"], checked_sdk
                    assert checked_sdk["trader_api_version"] and checked_sdk["market_api_version"]
                    print(
                        "Installed native CTP: query structs, topics and release passed offline",
                        flush=True,
                    )
                runs_before_import = command("research", "list")
                attempt = application.seed_study(study)
                assert attempt["status"] == "PUBLISHED", attempt
                snapshot_id = attempt["snapshot_id"]
                data = command("data", "dataset", snapshot_id)
                assert command("research", "list") == runs_before_import
                repeated = application.seed_study(study)
                committed = not attempt["code_revision"].endswith("-dirty")
                assert (repeated["snapshot_id"] == snapshot_id) is committed
                assert repeated["attempt_id"] != attempt["attempt_id"]
                assert (
                    sum(item["snapshot_id"] == snapshot_id for item in command("data", "datasets"))
                    == 1
                )
                assert command("data", "dataset", snapshot_id) == data
                saved = command("research", "run", snapshot_id, "--study", str(research_study))
                run_id = saved["run_id"]
                # Read-only result inspection must work when source storage is
                # unavailable; it must not construct a data or Paper workspace.
                unavailable_sources = runtime / "unavailable-sources"
                unavailable_sources.write_text("not a source directory", encoding="utf-8")
                source_root = application.environment["NORTHSTAR_DATA_DIR"]
                try:
                    application.environment["NORTHSTAR_DATA_DIR"] = str(unavailable_sources)
                    assert command("research", "show", run_id) == saved
                    assert any(item["run_id"] == run_id for item in command("research", "list"))
                finally:
                    application.environment["NORTHSTAR_DATA_DIR"] = source_root
                assert saved["snapshot"]["id"] == snapshot_id
                assert saved["result"]["data"] == data
                summary = saved["result"]["summary"]
                assert summary["bar_count"] == 12, summary
                assert summary["decision_count"] == 11, summary
                assert summary["fill_count"] == 7, summary
                assert Decimal(summary["total_fees"]) == Decimal(70), summary
                assert Decimal(summary["ending_equity"]) == Decimal(94580), summary
                assert Decimal(summary["ending_equity"]) == (
                    Decimal(summary["initial_cash"])
                    + Decimal(summary["realized_pnl"])
                    + Decimal(summary["unrealized_pnl"])
                    - Decimal(summary["total_fees"])
                ), summary
                assert (
                    command("research", "run", snapshot_id, "--study", str(research_study)) == saved
                )
                reimported = application.seed_study(study)
                reimported_run = command(
                    "research", "run", reimported["snapshot_id"], "--study", str(study)
                )
                assert (reimported_run["run_id"] == run_id) is saved["committed_code"]
                assert reimported_run["result"]["summary"] == summary
                assert command("research", "replay", run_id) == saved
                print(
                    "Installed CLI: data library, source-free research, accounting, repeat and "
                    "result replay passed (dirty builds provide development checks only)",
                    flush=True,
                )

                configuration = command(
                    "research",
                    "configure",
                    str(research_study),
                    "--name",
                    "Installed Paper",
                )
                paper_id = str(uuid4())
                paper = command(
                    "research",
                    "paper",
                    "create",
                    snapshot_id,
                    configuration["configuration_id"],
                    "--request-id",
                    paper_id,
                )
                assert paper["status"] == "PAUSED" and paper["cursor"] == 0
                assert paper["input_type"] == "FILE_REPLAY"
                assert paper["configuration"] == configuration
                step_command = str(uuid4())
                first_step = command(
                    "research", "paper", "next", paper_id, "--request-id", step_command
                )
                assert (
                    command(
                        "research",
                        "paper",
                        "next",
                        paper_id,
                        "--request-id",
                        step_command,
                    )
                    == first_step
                )
                paused_paper = command("research", "paper", "show", paper_id)
                assert paused_paper["cursor"] == 1 and paused_paper["status"] == "PAUSED"

                # Admit while no worker exists, close the entire API, then process.
                with application.api("data-api") as intake:
                    pending = application.seed_source(
                        {
                            "content_base64": base64.b64encode(csv).decode(),
                            "filename": source_file,
                            "source_name": source["source_name"],
                            "use_basis": settings["archive"]["use_basis"],
                            "allow_retention": True,
                            "allow_download": True,
                            "input_kind": "RECEIVED_CSV",
                            "spec": source,
                            "request_id": str(uuid4()),
                        }
                    )
                    assert pending["status"] == "PENDING"
                assert application.api_processes[intake].poll() is not None
                with application.data_worker() as owner:
                    completed = application.await_attempt(pending)
                    assert completed["status"] == "PUBLISHED"
                    with application.api("data-api") as restarted:
                        assert owner.poll() is None
                        assert (
                            json.loads(
                                request(restarted + f"/api/attempts/{completed['attempt_id']}")
                            )
                            == completed
                        )
                print(
                    "Installed Data: synthetic processing completed with API stopped",
                    flush=True,
                )

                with (
                    application.data_worker(),
                    application.api() as live_url,
                    application.api("data-api") as base_url,
                    application.api("research-api") as research_url,
                ):
                    application.assert_live(live_process, live_status, live_url)
                    check_broker_access(application, live_url, configuration)
                    upload = {
                        "content_base64": base64.b64encode(csv).decode("ascii"),
                        "filename": source_file,
                        "source_name": source["source_name"],
                        "use_basis": settings["archive"]["use_basis"],
                        "allow_retention": True,
                        "allow_download": True,
                        "input_kind": "RECEIVED_CSV",
                        "upstream_source_id": None,
                        "transformation_note": None,
                        "spec": source,
                        "request_id": str(uuid4()),
                    }
                    imported = application.seed_source(upload)
                    assert imported["status"] in {"PENDING", "RUNNING", "PUBLISHED"}, imported
                    imported = application.await_attempt(imported)
                    assert imported["status"] == "PUBLISHED", imported
                    assert (imported["snapshot_id"] == snapshot_id) is saved["committed_code"]
                    assert (
                        request(f"{base_url}/api/sources/{imported['source_id']}/download") == csv
                    )
                    assert application.seed_source(upload) == imported
                    invalid = dict(
                        upload, request_id=str(uuid4()), spec=dict(source, price_tick="invalid")
                    )
                    failed = application.seed_source(invalid)
                    failed = application.await_attempt(failed)
                    assert failed["status"] == "FAILED", failed
                    assert request(f"{base_url}/api/attempts/{failed['attempt_id']}")
                    repaired = application.seed_source({**upload, "request_id": str(uuid4())})
                    repaired = application.await_attempt(repaired)
                    assert repaired["status"] == "PUBLISHED"
                    assert (repaired["snapshot_id"] == snapshot_id) is saved["committed_code"]
                    datasets = json.loads(request(f"{base_url}/api/datasets"))
                    assert sum(item["snapshot_id"] == snapshot_id for item in datasets) == 1
                    assert json.loads(request(f"{base_url}/api/datasets/{snapshot_id}")) == data
                    submitted = application.submit_research(
                        research_url, imported["snapshot_id"], settings["research"]
                    )
                    assert (submitted["run_id"] == run_id) is saved["committed_code"]
                    submitted_run = json.loads(
                        request(f"{research_url}/api/runs/{submitted['run_id']}")
                    )
                    assert submitted_run["result"]["summary"] == summary
                    catalog_evidence = check_catalog(
                        request, research_url, snapshot_id, configuration, run_id
                    )
                    assert json.loads(request(f"{research_url}/api/runs/{run_id}")) == saved
                    assert request(f"{base_url}/health/ready")
                    assert (
                        json.loads(request(f"{research_url}/api/paper/{paper_id}")) == paused_paper
                    )
                application.assert_live(live_process, live_status)
                print(
                    "Installed HTTP: data queries, publication API, source evidence, research and "
                    "report passed",
                    flush=True,
                )

                with application.api("data-api"), application.api("research-api") as base_url:
                    application.assert_live(live_process, live_status)
                    assert json.loads(request(f"{base_url}/api/paper/{paper_id}")) == paused_paper
                    datasets = json.loads(request(f"{base_url}/api/datasets"))
                    selected = next(item for item in datasets if item["snapshot_id"] == snapshot_id)
                    assert (
                        json.loads(request(f"{base_url}/api/datasets/{selected['snapshot_id']}"))
                        == data
                    )
                    resumed = application.submit_research(
                        base_url, selected["snapshot_id"], settings["research"]
                    )
                    assert resumed["run_id"] == run_id
                    assert json.loads(request(f"{base_url}/api/runs/{run_id}")) == saved
                    listed = json.loads(request(f"{base_url}/api/runs"))
                    assert sum(item["run_id"] == run_id for item in listed) == 1
                application.assert_live(live_process, live_status)
                with application.api() as base_url:
                    application.assert_live(live_process, live_status, base_url)
                    assert request(f"{base_url}/health/ready")
                application.assert_live(live_process, live_status)
                assert len(set(application.web_pids)) == len(application.web_pids)
                assert live_process.pid not in application.web_pids
                print(
                    "Installed process isolation: independent application restarts preserved the "
                    "same Live PID/runtime and fresh CLI/HTTP status; no broker connected",
                    flush=True,
                )
                assert command("research", "show", run_id) == saved
                assert command("research", "replay", run_id) == saved
                assert command("data", "dataset", snapshot_id) == data
                assert (
                    command("research", "run", snapshot_id, "--study", str(research_study)) == saved
                )
                for _ in range(1, summary["bar_count"]):
                    command("research", "paper", "next", paper_id, "--request-id", str(uuid4()))
                complete_paper = command("research", "paper", "show", paper_id)
                assert complete_paper["status"] == "COMPLETED"
                assert complete_paper["summary"] == summary
                assert complete_paper["fills"] == saved["result"]["fills"]
                assert complete_paper["pending_order"] == saved["result"]["pending_order"]
                print(
                    "Installed Paper: fixed configuration, one-step retries, paused process "
                    "restart and batch-account equivalence passed",
                    flush=True,
                )
                print(
                    "Process restart: fixed dataset, persisted source/result and result "
                    "replay passed",
                    flush=True,
                )
                export = runtime / "retained.csv"
                command("data", "download", attempt["source_id"], str(export))
                assert export.read_bytes() == csv
                backup = command("maintenance", "backup", str(runtime / "backup"))
                assert backup["sources"]
                assert (
                    hashlib.sha256((runtime / "backup/database.dump").read_bytes()).hexdigest()
                    == backup["database_sha256"]
                )
                subprocess.run(
                    [executable, "maintenance", "backup", str(runtime / "research-backup")],
                    env=dict(application.environment, NORTHSTAR_DATABASE_OWNER="research"),
                    cwd=runtime,
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
                check_restore(
                    executable,
                    runtime,
                    application.environment,
                    parsed,
                    run_id,
                    snapshot_id,
                    data,
                    command("advanced", "broker", "list"),
                    [item["stream_id"] for item in command("advanced", "stream", "list")],
                    catalog_evidence,
                )
                print(json.dumps({"run_id": run_id, "summary": summary}, ensure_ascii=False))
            assert live_process.poll() is not None
            application.assert_unavailable()
            application.assert_file_logs()
            print(
                "Installed offline state: Live Web remains available but reports stopped Live "
                "as unavailable; no replacement owner is started",
                flush=True,
            )
            outcome = "passed"
        except Exception:
            print(application.logs(), file=sys.stderr)
            raise
        finally:
            save_evidence(runtime, executable, application.environment, outcome)


if __name__ == "__main__":
    main()

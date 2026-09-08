"""Run the installed CLI and HTTP workflow against an explicit disposable database.

Uses only the standard library, never imports application source, and launches
the installed entrypoint from an empty working directory. The study is the
synthetic intraday example; no application database is reset or deleted.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import tempfile
import tomllib
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from install_check.broker import check_broker_access
from install_check.processes import InstalledApplication
from install_check.restore import check_restore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path, help="path to examples/intraday.toml")
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
    environment.pop("NORTHSTAR_SIMNOW_CONFIG", None)
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
        research_study = runtime / "research-only.toml"
        research_study.write_text(study.read_text("utf-8"), encoding="utf-8")
        assert not (runtime / source_file).exists(), "reuse check must not have the source CSV"

        application = InstalledApplication(executable, runtime, environment)
        command, request = application.command, application.request

        try:
            assert command("init-db") == {"status": "ready"}
            assert command("init-db") == {"status": "ready"}
            authentication = command("init-live-auth", str(runtime / "auth"))
            application.environment["NORTHSTAR_LIVE_AUTH"] = authentication["web_auth"]
            with application.live() as live_process:
                live_status = command("live-status")
                application.assert_live(live_process, live_status)
                broker_setup = command("broker-status")
                assert not broker_setup["credentials"]["configured"], broker_setup
                assert broker_setup["execution"] == {
                    "order_sending": False,
                    "cancel_sending": False,
                }
                if broker_setup["sdk"]["supported"]:
                    checked_sdk = command("broker-sdk-check")
                    assert checked_sdk["native_verified"], checked_sdk
                    assert checked_sdk["trader_api_version"] and checked_sdk["market_api_version"]
                    print(
                        "Installed native CTP: query structs, topics and release passed offline",
                        flush=True,
                    )
                runs_before_import = command("list")
                attempt = command("import", str(study))
                assert attempt["status"] == "PUBLISHED", attempt
                snapshot_id = attempt["snapshot_id"]
                data = command("dataset", snapshot_id)
                assert command("list") == runs_before_import
                repeated = command("import", str(study))
                assert repeated["snapshot_id"] == snapshot_id
                assert repeated["attempt_id"] != attempt["attempt_id"]
                assert sum(item["snapshot_id"] == snapshot_id for item in command("datasets")) == 1
                assert command("dataset", snapshot_id) == data
                saved = command("research", snapshot_id, "--study", str(research_study))
                run_id = saved["run_id"]
                assert saved["snapshot"]["id"] == snapshot_id
                assert saved["result"]["data"] == data
                summary = saved["result"]["summary"]
                assert summary["bar_count"] == 12, summary
                assert summary["decision_count"] == 11, summary
                assert summary["fill_count"] == 7, summary
                assert Decimal(summary["total_fees"]) == Decimal("70"), summary
                assert Decimal(summary["ending_equity"]) == Decimal("94580"), summary
                assert Decimal(summary["ending_equity"]) == (
                    Decimal(summary["initial_cash"])
                    + Decimal(summary["realized_pnl"])
                    + Decimal(summary["unrealized_pnl"])
                    - Decimal(summary["total_fees"])
                ), summary
                assert command("research", snapshot_id, "--study", str(research_study)) == saved
                assert command("run", str(study)) == saved
                assert command("replay", run_id) == saved
                print(
                    "Installed CLI: data library, source-free research, accounting, repeat and "
                    "exact replay passed",
                    flush=True,
                )

                configuration = command(
                    "configure", str(research_study), "--name", "Installed Paper"
                )
                paper_id = str(uuid4())
                paper = command(
                    "paper-create",
                    snapshot_id,
                    configuration["configuration_id"],
                    "--request-id",
                    paper_id,
                )
                assert paper["status"] == "PAUSED" and paper["cursor"] == 0
                assert paper["input_type"] == "FILE_REPLAY"
                assert paper["configuration"] == configuration
                step_command = str(uuid4())
                first_step = command("paper-next", paper_id, "--request-id", step_command)
                assert command("paper-next", paper_id, "--request-id", step_command) == first_step
                paused_paper = command("paper-show", paper_id)
                assert paused_paper["cursor"] == 1 and paused_paper["status"] == "PAUSED"

                with (
                    application.web() as live_url,
                    application.web("data-hub") as base_url,
                    application.web("research-web") as research_url,
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
                    imported = json.loads(request(f"{base_url}/api/import", upload))
                    assert imported["status"] == "PUBLISHED", imported
                    assert imported["snapshot_id"] == saved["snapshot"]["id"]
                    assert (
                        request(f"{base_url}/api/sources/{imported['source_id']}/download") == csv
                    )
                    assert json.loads(request(f"{base_url}/api/import", upload)) == imported
                    invalid = dict(
                        upload, request_id=str(uuid4()), spec=dict(source, price_tick="invalid")
                    )
                    failed = json.loads(request(f"{base_url}/api/import", invalid))
                    assert failed["status"] == "FAILED", failed
                    assert request(f"{base_url}/attempts/{failed['attempt_id']}")
                    repaired = json.loads(
                        request(
                            f"{base_url}/api/sources/{failed['source_id']}/reprocess",
                            {"spec": source, "request_id": str(uuid4())},
                        )
                    )
                    assert (
                        repaired["status"] == "PUBLISHED" and repaired["snapshot_id"] == snapshot_id
                    )
                    datasets = json.loads(request(f"{base_url}/api/datasets"))
                    assert sum(item["snapshot_id"] == snapshot_id for item in datasets) == 1
                    assert json.loads(request(f"{base_url}/api/datasets/{snapshot_id}")) == data
                    submitted = json.loads(
                        request(
                            f"{research_url}/api/runs",
                            {
                                "snapshot_id": imported["snapshot_id"],
                                "config": settings["research"],
                            },
                        )
                    )
                    assert submitted["run_id"] == run_id
                    assert json.loads(request(f"{research_url}/api/runs/{run_id}")) == saved
                    assert source["symbol"] in request(f"{base_url}/datasets/{snapshot_id}").decode(
                        "utf-8"
                    )
                    assert source["symbol"] in request(f"{research_url}{submitted['url']}").decode(
                        "utf-8"
                    )
                    assert request(f"{base_url}/")
                    assert request(f"{base_url}/assets/app.js")
                    assert request(f"{base_url}/assets/app.css")
                    assert (
                        json.loads(request(f"{research_url}/api/paper/{paper_id}")) == paused_paper
                    )
                    assert source["symbol"] in request(f"{research_url}/paper/{paper_id}").decode(
                        "utf-8"
                    )
                application.assert_live(live_process, live_status)
                print(
                    "Installed HTTP: import, data library, source evidence, research and "
                    "report passed",
                    flush=True,
                )

                with application.web("research-web") as base_url:
                    application.assert_live(live_process, live_status)
                    assert json.loads(request(f"{base_url}/api/paper/{paper_id}")) == paused_paper
                    datasets = json.loads(request(f"{base_url}/api/datasets"))
                    selected = next(item for item in datasets if item["snapshot_id"] == snapshot_id)
                    assert (
                        json.loads(request(f"{base_url}/api/datasets/{selected['snapshot_id']}"))
                        == data
                    )
                    resumed = json.loads(
                        request(
                            f"{base_url}/api/runs",
                            {
                                "snapshot_id": selected["snapshot_id"],
                                "config": settings["research"],
                            },
                        )
                    )
                    assert resumed["run_id"] == run_id
                    assert json.loads(request(f"{base_url}/api/runs/{run_id}")) == saved
                    listed = json.loads(request(f"{base_url}/api/runs"))
                    assert sum(item["run_id"] == run_id for item in listed) == 1
                application.assert_live(live_process, live_status)
                with application.web() as base_url:
                    application.assert_live(live_process, live_status, base_url)
                    assert "SHADOW_ONLY" in request(f"{base_url}/streams").decode()
                application.assert_live(live_process, live_status)
                assert len(set(application.web_pids)) == 5
                assert live_process.pid not in application.web_pids
                print(
                    "Installed process isolation: independent application restarts preserved the "
                    "same Live PID/runtime and fresh CLI/HTTP status; no broker connected",
                    flush=True,
                )
                assert command("show", run_id) == saved
                assert command("replay", run_id) == saved
                assert command("dataset", snapshot_id) == data
                assert command("research", snapshot_id, "--study", str(research_study)) == saved
                for _ in range(1, summary["bar_count"]):
                    command("paper-next", paper_id, "--request-id", str(uuid4()))
                complete_paper = command("paper-show", paper_id)
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
                    "Process restart: dataset reuse, persisted source/result and exact "
                    "replay passed",
                    flush=True,
                )
                export = runtime / "retained.csv"
                command("download", attempt["source_id"], str(export))
                assert export.read_bytes() == csv
                backup = command("backup", str(runtime / "backup"))
                assert backup["sources"]
                assert (
                    hashlib.sha256((runtime / "backup/database.dump").read_bytes()).hexdigest()
                    == backup["database_sha256"]
                )
                check_restore(
                    executable,
                    runtime,
                    application.environment,
                    parsed,
                    run_id,
                    snapshot_id,
                    data,
                    command("broker-list"),
                    [item["stream_id"] for item in command("stream-list")],
                )
                print(json.dumps({"run_id": run_id, "summary": summary}, ensure_ascii=False))
            assert live_process.poll() is not None
            application.assert_unavailable()
            print(
                "Installed offline state: Live Web remains available but reports stopped Live "
                "as unavailable; no replacement owner is started",
                flush=True,
            )
        except Exception:
            print(application.logs(), file=sys.stderr)
            raise


if __name__ == "__main__":
    main()

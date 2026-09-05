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
import re
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener
from uuid import uuid4


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
    executable = str(Path(sys.executable).parent / "northstar")
    settings = tomllib.loads(study.read_text("utf-8"))
    source = dict(settings["source"])
    source_file = source.pop("file")
    csv = (study.parent / source_file).read_bytes()
    opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))

    def request(url: str, payload: object = None) -> bytes:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {} if body is None else {"Content-Type": "application/json"}
        if body is not None:
            parsed_url = urlsplit(url)
            with opener.open(f"{parsed_url.scheme}://{parsed_url.netloc}/", timeout=15) as page:
                html = page.read().decode()
            token = re.search(r'<meta name="northstar-csrf" content="([^"]+)"', html)
            assert token is not None, "workspace must provide a CSRF token"
            headers["X-Northstar-CSRF"] = token.group(1)
        with opener.open(Request(url, data=body, headers=headers), timeout=15) as response:
            return response.read()

    with tempfile.TemporaryDirectory(prefix="northstar-install-") as directory:
        runtime = Path(directory)
        environment["NORTHSTAR_DATA_DIR"] = str(runtime / "sources")
        log_path = runtime / "server.log"
        research_study = runtime / "research-only.toml"
        research_study.write_text(study.read_text("utf-8"), encoding="utf-8")
        assert not (runtime / source_file).exists(), "reuse check must not have the source CSV"

        def command(*arguments: str):
            completed = subprocess.run(
                [executable, *arguments],
                cwd=runtime,
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if completed.returncode:
                raise RuntimeError(f"northstar {arguments[0]} failed: {completed.stderr}")
            return json.loads(completed.stdout)

        @contextmanager
        def server() -> Iterator[str]:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            base_url = f"http://127.0.0.1:{port}"
            with log_path.open("a", encoding="utf-8") as log:
                process = subprocess.Popen(
                    [executable, "serve", "--port", str(port)],
                    cwd=runtime,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                try:
                    deadline = time.monotonic() + 30
                    while True:
                        if process.poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError("installed HTTP application did not become ready")
                        try:
                            if json.loads(request(f"{base_url}/health/ready")) == {
                                "status": "ready"
                            }:
                                break
                        except URLError:
                            pass
                        time.sleep(0.1)
                    yield base_url
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=10)
                            raise RuntimeError("installed HTTP application did not stop") from None

        try:
            broker_setup = command("broker-status")
            assert not broker_setup["credentials"]["configured"], broker_setup
            assert broker_setup["execution"] == {"order_sending": False, "cancel_sending": False}
            if broker_setup["sdk"]["supported"]:
                checked_sdk = command("broker-sdk-check")
                assert checked_sdk["native_verified"], checked_sdk
                assert checked_sdk["trader_api_version"] and checked_sdk["market_api_version"]
                print(
                    "Installed native CTP: query structs, topics and release passed offline",
                    flush=True,
                )
            assert command("init-db") == {"status": "ready"}
            assert command("init-db") == {"status": "ready"}
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

            configuration = command("configure", str(research_study), "--name", "Installed Paper")
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

            with server() as base_url:
                assert "SimNow" in request(f"{base_url}/broker").decode()
                broker_status = json.loads(request(f"{base_url}/api/broker/status"))
                assert not broker_status["credentials"]["configured"]
                assert broker_status["connection"] == "ON_DEMAND_READ_ONLY"
                saved_queries = command("broker-list")
                missing_query = str(uuid4())
                anonymous = build_opener(ProxyHandler({}))
                for path in (
                    f"/api/broker/queries/{missing_query}/baseline-context",
                    f"/api/broker/queries/{missing_query}/ledger-context",
                    f"/api/broker/position-entries/{missing_query}",
                    f"/api/broker/position-checks/{missing_query}",
                    f"/api/broker/order-checks/{missing_query}",
                ):
                    for browser, expected_status in ((anonymous, 403), (opener, 404)):
                        try:
                            with browser.open(f"{base_url}{path}", timeout=15):
                                pass
                        except HTTPError as error:
                            assert error.code == expected_status
                        else:
                            raise AssertionError("broker evidence requires a session and saved ID")
                for path, payload in (
                    (
                        "/api/broker/baselines",
                        {"source_batch_id": missing_query, "request_id": str(uuid4())},
                    ),
                    (
                        "/api/broker/baseline-checks",
                        {
                            "baseline_id": str(uuid4()),
                            "query_batch_id": missing_query,
                            "request_id": str(uuid4()),
                        },
                    ),
                    (
                        "/api/broker/position-entries",
                        {
                            "baseline_id": str(uuid4()),
                            "source_batch_id": missing_query,
                            "request_id": str(uuid4()),
                        },
                    ),
                    (
                        "/api/broker/position-checks",
                        {
                            "entry_id": str(uuid4()),
                            "query_batch_id": missing_query,
                            "request_id": str(uuid4()),
                        },
                    ),
                    (
                        "/api/broker/order-checks",
                        {"position_check_id": missing_query, "request_id": str(uuid4())},
                    ),
                ):
                    # Even an existing browser cookie cannot mutate without CSRF.
                    unprotected = Request(
                        f"{base_url}{path}",
                        data=json.dumps(payload).encode(),
                        headers={"Content-Type": "application/json"},
                    )
                    for browser in (anonymous, opener):
                        try:
                            with browser.open(unprotected, timeout=15):
                                pass
                        except HTTPError as error:
                            assert error.code == 403
                        else:
                            raise AssertionError("broker mutation requires a session and CSRF")
                assert command("broker-list") == saved_queries
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
                assert request(f"{base_url}/api/sources/{imported['source_id']}/download") == csv
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
                assert repaired["status"] == "PUBLISHED" and repaired["snapshot_id"] == snapshot_id
                datasets = json.loads(request(f"{base_url}/api/datasets"))
                assert sum(item["snapshot_id"] == snapshot_id for item in datasets) == 1
                assert json.loads(request(f"{base_url}/api/datasets/{snapshot_id}")) == data
                submitted = json.loads(
                    request(
                        f"{base_url}/api/runs",
                        {"snapshot_id": imported["snapshot_id"], "config": settings["research"]},
                    )
                )
                assert submitted["run_id"] == run_id
                assert json.loads(request(f"{base_url}/api/runs/{run_id}")) == saved
                assert source["symbol"] in request(f"{base_url}/datasets/{snapshot_id}").decode(
                    "utf-8"
                )
                assert source["symbol"] in request(f"{base_url}{submitted['url']}").decode("utf-8")
                assert request(f"{base_url}/")
                assert request(f"{base_url}/assets/app.js")
                assert request(f"{base_url}/assets/app.css")
                assert json.loads(request(f"{base_url}/api/paper/{paper_id}")) == paused_paper
                assert source["symbol"] in request(f"{base_url}/paper/{paper_id}").decode("utf-8")
            print(
                "Installed HTTP: import, data library, source evidence, research and report passed",
                flush=True,
            )

            with server() as base_url:
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
                        {"snapshot_id": selected["snapshot_id"], "config": settings["research"]},
                    )
                )
                assert resumed["run_id"] == run_id
                assert json.loads(request(f"{base_url}/api/runs/{run_id}")) == saved
                listed = json.loads(request(f"{base_url}/api/runs"))
                assert sum(item["run_id"] == run_id for item in listed) == 1
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
                "Installed Paper: fixed configuration, one-step retries, paused process restart "
                "and batch-account equivalence passed",
                flush=True,
            )
            print(
                "Process restart: dataset reuse, persisted source/result and exact replay passed",
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
            _check_restore(
                executable,
                runtime,
                environment,
                parsed,
                run_id,
                snapshot_id,
                data,
                command("broker-list"),
            )
            print(json.dumps({"run_id": run_id, "summary": summary}, ensure_ascii=False))
        except Exception:
            if log_path.exists():
                print(log_path.read_text("utf-8"), file=sys.stderr)
            raise


def _check_restore(
    executable, runtime, environment, parsed, run_id, snapshot_id, data, saved_queries
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

    def command(*arguments):
        result = subprocess.run(
            [executable, *arguments],
            env=restored,
            cwd=runtime,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode:
            raise RuntimeError(f"restore acceptance {arguments[0]} failed: {result.stderr}")
        return json.loads(result.stdout)

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
        result = command("restore", str(runtime / "backup"))
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
        }
        assert all(type(count) is int and count >= 0 for count in evidence.values())
        assert evidence["query_batches_count"] >= len(saved_queries)
        if not saved_queries:
            assert evidence == {
                "query_batches_count": 0,
                "pending_queries_count": 0,
                "baselines_count": 0,
                "checks_count": 0,
                "position_entries_count": 0,
                "position_checks_count": 0,
                "order_checks_count": 0,
            }
        assert command("broker-list") == saved_queries
        assert command("dataset", snapshot_id) == data
        assert command("replay", run_id)["run_id"] == run_id
        assert all(item["file_status"] == "AVAILABLE" for item in command("sources"))
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


if __name__ == "__main__":
    main()

"""Run the installed CLI and HTTP workflow against an explicit disposable database.

Uses only the standard library, never imports application source, and launches
the installed entrypoint from an empty working directory. The study is the
synthetic intraday example; no application database is reset or deleted.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener
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
    executable = str(Path(sys.executable).parent / "northstar")
    settings = tomllib.loads(study.read_text("utf-8"))
    source = dict(settings["source"])
    source_file = source.pop("file")
    csv = (study.parent / source_file).read_text("utf-8")
    opener = build_opener(ProxyHandler({}))

    def request(url: str, payload: object = None) -> bytes:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {} if body is None else {"Content-Type": "application/json"}
        with opener.open(Request(url, data=body, headers=headers), timeout=15) as response:
            return response.read()

    with tempfile.TemporaryDirectory(prefix="northstar-install-") as directory:
        runtime = Path(directory)
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
            assert command("init-db") == {"status": "ready"}
            assert command("init-db") == {"status": "ready"}
            runs_before_import = command("list")
            data = command("import", str(study))
            snapshot_id = data["snapshot_id"]
            assert command("list") == runs_before_import
            assert command("import", str(study)) == data
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
                imported = json.loads(
                    request(f"{base_url}/api/import", {"csv": csv, "spec": source})
                )
                assert imported["snapshot_id"] == saved["snapshot"]["id"]
                assert imported["content_hash"] == saved["snapshot"]["content_hash"]
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
            print(json.dumps({"run_id": run_id, "summary": summary}, ensure_ascii=False))
        except Exception:
            if log_path.exists():
                print(log_path.read_text("utf-8"), file=sys.stderr)
            raise


if __name__ == "__main__":
    main()

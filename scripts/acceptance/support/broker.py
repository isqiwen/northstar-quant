"""Installed Live Web authorization must still protect remote broker evidence and commands."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4

from support.processes import InstalledApplication


def check_broker_access(
    application: InstalledApplication, base_url: str, configuration: dict[str, Any]
) -> None:
    subprocess.run(
        [
            str(Path(application.executable).parent / "python"),
            "-c",
            "import json,sys; from northstar_quant.apps.storage import open_database; "
            "from northstar_quant.research.configurations import ConfigurationStore; "
            "from northstar_quant.research.configuration import ResearchConfig; "
            "v=json.load(sys.stdin); ConfigurationStore(open_database()).save_configuration("
            "v['name'],ResearchConfig.from_mapping(v['config']))",
        ],
        input=json.dumps(configuration),
        text=True,
        env=application.live_environment,
        check=True,
        capture_output=True,
        timeout=30,
    )
    command, request, opener = application.command, application.request, application.opener
    assert request(f"{base_url}/health/ready")
    broker_status = json.loads(request(f"{base_url}/api/broker/status"))
    assert not broker_status["credentials"]["configured"]
    assert broker_status["connection"] == "ON_DEMAND_READ_ONLY"
    saved_queries = command("advanced", "broker", "list")
    saved_streams = [item["stream_id"] for item in command("advanced", "stream", "list")]
    assert request(f"{base_url}/api/streams")
    assert [item["stream_id"] for item in command("advanced", "stream", "list")] == saved_streams
    missing_query = str(uuid4())
    anonymous = build_opener(ProxyHandler({}))
    for path in (
        f"/api/broker/queries/{missing_query}/baseline-context",
        f"/api/broker/queries/{missing_query}/ledger-context",
        f"/api/broker/position-entries/{missing_query}",
        f"/api/broker/funds-entries/{missing_query}",
        f"/api/broker/position-checks/{missing_query}",
        f"/api/broker/order-checks/{missing_query}",
        f"/api/broker/opening-budgets/{missing_query}",
        f"/api/streams/{missing_query}",
        f"/api/streams/{missing_query}/events",
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
            "/api/broker/funds-entries",
            {
                "baseline_id": str(uuid4()),
                "source_batch_id": missing_query,
                "request_id": str(uuid4()),
            },
        ),
        (
            f"/api/streams/{missing_query}/position-entries",
            {
                "baseline_id": str(uuid4()),
                "through_sequence": 1,
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
        (
            "/api/streams",
            {
                "query_batch_id": missing_query,
                "configuration_id": configuration["configuration_id"],
                "request_id": str(uuid4()),
                "duration_seconds": 300,
                "allow_retention": True,
                "use_basis": "Synthetic installation acceptance; never connect",
            },
        ),
        (
            f"/api/streams/{missing_query}/control",
            {"action": "RESUME", "request_id": str(uuid4())},
        ),
        (
            f"/api/streams/{missing_query}/archive",
            {
                "through_sequence": 1,
                "session_open": "2026-09-07T01:01:00Z",
                "session_close": "2026-09-07T01:03:00Z",
                "allow_download": False,
                "request_id": str(uuid4()),
            },
        ),
        (
            f"/api/streams/{missing_query}/opening-budgets",
            {
                "sequence": 1,
                "order_check_id": missing_query,
                "limit_price": "3101",
                "request_id": str(uuid4()),
            },
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
    assert command("advanced", "broker", "list") == saved_queries
    assert [item["stream_id"] for item in command("advanced", "stream", "list")] == saved_streams

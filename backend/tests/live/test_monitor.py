"""The observer reports failures without possessing or invoking trading authority."""

import json
import os
import selectors
import subprocess
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest

from northstar_quant.apps.live.bootstrap import initialize_auth
from northstar_quant.live.auth import LiveAuth
from northstar_quant.live.client import PROTOCOL_VERSION, LiveClient
from northstar_quant.live.instances import Instance
from northstar_quant.live.monitor import HealthMonitor


def test_read_only_observer_reports_failure_recovery_restart_and_output_retry(tmp_path):
    auth_paths = initialize_auth(tmp_path / "auth")
    auth = LiveAuth.from_file(Path(auth_paths["monitor_auth"]))
    assert auth.control_token is None
    assert initialize_auth(tmp_path / "auth") == auth_paths
    runtime = str(uuid4())
    state = {"fault": False, "wrong_runtime": False}
    events = []

    def transport(request):
        assert request.method == "GET"
        assert request.headers["authorization"] == "Bearer " + auth.read_token
        if state["fault"]:
            raise httpx2.ConnectError("never log private credentials or exception text")
        headers = {
            "x-northstar-protocol": PROTOCOL_VERSION,
            "x-live-runtime-id": str(uuid4()) if state["wrong_runtime"] else runtime,
            "x-live-observed-at": datetime.now(UTC).isoformat(),
        }
        value = (
            {"runtime_id": runtime, "status": "AVAILABLE"}
            if request.url.path == "/runtime"
            else {
                "status": "OK",
                "database": {"status": "REACHABLE", "disk_capacity": "OBSERVED"},
                "source_filesystem": {"status": "OK"},
            }
        )
        return httpx2.Response(200, json=value, headers=headers)

    client = LiveClient("http://127.0.0.1", auth, transport=httpx2.MockTransport(transport))
    monitor = HealthMonitor(Instance("sim", "simnow_trading"), events.append)
    try:
        monitor.poll(client, now=0)
        monitor.poll(client, now=1)
        assert len(events) == 1
        state["fault"] = True
        monitor.poll(client, now=2)
        monitor.poll(client, now=62)
        state["fault"] = False
        monitor.poll(client, now=63)
        runtime = str(uuid4())
        monitor.poll(client, now=64)
        state["wrong_runtime"] = True
        monitor.poll(client, now=65)
        assert [item["kind"] for item in events] == [
            "INITIAL", "FAULT", "HEARTBEAT", "RECOVERED", "RUNTIME_CHANGED", "FAULT"
        ]
        assert "private credentials" not in json.dumps(events)
        assert auth.read_token not in json.dumps(events)

        def broken(event):
            raise OSError("collector pipe unavailable")

        state["wrong_runtime"] = False
        monitor.emit = broken
        with pytest.raises(OSError):
            monitor.poll(client, now=66)
        monitor.emit = events.append
        monitor.poll(client, now=67)
        assert events[-1]["kind"] == "RECOVERED"
    finally:
        client.close()


def test_cli_process_observes_without_web_or_database(tmp_path):
    """Real process/HTTP/stdout boundary; scripted facts, not a broker acceptance."""
    auth_paths = initialize_auth(tmp_path / "auth")
    runtime = str(uuid4())
    state = {"fault": False}
    paths = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            paths.append(self.path)
            self.send_response(503 if state["fault"] else 200)
            self.send_header("x-northstar-protocol", PROTOCOL_VERSION)
            self.send_header("x-live-runtime-id", runtime)
            self.send_header("x-live-instance-id", "sim")
            self.send_header("x-live-observed-at", datetime.now(UTC).isoformat())
            self.end_headers()
            value = (
                {"runtime_id": runtime, "status": "AVAILABLE"}
                if self.path == "/runtime"
                else {
                    "status": "OK",
                    "database": {"status": "REACHABLE", "disk_capacity": "OBSERVED"},
                    "source_filesystem": {"status": "OK"},
                }
            )
            self.wfile.write(json.dumps(value).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    env = {
        **os.environ,
        "NORTHSTAR_LIVE_AUTH": auth_paths["monitor_auth"],
        "NORTHSTAR_LIVE_URL": f"http://127.0.0.1:{server.server_port}",
        "NORTHSTAR_LIVE_INSTANCE": "sim",
        "NORTHSTAR_BROKER_PROFILE": "simnow_trading",
        "NORTHSTAR_ENVIRONMENT": "SANDBOX",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "northstar_quant.cli", "serve", "live-monitor"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    selector = selectors.DefaultSelector()
    assert process.stdout is not None
    selector.register(process.stdout, selectors.EVENT_READ)

    def received():
        assert selector.select(timeout=15), "monitor did not emit its bounded observation"
        return json.loads(process.stdout.readline())

    try:
        assert received()["status"] == "HEALTHY"
        state["fault"] = True
        assert received()["kind"] == "FAULT"
        state["fault"] = False
        assert received()["kind"] == "RECOVERED"
        assert set(paths) == {"/runtime", "/diagnostics"}
        assert process.poll() is None
        process.terminate()
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        selector.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

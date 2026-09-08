"""Exercise the installed Live-only topology without broker credentials or personal data."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import httpx


class Deployment:
    def __init__(self, image: str) -> None:
        self.name = "northstar-live-check-" + uuid4().hex[:12]
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.password = secrets.token_urlsafe(32)
        self.environment = {
            key: value for key, value in os.environ.items() if not key.startswith("NORTHSTAR_")
        }
        self.environment.update(
            NORTHSTAR_LIVE_IMAGE=image,
            NORTHSTAR_LIVE_DATABASE_PASSWORD=self.password,
            NORTHSTAR_LIVE_KERNEL_MEMORY="1g",
            NORTHSTAR_LIVE_DATABASE_MEMORY="512m",
            NORTHSTAR_LIVE_WEB_PORT=str(self.port),
        )
        self.compose = Path(__file__).resolve().parents[1] / "deploy/live/compose.yaml"

    def run(self, *arguments: str) -> str:
        completed = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                os.devnull,
                "-p",
                self.name,
                "-f",
                str(self.compose),
                *arguments,
            ],
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if completed.returncode:
            detail = completed.stderr[-2000:].replace(self.password, "<REDACTED>")
            raise RuntimeError(f"isolated deployment {arguments[0]} failed: {detail}")
        return completed.stdout

    def wait_http(self, client: httpx.Client, path: str, code: int = 200) -> httpx.Response:
        deadline = monotonic() + 45
        while monotonic() < deadline:
            try:
                response = client.get(path)
                if response.status_code == code:
                    return response
            except httpx.TransportError:
                pass
            sleep(0.5)
        raise RuntimeError(f"isolated deployment did not return HTTP {code} for {path}")

    def exercise(self) -> None:
        self.run("up", "-d", "--no-build", "--wait", "--wait-timeout", "120")
        base = f"http://127.0.0.1:{self.port}"
        with httpx.Client(base_url=base, headers={"Origin": base}, timeout=4) as client:
            page = self.wait_http(client, "/")
            assert "Northstar Live" in page.text and "_nicegui" in page.text
            before = self.wait_http(client, "/api/live/status").json()
            identity = before["runtime_id"]
            kernel = self.run("ps", "-q", "live").strip()
            assert json.loads(self.run("exec", "-T", "live", "northstar", "stream-list")) == []
            self.run(
                "exec",
                "-T",
                "live-web",
                "python",
                "-c",
                "import os,socket\n"
                "assert not any(k in os.environ for k in "
                "('NORTHSTAR_DATABASE_URL','NORTHSTAR_DATA_DIR','NORTHSTAR_SIMNOW_CONFIG'))\n"
                "try: socket.create_connection(('postgres',5432),timeout=2)\n"
                "except OSError: print('Web cannot reach storage')\n"
                "else: raise RuntimeError('Web unexpectedly reached storage')",
            )
            self.run("restart", "live-web")
            self.wait_http(client, "/health/ready")
            assert client.get("/api/live/status").status_code == 403
            client.cookies.clear()
            self.wait_http(client, "/")
            after = self.wait_http(client, "/api/live/status").json()
            assert after["runtime_id"] == identity
            assert self.run("ps", "-q", "live").strip() == kernel
            print("Live-only installed page and Web restart: same independent kernel", flush=True)

            self.run("stop", "postgres")
            self.wait_http(client, "/api/live/status", 503)
            self.wait_http(client, "/health/ready")
            self.run("start", "postgres")
            recovered = self.wait_http(client, "/api/live/status").json()
            assert recovered["runtime_id"] == identity
            print(
                "Database outage: Web remains available, runtime fails closed then recovers",
                flush=True,
            )

            self.run("stop", "live")
            self.wait_http(client, "/api/live/status", 503)
            self.wait_http(client, "/health/ready")
            assert not self.run("ps", "-q", "live").strip()
            self.run("start", "live")
            restarted = self.wait_http(client, "/api/live/status").json()
            assert restarted["runtime_id"] != identity
            assert json.loads(self.run("exec", "-T", "live", "northstar", "stream-list")) == []
            print(
                "Kernel stop/restart: no Web-owned replacement and no broker connection", flush=True
            )
            print(json.dumps({"project": self.name, "status": "passed", "broker_connected": False}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Already-built Linux amd64 image")
    arguments = parser.parse_args()
    if not __debug__:
        parser.error("run without Python optimization; acceptance assertions must execute")
    deployment = Deployment(arguments.image)
    try:
        deployment.exercise()
    finally:
        # The generated project name is never user input or the personal project's name.
        deployment.run("down", "--volumes", "--timeout", "10")


if __name__ == "__main__":
    main()

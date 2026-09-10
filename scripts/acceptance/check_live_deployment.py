"""Exercise the installed Live-only topology without broker credentials or personal data."""

from __future__ import annotations

import argparse
import json
import os
import runpy
import secrets
import socket
import subprocess
import tempfile
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import httpx2 as httpx
from support.deployment import cleanup_files, isolated_compose

from northstar_quant.web.protobuf import decode, methods

lifecycle = runpy.run_path(str(Path(__file__).resolve().parents[1] / "operations/compose.py"))[
    "lifecycle"
]


class Deployment:
    def __init__(self, image: str, frontend_image: str) -> None:
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
            NORTHSTAR_LIVE_ENVIRONMENT="simnow_dev",
            NORTHSTAR_LIVE_FRONTEND_IMAGE=frontend_image,
            NORTHSTAR_LIVE_DATABASE_PASSWORD=self.password,
        )
        self.files = tempfile.TemporaryDirectory(prefix="northstar-live-files-")
        self.root = Path(self.files.name)
        self.image = image
        self.compose = isolated_compose(
            Path(__file__).resolve().parents[2] / "deploy/live/compose.yaml",
            self.root / "live.json",
            self.root,
            self.environment,
        )

        # Remap only the disposable acceptance project, never production configuration.
        config = json.loads(self.compose.read_text())
        config["services"]["live-web"]["ports"] = [
            {"target": 3000, "published": str(self.port), "host_ip": "127.0.0.1"}
        ]
        config["services"]["live-api"].pop("ports", None)
        self.compose.write_text(json.dumps(config))

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
                    if (
                        response.headers.get("content-type", "").startswith("application/protobuf")
                        and code == 200
                    ):
                        response._content = json.dumps(
                            decode(methods("live")[("GET", path)].output_type, response.content)
                        ).encode()
                    return response
            except httpx.TransportError:
                pass
            sleep(0.5)
        raise RuntimeError(
            f"isolated deployment did not return HTTP {code} for {path}: "
            f"last={response.status_code} {response.text[:200]}"
        )

    def exercise(self) -> None:
        self.run("up", "-d", "--no-build", "--wait", "--wait-timeout", "120")
        base = f"http://127.0.0.1:{self.port}"
        with httpx.Client(base_url=base, headers={"Origin": base}, timeout=4) as client:
            page = self.wait_http(client, "/")
            assert "NORTHSTAR" in page.text
            self.wait_http(client, "/api/browser-session")
            before = self.wait_http(client, "/api/live/status").json()
            identity = before["runtime_id"]
            kernel = self.run("ps", "-q", "live").strip()
            assert (
                json.loads(
                    self.run("exec", "-T", "live", "northstar", "advanced", "stream", "list")
                )
                == []
            )
            self.run(
                "exec",
                "-T",
                "live-api",
                "python",
                "-c",
                "import os,socket\n"
                "assert not any(k in os.environ for k in "
                "('NORTHSTAR_DATABASE_URL','NORTHSTAR_DATA_DIR','NORTHSTAR_SIMNOW_PASSWORD'))\n"
                "try: socket.create_connection(('postgres',5432),timeout=2)\n"
                "except OSError: print('Web cannot reach storage')\n"
                "else: raise RuntimeError('Web unexpectedly reached storage')",
            )
            self.run("restart", "live-api", "live-web")
            self.wait_http(client, "/health/ready")
            # Recreated management containers preserve the independent kernel identity.
            self.wait_http(client, "/api/browser-session")
            after = self.wait_http(client, "/api/live/status").json()
            assert after["runtime_id"] == identity
            assert self.run("ps", "-q", "live").strip() == kernel
            print("Live management restart: same independent kernel", flush=True)

            self.run("stop", "live-api", "live-web")
            assert self.run("ps", "-q", "live").strip() == kernel
            self.run("start", "live-api", "live-web")
            self.wait_http(client, "/api/browser-session")
            assert self.wait_http(client, "/api/live/status").json()["runtime_id"] == identity
            print("Live management stop/start: same independent kernel", flush=True)

            self.run("stop", "live-api")
            self.wait_http(client, "/health/ready")
            self.wait_http(client, "/api/live/status", 503)
            assert self.run("ps", "-q", "live").strip() == kernel
            self.run("start", "live-api")
            self.wait_http(client, "/api/browser-session")
            self.wait_http(client, "/api/live/status")

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
            assert (
                json.loads(
                    self.run("exec", "-T", "live", "northstar", "advanced", "stream", "list")
                )
                == []
            )
            print(
                "Kernel stop/restart: no Web-owned replacement and no broker connection", flush=True
            )
            for action in ("restart", "stop-start"):
                previous = self.wait_http(client, "/api/live/status").json()["runtime_id"]
                if action == "stop-start":
                    lifecycle([], "live", "stop", runner=self.run)
                    assert not self.run("ps", "-q").strip()
                    lifecycle([], "live", "start", runner=self.run)
                else:
                    lifecycle([], "live", "restart", runner=self.run)
                self.wait_http(client, "/api/browser-session")
                assert self.wait_http(client, "/api/live/status").json()["runtime_id"] != previous
                assert (
                    json.loads(
                        self.run("exec", "-T", "live", "northstar", "advanced", "stream", "list")
                    )
                    == []
                )
            print(
                "Whole Live restart and stop/start: kernel restarts without broker connection",
                flush=True,
            )

            self.run(
                "exec",
                "-T",
                "live",
                "python",
                "-c",
                "import json,pathlib,time; time.sleep(0.2); "
                "root=pathlib.Path('/var/log/northstar/live'); "
                "rows={name:[json.loads(x) "
                "for p in root.glob('northstar-live-'+name+'-????-??-??.log*') "
                "for x in p.read_text().splitlines()] "
                "for name in ('api','kernel')}; "
                "assert all(len({r['session'] for r in records if 'session' in r})>=2 "
                "for records in rows.values()); "
                "assert all(all(r['component']==name and r['application']=='live' "
                "for r in records) "
                "for name,records in rows.items())",
            )
            print(
                "Live file logs: separate API/kernel files survived container restarts", flush=True
            )
            print(json.dumps({"project": self.name, "status": "passed", "broker_connected": False}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Already-built Linux amd64 image")
    parser.add_argument("--frontend-image", required=True)
    arguments = parser.parse_args()
    if not __debug__:
        parser.error("run without Python optimization; acceptance assertions must execute")
    deployment = Deployment(arguments.image, arguments.frontend_image)
    try:
        deployment.exercise()
    except BaseException:
        try:
            diagnostic = deployment.run("logs", "--no-color", "--tail=100").replace(
                deployment.password, "<REDACTED>"
            )
            print(diagnostic, flush=True)
            if output := os.environ.get("NORTHSTAR_ACCEPTANCE_ARTIFACTS"):
                destination = Path(output) / (deployment.name + ".log")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(diagnostic)
        except (OSError, RuntimeError):
            print("Live acceptance container diagnostics unavailable", flush=True)
        raise
    finally:
        # The generated project name is never user input or the personal project's name.
        deployment.run("down", "--volumes", "--timeout", "10")
        cleanup_files(deployment.root, deployment.image)
        deployment.files.cleanup()


if __name__ == "__main__":
    main()

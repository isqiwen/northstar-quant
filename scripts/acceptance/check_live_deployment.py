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

from northstar_quant.web.passwords import hash_password
from northstar_quant.web.protobuf import decode, methods, pack

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
            NORTHSTAR_WORKSPACE_PASSWORD_HASH=hash_password(self.password),
            NORTHSTAR_LIVE_IMAGE=image,
            NORTHSTAR_LIVE_INSTANCES="sim:simnow_dev,other:simnow_trading",
            NORTHSTAR_LIVE_FRONTEND_IMAGE=frontend_image,
            # Synthetic identities only; starting a kernel never connects CTP.
            NORTHSTAR_SIMNOW_USER_ID="123456",
            NORTHSTAR_SIMNOW_PASSWORD="acceptance_only",
            NORTHSTAR_SIMNOW_APP_ID="acceptance_only",
            NORTHSTAR_SIMNOW_AUTH_CODE="acceptance_only",
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

    def login(self, client: httpx.Client) -> None:
        self.wait_http(client, "/api/browser-session")
        body = pack(
            methods("live")[("POST", "/api/login")].input_type, {"password": self.password}
        ).SerializeToString()
        response = client.post(
            "/api/login", content=body, headers={"Content-Type": "application/protobuf"}
        )
        response.raise_for_status()

    def exercise(self) -> None:
        self.run("up", "-d", "--no-build", "--wait", "--wait-timeout", "120")
        base = f"http://127.0.0.1:{self.port}"
        with httpx.Client(
            base_url=base, headers={"Origin": base, "X-Live-Instance-ID": "sim"}, timeout=4
        ) as client:
            page = self.wait_http(client, "/")
            assert "NORTHSTAR" in page.text
            self.login(client)
            before = self.wait_http(client, "/api/live/status").json()
            identity = before["runtime_id"]
            # Same account/environment is refused from another container, even
            # though it owns a different database. Its own environment is active.
            self.run(
                "exec",
                "-T",
                "other-live",
                "python",
                "-c",
                "from northstar_quant.live.account_ownership import AccountOwnership\n"
                "try:\n"
                "    lock = AccountOwnership('simnow_dev', '9999', '123456')\n"
                "except ValueError as error:\n"
                "    assert 'account already has an active Live instance' in str(error)\n"
                "else:\n"
                "    lock.close(); raise AssertionError('duplicate account owner admitted')\n",
            )
            print(
                "Duplicate account refused across independent kernel containers",
                flush=True,
            )
            kernel = self.run("ps", "-q", "sim-live").strip()
            assert (
                json.loads(
                    self.run("exec", "-T", "sim-live", "northstar", "advanced", "stream", "list")
                )
                == []
            )
            self.run(
                "exec",
                "-T",
                "live-api",
                "python",
                "-c",
                "import os,pathlib\n"
                "assert not any(k in os.environ for k in "
                "('NORTHSTAR_DATABASE_URL','NORTHSTAR_DATA_DIR','NORTHSTAR_SIMNOW_PASSWORD'))\n"
                "assert not pathlib.Path('/var/lib/northstar/state/live.sqlite').exists()\n"
                "print('Management cannot read kernel storage')",
            )
            self.run("restart", "live-api", "live-web")
            self.wait_http(client, "/health/ready")
            # Recreated management containers preserve the independent kernel identity.
            self.login(client)
            after = self.wait_http(client, "/api/live/status").json()
            assert after["runtime_id"] == identity
            assert self.run("ps", "-q", "sim-live").strip() == kernel
            print("Live management restart: same independent kernel", flush=True)

            self.run("stop", "live-api", "live-web")
            assert self.run("ps", "-q", "sim-live").strip() == kernel
            self.run("start", "live-api", "live-web")
            self.login(client)
            assert self.wait_http(client, "/api/live/status").json()["runtime_id"] == identity
            print("Live management stop/start: same independent kernel", flush=True)

            self.run("stop", "live-api")
            self.wait_http(client, "/health/ready")
            self.wait_http(client, "/api/live/status", 503)
            assert self.run("ps", "-q", "sim-live").strip() == kernel
            self.run("start", "live-api")
            self.login(client)
            self.wait_http(client, "/api/live/status")

            state = self.root / "state/live/instances/sim/database"
            database = state / "live.sqlite"
            hidden = state / "live.sqlite.unavailable"
            database.rename(hidden)
            try:
                self.wait_http(client, "/api/live/status", 503)
                self.wait_http(client, "/health/ready")
                assert (
                    client.get(
                        "/api/live/status", headers={"X-Live-Instance-ID": "other"}
                    ).status_code
                    == 200
                )
            finally:
                hidden.rename(database)
            self.run("restart", "sim-live")
            recovered = self.wait_http(client, "/api/live/status").json()
            assert recovered["runtime_id"] != identity
            identity = recovered["runtime_id"]
            print(
                "Local storage failure: management and other instance remain available", flush=True
            )

            other = client.get("/api/live/status", headers={"X-Live-Instance-ID": "other"})
            assert other.status_code == 200
            self.run("stop", "sim-live")
            assert (
                client.get("/api/live/status", headers={"X-Live-Instance-ID": "other"}).status_code
                == 200
            )
            self.wait_http(client, "/api/live/status", 503)
            self.wait_http(client, "/health/ready")
            assert not self.run("ps", "-q", "sim-live").strip()
            self.run("start", "sim-live")
            restarted = self.wait_http(client, "/api/live/status").json()
            assert restarted["runtime_id"] != identity
            assert (
                json.loads(
                    self.run("exec", "-T", "sim-live", "northstar", "advanced", "stream", "list")
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
                self.login(client)
                assert self.wait_http(client, "/api/live/status").json()["runtime_id"] != previous
                assert (
                    json.loads(
                        self.run(
                            "exec", "-T", "sim-live", "northstar", "advanced", "stream", "list"
                        )
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
                "sim-live",
                "python",
                "-c",
                "import json,pathlib,time; time.sleep(0.2); "
                "root=pathlib.Path('/var/log/northstar/live'); "
                "rows={name:[json.loads(x) "
                "for p in root.glob('northstar-live-'+name+'-????-??-??.log*') "
                "for x in p.read_text().splitlines()] "
                "for name in ('kernel',)}; "
                "assert all(len({r['session'] for r in records if 'session' in r})>=2 "
                "for records in rows.values()); "
                "assert all(all(r['component']==name and r['application']=='live' "
                "for r in records) "
                "for name,records in rows.items())",
            )
            print("Live instance kernel logs survived container restarts", flush=True)
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

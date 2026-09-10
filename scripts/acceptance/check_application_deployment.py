"""Exercise core PostgreSQL, Research SQLite and fixed publications over shared files.

Uses isolated directories; this does not verify physical multi-host infrastructure.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import runpy
import secrets
import subprocess
import tempfile
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import httpx2 as httpx
from support.deployment import cleanup_files, isolated_compose

from northstar_quant.data_management.storage_identity import initialize
from northstar_quant.web.passwords import hash_password
from northstar_quant.web.protobuf import decode, methods, pack

ROOT = Path(__file__).resolve().parents[2]


class Deployment:
    def __init__(self, image: str, data_image: str, research_image: str) -> None:
        prefix = "northstar-app-check-" + uuid4().hex[:12]
        self.projects = {
            app: f"{prefix}-{app.replace('_', '-')}" for app in ("database", "data_hub", "research")
        }
        self.files = tempfile.TemporaryDirectory(prefix="northstar-storage-check-")
        self.root = Path(self.files.name)
        for share in ("source", "market", "research", "backup", "pgdata"):
            (self.root / share).mkdir()
        self.image = image
        self.password = secrets.token_urlsafe(32)
        self.environment = {
            key: value for key, value in os.environ.items() if not key.startswith("NORTHSTAR_")
        }
        self.environment.update(
            NORTHSTAR_WORKSPACE_PASSWORD_HASH=hash_password(self.password),
            NORTHSTAR_BACKEND_IMAGE=image,
            NORTHSTAR_DATA_FRONTEND_IMAGE=data_image,
            NORTHSTAR_RESEARCH_FRONTEND_IMAGE=research_image,
            NORTHSTAR_DATABASE_ADMIN_PASSWORD=self.password,
            NORTHSTAR_DATA_HUB_DATABASE_PASSWORD=secrets.token_urlsafe(32),
        )

        self.bindings = {
            f"/opt/northstar/files/{share}": self.root / share
            for share in ("source", "market", "research", "backup")
        }
        self.bindings["/opt/northstar/state/data-hub/postgresql"] = self.root / "pgdata"

    def run(self, app: str, *arguments: str) -> str:
        compose = isolated_compose(
            ROOT / "deploy" / app / "compose.yaml",
            self.root / f"{app}.json",
            self.root,
            self.environment,
            self.bindings,
        )
        binder = runpy.run_path(str(ROOT / "scripts/operations/storage_bindings.py"))["bind"]
        owner = "research" if app == "research" else "data-hub"
        registry = self.root / "state" / owner / "bindings/storage.json"
        config = json.loads(compose.read_text())
        # These APIs are reached through their frontend or container exec. Extra
        # anonymous host ports can collide during concurrent Docker recreation.
        for service_name in ("data-api", "research-api"):
            if service_name in config["services"]:
                config["services"][service_name].pop("ports", None)
        try:
            identities, _ = binder(config, app, registry)
        except ValueError as error:
            raise RuntimeError(str(error)) from error
        for service in config["services"].values():
            service.setdefault("environment", {}).update(identities)
        compose.write_text(json.dumps(config))
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                os.devnull,
                "-p",
                self.projects[app],
                "-f",
                str(compose),
                *arguments,
            ],
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=240,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr[-3000:].replace(self.password, "<REDACTED>"))
        if app == "database" and arguments == ("run", "--rm", "initialize"):
            binder(config, app, registry, complete=True)
        return result.stdout

    def up(self, app: str) -> None:
        if app == "database":
            self.run(
                app,
                "up",
                "-d",
                "--no-build",
                "--wait",
                "--wait-timeout",
                "120",
                "postgres",
            )
            self.run(app, "run", "--rm", "initialize")
            return
        self.run(
            app, "up", "-d", "--no-build", "--force-recreate", "--wait", "--wait-timeout", "120"
        )
        if app == "data_hub":
            try:
                port = self.run(app, "port", "publications", "8080").strip().rsplit(":", 1)[1]
            except RuntimeError:
                print(self.run(app, "logs", "publications"), flush=True)
                raise
            self.environment["NORTHSTAR_DATA_HUB_URL"] = "http://host.docker.internal:" + port
            with httpx.Client(
                base_url="http://127.0.0.1:" + port, trust_env=False, timeout=10
            ) as remote:
                assert remote.get("/api/sync", headers={"Host": "127.0.0.1"}).status_code == 404
                assert remote.get("/api/sources", headers={"Host": "core.local"}).status_code == 404
                assert remote.get("/api/publications").status_code == 200
                assert remote.post("/api/publications").status_code == 403

    @contextmanager
    def client(self, app: str, service: str) -> Iterator[httpx.Client]:
        address = self.run(app, "port", service, "3000").strip()
        base = "http://" + address
        with httpx.Client(base_url=base, headers={"Origin": base}, timeout=30) as client:
            assert "NORTHSTAR" in client.get("/").text
            request(client, app, "/api/login", {"password": self.password})
            yield client

    def exercise(self) -> None:
        self.up("database")
        # Research starts with storage alone: Data Hub has never been started.
        self.up("data_hub")
        self.up("research")
        with self.client("research", "research") as research:
            assert request(research, "research", "/api/datasets") == []
            self.run("data_hub", "stop", "data-worker")
            settings = tomllib.loads((ROOT / "backend/tests/data/intraday.toml").read_text())
            source = settings["source"]
            filename = source.pop("file")
            with self.client("data_hub", "data-hub") as data:
                # Seed synthetic research evidence inside the installed test container.
                # Data Hub has no public/manual file upload endpoint.
                payload = {
                    "content_base64": base64.b64encode(
                        (ROOT / "backend/tests/data" / filename).read_bytes()
                    ).decode(),
                    "filename": filename,
                    "source_name": source["source_name"],
                    **settings["archive"],
                    "spec": source,
                    "request_id": str(uuid4()),
                }
                code = f"""
import base64,json
from northstar_quant.apps.storage import open_database
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
payload=json.loads({json.dumps(payload)!r})
content=base64.b64decode(payload.pop('content_base64'))
print(json.dumps(DataLibrary(open_database(),SourceFiles.from_environment()).submit(content,**payload)))
"""
                pending = json.loads(
                    self.run("data_hub", "exec", "-T", "data-api", "python", "-c", code)
                )
                assert request(data, "data_hub", "/api/sync")["token_configured"] is False
                assert pending["status"] == "PENDING"
            # Work admitted by the API must complete after both Web containers stop.
            self.run("data_hub", "stop", "data-hub", "data-api")
            self.run("data_hub", "start", "data-worker")
            deadline = monotonic() + 60
            while True:
                attempt = json.loads(
                    self.run(
                        "data_hub",
                        "exec",
                        "-T",
                        "data-worker",
                        "northstar",
                        "data",
                        "attempt",
                        pending["attempt_id"],
                    )
                )
                if attempt["status"] == "PUBLISHED":
                    break
                assert attempt["status"] in {"PENDING", "RUNNING"}, attempt
                assert monotonic() < deadline, attempt
                sleep(0.5)
            self.run(
                "data_hub",
                "up",
                "-d",
                "--no-build",
                "--no-deps",
                "--force-recreate",
                "--wait",
                "--wait-timeout",
                "120",
                "data-api",
                "data-hub",
            )
            # API replacement changes its container address. The publication
            # proxy has a bounded DNS cache; readiness must include that route,
            # not just the API container's own health check.
            deadline = monotonic() + 20
            while True:
                try:
                    datasets = request(research, "research", "/api/datasets")
                except RuntimeError as error:
                    if "Data Hub publication service unavailable" not in str(error):
                        raise
                    if monotonic() >= deadline:
                        raise
                else:
                    assert datasets[0]["snapshot_id"] == attempt["snapshot_id"]
                    break
                sleep(0.2)
            self.run("data_hub", "stop", "data-api", "data-hub", "data-worker", "publications")
            self.run("database", "stop", "postgres")
            run = request(
                research,
                "research",
                "/api/tasks",
                {
                    "request_id": str(uuid4()),
                    "snapshot_id": attempt["snapshot_id"],
                    "config": settings["research"],
                },
            )
            deadline = monotonic() + 60
            while (
                run["status"] not in {"SUCCEEDED", "FAILED", "INTERRUPTED"}
                and monotonic() < deadline
            ):
                sleep(0.2)
                run = request(research, "research", "/api/tasks/" + run["task_id"])
            assert run["status"] == "SUCCEEDED", run
            print(
                "Research SQLite computes while Data Hub and core PostgreSQL are stopped",
                flush=True,
            )
            self.run("database", "up", "-d", "--no-build", "--wait", "postgres")
            # Recreate stopped ephemeral-port containers: a released host port
            # may now be occupied by another connection on the acceptance host.
            self.up("data_hub")
            backup = json.loads(
                self.run(
                    "data_hub",
                    "run",
                    "--rm",
                    "--no-deps",
                    "maintenance",
                    "northstar",
                    "maintenance",
                    "backup",
                    "/var/lib/northstar/backup/acceptance",
                )
            )
            assert backup
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--mount",
                    f"type=bind,source={self.root},target=/evidence,readonly",
                    self.image,
                    "python",
                    "-c",
                    "from pathlib import Path; "
                    "assert Path('/evidence/backup/acceptance/database.dump').stat().st_size > 0",
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            self.run("research", "down", "--timeout", "10")
            with self.client("data_hub", "data-hub") as data:
                assert (
                    request(data, "data_hub", "/api/datasets")[0]["snapshot_id"]
                    == attempt["snapshot_id"]
                )
            self.up("research")
            with self.client("research", "research") as restarted:
                saved = request(restarted, "research", "/api/runs/" + run["run_id"])
                assert saved["run_id"] == run["run_id"]
            print(
                self.run(
                    "database",
                    "run",
                    "--rm",
                    "--no-deps",
                    "initialize",
                    "python",
                    "-c",
                    (ROOT / "scripts/acceptance/check_owned_storage.py").read_text(),
                ),
                flush=True,
            )
            backup = json.loads(self.run("database", "run", "--rm", "--no-deps", "backup"))
            assert backup["status"] == "complete"
            print("Core backup includes Data Hub database, roles and pinned files", flush=True)
            # A different initialized store cannot impersonate the bound source storage.
            self.run("data_hub", "down", "--timeout", "10")
            wrong = self.root / "different-source"
            wrong.mkdir(parents=True)
            initialize(wrong, str(uuid4()))
            original_identity = (wrong / ".northstar-storage-id").read_bytes()
            self.bindings["/opt/northstar/files/source"] = wrong
            try:
                for app, service in (("data_hub", "storage-check"), ("database", "initialize")):
                    try:
                        self.run(app, "run", "--rm", "--no-deps", service)
                    except RuntimeError as error:
                        assert "identity" in str(error).lower(), str(error)
                    else:
                        raise AssertionError("different storage identity was accepted")
                    assert (wrong / ".northstar-storage-id").read_bytes() == original_identity
            finally:
                self.bindings["/opt/northstar/files/source"] = self.root / "source"
            print(
                "Each application stops independently; shared data and research results survive",
                flush=True,
            )

    def preserve_failure(self) -> None:
        directory = os.environ.get("NORTHSTAR_ACCEPTANCE_ARTIFACTS")
        if not directory:
            return
        records = []
        for project in self.projects.values():
            found = subprocess.run(
                ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + project],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for identity in found.stdout.split():
                details = subprocess.run(
                    ["docker", "inspect", identity],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if details.returncode:
                    continue
                value = json.loads(details.stdout)[0]
                records.append(
                    {
                        "name": value["Name"],
                        "image": value["Image"],
                        "status": value["State"]["Status"],
                        "requested_ports": value["HostConfig"]["PortBindings"],
                        "actual_ports": value["NetworkSettings"]["Ports"],
                        "networks": value["NetworkSettings"]["Networks"],
                        "extra_hosts": value["HostConfig"]["ExtraHosts"],
                    }
                )
        probes = []
        for record in records:
            if "research-api" not in record["name"] or record["status"] != "running":
                continue
            probe = subprocess.run(
                [
                    "docker",
                    "exec",
                    record["name"],
                    "python",
                    "-c",
                    (
                        "import os,urllib.request; u=os.environ['NORTHSTAR_DATA_HUB_URL']; "
                        "print(u,flush=True); "
                        "r=urllib.request.urlopen(u+'/api/publications',timeout=5); "
                        "print(r.status,len(r.read()))"
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            probes.append(
                {"container": record["name"], "stdout": probe.stdout, "stderr": probe.stderr}
            )
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        (target / "application-deployment-failure.json").write_text(
            json.dumps({"containers": records, "publication_probes": probes}, indent=2)
        )

    def close(self) -> None:
        errors = []
        for app in ("data_hub", "research", "database"):
            try:
                self.run(app, "down", "--volumes", "--timeout", "10")
            except RuntimeError as error:
                errors.append(str(error))
        if not errors:
            cleanup_files(self.root, self.image)
            self.files.cleanup()
        if errors:
            raise RuntimeError("Disposable cleanup failed: " + "\n".join(errors))


def request(client: httpx.Client, app: str, path: str, payload: dict | None = None):
    verb = "GET" if payload is None else "POST"
    binding = next(
        m
        for (method, route), m in methods(app).items()
        if method == verb and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", route), path)
    )
    headers = {}
    content = None
    if payload is not None:
        headers = {"Content-Type": "application/protobuf"}
        if path != "/api/login":
            session = request(client, app, "/api/browser-session")
            headers["X-Northstar-CSRF"] = session["csrf"]
        content = pack(binding.input_type, payload).SerializeToString()
    response = client.request(verb, path, content=content, headers=headers)
    if response.status_code >= 400:
        from northstar_quant.web.common_pb2 import Error

        raise RuntimeError(str(decode(Error.DESCRIPTOR, response.content)))
    return decode(binding.output_type, response.content)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--data-image", required=True)
    parser.add_argument("--research-image", required=True)
    args = parser.parse_args()
    if not __debug__:
        parser.error("acceptance assertions must execute")
    deployment = Deployment(args.image, args.data_image, args.research_image)
    try:
        deployment.exercise()
    except Exception:
        try:
            deployment.preserve_failure()
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print(f"Failed to preserve container diagnostics: {type(error).__name__}", flush=True)
        raise
    finally:
        deployment.close()


if __name__ == "__main__":
    main()

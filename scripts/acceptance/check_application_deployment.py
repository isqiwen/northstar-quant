"""Exercise core PostgreSQL, Research SQLite and fixed publications over shared files.

Local bind directories model the share; this is not a real NFS/SMB or three-host acceptance.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
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

from northstar_quant.web.protobuf import decode, methods, pack

ROOT = Path(__file__).resolve().parents[2]


class Deployment:
    def __init__(self, image: str, data_image: str, research_image: str) -> None:
        prefix = "northstar-app-check-" + uuid4().hex[:12]
        self.projects = {
            app: f"{prefix}-{app.replace('_', '-')}" for app in ("database", "data_hub", "research")
        }
        self.files = tempfile.TemporaryDirectory(prefix="northstar-nas-check-")
        self.root = Path(self.files.name)
        for share in ("source", "market", "research", "backup", "pgdata"):
            (self.root / share).mkdir()
        self.image = image
        self.password = secrets.token_urlsafe(32)
        self.environment = {
            key: value for key, value in os.environ.items() if not key.startswith("NORTHSTAR_")
        }
        self.environment.update(
            NORTHSTAR_BACKEND_IMAGE=image,
            NORTHSTAR_DATA_FRONTEND_IMAGE=data_image,
            NORTHSTAR_RESEARCH_FRONTEND_IMAGE=research_image,
            NORTHSTAR_NAS_ADDRESS="host.docker.internal",
            NORTHSTAR_NFS_VERSION="4",
            NORTHSTAR_PUBLICATION_BIND_ADDRESS="0.0.0.0",
            NORTHSTAR_PUBLICATION_PORT="0",
            NORTHSTAR_DATA_DATABASE_NETWORK=prefix + "-storage",
            NORTHSTAR_PUBLICATION_TOKEN=secrets.token_urlsafe(32),
            NORTHSTAR_DATABASE_ADMIN_PASSWORD=self.password,
            NORTHSTAR_DATA_HUB_DATABASE_PASSWORD=secrets.token_urlsafe(32),
            NORTHSTAR_DATABASE_PGDATA=str(self.root / "pgdata"),
            NORTHSTAR_DATA_API_PORT="0",
            NORTHSTAR_DATA_WEB_PORT="0",
            NORTHSTAR_RESEARCH_API_PORT="0",
            NORTHSTAR_RESEARCH_WEB_PORT="0",
        )

        for share in ("SOURCE", "MARKET", "RESEARCH", "BACKUP"):
            self.environment[f"NORTHSTAR_NAS_{share}_DIR"] = str(self.root / share.lower())
            self.environment[f"NORTHSTAR_{share}_MOUNT"] = str(self.root / share.lower())
            self.environment[f"NORTHSTAR_{share}_STORAGE_ID"] = str(uuid4())
            self.environment[f"NORTHSTAR_{share}_NFS_EXPORT"] = (
                "/synthetic-bind-not-nfs/" + share.lower()
            )

    def run(self, app: str, *arguments: str) -> str:
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                os.devnull,
                "-p",
                self.projects[app],
                "-f",
                str(ROOT / "deploy" / app / "compose.yaml"),
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
        self.run(app, "up", "-d", "--no-build", "--wait", "--wait-timeout", "120")
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
                assert remote.get("/api/publications").status_code == 403
                assert (
                    remote.post(
                        "/api/publications",
                        headers={
                            "Authorization": "Bearer "
                            + self.environment["NORTHSTAR_PUBLICATION_TOKEN"]
                        },
                    ).status_code
                    == 403
                )

    @contextmanager
    def client(self, app: str, service: str) -> Iterator[httpx.Client]:
        address = self.run(app, "port", service, "3000").strip()
        base = "http://" + address
        with httpx.Client(base_url=base, headers={"Origin": base}, timeout=30) as client:
            assert "NORTHSTAR" in client.get("/").text
            request(client, app, "/api/browser-session")
            yield client

    def exercise(self) -> None:
        self.up("database")
        # Research starts with storage alone: Data Hub has never been started.
        self.up("data_hub")
        self.up("research")
        with self.client("research", "research") as research:
            assert request(research, "research", "/api/datasets") == []
            self.run("data_hub", "stop", "data-worker")
            settings = tomllib.loads((ROOT / "examples/intraday.toml").read_text())
            source = settings["source"]
            filename = source.pop("file")
            with self.client("data_hub", "data-hub") as data:
                # Seed synthetic research evidence inside the installed test container.
                # Data Hub has no public/manual file upload endpoint.
                payload = {
                    "content_base64": base64.b64encode(
                        (ROOT / "examples" / filename).read_bytes()
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
                "--wait",
                "--wait-timeout",
                "120",
                "data-api",
                "data-hub",
            )
            assert (
                request(research, "research", "/api/datasets")[0]["snapshot_id"]
                == attempt["snapshot_id"]
            )
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
            self.run("data_hub", "start")
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
            # A bind directory existing locally is insufficient evidence of a NAS mount.
            self.run("data_hub", "down", "--timeout", "10")
            wrong = self.root / "unmounted"
            wrong.mkdir(parents=True)
            self.environment["NORTHSTAR_SOURCE_MOUNT"] = str(wrong)
            try:
                try:
                    self.run("data_hub", "run", "--rm", "--no-deps", "storage-check")
                except RuntimeError as error:
                    assert "mount" in str(error), str(error)
                else:
                    raise AssertionError("unidentified local directory was accepted as NAS storage")
                assert list(wrong.iterdir()) == []
            finally:
                self.environment["NORTHSTAR_SOURCE_MOUNT"] = str(self.root / "source")
            print(
                "Each application stops independently; shared data and research results survive",
                flush=True,
            )

    def close(self) -> None:
        errors = []
        for app in ("data_hub", "research", "database"):
            try:
                self.run(app, "down", "--volumes", "--timeout", "10")
            except RuntimeError as error:
                errors.append(str(error))
        if not errors:
            # Container-created private directories are root-owned on Linux. Remove only
            # this acceptance's generated temporary tree through the same container UID.
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--mount",
                    f"type=bind,source={self.root},target=/cleanup",
                    self.image,
                    "python",
                    "-c",
                    "import pathlib,shutil; "
                    "[shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink() "
                    "for p in pathlib.Path('/cleanup').iterdir()]",
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
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
        session = request(client, app, "/api/browser-session")
        headers = {
            "X-Northstar-CSRF": session["csrf"],
            "Content-Type": "application/protobuf",
        }
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
    finally:
        deployment.close()


if __name__ == "__main__":
    main()

"""Launch installed roles and exercise their real CLI/HTTP interfaces without source imports."""

from __future__ import annotations

import json
import re
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

from northstar_quant.web.protobuf import decode, methods, pack


class InstalledApplication:
    """Keep Live independent while Live Web processes come and go in an empty directory."""

    def __init__(self, executable: str, directory: Path, environment: dict[str, str]) -> None:
        self.executable = executable
        self.directory = directory
        self.environment = dict(
            environment,
            NORTHSTAR_LOG_DIR=str(directory / "logs"),
            NORTHSTAR_PUBLICATION_TOKEN="synthetic-publication-token-for-acceptance-only",
        )
        self.environment.setdefault(
            "NORTHSTAR_RESEARCH_DATABASE", str(directory / "research.sqlite3")
        )
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
        self.web_pids: list[int] = []
        self.backend_starts: dict[str, int] = {}
        self.protocols: dict[str, dict] = {}
        self.api_processes: dict[str, subprocess.Popen[str]] = {}
        self.log_paths: list[Path] = []

    def command(self, *arguments: str) -> Any:
        if arguments[0] == "research" and arguments[1] in {"run", "replay", "paper"}:
            with self.api("data-api"):
                return self._command(*arguments)
        result = self._command(*arguments)
        if arguments == ("maintenance", "init-db"):
            subprocess.run(
                [self.executable, *arguments],
                cwd=self.directory,
                env=dict(self.environment, NORTHSTAR_DATABASE_OWNER="research"),
                check=True,
                capture_output=True,
                timeout=60,
            )
        return result

    def _command(self, *arguments: str) -> Any:
        completed = subprocess.run(
            [self.executable, *arguments],
            cwd=self.directory,
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if completed.returncode:
            raise RuntimeError(f"northstar {arguments[0]} failed: {completed.stderr}")
        return json.loads(completed.stdout)

    def seed_source(self, payload: dict, *, wait: bool = False) -> dict:
        """Synthetic acceptance setup in the installed interpreter; no upload API."""
        code = """
import base64,json,sys
from northstar_quant.apps.storage import open_database
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
payload=json.load(sys.stdin)
content=base64.b64decode(payload.pop('content_base64'))
wait=payload.pop('_wait')
library=DataLibrary(open_database(),SourceFiles.from_environment())
result=(library.receive if wait else library.submit)(content,**payload)
print(json.dumps(result))
"""
        result = subprocess.run(
            [str(Path(self.executable).parent / "python"), "-c", code],
            input=json.dumps({**payload, "_wait": wait}),
            text=True,
            capture_output=True,
            env=self.environment,
            cwd=self.directory,
            check=True,
            timeout=60,
        )
        return json.loads(result.stdout)

    def seed_study(self, study: Path) -> dict:
        import base64
        import tomllib
        from uuid import uuid4

        document = tomllib.loads(study.read_text())
        source = dict(document["source"])
        filename = source.pop("file")
        return self.seed_source(
            {
                "content_base64": base64.b64encode((study.parent / filename).read_bytes()).decode(),
                "filename": filename,
                "source_name": source["source_name"],
                "spec": source,
                **document["archive"],
                "request_id": str(uuid4()),
            },
            wait=True,
        )

    def request(self, url: str, payload: object = None) -> bytes:
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        verb = "GET" if payload is None else "POST"
        binding = next(
            (
                m
                for (method, path), m in self.protocols.get(origin, {}).items()
                if method == verb and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", path), parsed.path)
            ),
            None,
        )
        body = None if payload is None else pack(binding.input_type, payload).SerializeToString()
        headers = {} if body is None else {"Content-Type": "application/protobuf"}
        if body is not None:
            session = json.loads(self.request(origin + "/api/browser-session"))
            headers["X-Northstar-CSRF"] = session["csrf"]
        with self.opener.open(Request(url, data=body, headers=headers), timeout=30) as response:
            content = response.read()
            if response.headers.get("Content-Type", "").startswith("application/protobuf"):
                return json.dumps(decode(binding.output_type, content)).encode()
            return content

    @contextmanager
    def research_worker(self) -> Iterator[subprocess.Popen[str]]:
        self.backend_starts["research-worker"] = self.backend_starts.get("research-worker", 0) + 1
        log_path = self.directory / "research-worker.log"
        self.log_paths.append(log_path)
        environment = {
            k: v
            for k, v in self.environment.items()
            if not k.startswith(("NORTHSTAR_LIVE", "NORTHSTAR_SIMNOW"))
            and k != "NORTHSTAR_DATABASE_URL"
        }
        with log_path.open("a") as log:
            process = subprocess.Popen(
                [self.executable, "serve", "research-worker"],
                cwd=self.directory,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                yield process
                assert process.poll() is None, log_path.read_text()
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

    def submit_research(self, url: str, snapshot: str, config: dict) -> dict:
        from uuid import uuid4

        task = json.loads(
            self.request(
                url + "/api/tasks",
                {"request_id": str(uuid4()), "snapshot_id": snapshot, "config": config},
            )
        )
        assert task["status"] == "QUEUED", task
        with self.research_worker():
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                task = json.loads(self.request(url + "/api/tasks/" + task["task_id"]))
                if task["status"] == "SUCCEEDED":
                    return task
                assert task["status"] not in {"FAILED", "INTERRUPTED", "CANCELLED"}, task
                time.sleep(0.2)
        raise AssertionError(task)

    @contextmanager
    def data_worker(self, *, synthetic_tushare: bool = False) -> Iterator[subprocess.Popen[str]]:
        """Start the installed Data processor without an API or frontend parent."""
        log_path = self.directory / "data-worker.log"
        self.log_paths.append(log_path)
        with log_path.open("a") as log:
            arguments = [self.executable, "serve", "data-worker"]
            if synthetic_tushare:
                arguments = [
                    str(Path(self.executable).parent / "python"),
                    "-c",
                    """
from northstar_quant.data_management.tushare import acquisition
from northstar_quant.apps.data_hub.worker import run
def denied(*args):
    raise acquisition.DownloadError('Synthetic acceptance: provider permission denied')
acquisition.fetch=denied
run()
""",
                ]
            process = subprocess.Popen(
                arguments,
                cwd=self.directory,
                env=self.environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self.backend_starts["data-worker"] = self.backend_starts.get("data-worker", 0) + 1
            try:
                yield process
                assert process.poll() is None, log_path.read_text()
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)

    def await_attempt(self, attempt: dict[str, Any]) -> dict[str, Any]:
        """Inspect persisted completion through the installed CLI with a bounded wait."""
        deadline = time.monotonic() + 30
        while attempt["status"] in {"PENDING", "RUNNING"}:
            if time.monotonic() > deadline:
                raise AssertionError(f"Data attempt did not complete: {attempt}")
            time.sleep(0.1)
            attempt = self.command("data", "attempt", attempt["attempt_id"])
        return attempt

    @contextmanager
    def live(self) -> Iterator[subprocess.Popen[str]]:
        auth = Path(self.environment["NORTHSTAR_LIVE_AUTH"])
        environment = dict(self.environment, NORTHSTAR_LIVE_AUTH=str(auth.with_name("live.toml")))
        with self._running("live-kernel", environment) as (_, process):
            yield process

    @contextmanager
    def api(self, role: str = "live-api") -> Iterator[str]:
        environment = dict(self.environment)
        if role == "research-api":
            environment["NORTHSTAR_DATABASE_OWNER"] = "research"
            environment.pop("NORTHSTAR_DATABASE_URL", None)
        if role == "live-api":
            environment.pop("NORTHSTAR_DATABASE_URL", None)
            environment.pop("NORTHSTAR_DATA_DIR", None)
        else:
            environment.pop("NORTHSTAR_LIVE_AUTH", None)
            environment.pop("NORTHSTAR_LIVE_URL", None)
        with self._running(role, environment) as (base_url, process):
            self.web_pids.append(process.pid)
            self.api_processes[base_url] = process
            self.protocols[base_url] = methods(
                {
                    "data-api": "data_hub",
                    "research-api": "research",
                    "live-api": "live",
                }[role]
            )
            self.request(base_url + "/api/browser-session")
            log_health = json.loads(self.request(base_url + "/health/logging"))
            assert log_health["status"] == "OK", log_health
            assert (
                log_health["application"]
                == {"data-api": "data_hub", "research-api": "research", "live-api": "live"}[role]
            )
            yield base_url

    @contextmanager
    def web(self, role: str = "live-api") -> Iterator[str]:
        application = {
            "data-api": "data_hub",
            "research-api": "research",
            "live-api": "live",
        }[role]
        frontend = Path(__file__).resolve().parents[3] / "frontend"
        with self.api(role) as backend:
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            url = f"http://127.0.0.1:{port}"
            environment = {
                key: value
                for key, value in self.environment.items()
                if not key.startswith("NORTHSTAR_")
            }
            environment.update(
                NORTHSTAR_API_URL=backend,
                HOSTNAME="127.0.0.1",
                PORT=str(port),
                NODE_ENV="production",
            )
            log_path = self.directory / f"next-{application}-{port}.log"
            self.log_paths.append(log_path)
            with log_path.open("w") as log:
                process = subprocess.Popen(
                    [
                        "node",
                        str(
                            frontend
                            / "apps"
                            / application
                            / ".next/standalone/apps"
                            / application
                            / "server.js"
                        ),
                    ],
                    env=environment,
                    cwd=self.directory,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                try:
                    deadline = time.monotonic() + 30
                    while True:
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError(
                                "Next frontend did not become ready: " + log_path.read_text()
                            )
                        try:
                            self.request(url + "/health/ready")
                            break
                        except URLError:
                            time.sleep(0.1)
                    self.protocols[url] = self.protocols[backend]
                    yield url
                    # Stop only the frontend, then observe the still-running API.
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        # Browser keep-alive requests can delay Next shutdown. This
                        # checks failure isolation, not graceful connection draining.
                        process.kill()
                        process.wait(timeout=10)
                    assert json.loads(self.request(backend + "/health/ready")) == {
                        "status": "ready"
                    }
                    process = subprocess.Popen(
                        process.args,
                        env=environment,
                        cwd=self.directory,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                    deadline = time.monotonic() + 30
                    while True:
                        try:
                            self.request(url + "/health/ready")
                            break
                        except URLError:
                            if time.monotonic() >= deadline:
                                raise RuntimeError("Next restart did not become ready")
                            time.sleep(0.1)
                    # Stop only the API: the independent page service must still respond.
                    owner = self.api_processes[backend]
                    owner.terminate()
                    owner.wait(timeout=10)
                    assert json.loads(self.request(url + "/health/ready")) == {"status": "ready"}
                    assert self.request(url + "/")
                    try:
                        self.request(url + "/api/browser-session")
                    except HTTPError as error:
                        assert error.code == 503
                    else:
                        raise AssertionError("Stopped API must be unavailable")
                    print(
                        f"{application}: frontend/API stop and restart isolation passed",
                        flush=True,
                    )
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)

    def assert_live(
        self,
        process: subprocess.Popen[str],
        original: dict[str, Any],
        base_url: str | None = None,
    ) -> None:
        """Observe the actual owning process, not Live Web-local thread state."""
        assert process.poll() is None, "Live Web shutdown must not stop Live"
        current = self.command("status")
        assert current["pid"] == original["pid"] == process.pid
        assert current["runtime_id"] == original["runtime_id"]
        assert current["started_at"] == original["started_at"]
        assert current["status"] == "AVAILABLE"
        observed = datetime.fromisoformat(current["observed_at"])
        assert observed >= datetime.fromisoformat(original["observed_at"])
        assert 0 <= (datetime.now(UTC) - observed).total_seconds() < 5
        assert not current["order_sending"] and not current["cancel_sending"]
        if base_url is not None:
            self.request(f"{base_url}/health/ready")
            browser = json.loads(self.request(f"{base_url}/api/live/status"))
            assert browser["runtime_id"] == current["runtime_id"]
            assert browser["pid"] == current["pid"]
            assert browser["status"] == "AVAILABLE"

    def assert_unavailable(self) -> None:
        """A stopped Live must not turn into a new Live Web-owned runtime."""
        try:
            self.command("status")
        except RuntimeError:
            pass
        else:
            raise AssertionError("CLI must not report a stopped Live as available")
        with self.api() as base_url:
            self.request(f"{base_url}/health/ready")
            for path in ("/api/live/status", "/api/broker/status"):
                try:
                    self.request(f"{base_url}{path}")
                except HTTPError as error:
                    assert error.code == 503, "unavailable Live must be explicit"
                else:
                    raise AssertionError("Live Web must not invent an available Live owner")

    @contextmanager
    def _running(
        self, role: str, environment: dict[str, str]
    ) -> Iterator[tuple[str, subprocess.Popen[str]]]:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        if role == "data-api":
            self.environment["NORTHSTAR_DATA_HUB_URL"] = base_url
        if role == "live-kernel":
            self.environment["NORTHSTAR_LIVE_URL"] = base_url
        log_path = self.directory / f"{role}-{port}.log"
        self.log_paths.append(log_path)
        with log_path.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                [self.executable, "serve", role, "--port", str(port)],
                cwd=self.directory,
                env=environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self.backend_starts[role] = self.backend_starts.get(role, 0) + 1
            try:
                deadline = time.monotonic() + 30
                while True:
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(f"installed {role} process did not become ready")
                    try:
                        if role == "live-kernel":
                            self.command("status")
                            break
                        if json.loads(self.request(f"{base_url}/health/ready")) == {
                            "status": "ready"
                        }:
                            break
                    except (URLError, RuntimeError):
                        pass
                    time.sleep(0.1)
                yield base_url, process
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
                        raise RuntimeError(f"installed {role} process did not stop") from None

    def assert_file_logs(self) -> None:
        root = Path(self.environment["NORTHSTAR_LOG_DIR"])
        for application, component in (
            ("data_hub", "api"),
            ("data_hub", "worker"),
            ("research", "api"),
            ("research", "worker"),
            ("live", "api"),
            ("live", "kernel"),
        ):
            paths = (root / application).glob(
                f"northstar-{application.replace('_', '-')}-{component}-????-??-??.log*"
            )
            records = [json.loads(line) for path in paths for line in path.read_text().splitlines()]
            assert records
            assert all(
                r["application"] == application and r["component"] == component for r in records
            )
            sessions = {r["session"] for r in records if "session" in r}
            role = {
                ("data_hub", "api"): "data-api",
                ("data_hub", "worker"): "data-worker",
                ("research", "api"): "research-api",
                ("research", "worker"): "research-worker",
                ("live", "api"): "live-api",
                ("live", "kernel"): "live-kernel",
            }[(application, component)]
            assert len(sessions) == self.backend_starts[role], (role, sessions)
            assert any(r.get("message") == "application logging started" for r in records)
        print(
            "Installed file logs: six isolated writers retained records across restarts",
            flush=True,
        )

    def logs(self) -> str:
        paths = [*self.log_paths, *Path(self.environment["NORTHSTAR_LOG_DIR"]).glob("**/*.log")]
        return "\n".join(path.read_text("utf-8")[-16000:] for path in paths if path.exists())

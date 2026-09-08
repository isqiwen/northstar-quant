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
        self.environment = dict(environment)
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
        self.web_pids: list[int] = []
        self.protocols: dict[str, dict] = {}
        self.api_processes: dict[str, subprocess.Popen[str]] = {}
        self.log_paths: list[Path] = []

    def command(self, *arguments: str) -> Any:
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
    def live(self) -> Iterator[subprocess.Popen[str]]:
        auth = Path(self.environment["NORTHSTAR_LIVE_AUTH"])
        environment = dict(self.environment, NORTHSTAR_LIVE_AUTH=str(auth.with_name("live.toml")))
        with self._running("live-kernel", environment) as (_, process):
            yield process

    @contextmanager
    def api(self, role: str = "live-api") -> Iterator[str]:
        environment = dict(self.environment)
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
                {"data-api": "data_hub", "research-api": "research", "live-api": "live"}[role]
            )
            self.request(base_url + "/api/browser-session")
            yield base_url

    @contextmanager
    def web(self, role: str = "live-api") -> Iterator[str]:
        application = {"data-api": "data_hub", "research-api": "research", "live-api": "live"}[role]
        frontend = Path(__file__).resolve().parents[2] / "frontend"
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
                        f"{application}: frontend/API stop and restart isolation passed", flush=True
                    )
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)

    def assert_live(
        self, process: subprocess.Popen[str], original: dict[str, Any], base_url: str | None = None
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

    def logs(self) -> str:
        return "\n".join(path.read_text("utf-8") for path in self.log_paths if path.exists())

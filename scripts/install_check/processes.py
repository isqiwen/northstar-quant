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


class InstalledApplication:
    """Keep Live independent while Console processes come and go in an empty directory."""

    def __init__(self, executable: str, directory: Path, environment: dict[str, str]) -> None:
        self.executable = executable
        self.directory = directory
        self.environment = dict(environment)
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
        self.console_pids: list[int] = []
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
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {} if body is None else {"Content-Type": "application/json"}
        if body is not None:
            parsed = urlsplit(url)
            with self.opener.open(f"{parsed.scheme}://{parsed.netloc}/", timeout=15) as page:
                html = page.read().decode()
            token = re.search(r'<meta name="northstar-csrf" content="([^"]+)"', html)
            assert token is not None, "workspace must provide a CSRF token"
            headers["X-Northstar-CSRF"] = token.group(1)
        with self.opener.open(Request(url, data=body, headers=headers), timeout=15) as response:
            return response.read()

    @contextmanager
    def live(self) -> Iterator[subprocess.Popen[str]]:
        auth = Path(self.environment["NORTHSTAR_LIVE_AUTH"])
        environment = dict(self.environment, NORTHSTAR_LIVE_AUTH=str(auth.with_name("live.toml")))
        with self._running("live", environment) as (_, process):
            yield process

    @contextmanager
    def console(self) -> Iterator[str]:
        with self._running("serve", self.environment) as (base_url, process):
            self.console_pids.append(process.pid)
            yield base_url

    def assert_live(
        self, process: subprocess.Popen[str], original: dict[str, Any], base_url: str | None = None
    ) -> None:
        """Observe the actual owning process, not Console-local thread state."""
        assert process.poll() is None, "Console shutdown must not stop Live"
        current = self.command("live-status")
        assert current["pid"] == original["pid"] == process.pid
        assert current["runtime_id"] == original["runtime_id"]
        assert current["started_at"] == original["started_at"]
        assert current["status"] == "AVAILABLE"
        observed = datetime.fromisoformat(current["observed_at"])
        assert observed >= datetime.fromisoformat(original["observed_at"])
        assert 0 <= (datetime.now(UTC) - observed).total_seconds() < 5
        assert not current["order_sending"] and not current["cancel_sending"]
        if base_url is not None:
            self.request(f"{base_url}/")
            browser = json.loads(self.request(f"{base_url}/api/live/status"))
            assert browser["runtime_id"] == current["runtime_id"]
            assert browser["pid"] == current["pid"]
            assert browser["status"] == "AVAILABLE"

    def assert_unavailable(self) -> None:
        """A stopped Live must not turn into a new Console-owned runtime."""
        try:
            self.command("live-status")
        except RuntimeError:
            pass
        else:
            raise AssertionError("CLI must not report a stopped Live as available")
        with self.console() as base_url:
            self.request(f"{base_url}/")
            for path in ("/api/live/status", "/api/broker/status"):
                try:
                    self.request(f"{base_url}{path}")
                except HTTPError as error:
                    assert error.code == 503, "unavailable Live must be explicit"
                else:
                    raise AssertionError("Console must not invent an available Live owner")

    @contextmanager
    def _running(
        self, role: str, environment: dict[str, str]
    ) -> Iterator[tuple[str, subprocess.Popen[str]]]:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"
        if role == "live":
            self.environment["NORTHSTAR_LIVE_URL"] = base_url
        log_path = self.directory / f"{role}-{port}.log"
        self.log_paths.append(log_path)
        with log_path.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                [self.executable, role, "--port", str(port)],
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
                        if role == "live":
                            self.command("live-status")
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

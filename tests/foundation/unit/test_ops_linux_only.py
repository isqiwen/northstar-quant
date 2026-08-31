"""Linux x86_64 fail-closed contracts for operational command entrypoints."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Callable
from unittest.mock import Mock

import pytest

from tests.helpers.paths import PROJECT_ROOT


OPS_ROOT = PROJECT_ROOT / "scripts" / "ops"
_ENTRYPOINT_NAMES = ("health", "logs", "diagnose", "backup")
_REMOTE_PAYLOAD_NAMES = (*_ENTRYPOINT_NAMES, "restore")


@pytest.fixture
def load_ops_module(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], ModuleType]:
    """Load a direct-script ops entrypoint with its sibling helper available."""

    monkeypatch.syspath_prepend(str(OPS_ROOT))

    def load(name: str) -> ModuleType:
        sys.modules.pop(name, None)
        return importlib.import_module(name)

    return load


@pytest.mark.parametrize("entrypoint_name", _ENTRYPOINT_NAMES)
def test_ops_entrypoints_reject_unsupported_hosts_before_loading_inventory(
    entrypoint_name: str,
    load_ops_module: Callable[[str], ModuleType],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_ops_module(entrypoint_name)
    inventory_loader = Mock()
    unsupported_host = module.PlatformSupportError("仅支持 Linux x86_64")
    guard = Mock(side_effect=unsupported_host)
    monkeypatch.setattr(module, "require_linux_x86_64", guard)
    monkeypatch.setattr(module, "load_deployment_inventory", inventory_loader)
    monkeypatch.setattr(
        sys,
        "argv",
        [f"{entrypoint_name}.py", "--inventory", "unsafe-but-unread.env", "--dry-run"],
    )

    assert module.main() == 1
    guard.assert_called_once_with()
    inventory_loader.assert_not_called()
    assert "仅支持 Linux x86_64" in capsys.readouterr().out


def test_remote_operation_rejects_unsupported_host_before_filesystem_or_ssh(
    load_ops_module: Callable[[str], ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = load_ops_module("_remote")
    unsupported_host = remote.PlatformSupportError("仅支持 Linux x86_64")
    guard = Mock(side_effect=unsupported_host)
    monkeypatch.setattr(remote, "require_linux_x86_64", guard)

    with pytest.raises(remote.PlatformSupportError, match="仅支持 Linux x86_64"):
        remote.run_linux_operation(
            inventory=Mock(),
            operation="missing-script-must-not-be-inspected",
            dry_run=True,
        )

    guard.assert_called_once_with()


def test_remote_operation_streams_target_architecture_guard_before_the_payload(
    load_ops_module: Callable[[str], ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = load_ops_module("_remote")
    calls: list[dict[str, object]] = []
    script_path = remote.REMOTE_LINUX_ROOT / "health.sh"

    monkeypatch.setattr(remote, "require_linux_x86_64", lambda: None)
    monkeypatch.setattr(remote.shutil, "which", lambda command: "/usr/bin/ssh")
    monkeypatch.setattr(
        remote.subprocess,
        "run",
        lambda command, **kwargs: calls.append(kwargs)
        or subprocess.CompletedProcess(command, 0),
    )

    result = remote.run_linux_operation(
        inventory=type("Inventory", (), {"deploy_host": "ops@example.test"})(),
        operation="health",
        arguments=("northstar-quant",),
    )

    assert result == 0
    assert len(calls) == 1
    payload = calls[0]["input"]
    assert isinstance(payload, str)
    assert payload.startswith(remote._REMOTE_LINUX_X86_64_GUARD)
    assert payload.endswith(script_path.read_text(encoding="utf-8"))


def test_target_architecture_guard_stops_an_arm_linux_payload_before_its_body(
    tmp_path: Path,
    load_ops_module: Callable[[str], ModuleType],
) -> None:
    remote = load_ops_module("_remote")
    fake_uname = tmp_path / "uname"
    fake_uname.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-s\" ]; then echo Linux; else echo aarch64; fi\n",
        encoding="utf-8",
    )
    fake_uname.chmod(0o755)
    payload = (
        "PATH="
        + str(tmp_path)
        + ":${PATH}\n"
        + "export PATH\n"
        + "set -e\n"
        + remote._REMOTE_LINUX_X86_64_GUARD
        + "printf 'body-ran\\n'\n"
    )

    result = subprocess.run(
        ["/bin/bash", "-s"],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "Linux x86_64" in result.stderr
    assert "body-ran" not in result.stdout


@pytest.mark.parametrize("payload_name", _REMOTE_PAYLOAD_NAMES)
def test_remote_operation_payloads_reject_non_x86_linux_when_invoked_directly(
    payload_name: str,
) -> None:
    source = (OPS_ROOT / "remote" / "linux" / f"{payload_name}.sh").read_text(
        encoding="utf-8"
    )

    assert 'case "$(uname -s):$(uname -m)" in' in source
    assert "Linux:x86_64|Linux:amd64" in source
    assert "远程运维目标仅支持 Linux x86_64。" in source

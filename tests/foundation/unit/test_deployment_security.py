from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.deploy import deploy
from scripts.deploy.inventory import DeploymentInventory, SshTarget
from tests.helpers.paths import PROJECT_ROOT


def _inventory(*, deploy_host: str = "deployer@host.example.test") -> DeploymentInventory:
    deploy_user, separator, host = deploy_host.partition("@")
    return DeploymentInventory(
        source=Path("deploy.env"),
        ssh_target=SshTarget(
            authority=deploy_host,
            host=host if separator else deploy_host,
            deploy_user=deploy_user if separator else None,
        ),
        app_name="northstar-quant",
        service_user="northstar",
        systemd_service_name="northstar-quant",
        service_mode="health",
        python_version="3.12",
        keep_releases=2,
        remote_tmp="/tmp",
        dashboard_deploy_enabled=False,
        ntfy_deploy_enabled=False,
        values={},
    )


def test_deployment_rejects_service_identity_even_when_host_uses_an_alias(monkeypatch) -> None:
    calls: list[str] = []

    def fake_remote_command(**kwargs: object) -> subprocess.CompletedProcess[str]:
        command = kwargs["command"]
        assert isinstance(command, str)
        calls.append(command)
        stdout = "Linux\n" if command == "uname -s" else "northstar\n"
        return subprocess.CompletedProcess([], 0, stdout=stdout)

    monkeypatch.setattr(deploy, "_run_remote_command", fake_remote_command)

    with pytest.raises(deploy.DeployError):
        deploy._assert_linux_target(ssh="ssh", inventory=_inventory(deploy_host="production-alias"))

    assert calls == ["uname -s", "id -un"]


def test_deployment_rejects_root_ssh_identity_before_invoking_the_gate(monkeypatch) -> None:
    calls: list[str] = []

    def fake_remote_command(**kwargs: object) -> subprocess.CompletedProcess[str]:
        command = kwargs["command"]
        assert isinstance(command, str)
        calls.append(command)
        stdout = "Linux\n" if command == "uname -s" else "root\n"
        return subprocess.CompletedProcess([], 0, stdout=stdout)

    monkeypatch.setattr(deploy, "_run_remote_command", fake_remote_command)

    with pytest.raises(deploy.DeployError):
        deploy._assert_linux_target(ssh="ssh", inventory=_inventory())

    assert calls == ["uname -s", "id -un"]


def test_deployment_requires_the_fixed_gate_identity_protocol_before_submission(monkeypatch) -> None:
    calls: list[str] = []
    gate_identity = "a" * 64

    def fake_remote_command(**kwargs: object) -> subprocess.CompletedProcess[str]:
        command = kwargs["command"]
        assert isinstance(command, str)
        calls.append(command)
        stdout = {
            "uname -s": "Linux\n",
            "id -un": "deployer\n",
            f"sudo -n {deploy.ROOT_RUNNER_PATH} identity": json.dumps(
                {
                    "gate_identity": gate_identity,
                    "gate_protocol": deploy.GATE_PROTOCOL,
                }
            ),
        }[command]
        return subprocess.CompletedProcess([], 0, stdout=stdout)

    monkeypatch.setattr(deploy, "_run_remote_command", fake_remote_command)

    identity = deploy._assert_linux_target(ssh="ssh", inventory=_inventory())

    assert identity == deploy.GateIdentity(
        gate_identity=gate_identity,
        gate_protocol=deploy.GATE_PROTOCOL,
    )
    assert calls == [
        "uname -s",
        "id -un",
        f"sudo -n {deploy.ROOT_RUNNER_PATH} identity",
    ]
    assert "sudo -n true" not in calls


@pytest.mark.parametrize(
    "identity_payload",
    [
        "not-json",
        json.dumps({"gate_identity": "a" * 64, "gate_protocol": "wrong-protocol"}),
        json.dumps({"gate_identity": "not-a-digest", "gate_protocol": deploy.GATE_PROTOCOL}),
        json.dumps(
            {
                "gate_identity": "a" * 64,
                "gate_protocol": deploy.GATE_PROTOCOL,
                "unexpected": "field",
            }
        ),
    ],
)
def test_deployment_fails_closed_when_gate_identity_response_is_not_exact(
    monkeypatch,
    identity_payload: str,
) -> None:
    def fake_remote_command(**kwargs: object) -> subprocess.CompletedProcess[str]:
        command = kwargs["command"]
        assert isinstance(command, str)
        stdout = {
            "uname -s": "Linux\n",
            "id -un": "deployer\n",
            f"sudo -n {deploy.ROOT_RUNNER_PATH} identity": identity_payload,
        }[command]
        return subprocess.CompletedProcess([], 0, stdout=stdout)

    monkeypatch.setattr(deploy, "_run_remote_command", fake_remote_command)

    with pytest.raises(deploy.DeployError):
        deploy._assert_linux_target(ssh="ssh", inventory=_inventory())


def test_deployment_security_audit_is_canonical_json_and_redacted(capsys) -> None:
    deploy._print_audit(
        action="deploy",
        outcome="denied",
        subject="deploy.env",
        token="not-for-output",  # secret-scan: allow; reason: disposable test fixture
    )

    output = capsys.readouterr().out.strip()
    assert output.startswith("security_audit=")
    assert "not-for-output" not in output
    payload = json.loads(output.removeprefix("security_audit="))
    assert payload["details"]["token"] == "[REDACTED]"
    assert payload["outcome"] == "denied"


def test_systemd_templates_run_as_the_unprivileged_service_identity() -> None:
    for template_name in ("health.service.in", "scheduler.service.in", "dashboard.service.in"):
        template = (PROJECT_ROOT / "infra" / "systemd" / template_name).read_text(
            encoding="utf-8"
        )
        assert "User=@SERVICE_USER@" in template
        assert "Group=@SERVICE_USER@" in template
        assert "UMask=0077" in template
        assert "NoNewPrivileges=true" in template
        assert "ProtectSystem=strict" in template
        assert "CapabilityBoundingSet=" in template
        assert "AmbientCapabilities=" in template
        assert "ProtectClock=true" in template
        assert "ProtectHostname=true" in template
        assert "ProtectKernelLogs=true" in template
        assert "RestrictNamespaces=true" in template
        assert "ProtectProc=invisible" in template
        assert "SystemCallFilter=@system-service" in template
        assert "@SHARED_DIR@" not in template
        assert "@UV_CACHE_DIR@" in template
        assert "/var/run/docker.sock" not in template

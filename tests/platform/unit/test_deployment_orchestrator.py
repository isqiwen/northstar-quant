from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.deploy import deploy
from scripts.deploy.control_bundle import ControlArtifact
from scripts.deploy.inventory import DeploymentInventory, SshTarget
from scripts.deploy.package import Artifact
from scripts.deploy.preflight import PreflightReport
from scripts.deploy.root_release_runner import Submission


def _inventory(
    *,
    ntfy_deploy_enabled: bool = False,
    values: dict[str, str] | None = None,
) -> DeploymentInventory:
    return DeploymentInventory(
        source=Path("deploy.env"),
        ssh_target=SshTarget(
            authority="deployer@[2001:db8::10]",
            host="[2001:db8::10]",
            deploy_user="deployer",
        ),
        app_name="northstar-quant",
        service_user="northstar",
        systemd_service_name="northstar-quant",
        service_mode="health",
        python_version="3.12",
        keep_releases=2,
        remote_tmp="/tmp",
        dashboard_deploy_enabled=False,
        ntfy_deploy_enabled=ntfy_deploy_enabled,
        values=values or {},
    )


def _artifact(path: Path | None = None) -> Artifact:
    revision = "a" * 40
    return Artifact(
        path=path or Path("northstar-quant-test.tar.gz"),
        release_id=f"{revision}-20260822000000",
        sha256="b" * 64,
        revision=revision,
    )


def test_remote_command_requires_strict_host_verification_and_timeout(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="Linux\n")

    monkeypatch.setattr(deploy.subprocess, "run", fake_run)

    deploy._run_remote_command(
        ssh="ssh",
        host="deployer@[2001:db8::10]",
        command="uname -s",
        capture_output=True,
    )

    command, kwargs = calls[0]
    assert "StrictHostKeyChecking=yes" in command
    assert command[-2:] == ["deployer@[2001:db8::10]", "uname -s"]
    assert kwargs["timeout"] == deploy._REMOTE_COMMAND_TIMEOUT_SECONDS
    assert kwargs["check"] is False


def test_apply_quality_gates_are_complete_and_not_configurable(monkeypatch, tmp_path: Path) -> None:
    commands: list[tuple[str, ...]] = []

    monkeypatch.setattr(deploy.shutil, "which", lambda name: "uv" if name == "uv" else None)

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append(tuple(command))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(deploy.subprocess, "run", fake_run)

    deploy._run_quality_gates(project_root=tmp_path)

    assert commands == [
        (sys.executable, "scripts/ci/check_dependency_policy.py"),
        ("uv", "lock", "--check", "--offline"),
        (sys.executable, "scripts/ci/check_secrets.py"),
        ("uv", "run", "--offline", "--no-sync", "ruff", "check", "."),
        (
            "uv",
            "run",
            "--offline",
            "--no-sync",
            "python",
            "scripts/ci/check_mypy_baseline.py",
            "check",
        ),
        ("uv", "run", "--offline", "--no-sync", "pytest"),
    ]
    parser = deploy._build_parser()
    for bypass in ("--allow-dirty", "--skip-ruff", "--skip-tests", "--keep-remote-staging"):
        with pytest.raises(SystemExit):
            parser.parse_args([bypass])


class _NonClosingBuffer(io.BytesIO):
    """Keep the submitted bytes observable after the controller closes stdin."""

    closed_by_controller: bool = False

    def close(self) -> None:
        self.closed_by_controller = True


class _GateProcess:
    def __init__(self, *, return_code: int = 0) -> None:
        self.stdin = _NonClosingBuffer()
        self.return_code = return_code
        self.wait_timeout: int | None = None
        self.terminated = False

    def wait(self, *, timeout: int) -> int:
        self.wait_timeout = timeout
        return self.return_code

    def terminate(self) -> None:
        self.terminated = True


def test_submit_release_gate_streams_framed_bytes_through_the_fixed_sudo_verb(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime.tar.gz"
    control = tmp_path / "control.tar.gz"
    runtime.write_bytes(b"runtime-bytes")
    control.write_bytes(b"control-bytes")
    submission = Submission(
        manifest=b'{"release":"test"}',
        signature=b"release-signature",
        runtime_path=runtime,
        control_path=control,
        environment_path=None,
    )
    calls: list[tuple[list[str], dict[str, object]]] = []
    process = _GateProcess()

    def fake_popen(command: list[str], **kwargs: object) -> _GateProcess:
        calls.append((command, kwargs))
        return process

    monkeypatch.setattr(deploy.subprocess, "Popen", fake_popen)

    deploy._submit_release_gate(
        ssh="ssh",
        host="deployer@host.example.test",
        submission=submission,
    )

    assert calls == [
        (
            [
                "ssh",
                *deploy._SSH_OPTIONS,
                "deployer@host.example.test",
                f"sudo -n {deploy.ROOT_RUNNER_PATH} submit",
            ],
            {"stdin": subprocess.PIPE},
        )
    ]
    assert process.wait_timeout == deploy._RELEASE_GATE_TIMEOUT_SECONDS
    assert process.stdin.closed_by_controller is True
    payload = process.stdin.getvalue()
    assert payload.startswith(b"NSRGATE1\x00")
    assert b"runtime.tar.gz" not in payload
    assert b"control.tar.gz" not in payload
    assert b"/tmp" not in payload


def test_submit_release_gate_does_not_retry_a_rejected_transaction(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / "runtime.tar.gz"
    control = tmp_path / "control.tar.gz"
    runtime.write_bytes(b"runtime")
    control.write_bytes(b"control")
    process = _GateProcess(return_code=1)
    monkeypatch.setattr(deploy.subprocess, "Popen", lambda *args, **kwargs: process)

    with pytest.raises(deploy.DeployError, match="durable transaction"):
        deploy._submit_release_gate(
            ssh="ssh",
            host="deployer@host.example.test",
            submission=Submission(
                manifest=b"manifest",
                signature=b"signature",
                runtime_path=runtime,
                control_path=control,
                environment_path=None,
            ),
        )

    assert process.terminated is False


def test_release_profile_is_complete_non_secret_and_uses_fixed_runtime_defaults() -> None:
    profile = deploy._release_profile(
        inventory=_inventory(),
        setup_server=True,
        confirm_live_deploy="NO",
        uv_version="0.9.0",
    )

    assert profile["setup_server"] == "1"
    assert profile["confirm_live_deploy"] == "NO"
    assert profile["runtime_storage_dir"] == "/var/lib/northstar/storage"
    assert profile["runtime_downloads_dir"] == "/var/lib/northstar/downloads"
    assert profile["runtime_reports_dir"] == "/var/lib/northstar/reports"
    assert profile["runtime_log_dir"] == "/var/log/northstar/app"
    assert profile["runtime_cache_dir"] == "/var/cache/northstar/runtime"
    assert profile["runtime_matplotlib_dir"] == "/var/cache/northstar/matplotlib"
    assert "remote_tmp" not in profile
    assert "deploy_host" not in profile
    assert all("SECRET" not in field and "TOKEN" not in field for field in profile)


def test_release_profile_rejects_ntfy_until_it_has_a_signed_gate_workflow() -> None:
    with pytest.raises(deploy.DeployError, match="NTFY_DEPLOY_ENABLED=1"):
        deploy._release_profile(
            inventory=_inventory(ntfy_deploy_enabled=True),
            setup_server=False,
            confirm_live_deploy="NO",
            uv_version="0.9.0",
        )


def test_deploy_to_linux_requires_explicit_release_authority_key(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(deploy.shutil, "which", lambda name: "ssh" if name == "ssh" else None)
    monkeypatch.setattr(
        deploy,
        "_assert_linux_target",
        lambda **kwargs: pytest.fail("missing signing key must fail before remote inspection"),
    )

    with pytest.raises(deploy.DeployError, match="--signing-key"):
        deploy._deploy_to_linux(
            project_root=tmp_path,
            inventory=_inventory(),
            artifact=_artifact(),
            env_file=None,
            args=SimpleNamespace(signing_key=None, setup_server=False, confirm_live_deploy="NO"),
        )


def test_deploy_to_linux_submits_signed_gate_request_without_remote_staging(
    monkeypatch,
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "runtime.tar.gz"
    artifact_path.write_bytes(b"runtime")
    artifact = _artifact(artifact_path)
    control_path = tmp_path / "control.tar.gz"
    control_path.write_bytes(b"control")
    manifest_inputs: dict[str, object] = {}
    submission_calls: list[dict[str, object]] = []

    monkeypatch.setattr(deploy.shutil, "which", lambda name: "ssh" if name == "ssh" else None)
    monkeypatch.setattr(deploy, "_local_uv_version", lambda: "0.9.0")
    monkeypatch.setattr(
        deploy,
        "_assert_linux_target",
        lambda **kwargs: deploy.GateIdentity("c" * 64, deploy.GATE_PROTOCOL),
    )
    monkeypatch.setattr(
        deploy,
        "build_control_artifact",
        lambda **kwargs: ControlArtifact(control_path, "d" * 64, control_path.stat().st_size),
    )

    def fake_build_manifest(**kwargs: object) -> object:
        manifest_inputs.update(kwargs)
        return object()

    monkeypatch.setattr(deploy, "build_manifest", fake_build_manifest)
    monkeypatch.setattr(deploy, "canonical_manifest_bytes", lambda manifest: b"canonical-manifest")
    monkeypatch.setattr(deploy, "sign_manifest", lambda **kwargs: b"manifest-signature")
    monkeypatch.setattr(
        deploy,
        "_submit_release_gate",
        lambda **kwargs: submission_calls.append(kwargs),
    )
    monkeypatch.setattr(
        deploy,
        "_run_remote_command",
        lambda **kwargs: pytest.fail("normal deployment must not execute a remote staging shell"),
    )

    deploy._deploy_to_linux(
        project_root=tmp_path,
        inventory=_inventory(),
        artifact=artifact,
        env_file=None,
        args=SimpleNamespace(
            signing_key=tmp_path / "release-authority",
            setup_server=False,
            confirm_live_deploy="NO",
        ),
    )

    assert manifest_inputs["gate_identity"] == "c" * 64
    assert manifest_inputs["runtime_bundle"] == artifact_path
    assert manifest_inputs["control_bundle"] == control_path
    assert manifest_inputs["environment_upload"] is False
    profile = manifest_inputs["profile"]
    assert isinstance(profile, dict)
    assert "remote_tmp" not in profile
    assert len(submission_calls) == 1
    submitted = submission_calls[0]["submission"]
    assert isinstance(submitted, Submission)
    assert submitted.manifest == b"canonical-manifest"
    assert submitted.signature == b"manifest-signature"
    assert submitted.runtime_path == artifact_path
    assert submitted.control_path == control_path
    assert submitted.environment_path is None


def test_main_dry_run_does_not_invoke_ssh_or_apply_quality_gates(monkeypatch, tmp_path: Path) -> None:
    inventory = _inventory()
    artifact = _artifact()
    monkeypatch.setattr(sys, "argv", ["deploy.py", "--project-root", str(tmp_path)])
    monkeypatch.setattr(deploy, "load_inventory", lambda path: inventory)
    monkeypatch.setattr(deploy, "run_preflight", lambda **kwargs: PreflightReport(checks=["ok"]))
    monkeypatch.setattr(deploy, "build_artifact", lambda **kwargs: artifact)
    monkeypatch.setattr(
        deploy,
        "_run_quality_gates",
        lambda **kwargs: pytest.fail("dry-run must not run apply quality gates"),
    )
    monkeypatch.setattr(
        deploy,
        "_deploy_to_linux",
        lambda **kwargs: pytest.fail("dry-run must not connect to Linux"),
    )

    assert deploy.main() == 0


def test_main_apply_requires_signing_key_before_loading_inventory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "argv", ["deploy.py", "--project-root", str(tmp_path), "--apply"])
    monkeypatch.setattr(
        deploy,
        "load_inventory",
        lambda path: pytest.fail("missing signing key must fail before inventory access"),
    )

    assert deploy.main() == 2


def test_main_apply_runs_quality_gates_before_clean_packaging_and_gate_submission(
    monkeypatch,
    tmp_path: Path,
) -> None:
    inventory = _inventory()
    artifact = _artifact()
    events: list[str] = []
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "deploy.py",
            "--project-root",
            str(tmp_path),
            "--apply",
            "--signing-key",
            str(tmp_path / "release-authority"),
        ],
    )
    monkeypatch.setattr(deploy, "load_inventory", lambda path: inventory)
    monkeypatch.setattr(deploy, "run_preflight", lambda **kwargs: PreflightReport(checks=["ok"]))
    monkeypatch.setattr(deploy, "_run_quality_gates", lambda **kwargs: events.append("quality"))

    def fake_build_artifact(**kwargs: object) -> Artifact:
        assert kwargs["require_clean_commit"] is True
        events.append("package")
        return artifact

    monkeypatch.setattr(deploy, "build_artifact", fake_build_artifact)
    monkeypatch.setattr(deploy, "_deploy_to_linux", lambda **kwargs: events.append("gate"))

    assert deploy.main() == 0
    assert events == ["quality", "package", "gate"]

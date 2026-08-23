"""Pure contracts for the fixed root release-gate wire protocol."""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import struct
from types import SimpleNamespace

import pytest

from scripts.deploy import root_release_runner as runner
from scripts.deploy.release_signing import environment_signature_payload as signing_environment_payload


_GATE_IDENTITY = "a" * 64
_RUNTIME_DIGEST = "b" * 64
_CONTROL_DIGEST = "c" * 64
_ENTRY_DIGEST = "d" * 64


def _profile() -> dict[str, str]:
    return {
        "app_name": "northstar-quant",
        "confirm_live_deploy": "NO",
        "dashboard_deploy_enabled": "0",
        "keep_releases": "2",
        "ntfy_deploy_enabled": "0",
        "python_version": "3.12",
        "runtime_cache_dir": "/var/cache/northstar/runtime",
        "runtime_downloads_dir": "/var/lib/northstar/downloads",
        "runtime_log_dir": "/var/log/northstar/app",
        "runtime_matplotlib_dir": "/var/cache/northstar/matplotlib",
        "runtime_reports_dir": "/var/lib/northstar/reports",
        "runtime_storage_dir": "/var/lib/northstar/storage",
        "service_mode": "health",
        "service_user": "northstar",
        "setup_server": "0",
        "systemd_service_name": "northstar-quant",
        "uv_version": "0.8.16",
    }


def _bundle(*, path: str, digest: str) -> dict[str, object]:
    return {
        "entries": [
            {
                "kind": "file",
                "mode": 0o640,
                "path": path,
                "sha256": _ENTRY_DIGEST,
                "size_bytes": 1,
            }
        ],
        "sha256": digest,
        "size_bytes": 1,
    }


def _manifest_payload() -> dict[str, object]:
    return {
        "control": _bundle(path=runner.CONTROL_ENTRYPOINT, digest=_CONTROL_DIGEST),
        "created_at": "2026-08-23T00:00:00Z",
        "entrypoint": runner.CONTROL_ENTRYPOINT,
        "environment_upload": False,
        "format": runner.MANIFEST_FORMAT,
        "gate_identity": _GATE_IDENTITY,
        "gate_protocol": runner.GATE_PROTOCOL,
        "profile": _profile(),
        "release_id": "abcdef123456-20260823000000",
        "revision": "1" * 40,
        "runtime": _bundle(path="DEPLOY_ARTIFACT_META.txt", digest=_RUNTIME_DIGEST),
    }


def _canonical_manifest(payload: dict[str, object] | None = None) -> bytes:
    return json.dumps(
        payload or _manifest_payload(),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _submission(tmp_path: Path, *, signature: bytes = b"detached-signature") -> runner.Submission:
    runtime = tmp_path / "runtime.tar.gz"
    control = tmp_path / "control.tar.gz"
    runtime.write_bytes(b"runtime archive")
    control.write_bytes(b"control archive")
    return runner.Submission(
        manifest=_canonical_manifest(),
        signature=signature,
        runtime_path=runtime,
        control_path=control,
        environment_path=None,
    )


def _read_u32(stream: BytesIO) -> int:
    return struct.unpack("!I", stream.read(4))[0]


def _read_u64(stream: BytesIO) -> int:
    return struct.unpack("!Q", stream.read(8))[0]


def test_parse_manifest_accepts_exact_canonical_bytes() -> None:
    manifest = runner.parse_manifest(
        _canonical_manifest(),
        expected_gate_identity=_GATE_IDENTITY,
    )

    assert manifest.release_id == "abcdef123456-20260823000000"
    assert manifest.revision == "1" * 40
    assert manifest.runtime.sha256 == _RUNTIME_DIGEST
    assert manifest.control.sha256 == _CONTROL_DIGEST


def test_parse_manifest_rejects_noncanonical_json_bytes() -> None:
    noncanonical = _canonical_manifest() + b"\n"

    with pytest.raises(runner.RootReleaseRunnerError, match="canonical"):
        runner.parse_manifest(noncanonical, expected_gate_identity=_GATE_IDENTITY)


def test_parse_manifest_rejects_duplicate_keys_before_trusting_contents() -> None:
    raw = b'{"release_id":"first","release_id":"second"}'

    with pytest.raises(runner.RootReleaseRunnerError, match="duplicate"):
        runner.parse_manifest(raw, expected_gate_identity=_GATE_IDENTITY)


@pytest.mark.parametrize("unsafe_path", ("/tmp/release.tar.gz", "../staging.tar.gz", r"C:\\staging"))
def test_parse_manifest_rejects_source_or_staging_bundle_paths(unsafe_path: str) -> None:
    payload = _manifest_payload()
    runtime = payload["runtime"]
    assert isinstance(runtime, dict)
    entries = runtime["entries"]
    assert isinstance(entries, list)
    entry = entries[0]
    assert isinstance(entry, dict)
    entry["path"] = unsafe_path

    with pytest.raises(runner.RootReleaseRunnerError, match="unsafe"):
        runner.parse_manifest(_canonical_manifest(payload), expected_gate_identity=_GATE_IDENTITY)


def test_parse_manifest_rejects_wrong_root_gate_identity() -> None:
    with pytest.raises(runner.RootReleaseRunnerError, match="different root gate identity"):
        runner.parse_manifest(_canonical_manifest(), expected_gate_identity="e" * 64)


def test_write_submission_frames_only_bytes_and_never_serializes_source_paths(tmp_path: Path) -> None:
    submission = _submission(tmp_path)
    destination = BytesIO()

    runner.write_submission(destination, submission)

    wire = destination.getvalue()
    assert str(tmp_path).encode("utf-8") not in wire
    assert b"runtime.tar.gz" not in wire
    assert b"control.tar.gz" not in wire

    stream = BytesIO(wire)
    assert stream.read(len(runner._MAGIC)) == runner._MAGIC
    manifest_length = _read_u32(stream)
    assert stream.read(manifest_length) == submission.manifest
    signature_length = _read_u32(stream)
    assert stream.read(signature_length) == submission.signature
    runtime_length = _read_u64(stream)
    assert stream.read(runtime_length) == b"runtime archive"
    control_length = _read_u64(stream)
    assert stream.read(control_length) == b"control archive"
    assert _read_u64(stream) == 0
    assert _read_u32(stream) == 0
    assert stream.read() == b""


def test_write_submission_rejects_missing_detached_signature(tmp_path: Path) -> None:
    submission = _submission(tmp_path, signature=b"")

    with pytest.raises(runner.RootReleaseRunnerError, match="signature"):
        runner.write_submission(BytesIO(), submission)


def test_environment_signature_payload_matches_the_controller_protocol() -> None:
    environment = b"NORTHSTAR_BROKER=paper\n"

    assert runner.environment_signature_payload(
        release_id="abcdef123456-20260823000000",
        environment=environment,
    ) == signing_environment_payload(
        release_id="abcdef123456-20260823000000",
        environment=environment,
    )


def test_root_gate_refuses_non_linux_or_nonroot_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner.sys, "platform", "win32")
    monkeypatch.setattr(runner.os, "geteuid", lambda: 0, raising=False)

    with pytest.raises(runner.RootReleaseRunnerError, match="Linux root"):
        runner._assert_linux_root()


def test_control_failure_requires_recovery_review_without_automatic_rollback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest = runner.parse_manifest(
        _canonical_manifest(),
        expected_gate_identity=_GATE_IDENTITY,
    )
    commands: list[list[str]] = []

    monkeypatch.setattr(runner, "_assert_root_controlled_path", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner.os, "unlink", lambda *args, **kwargs: pytest.fail("must retain evidence"))

    def _failed_control(command: list[str], **kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(runner.subprocess, "run", _failed_control)

    with pytest.raises(runner.RootReleaseRunnerError, match="durable transaction requires recovery review"):
        runner._invoke_control_entrypoint(
            transaction_dir=tmp_path / "transaction",
            manifest=manifest,
            artifact_candidate=tmp_path / "artifact.tar.gz",
            environment_candidate=None,
        )

    assert commands == [
        [
            "/bin/bash",
            "-p",
            str(tmp_path / "transaction" / "control" / runner.CONTROL_ENTRYPOINT),
        ]
    ]

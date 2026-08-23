from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.deploy import release_signing


def test_sign_manifest_uses_fixed_openssh_namespace_and_returns_detached_signature(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    signing_key = tmp_path / "release-signing-key"
    signing_key.write_text("private material is intentionally test-only\n", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(release_signing.shutil, "which", lambda name: "/usr/bin/ssh-keygen")

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        manifest = Path(command[-1])
        manifest.with_suffix(f"{manifest.suffix}.sig").write_bytes(
            b"-----BEGIN SSH SIGNATURE-----\ntrusted-test-signature\n"
        )
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(release_signing.subprocess, "run", fake_run)

    signature = release_signing.sign_manifest(
        manifest_bytes=b'{"format":"northstar.release-manifest.v1"}',
        signing_key=signing_key,
    )

    assert signature.startswith(b"-----BEGIN SSH SIGNATURE-----")
    assert calls[0][1:5] == ["-Y", "sign", "-f", str(signing_key.resolve())]
    assert release_signing.SIGNATURE_NAMESPACE in calls[0]


def test_sign_environment_binds_opaque_configuration_to_the_release_identifier(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    signing_key = tmp_path / "release-signing-key"
    environment = tmp_path / ".env"
    signing_key.write_text("private material is intentionally test-only\n", encoding="utf-8")
    environment.write_text("NORTHSTAR_BROKER=paper\n", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(release_signing.shutil, "which", lambda name: "/usr/bin/ssh-keygen")

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        signed_payload = Path(command[-1])
        assert b"NORTHSTAR_BROKER=paper" in signed_payload.read_bytes()
        signed_payload.with_suffix(f"{signed_payload.suffix}.sig").write_bytes(
            b"-----BEGIN SSH SIGNATURE-----\ntrusted-test-signature\n"
        )
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(release_signing.subprocess, "run", fake_run)

    signature = release_signing.sign_environment(
        release_id="abc123-20260823",
        environment_path=environment,
        signing_key=signing_key,
    )

    assert signature.startswith(b"-----BEGIN SSH SIGNATURE-----")
    assert calls[0][calls[0].index("-n") + 1] == release_signing.ENVIRONMENT_SIGNATURE_NAMESPACE


def test_environment_signature_payload_never_accepts_an_unbound_or_empty_secret() -> None:
    with pytest.raises(release_signing.ReleaseSigningError, match="release identifier"):
        release_signing.environment_signature_payload(release_id="../../bad", environment=b"value")
    with pytest.raises(release_signing.ReleaseSigningError, match="size"):
        release_signing.environment_signature_payload(release_id="abc123", environment=b"")


def test_verify_manifest_signature_uses_fixed_principal_and_namespace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    allowed_signers = tmp_path / "allowed-signers"
    allowed_signers.write_text("northstar-release ssh-ed25519 AAAA test\n", encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(release_signing.shutil, "which", lambda name: "/usr/bin/ssh-keygen")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured["command"] = command
        captured["input"] = kwargs["input"]
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(release_signing.subprocess, "run", fake_run)

    release_signing.verify_manifest_signature(
        manifest_bytes=b"{}",
        signature=b"-----BEGIN SSH SIGNATURE-----\ntrusted-test-signature\n",
        allowed_signers=allowed_signers,
    )

    command = captured["command"]
    assert isinstance(command, list)
    assert command[1:3] == ["-Y", "verify"]
    assert command[command.index("-I") + 1] == release_signing.SIGNER_PRINCIPAL
    assert command[command.index("-n") + 1] == release_signing.SIGNATURE_NAMESPACE
    assert captured["input"] == b"{}"


@pytest.mark.parametrize(
    "signature",
    (
        pytest.param(b"", id="empty"),
        pytest.param(b"not-an-openssh-signature", id="wrong-preamble"),
        pytest.param(b"x" * 65_537, id="oversized"),
    ),
)
def test_verify_rejects_invalid_signature_before_calling_openssh(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    signature: bytes,
) -> None:
    allowed_signers = tmp_path / "allowed-signers"
    allowed_signers.write_text("northstar-release ssh-ed25519 AAAA test\n", encoding="utf-8")
    monkeypatch.setattr(
        release_signing.shutil,
        "which",
        lambda name: pytest.fail("invalid signature must not invoke ssh-keygen"),
    )

    with pytest.raises(release_signing.ReleaseSigningError, match="signature"):
        release_signing.verify_manifest_signature(
            manifest_bytes=b"{}",
            signature=signature,
            allowed_signers=allowed_signers,
        )

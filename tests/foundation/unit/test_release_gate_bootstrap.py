"""Unit coverage for the one-time root release-gate bootstrap boundary."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.deploy import release_gate_bootstrap


def test_bootstrap_requires_an_apply_flag_and_exact_manual_confirmation() -> None:
    with pytest.raises(release_gate_bootstrap.ReleaseGateBootstrapError, match="--apply"):
        release_gate_bootstrap._require_explicit_root_confirmation(
            apply=False,
            confirmation=release_gate_bootstrap.ROOT_CONFIRMATION,
        )
    with pytest.raises(release_gate_bootstrap.ReleaseGateBootstrapError, match="confirm-root-gate-bootstrap"):
        release_gate_bootstrap._require_explicit_root_confirmation(
            apply=True,
            confirmation="YES",
        )


def test_bootstrap_fails_closed_without_linux_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_gate_bootstrap.platform, "system", lambda: "Windows")
    monkeypatch.setattr(release_gate_bootstrap.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(release_gate_bootstrap.os, "geteuid", lambda: 0, raising=False)

    with pytest.raises(release_gate_bootstrap.ReleaseGateBootstrapError, match="Linux x86_64 root"):
        release_gate_bootstrap._require_linux_root()


def test_bootstrap_fails_closed_on_non_x86_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_gate_bootstrap.platform, "system", lambda: "Linux")
    monkeypatch.setattr(release_gate_bootstrap.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(release_gate_bootstrap.os, "geteuid", lambda: 0, raising=False)

    with pytest.raises(release_gate_bootstrap.ReleaseGateBootstrapError, match="Linux x86_64 root"):
        release_gate_bootstrap._require_linux_root()


@pytest.mark.parametrize("value", ("", "A" * 64, "a" * 63, "g" * 64))
def test_bootstrap_requires_exact_lowercase_digests(value: str) -> None:
    with pytest.raises(release_gate_bootstrap.ReleaseGateBootstrapError, match="SHA-256"):
        release_gate_bootstrap._validate_sha256(value, label="reviewed gate source")


def test_bootstrap_compares_the_exact_reviewed_gate_bytes() -> None:
    payload = b"#!/usr/bin/env python3\nprint('reviewed')\n"
    digest = hashlib.sha256(payload).hexdigest()

    assert release_gate_bootstrap._verify_expected_digest(
        payload=payload,
        expected_sha256=digest,
        label="reviewed gate source",
    ) == digest
    with pytest.raises(release_gate_bootstrap.ReleaseGateBootstrapError, match="does not match"):
        release_gate_bootstrap._verify_expected_digest(
            payload=payload,
            expected_sha256="a" * 64,
            label="reviewed gate source",
        )


def test_bootstrap_rejects_known_ssh_staging_paths() -> None:
    assert release_gate_bootstrap._is_untrusted_staging_path(Path("/tmp/release-gate.py"))
    assert release_gate_bootstrap._is_untrusted_staging_path(Path("/var/tmp/release-gate.py"))
    assert release_gate_bootstrap._is_untrusted_staging_path(Path("/run/user/1000/release-gate.py"))
    assert not release_gate_bootstrap._is_untrusted_staging_path(
        Path("/root/review/root_release_runner.py")
    )


def test_bootstrap_accepts_only_public_release_signers() -> None:
    valid = b"northstar-release ssh-ed25519 cHVibGljLWtleQ== operator@example\n"
    release_gate_bootstrap._validate_allowed_signers(valid)

    with pytest.raises(release_gate_bootstrap.ReleaseGateBootstrapError, match="private"):
        release_gate_bootstrap._validate_allowed_signers(
            b"-----BEGIN OPENSSH PRIVATE KEY-----\n"  # secret-scan: allow; reason: disposable test fixture
        )
    with pytest.raises(release_gate_bootstrap.ReleaseGateBootstrapError, match="northstar-release"):
        release_gate_bootstrap._validate_allowed_signers(
            b"unexpected ssh-ed25519 cHVibGljLWtleQ== operator@example\n"
        )


def test_bootstrap_wrapper_is_fixed_and_uses_isolated_python() -> None:
    wrapper = release_gate_bootstrap._ROOT_GATE_WRAPPER

    assert b"/usr/bin/python3 -I " in wrapper
    assert b"/usr/local/libexec/northstar-quant/root_release_runner.py" in wrapper
    assert b"$PATH" not in wrapper

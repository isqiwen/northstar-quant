from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from scripts.deploy import artifact_handoff


@pytest.mark.parametrize("release_id", ("", ".hidden", "../escape", "release/escape", "release id"))
def test_artifact_handoff_rejects_unsafe_release_identifier(release_id: str) -> None:
    with pytest.raises(artifact_handoff.ArtifactHandoffError, match="release identifier"):
        artifact_handoff._candidate_name(release_id)


@pytest.mark.parametrize("expected_sha256", ("", "A" * 64, "a" * 63, "g" * 64))
def test_artifact_handoff_requires_an_exact_lowercase_sha256(expected_sha256: str) -> None:
    with pytest.raises(artifact_handoff.ArtifactHandoffError, match="SHA-256"):
        artifact_handoff._validate_expected_sha256(expected_sha256)


def test_artifact_handoff_hashes_the_exact_stream_while_copying(tmp_path: Path) -> None:
    payload = b"northstar artifact\x00payload" * 32
    source_read, source_write = os.pipe()
    target_path = tmp_path / "candidate.tar.gz"
    target_fd = os.open(target_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(source_write, payload)
        os.close(source_write)
        source_write = -1

        copied_bytes, actual_sha256 = artifact_handoff._copy_stream(
            source_fd=source_read,
            target_fd=target_fd,
        )
    finally:
        os.close(source_read)
        if source_write >= 0:
            os.close(source_write)
        os.close(target_fd)

    assert copied_bytes == len(payload)
    assert actual_sha256 == hashlib.sha256(payload).hexdigest()
    assert target_path.read_bytes() == payload


def test_artifact_handoff_rejects_streams_above_the_fixed_limit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = b"artifact-bytes"
    source_read, source_write = os.pipe()
    target_fd = os.open(
        tmp_path / "candidate.tar.gz",
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    monkeypatch.setattr(artifact_handoff, "_MAX_ARTIFACT_BYTES", len(payload) - 1)
    try:
        os.write(source_write, payload)
        os.close(source_write)
        source_write = -1
        with pytest.raises(artifact_handoff.ArtifactHandoffError, match="size limit"):
            artifact_handoff._copy_stream(source_fd=source_read, target_fd=target_fd)
    finally:
        os.close(source_read)
        if source_write >= 0:
            os.close(source_write)
        os.close(target_fd)


def test_artifact_handoff_fails_closed_without_linux_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifact_handoff.platform, "system", lambda: "Windows")
    monkeypatch.setattr(artifact_handoff.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(artifact_handoff.os, "geteuid", lambda: 0, raising=False)

    with pytest.raises(artifact_handoff.ArtifactHandoffError, match="requires Linux x86_64 root"):
        artifact_handoff.receive_from_standard_input(
            release_id="release-20260822",
            expected_sha256="a" * 64,
        )


def test_artifact_handoff_fails_closed_on_non_x86_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(artifact_handoff.platform, "system", lambda: "Linux")
    monkeypatch.setattr(artifact_handoff.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(artifact_handoff.os, "geteuid", lambda: 0, raising=False)

    with pytest.raises(artifact_handoff.ArtifactHandoffError, match="requires Linux x86_64 root"):
        artifact_handoff.receive_from_standard_input(
            release_id="release-20260822",
            expected_sha256="a" * 64,
        )

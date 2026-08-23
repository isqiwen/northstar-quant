from __future__ import annotations

import io
import json
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.deploy.release_manifest import (
    BundleDescriptor,
    BundleEntry,
    ReleaseManifest,
    ReleaseManifestError,
    build_manifest,
    bundle_descriptor,
    canonical_manifest_bytes,
    parse_manifest,
    verify_bundle,
)


_PROFILE = {
    "app_name": "northstar-quant",
    "confirm_live_deploy": "NO",
    "dashboard_deploy_enabled": "0",
    "keep_releases": "5",
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


def _tar(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.mode = 0o644
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


def _manifest(tmp_path: Path) -> ReleaseManifest:
    runtime = tmp_path / "runtime.tar.gz"
    control = tmp_path / "control.tar.gz"
    _tar(runtime, {"pyproject.toml": b"[project]\n"})
    _tar(
        control,
        {
            "DEPLOY_CONTROL_META.json": b"{}",
            "scripts/deploy/gate_release.sh": b"#!/bin/bash -p\n",
        },
    )
    return build_manifest(
        release_id="abc123-20260823",
        revision="a" * 40,
        gate_identity="b" * 64,
        profile=_PROFILE,
        environment_upload=False,
        runtime_bundle=runtime,
        control_bundle=control,
        created_at=datetime(2026, 8, 23, 12, 0, tzinfo=UTC),
    )


def test_manifest_is_canonical_deterministic_and_public(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)

    first = canonical_manifest_bytes(manifest)
    second = canonical_manifest_bytes(manifest)
    parsed = parse_manifest(first)

    assert first == second
    assert parsed == manifest
    assert b"environment_hash" not in first
    assert b"token" not in first
    assert b"password" not in first
    assert b"DEPLOY_CONTROL_META.json" in first


def test_manifest_rejects_noncanonical_unknown_duplicate_and_secret_profile_fields(
    tmp_path: Path,
) -> None:
    raw = canonical_manifest_bytes(_manifest(tmp_path))
    payload = json.loads(raw)
    payload["unexpected"] = "no"
    with pytest.raises(ReleaseManifestError, match="fields"):
        parse_manifest(json.dumps(payload).encode("utf-8"))

    duplicate = raw[:-1] + b',"release_id":"other"}'
    with pytest.raises(ReleaseManifestError, match="duplicate"):
        parse_manifest(duplicate)

    profile = dict(_PROFILE)
    profile["token"] = "must-not-be-accepted"
    with pytest.raises(ReleaseManifestError, match="profile fields"):
        build_manifest(
            release_id="abc123-20260823",
            revision="a" * 40,
            gate_identity="b" * 64,
            profile=profile,
            environment_upload=False,
            runtime_bundle=tmp_path / "missing-runtime.tar.gz",
            control_bundle=tmp_path / "missing-control.tar.gz",
        )


def test_bundle_verification_rejects_tampered_missing_and_extra_members(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.tar.gz"
    _tar(bundle, {"pyproject.toml": b"one"})
    descriptor = bundle_descriptor(bundle)
    verify_bundle(bundle, descriptor)

    _tar(bundle, {"pyproject.toml": b"two"})
    with pytest.raises(ReleaseManifestError, match="immutable manifest"):
        verify_bundle(bundle, descriptor)

    _tar(bundle, {"pyproject.toml": b"one", "src/extra.py": b"extra"})
    with pytest.raises(ReleaseManifestError, match="immutable manifest"):
        verify_bundle(bundle, descriptor)


def test_descriptor_rejects_duplicate_unsafe_and_link_members(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.tar.gz"
    with tarfile.open(duplicate, "w:gz") as archive:
        for content in (b"one", b"two"):
            info = tarfile.TarInfo("same.txt")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    with pytest.raises(ReleaseManifestError, match="duplicate"):
        bundle_descriptor(duplicate)

    unsafe = tmp_path / "unsafe.tar.gz"
    _tar(unsafe, {"../escape": b"no"})
    with pytest.raises(ReleaseManifestError, match="unsafe"):
        bundle_descriptor(unsafe)

    link = tmp_path / "link.tar.gz"
    with tarfile.open(link, "w:gz") as archive:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "target"
        archive.addfile(info)
    with pytest.raises(ReleaseManifestError, match="non-regular"):
        bundle_descriptor(link)


def test_manifest_descriptor_requires_sorted_unique_paths() -> None:
    descriptor = BundleDescriptor(
        sha256="a" * 64,
        size_bytes=1,
        entries=(
            BundleEntry("z", "file", 0o644, 0, "b" * 64),
            BundleEntry("a", "file", 0o644, 0, "c" * 64),
        ),
    )
    manifest = ReleaseManifest(
        release_id="abc123-20260823",
        revision="a" * 40,
        created_at=datetime(2026, 8, 23, tzinfo=UTC),
        gate_identity="b" * 64,
        profile=_PROFILE,
        environment_upload=False,
        runtime=descriptor,
        control=descriptor,
    )
    with pytest.raises(ReleaseManifestError, match="sorted"):
        canonical_manifest_bytes(manifest)

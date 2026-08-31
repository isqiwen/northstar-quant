"""Narrow standalone CLI for local, governed factor-mining research.

Unlike the broad ``northstar`` CLI, this entry point deliberately has no import
edge to live operations.  It consumes only hash-addressed immutable bundle and
manifest artifacts from the local configured storage root.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final, cast

import typer
from typer.core import TyperCommand, TyperGroup

from northstar_quant.application.local_factor_research import (
    LocalFactorMiningResearchError,
    LocalFactorMiningResearchService,
)
from northstar_quant.data.artifacts.fingerprints import FingerprintError, require_sha256
from northstar_quant.data.artifacts.immutable_store import ArtifactStore, ArtifactStoreError
from northstar_quant.foundation.config.app_runtime import AppConfigError, load_app_runtime_paths
from northstar_quant.foundation.platform_support import (
    PlatformSupportError,
    require_linux_x86_64,
)


__all__ = ["app"]


_HELP_CONTEXT_SETTINGS: Final = {"help_option_names": ["-h", "--help"]}
_GROUP_KWARGS: Final[dict[str, Any]] = {
    "cls": TyperGroup,
    "context_settings": _HELP_CONTEXT_SETTINGS,
    "add_completion": False,
}
_COMMAND_KWARGS: Final[dict[str, Any]] = {
    "cls": TyperCommand,
    "context_settings": _HELP_CONTEXT_SETTINGS,
}

app = typer.Typer(
    help="Northstar 本地、受治理的因子研究命令。",
    **_GROUP_KWARGS,
)
factor_app = typer.Typer(
    help="只接受不可变研究 bundle hash 的因子挖掘操作。",
    **_GROUP_KWARGS,
)
app.add_typer(factor_app, name="factor")


@app.callback()
def _require_supported_host() -> None:
    """Reject unsupported hosts before resolving local research artifacts."""

    try:
        require_linux_x86_64()
    except PlatformSupportError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


def _service() -> LocalFactorMiningResearchService:
    """Resolve only the local artifact root; no broad settings/live profile is loaded."""

    runtime = load_app_runtime_paths(Path.cwd())
    return LocalFactorMiningResearchService(
        artifact_store=ArtifactStore(runtime.storage_dir / "artifacts")
    )


def _snapshot_hash(value: str, field_name: str) -> str:
    try:
        return require_sha256(value, field_name=field_name)
    except FingerprintError as exc:
        raise typer.BadParameter(
            "必须是精确的 64 位 SHA-256 制品快照 hash；不接受路径、latest 或原始数据。"
        ) from exc


def _emit(payload: dict[str, object]) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))


@factor_app.command("run", **_COMMAND_KWARGS)
def run_factor_bundle_command(
    bundle_snapshot: str = typer.Option(
        ...,
        "--bundle-snapshot",
        help="已发布的 LocalFactorMiningRunBundle derived-artifact SHA-256 快照。",
    ),
) -> None:
    """运行一份已经验证、固定且 hash-addressed 的本地研究声明。"""

    bundle_hash = _snapshot_hash(bundle_snapshot, "bundle_snapshot")
    try:
        result = _service().run(bundle_snapshot_hash=bundle_hash)
    except (AppConfigError, ArtifactStoreError, LocalFactorMiningResearchError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(result.as_mapping())


@factor_app.command("replay", **_COMMAND_KWARGS)
def replay_factor_bundle_command(
    bundle_snapshot: str = typer.Option(
        ...,
        "--bundle-snapshot",
        help="已发布的 LocalFactorMiningRunBundle derived-artifact SHA-256 快照。",
    ),
    expected_manifest_snapshot: str = typer.Option(
        ...,
        "--expected-manifest-snapshot",
        help="先前已发布的 LocalFactorMiningRunManifest SHA-256 快照。",
    ),
) -> None:
    """重放同一声明，并要求 manifest 与 result hash 完全一致。"""

    bundle_hash = _snapshot_hash(bundle_snapshot, "bundle_snapshot")
    manifest_hash = _snapshot_hash(expected_manifest_snapshot, "expected_manifest_snapshot")
    try:
        result = _service().replay(
            bundle_snapshot_hash=bundle_hash,
            expected_manifest_snapshot_hash=manifest_hash,
        )
    except (AppConfigError, ArtifactStoreError, LocalFactorMiningResearchError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(result.as_mapping())


@factor_app.command("inspect", **_COMMAND_KWARGS)
def inspect_factor_artifact_command(
    artifact_snapshot: str = typer.Option(
        ...,
        "--artifact-snapshot",
        help="已发布研究 definition、evidence 或 manifest 的 SHA-256 快照。",
    ),
) -> None:
    """检查一份已校验的本地研究制品，而不接受任何文件路径。"""

    artifact_hash = _snapshot_hash(artifact_snapshot, "artifact_snapshot")
    try:
        payload = _service().inspect(artifact_snapshot_hash=artifact_hash)
    except (AppConfigError, ArtifactStoreError, LocalFactorMiningResearchError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(cast(dict[str, object], payload))


if __name__ == "__main__":  # pragma: no cover - installed script invokes the Typer app.
    app()

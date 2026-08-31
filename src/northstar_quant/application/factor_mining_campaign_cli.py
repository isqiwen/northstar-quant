"""Hash-addressed CLI for durable local factor-mining campaigns.

This command surface is deliberately separate from ``northstar-research``.
It accepts only stable identifiers and immutable commitments; neither a raw
prompt, a provider configuration, a filesystem path, nor research data can
cross the command boundary.  The concrete runner composition is intentionally
provided by the local application runtime, so importing this module never
opens a database, a network connection, or a worker.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Final

import typer
from sqlalchemy.exc import SQLAlchemyError
from typer.core import TyperCommand, TyperGroup

from northstar_quant.application.ai_factor_mining import FactorCandidateGenerator
from northstar_quant.application.durable_factor_mining_campaign import (
    DurableFactorMiningCampaignRunner,
    FactorMiningCampaignDurabilityError,
    FactorMiningCampaignReplayAuthorizationIntent,
    FactorMiningCampaignRunRequest,
    PostgresFactorMiningCampaignLedger,
    build_local_factor_mining_campaign_runner,
)
from northstar_quant.data.artifacts.fingerprints import FingerprintError, require_sha256
from northstar_quant.data.artifacts.immutable_store import ArtifactStore, ArtifactStoreError
from northstar_quant.foundation.config.app_runtime import AppConfigError, load_app_runtime_paths
from northstar_quant.foundation.db.repositories import FactorMiningCampaignLedgerError
from northstar_quant.foundation.platform_support import (
    PlatformSupportError,
    require_linux_x86_64,
)


__all__ = ["app", "configure_local_factor_mining_campaign_generator"]


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
_RUN_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_ACTOR_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_FORBIDDEN_SAFE_TOKENS = frozenset(
    {"__import__", "compile", "eval", "exec", "latest", "open", "select", "shell"}
)

# A generator is a trusted in-process capability installed by a local runtime
# bootstrap.  It is intentionally not an environment setting or CLI value:
# this command must never turn user-supplied provider text/configuration into
# an execution capability.
_configured_generator: FactorCandidateGenerator | None = None

app = typer.Typer(
    help="Northstar durable local factor-mining campaign commands.",
    **_GROUP_KWARGS,
)
campaign_app = typer.Typer(
    help="Append-only, research-only campaign operations using IDs and SHA-256 commitments.",
    **_GROUP_KWARGS,
)
app.add_typer(campaign_app, name="campaign")


@app.callback()
def _require_supported_host() -> None:
    """Reject an unsupported host before any runtime dependency is resolved."""

    try:
        require_linux_x86_64()
    except PlatformSupportError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


def _sha256(value: str, field_name: str) -> str:
    try:
        return require_sha256(value, field_name=field_name)
    except FingerprintError as exc:
        raise typer.BadParameter(
            "must be an exact 64-character SHA-256 commitment; paths, latest, and raw text are refused."
        ) from exc


def _run_id(value: str, field_name: str) -> str:
    if _RUN_ID_RE.fullmatch(value) is None or value.casefold() == "latest":
        raise typer.BadParameter(
            "must be a lower-case stable identifier; SHA-256 commitments, paths, latest, and raw text are refused."
        )
    return value


def _actor_id(value: str, field_name: str) -> str:
    if (
        _ACTOR_ID_RE.fullmatch(value) is None
        or value.casefold() in _FORBIDDEN_SAFE_TOKENS
    ):
        raise typer.BadParameter(
            "must be a stable actor identifier; SHA-256 commitments, paths, latest, and raw text are refused."
        )
    return value


def configure_local_factor_mining_campaign_generator(
    generator: FactorCandidateGenerator,
) -> None:
    """Install one code-owned generator before invoking the campaign CLI.

    This is a process bootstrap seam, not a user configuration channel.  It
    permits a future reviewed local worker entry point to compose a provider
    adapter while keeping the public CLI hash/ID-only.  Replacing a generator
    in a running process is refused so a campaign cannot silently change
    provider behavior mid-process.
    """

    if generator is None:
        raise FactorMiningCampaignDurabilityError(
            "FACTOR_MINING_CAMPAIGN_GENERATOR_UNAVAILABLE"
        )
    global _configured_generator
    if _configured_generator is not None and generator is not _configured_generator:
        raise FactorMiningCampaignDurabilityError(
            "FACTOR_MINING_CAMPAIGN_GENERATOR_RECONFIGURATION_REFUSED"
        )
    _configured_generator = generator


def _artifact_store() -> ArtifactStore:
    """Resolve the governed local artifact root for an authorized run only."""

    runtime = load_app_runtime_paths(Path.cwd())
    return ArtifactStore(runtime.storage_dir / "artifacts")


def _runner() -> DurableFactorMiningCampaignRunner:
    """Compose PostgreSQL ledger, governed artifacts, and one trusted worker.

    The CLI cannot infer a provider.  If a reviewed process bootstrap has not
    installed one, refuse before touching the database or an artifact store.
    """

    generator = _configured_generator
    if generator is None:
        raise FactorMiningCampaignDurabilityError(
            "FACTOR_MINING_CAMPAIGN_GENERATOR_UNAVAILABLE"
        )
    return build_local_factor_mining_campaign_runner(
        artifact_store=_artifact_store(),
        generator=generator,
    )


def _ledger() -> PostgresFactorMiningCampaignLedger:
    """Return the concrete Foundation PostgreSQL reader/authorization seam."""

    return PostgresFactorMiningCampaignLedger()


def _emit(payload: dict[str, object]) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))


@campaign_app.command("run", **_COMMAND_KWARGS)
def run_campaign_command(
    run_id: str = typer.Option(..., "--run-id", help="One new stable campaign request ID."),
    actor_id: str = typer.Option(..., "--actor-id", help="Human or service actor identity."),
    declaration_snapshot: str = typer.Option(
        ...,
        "--declaration-snapshot",
        help="Published receipt-free campaign declaration SHA-256 snapshot.",
    ),
    replay_authorization_hash: str | None = typer.Option(
        None,
        "--replay-authorization-hash",
        help="Explicit replay authorization commitment for a new replay request.",
    ),
) -> None:
    """Reserve and execute one resource-bounded, research-only campaign."""

    request = FactorMiningCampaignRunRequest(
        run_id=_run_id(run_id, "run_id"),
        actor_id=_actor_id(actor_id, "actor_id"),
        declaration_snapshot_hash=_sha256(declaration_snapshot, "declaration_snapshot"),
        replay_authorization_hash=(
            None
            if replay_authorization_hash is None
            else _sha256(replay_authorization_hash, "replay_authorization_hash")
        ),
    )
    try:
        result = _runner().run(request)
    except (
        AppConfigError,
        ArtifactStoreError,
        FactorMiningCampaignDurabilityError,
    ) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(result.as_mapping())


@campaign_app.command("authorize-replay", **_COMMAND_KWARGS)
def authorize_replay_command(
    authorization_id: str = typer.Option(
        ...,
        "--authorization-id",
        help="Trusted-verifier-issued immutable human approval reference.",
    ),
    unresolved_request_hash: str = typer.Option(
        ...,
        "--unresolved-request-hash",
        help="Hash of the unresolved prior request.",
    ),
) -> None:
    """Submit an approval intent; this command never self-approves it."""

    request = FactorMiningCampaignReplayAuthorizationIntent(
        authorization_id=_run_id(authorization_id, "authorization_id"),
        unresolved_request_hash=_sha256(unresolved_request_hash, "unresolved_request_hash"),
    )
    try:
        authorization = _ledger().authorize_replay(request=request)
    except (
        FactorMiningCampaignDurabilityError,
        FactorMiningCampaignLedgerError,
        SQLAlchemyError,
    ) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(
        {
            "authorization_hash": authorization.authorization_hash,
            "authorization_record_hash": authorization.authorization_record_hash,
            "research_only": True,
            "unresolved_request_hash": authorization.unresolved_request_hash,
        }
    )


@campaign_app.command("inspect", **_COMMAND_KWARGS)
def inspect_campaign_command(
    request_id: str = typer.Option(
        ...,
        "--request-id",
        help="Stable campaign request ID to inspect.",
    ),
) -> None:
    """Inspect every durable request state using only a stable request ID."""

    normalized_request_id = _run_id(request_id, "request_id")
    try:
        events = _ledger().read_request_events(request_id=normalized_request_id)
    except (
        FactorMiningCampaignDurabilityError,
        FactorMiningCampaignLedgerError,
        SQLAlchemyError,
    ) as exc:
        raise typer.BadParameter(str(exc)) from exc
    _emit(
        {
            "events": [
                {
                    "event_kind": event.kind.value,
                    "record_hash": event.record_hash,
                    "request_hash": event.request_hash,
                }
                for event in events
            ],
            "request_id": normalized_request_id,
            "research_only": True,
        }
    )


if __name__ == "__main__":  # pragma: no cover - installed script invokes the Typer app.
    app()

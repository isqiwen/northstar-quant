"""Decode authenticated HTTP command identity before entering durable Live control."""

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, Request

from northstar_quant.live.auth import LiveAuth
from northstar_quant.live.commands import CommandConflict
from northstar_quant.live.owner import LiveOwner


def execute_command(
    owner: LiveOwner,
    request: Request,
    body: dict[str, Any],
    effect: Callable[[UUID], dict[str, Any]],
) -> dict[str, Any]:
    try:
        auth: LiveAuth = request.app.state.auth
        auth.authorize(request.headers.get("authorization", ""), control=True)
    except PermissionError as error:
        raise HTTPException(403, str(error)) from error
    try:
        operator = request.headers["x-northstar-operator"]
        command_id = UUID(request.headers["x-live-command-id"])
        runtime_id = UUID(request.headers["x-live-runtime-id"])
        expires_at = datetime.fromisoformat(request.headers["x-live-expires-at"])
    except (KeyError, ValueError) as error:
        raise HTTPException(
            400, "Live commands require fixed identity, runtime and deadline"
        ) from error
    try:
        return owner.commands.execute(
            command_id,
            runtime_id,
            expires_at,
            request.url.path,
            body,
            lambda: effect(command_id),
            operator=operator,
        )
    except CommandConflict as error:
        raise HTTPException(409, str(error)) from error

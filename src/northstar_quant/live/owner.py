"""The only HTTP runtime which creates and shuts down broker owners."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, Request
from sqlalchemy import Engine

from northstar_quant.broker.budgets import BrokerOpeningBudgets
from northstar_quant.broker.streams import BrokerStreams
from northstar_quant.broker.workspace import BrokerWorkspace
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.runtime import release_hash

from .auth import LiveAuth
from .commands import Commands


class LiveOwner:
    def __init__(self, engine: Engine, library: DataLibrary, auth: LiveAuth) -> None:
        if auth.control_token is None:
            raise ValueError("Live requires separate read and control credentials")
        self.auth = auth
        self.identifier = uuid4()
        self.started_at = datetime.now(UTC).isoformat()
        self.commands = Commands(engine, self.identifier)
        self.broker = BrokerWorkspace(engine)
        self.streams = BrokerStreams(engine, library)
        self.opening_budgets = BrokerOpeningBudgets(engine, library)

    def status(self) -> dict[str, Any]:
        return {
            "runtime_id": str(self.identifier),
            "pid": os.getpid(),
            "started_at": self.started_at,
            "observed_at": datetime.now(UTC).isoformat(),
            "status": "AVAILABLE",
            "release": release_hash(),
            "order_sending": False,
            "cancel_sending": False,
        }

    def read(self, value: dict[str, Any]) -> dict[str, Any]:
        return {**value, "live_runtime": self.status()}

    def execute(
        self, request: Request, body: dict[str, Any], effect: Callable[[UUID], dict[str, Any]]
    ) -> dict[str, Any]:
        self.auth.authorize(request, control=True)
        try:
            command_id = UUID(request.headers["x-live-command-id"])
            runtime_id = UUID(request.headers["x-live-runtime-id"])
            expires_at = datetime.fromisoformat(request.headers["x-live-expires-at"])
        except (KeyError, ValueError) as error:
            raise HTTPException(
                400, "Live commands require fixed identity, runtime and deadline"
            ) from error
        return self.commands.execute(
            command_id, runtime_id, expires_at, request.url.path, body, lambda: effect(command_id)
        )

    def close(self) -> None:
        self.streams.close()

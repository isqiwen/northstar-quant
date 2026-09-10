"""Management routes select a configured kernel, never a browser-supplied URL."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

from northstar_quant.live.auth import LiveAuth
from northstar_quant.live.client import LiveClient
from northstar_quant.live.instances import Instance
from northstar_quant.web.access import WorkspaceAccess
from northstar_quant.web.requests import ApiModel


class InstanceRecord(ApiModel):
    instance_id: str
    environment: str


class InstanceCatalog(ApiModel):
    instances: list[InstanceRecord]
    production_available: bool = False


class Instances:
    def __init__(self, clients: dict[str, LiveClient], identities: list[Instance]):
        self.clients = clients
        self.identities = identities
        if (
            not clients
            or len(clients) != len(identities)
            or set(clients) != {i.identifier for i in identities}
        ):
            raise ValueError("Live management requires matching unique instance identities")

    @classmethod
    def from_environment(cls) -> Instances:
        document = os.environ.get("NORTHSTAR_LIVE_ENDPOINTS")
        if not document:
            instance = Instance.from_environment()
            return cls({instance.identifier: LiveClient.from_environment()}, [instance])
        clients = {}
        identities = []
        try:
            for entry in json.loads(document):
                instance = Instance(entry["id"], entry["environment"])
                if instance.environment == "production" or instance.identifier in clients:
                    raise ValueError("Unsupported or duplicate Live instance")
                auth = LiveAuth.from_file(Path(entry["auth"]))
                clients[instance.identifier] = LiveClient(
                    entry["url"], auth, expected_instance_id=instance.identifier
                )
                identities.append(instance)
            return cls(clients, identities)
        except BaseException:
            for client in clients.values():
                client.close()
            raise

    def for_request(self, request: Request) -> LiveClient:
        identifier = request.headers.get("x-live-instance-id")
        if identifier is None and len(self.clients) == 1:
            identifier = next(iter(self.clients))
        if identifier not in self.clients:
            raise HTTPException(409, "Select an explicitly configured Live instance")
        return self.clients[identifier]

    def close(self) -> None:
        for client in self.clients.values():
            client.close()

    def register(self, app: FastAPI, access: WorkspaceAccess) -> None:
        @app.get("/api/live/instances", response_model=InstanceCatalog)
        async def catalog(request: Request) -> dict[str, object]:
            access.require_request(request)
            return {
                "instances": [
                    {"instance_id": i.identifier, "environment": i.environment}
                    for i in self.identities
                ],
                "production_available": False,
            }

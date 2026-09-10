"""Immutable account/environment binding for one independently supervised kernel."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy import Connection, Engine


@dataclass(frozen=True)
class Instance:
    identifier: str
    environment: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", self.identifier):
            raise ValueError("Live instance ID must use lowercase letters, digits and hyphens")
        if self.environment not in {"simnow_trading", "simnow_dev", "production"}:
            raise ValueError("Unknown Live instance environment")

    @classmethod
    def from_environment(cls) -> Instance:
        return cls(
            os.environ.get("NORTHSTAR_LIVE_INSTANCE", "sim"),
            os.environ.get("NORTHSTAR_LIVE_ENVIRONMENT", "simnow_trading"),
        )


def configured_instances(value: str) -> list[Instance]:
    """Deployment bindings, not an order authorization or mutable account selector."""
    result = []
    for item in value.split(","):
        parts = item.strip().split(":")
        if len(parts) != 2:
            raise ValueError("Live instances use id:environment separated by commas")
        result.append(Instance(*parts))
    if len({i.identifier for i in result}) != len(result):
        raise ValueError("Duplicate Live instance ID")
    # This deployment has one credential set per environment. Never create two
    # owners for the same configured account/environment.
    if len({i.environment for i in result}) != len(result):
        raise ValueError("One Live instance per configured account and environment")
    if any(i.environment == "production" for i in result):
        raise ValueError("Production Live is not implemented or admitted")
    return result


def initialize(connection: Connection) -> None:
    from sqlalchemy import text

    connection.execute(
        text("""
        CREATE TABLE IF NOT EXISTS live_instance_binding (
            singleton integer PRIMARY KEY CHECK (singleton = 1),
            instance_id text NOT NULL,
            environment text NOT NULL,
            broker_id text NOT NULL,
            account_id text NOT NULL
        )
    """)
    )


class InstanceBinding:
    """Bind local facts and hold a non-expiring kernel process lock."""

    def __init__(self, engine: Engine, instance: Instance, broker_id: str, account_id: str):
        from pathlib import Path

        from sqlalchemy import text

        from northstar_quant.live.storage import KernelLock, write_transaction

        if engine.dialect.name != "sqlite":
            raise ValueError("Live instances require local SQLite")
        self.instance = instance
        self._path = Path(str(engine.url.database))
        self._identity = (self._path.stat().st_dev, self._path.stat().st_ino)
        try:
            self._lock = KernelLock(self._path)
        except BlockingIOError as exc:
            raise ValueError("This Live database already has an active kernel") from exc
        try:
            identity = dict(
                instance_id=instance.identifier,
                environment=instance.environment,
                broker_id=broker_id,
                account_id=account_id,
            )
            with write_transaction(engine) as connection:
                row = (
                    connection.execute(text("SELECT * FROM live_instance_binding"))
                    .mappings()
                    .first()
                )
                saved = dict(row) if row is not None else None
                if saved is not None and not saved["account_id"] and account_id:
                    if all(
                        saved[k] == identity[k] for k in ("instance_id", "environment", "broker_id")
                    ):
                        connection.execute(
                            text(
                                "UPDATE live_instance_binding SET "
                                "account_id=:account_id WHERE singleton=1"
                            ),
                            {"account_id": account_id},
                        )
                        saved = {**saved, "account_id": account_id}
                if saved is not None and any(saved[k] != v for k, v in identity.items()):
                    raise ValueError(
                        "Live instance/account binding differs from saved database facts"
                    )
                if saved is None:
                    connection.execute(
                        text(
                            "INSERT INTO live_instance_binding VALUES (1, "
                            ":instance_id, :environment, :broker_id, "
                            ":account_id)"
                        ),
                        identity,
                    )
        except BaseException:
            self._lock.close()
            raise

    def status(self) -> dict[str, str]:
        info = self._path.stat()
        if (info.st_dev, info.st_ino) != self._identity:
            raise ValueError("Live database file was replaced; restart and reconcile required")
        return {"instance_id": self.instance.identifier, "environment": self.instance.environment}

    def close(self) -> None:
        self._lock.close()

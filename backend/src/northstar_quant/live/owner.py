"""Production-session coordination and independently supervised reception owners."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine

from northstar_quant import code_revision
from northstar_quant.accounting.baselines import BrokerBaselines
from northstar_quant.accounting.funds import BrokerFunds
from northstar_quant.accounting.ledger import BrokerLedger
from northstar_quant.broker.queries import BrokerQueries
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.execution.reviews import OrderReviews
from northstar_quant.live.client import PROTOCOL_VERSION
from northstar_quant.live.opening_budgets import BrokerOpeningBudgets
from northstar_quant.live.streams import LiveStreams

from .commands import Commands


class LiveOwner:
    def __init__(self, engine: Engine, library: DataLibrary) -> None:
        self.identifier = uuid4()
        self.started_at = datetime.now(UTC).isoformat()
        self.commands = Commands(engine, self.identifier)
        self.broker = BrokerQueries(engine)
        self.baselines = BrokerBaselines(engine)
        self.ledger = BrokerLedger(engine)
        self.funds = BrokerFunds(engine)
        self.orders = OrderReviews(engine)
        self.streams = LiveStreams(engine, library)
        self.opening_budgets = BrokerOpeningBudgets(engine, library)

    def status(self) -> dict[str, Any]:
        return {
            "runtime_id": str(self.identifier),
            "pid": os.getpid(),
            "started_at": self.started_at,
            "observed_at": datetime.now(UTC).isoformat(),
            "status": "AVAILABLE",
            "release": code_revision(),
            "protocol": PROTOCOL_VERSION,
            "order_sending": False,
            "cancel_sending": False,
        }

    def read(self, value: dict[str, Any]) -> dict[str, Any]:
        return {**value, "live_runtime": self.status()}

    def close(self) -> None:
        self.streams.close()

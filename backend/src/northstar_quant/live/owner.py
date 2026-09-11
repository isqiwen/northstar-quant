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
from northstar_quant.broker.execution_reports import verify_all as verify_ctp_receipts
from northstar_quant.broker.order_transport import verify_all as verify_ctp_orders
from northstar_quant.broker.queries import BrokerQueries
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.execution.journal import OrderJournal
from northstar_quant.execution.reviews import OrderReviews
from northstar_quant.live.client import PROTOCOL_VERSION
from northstar_quant.live.execution_authority import ExecutionAuthority
from northstar_quant.live.opening_budgets import BrokerOpeningBudgets
from northstar_quant.live.streams import LiveStreams

from .commands import Commands
from .instances import InstanceBinding


class LiveOwner:
    def __init__(self, engine: Engine, library: DataLibrary) -> None:
        self.binding: InstanceBinding | None = None
        self.identifier = uuid4()
        self.started_at = datetime.now(UTC).isoformat()
        self.commands = Commands(engine, self.identifier)
        self.authority = ExecutionAuthority(engine, self.identifier, self.check_ownership)
        self.authority.verify_all()
        self.broker = BrokerQueries(engine)
        self.baselines = BrokerBaselines(engine)
        self.ledger = BrokerLedger(engine)
        self.funds = BrokerFunds(engine)
        self.orders = OrderReviews(engine)
        self.execution = OrderJournal(engine, self.identifier)
        self.execution.verify_all()
        verify_ctp_orders(engine)
        verify_ctp_receipts(engine)
        self.streams = LiveStreams(
            engine, library, check_ownership=self.check_ownership, runtime_id=self.identifier
        )
        self.opening_budgets = BrokerOpeningBudgets(engine, library)

    def check_ownership(self) -> None:
        if self.binding is None:
            raise ValueError("Broker reception requires an active Live account owner")
        self.binding.status()

    def status(self) -> dict[str, Any]:
        return {
            **(self.binding.status() if self.binding else {}),
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

    def require_account(self) -> None:
        from northstar_quant.broker.settings import configured_profile, load_credentials

        if self.binding is None:
            raise ValueError("Broker access requires an active Live account owner")
        credentials = load_credentials()
        self.binding.require_account(
            configured_profile().name, credentials.broker_id, credentials.user_id
        )

    def close(self) -> None:
        self.streams.close()
        if self.binding:
            self.binding.close()

"""Ordered accepted account facts inside the owning writer transaction.

This is the monetary ledger, not a mutable balance cache. Source adapters prove
their inputs before posting; replay never reconnects to a broker or manufactures
fees from a balance observation. OMS may post in the same transaction.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Column,
    Connection,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB

from northstar_quant.market_data import Instrument

from .amounts import decimal_text
from .cashflows import CashFlowFact
from .fees import FeeFact
from .fifo import Account
from .fills import FillFact
from .settlement import SettlementFact

type AccountFact = FillFact | FeeFact | CashFlowFact | SettlementFact


class AccountJournalError(ValueError):
    """Accepted account history cannot be reconstructed; never degrade it to unknown."""


_metadata = MetaData()
_entries = Table(
    "account_journal",
    _metadata,
    Column("account_id", String, primary_key=True),
    Column("ordinal", Integer, primary_key=True),
    Column("source_id", String, nullable=False),
    Column("document", JSON().with_variant(JSONB, "postgresql"), nullable=False),
    Column("content_hash", String(64), nullable=False),
    UniqueConstraint("account_id", "source_id"),
)


def initialize(connection: Connection) -> None:
    _metadata.create_all(connection)
    if connection.dialect.name == "sqlite":
        for action in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS account_journal_{action} "
                f"BEFORE {action} ON account_journal "
                "BEGIN SELECT RAISE(ABORT, 'Account facts are immutable'); END"
            )
    else:
        connection.exec_driver_sql("""
            CREATE OR REPLACE FUNCTION preserve_account_journal() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Account facts are immutable'; END; $$ LANGUAGE plpgsql;
            DROP TRIGGER IF EXISTS immutable ON account_journal;
            CREATE TRIGGER immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON account_journal
                FOR EACH STATEMENT EXECUTE FUNCTION preserve_account_journal();
        """)


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _market(value: Instrument) -> dict[str, str]:
    return {
        name: decimal_text(item) if isinstance(item, Decimal) else str(item)
        for name, item in asdict(value).items()
    }


def _instrument(value: dict[str, str]) -> Instrument:
    return Instrument(
        UUID(value["contract_id"]),
        value["symbol"],
        value["exchange_timezone"],
        value["currency"],
        value["quantity_unit"],
        Decimal(value["price_tick"]),
        Decimal(value["multiplier"]),
    )


def _apply(account: Account, document: dict[str, Any]) -> None:
    kind, fact = document["kind"], document["fact"]
    if kind == "FillFact":
        account.apply(FillFact.from_dict(fact))
    elif kind == "FeeFact":
        account.confirm_fee(FeeFact.from_dict(fact))
    elif kind == "CashFlowFact":
        account.transfer(CashFlowFact.from_dict(fact))
    elif kind == "SettlementFact":
        settlement = SettlementFact.from_dict(fact)
        account.settle(settlement, at=settlement.available_at)
    else:
        raise ValueError("unknown account fact kind")


def _read(connection: Connection, account_id: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    previous = None
    for row in connection.execute(
        select(_entries).where(_entries.c.account_id == account_id).order_by(_entries.c.ordinal)
    ).mappings():
        document = row["document"]
        if (
            document["account_id"] != account_id
            or document["ordinal"] != len(entries) + 1
            or document["ordinal"] != row["ordinal"]
            or document["source_id"] != row["source_id"]
            or document["previous_hash"] != previous
            or _hash(document) != row["content_hash"]
        ):
            raise AccountJournalError("account journal identity or chain is damaged")
        previous = row["content_hash"]
        entries.append(document)
    return entries


def _replay(entries: list[dict[str, Any]], extra_markets: tuple[Instrument, ...] = ()) -> Account:
    if not entries:
        raise LookupError("account has no accepted facts")
    opening = entries[0]["opening_cash"]
    markets: dict[str, dict[str, str]] = {}
    for entry in entries:
        if entry["opening_cash"] != opening:
            raise AccountJournalError("account opening cash changed")
        for market in entry["markets"]:
            previous = markets.setdefault(market["contract_id"], market)
            if previous != market:
                raise AccountJournalError("account contract economics changed")
    for instrument in extra_markets:
        market = _market(instrument)
        if markets.setdefault(market["contract_id"], market) != market:
            raise ValueError("account contract economics changed")
    account = Account(Decimal(opening), tuple(_instrument(value) for value in markets.values()))
    observed: set[str] = set()
    for entry in entries:
        observed.update(market["contract_id"] for market in entry["markets"])
        for fact in entry["facts"]:
            try:
                _apply(account, fact)
            except ValueError as error:
                raise AccountJournalError("accepted account fact cannot be replayed") from error
        checkpoint = account.checkpoint()
        positions = checkpoint["positions"]
        assert isinstance(positions, dict)
        checkpoint["positions"] = {
            key: value for key, value in positions.items() if key in observed
        }
        if checkpoint != entry["checkpoint"]:
            raise AccountJournalError("account checkpoint differs from its accepted facts")
    return account


def replay(
    connection: Connection,
    account_id: str,
    *,
    source_id: str | None = None,
    source_hash: str | None = None,
) -> Account:
    entries = _read(connection, account_id)
    if source_id is not None:
        for index, entry in enumerate(entries):
            if entry["source_id"] == source_id and entry["source_hash"] == source_hash:
                return _replay(entries[: index + 1])
        raise AccountJournalError("account journal lacks the requested fixed source prefix")
    return _replay(entries)


def verify_source(
    connection: Connection,
    account_id: str,
    source_id: str,
    source_hash: str,
    facts: tuple[AccountFact, ...],
) -> None:
    expected = [{"kind": type(fact).__name__, "fact": fact.to_dict()} for fact in facts]
    for entry in _read(connection, account_id):
        if entry["source_id"] == source_id:
            if entry["source_hash"] != source_hash or entry["facts"] != expected:
                raise AccountJournalError("account facts differ from their verified source")
            return
    raise AccountJournalError("accepted account source is missing from its monetary journal")


def verify_all(connection: Connection) -> dict[str, Account]:
    identities = tuple(connection.scalars(select(_entries.c.account_id).distinct()))
    return {identity: replay(connection, identity) for identity in identities}


def post(
    connection: Connection,
    account_id: str,
    *,
    opening_cash: Decimal,
    markets: tuple[Instrument, ...],
    facts: tuple[AccountFact, ...],
    source_id: str,
    source_hash: str,
) -> Account:
    """Caller holds the account writer and owns commit/rollback with OMS and receipts."""
    if not connection.in_transaction():
        raise ValueError("account posting requires the owning transaction")
    if not account_id or not source_id or len(source_hash) != 64:
        raise ValueError("account posting requires fixed source identity")
    int(source_hash, 16)
    if any(
        not isinstance(fact, (FillFact, FeeFact, CashFlowFact, SettlementFact)) for fact in facts
    ):
        raise ValueError("account posting accepts identified financial facts only")
    history = _read(connection, account_id)
    encoded = [{"kind": type(fact).__name__, "fact": fact.to_dict()} for fact in facts]
    market_values = [_market(market) for market in markets]
    for entry in history:
        if entry["source_id"] == source_id:
            if (entry["source_hash"], entry["facts"], entry["markets"], entry["opening_cash"]) != (
                source_hash,
                encoded,
                market_values,
                decimal_text(opening_cash),
            ):
                raise AccountJournalError("account source identity has conflicting financial facts")
            return _replay(history)
    document = dict(
        account_id=account_id,
        ordinal=len(history) + 1,
        source_id=source_id,
        source_hash=source_hash,
        previous_hash=_hash(history[-1]) if history else None,
        opening_cash=decimal_text(opening_cash),
        markets=market_values,
        facts=encoded,
    )
    if history:
        account = _replay(history, markets)
        if account.initial_cash != opening_cash:
            raise ValueError("account posting differs from its fixed opening")
    else:
        account = Account(opening_cash, markets)
    for fact in encoded:
        _apply(account, fact)
    document["checkpoint"] = account.checkpoint()
    connection.execute(
        _entries.insert().values(
            account_id=account_id,
            ordinal=document["ordinal"],
            source_id=source_id,
            document=document,
            content_hash=_hash(document),
        )
    )
    return account

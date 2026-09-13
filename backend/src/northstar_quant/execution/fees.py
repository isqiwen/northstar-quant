"""Keep confirmed order charges and the owning monetary book in one transaction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from sqlalchemy import Connection

from northstar_quant.accounting import journal
from northstar_quant.accounting.fees import FeeFact
from northstar_quant.accounting.fills import FillFact


def _source(fact: FeeFact) -> tuple[str, str]:
    content = json.dumps(fact.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "execution-fee:" + fact.fee_id, hashlib.sha256(content.encode()).hexdigest()


def post_fee(connection: Connection, account_id: str, fact: FeeFact) -> None:
    """Accept already verified coverage, never infer a charge from counter balances.

    The fee caller must supply its verified fact. This operation only establishes
    the common account/OMS ownership and atomic posting, not external coverage.
    """
    account = journal.replay(connection, account_id)
    fills = {item.fact.fill_id: item.fact for item in account.applied_fills}
    for identity in fact.fill_ids:
        original = fills.get(identity)
        document = connection.exec_driver_sql(
            "SELECT document FROM execution_order_events WHERE event_id=? AND kind='FILL'",
            ("fill:" + identity,),
        ).scalar_one_or_none()
        if original is None or document is None:
            raise ValueError("fee requires the same accepted account and execution fills")
        executed = FillFact.from_dict(json.loads(document))
        # Account history preserves the external order alias and its own receipt
        # availability; neither may rewrite the independently retained OMS fact.
        if (
            replace(original, order_id=executed.order_id, available_at=executed.available_at)
            != executed
        ):
            raise ValueError("fee account economics differ from the execution fact")
    source_id, source_hash = _source(fact)
    journal.post(
        connection,
        account_id,
        opening_cash=account.initial_cash,
        markets=account.markets,
        facts=(fact,),
        source_id=source_id,
        source_hash=source_hash,
    )


class FeeAudit:
    """One recovery/read transaction; reconstruct each covered account once.

    This index is discarded after verification, never used to admit orders or
    carried across account writes. Full replay validates every batch checkpoint.
    """

    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self._verified: dict[str, dict[str, FeeFact]] = {}

    def verify(self, account_id: str, fact: FeeFact) -> None:
        source_id, source_hash = _source(fact)
        journal.verify_source(self.connection, account_id, source_id, source_hash, (fact,))
        if account_id not in self._verified:
            account = journal.replay(self.connection, account_id)
            self._verified[account_id] = {
                item.fact.fee_id: item.fact for item in account.applied_fees
            }
        if self._verified[account_id].get(fact.fee_id) != fact:
            raise ValueError("execution fee has no matching accepted account charge")


def verify_fee_sources(connection: Connection, fees: set[tuple[str, str]]) -> None:
    expected = {(account_id, "execution-fee:" + fee_id) for account_id, fee_id in fees}
    if journal.source_ids(connection, prefix="execution-fee:") != expected:
        raise ValueError("account fee postings differ from their owned execution charges")

"""One durable account replays fills, charges, transfers and daily variation."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import DBAPIError

from northstar_quant.accounting.cashflows import CashFlowFact
from northstar_quant.accounting.journal import post, replay
from northstar_quant.accounting.settlement import SettlementFact
from northstar_quant.execution.orders import Offset, Side
from northstar_quant.live.storage import initialize, open_store
from tests.accounting.test_fees import charge
from tests.accounting.test_portfolio_account import AT, A, fills


def test_account_facts_atomic_replay_unknown_fees_settlement_and_reversal(tmp_path):
    engine = open_store(tmp_path / "account.sqlite")
    initialize(engine)
    first = replace(fills()[0], fee=None)
    fee = charge(first.fill_id)
    transfer = CashFlowFact("deposit", Decimal(100), "CNY", AT, fee.available_at, "bank-proof")
    settlement = SettlementFact(
        "settlement",
        A.contract_id,
        AT.date(),
        AT.date() + timedelta(days=1),
        AT + timedelta(hours=8),
        AT + timedelta(hours=8),
        Decimal(110),
        "settlement-proof",
    )

    def save(connection, source, *facts):
        return post(
            connection,
            "account",
            opening_cash=Decimal(10000),
            markets=(A,),
            facts=facts,
            source_id=source,
            source_hash="a" * 64,
        )

    with engine.begin() as connection:
        account = save(connection, "fill", first)
        assert account.pending_fee_fill_ids == (first.fill_id,)
        with pytest.raises(ValueError, match="confirmed fees"):
            _ = account.cash
    with pytest.raises(RuntimeError):
        with engine.begin() as connection:
            save(connection, "money", fee, transfer)
            raise RuntimeError("failure in the owning OMS transaction")
    with engine.connect() as connection:
        assert replay(connection, "account").pending_fee_fill_ids == (first.fill_id,)
    with engine.begin() as connection:
        saved = save(connection, "money", fee, transfer).checkpoint()
        assert save(connection, "money", fee, transfer).checkpoint() == saved
    with engine.begin() as connection:
        settled = save(connection, "day", settlement)
        assert settled.position(A.contract_id).long_yesterday == first.quantity_lots
        before = settled.checkpoint()
    close_at = AT + timedelta(days=1)
    close = replace(
        first,
        fill_id="close",
        order_id="close",
        filled_at=close_at,
        available_at=close_at,
        trading_day=close_at.date(),
        side=Side.SELL,
        offset=Offset.CLOSE_YESTERDAY,
        price=Decimal(120),
    )
    with engine.begin() as connection:
        closed = save(connection, "close", close)
        assert closed.pending_fee_fill_ids == ("close",)
        closed = save(connection, "close-fee", charge("close", identity="close-fee", at=close_at))
        assert closed.position(A.contract_id).long_yesterday == 0
        reversal = replace(
            transfer,
            cash_flow_id="reversal",
            amount=Decimal(-100),
            reverses_id="deposit",
            transferred_at=close_at,
            available_at=close_at,
        )
        closed = save(connection, "reversal", reversal)
        assert closed.net_cash_flow == 0
        assert closed.cash == Decimal("10385.5")
        before = closed.checkpoint()
    engine.dispose()

    engine = open_store(tmp_path / "account.sqlite")
    with engine.connect() as connection:
        assert replay(connection, "account").checkpoint() == before
        assert (
            replay(connection, "account", source_id="money", source_hash="a" * 64).checkpoint()
            == saved
        )
    with pytest.raises(ValueError, match="conflicting"):
        with engine.begin() as connection:
            save(connection, "money", replace(fee, amount=Decimal(9)), transfer)
    with pytest.raises(DBAPIError, match="immutable"):
        with engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM account_journal")
    engine.dispose()


def test_broker_receipt_and_monetary_post_share_rollback_and_recovery(
    postgres_engine,
    clean_database,
    monkeypatch,
):
    from uuid import uuid4

    from northstar_quant.accounting import journal
    from northstar_quant.accounting.ledger import BrokerLedger
    from tests.accounting.test_ledger import ledger_query, position_baseline, trade

    baseline = position_baseline(postgres_engine)
    source = ledger_query(postgres_engine, trades=(trade(),))
    ledger = BrokerLedger(postgres_engine)
    original = journal.post

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("failure after monetary posting")

    monkeypatch.setattr(journal, "post", fail)
    command = uuid4()
    with pytest.raises(RuntimeError, match="after monetary"):
        ledger.ingest(baseline, source, request_id=command)
    with postgres_engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM account_journal").scalar_one() == 0
    with pytest.raises(LookupError):
        ledger.get(command)
    monkeypatch.setattr(journal, "post", original)
    entry = ledger.ingest(baseline, source, request_id=command)
    assert entry["monetary_status"] == "POSTED"
    assert ledger.verify_all()["position_entries_count"] == 1
    with postgres_engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE account_journal DISABLE TRIGGER immutable")
        connection.exec_driver_sql("DELETE FROM account_journal")
        connection.exec_driver_sql("ALTER TABLE account_journal ENABLE TRIGGER immutable")
    with pytest.raises(ValueError, match="missing.*monetary journal"):
        ledger.verify_all()


@pytest.mark.parametrize("failed_before_fill", [False, True])
def test_reception_uncertainty_does_not_discard_later_identified_monetary_facts(
    postgres_engine, clean_database, failed_before_fill
):
    from uuid import uuid4

    from northstar_quant.accounting.ledger import BrokerLedger
    from tests.accounting.test_ledger import ledger_query, position_baseline, trade

    baseline = position_baseline(postgres_engine)
    ledger = BrokerLedger(postgres_engine)
    if failed_before_fill:
        ledger.ingest(
            baseline,
            ledger_query(postgres_engine, failure="SYNTHETIC_QUERY_TIMEOUT"),
            request_id=uuid4(),
        )
    entry = ledger.ingest(
        baseline,
        ledger_query(
            postgres_engine,
            trades=(trade(),),
            failure=None if failed_before_fill else "SYNTHETIC_QUERY_TIMEOUT",
        ),
        request_id=uuid4(),
    )
    assert entry["status"] == "UNKNOWN"
    assert entry["monetary_status"] == "POSTED"
    assert entry["execution"]["order_sending"] is False
    with postgres_engine.connect() as connection:
        account = replay(connection, str(baseline))
        assert account.fill_count == 1
        assert account.position(account.markets[0].contract_id).long_today == 2
        with pytest.raises(ValueError, match="confirmed fees"):
            _ = account.cash
    assert ledger.verify_all()["position_entries_count"] == (2 if failed_before_fill else 1)


def test_recovery_rejects_money_with_valid_chain_but_no_retained_broker_source(
    postgres_engine, clean_database
):
    from datetime import datetime
    from uuid import uuid4

    from northstar_quant.accounting.ledger import BrokerLedger
    from tests.accounting.test_ledger import ledger_query, position_baseline, trade

    baseline = position_baseline(postgres_engine)
    ledger = BrokerLedger(postgres_engine)
    entry = ledger.ingest(
        baseline, ledger_query(postgres_engine, trades=(trade(),)), request_id=uuid4()
    )
    assert ledger.verify_all()["position_entries_count"] == 1
    with postgres_engine.begin() as connection:
        account = replay(connection, str(baseline))
        at = datetime.fromisoformat(entry["recorded_at"]) + timedelta(seconds=1)
        # A well-formed journal batch is not a retained bank/broker receipt.
        post(
            connection,
            str(baseline),
            opening_cash=account.initial_cash,
            markets=account.markets,
            facts=(CashFlowFact("unowned", Decimal(100), "CNY", at, at, "missing-proof"),),
            source_id=str(uuid4()),
            source_hash="a" * 64,
        )
        assert replay(connection, str(baseline)).net_cash_flow == Decimal(100)
    with pytest.raises(ValueError, match="unowned broker source"):
        ledger.verify_all()


@pytest.mark.parametrize(
    "unsupported", [{"HedgeFlag": "3"}, {"InstrumentID": "cu2610"}, {"OffsetFlag": "2"}]
)
def test_mixed_broker_batch_posts_supported_facts_without_claiming_complete_coverage(
    postgres_engine, clean_database, unsupported
):
    from uuid import uuid4

    from northstar_quant.accounting.ledger import BrokerLedger
    from tests.accounting.test_ledger import ledger_query, position_baseline, trade

    baseline = position_baseline(postgres_engine)
    ledger = BrokerLedger(postgres_engine)
    source = ledger_query(
        postgres_engine, trades=(trade(), trade("unsupported", **unsupported))
    )
    command = uuid4()
    entry = ledger.ingest(baseline, source, request_id=command)
    assert entry["status"] == "UNKNOWN"
    assert entry["monetary_status"] == "PARTIAL"
    assert len(entry["added_fills"]) == 2
    assert entry["cash_projection"] is None
    assert entry["execution"]["order_sending"] is False
    assert ledger.ingest(baseline, source, request_id=command) == entry
    with postgres_engine.connect() as connection:
        account = replay(connection, str(baseline))
        assert account.fill_count == 1
        assert account.position(account.markets[0].contract_id).long_today == 2
        assert len(account.pending_fee_fill_ids) == 1
        with pytest.raises(ValueError, match="confirmed fees"):
            _ = account.cash
    assert ledger.context(source)["accounting_projection"]["status"] == "UNAVAILABLE"
    assert ledger.verify_all()["position_entries_count"] == 1

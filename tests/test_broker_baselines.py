"""An independent observation may reveal drift; copying an input proves nothing."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from test_broker_records import _capture

from northstar_quant.broker.baselines import BrokerBaselines
from northstar_quant.broker.records import BrokerRecords
from northstar_quant.broker.settings import get_profile


def saved_query(
    engine: Engine,
    *,
    money: dict[str, object] | None = None,
    position: bool = False,
    day: str | None = None,
    failure: str | None = None,
    profile: str = "simnow_dev",
    account: str = "123456",
) -> UUID:
    """Synthetic CTP callbacks through the public record writer; no SDK or credentials."""
    records = BrokerRecords(engine)
    identifier = uuid4()
    records.begin(get_profile(profile).identity(), account, "rb2610", request_id=identifier)
    capture = _capture(profile=profile, account=account, with_position=position)
    amounts = {
        "Balance": "100000",
        "Available": "100000",
        "PreBalance": "100000",
        "PreMargin": "0",
        "Deposit": "0",
        "Withdraw": "0",
        "CurrMargin": "0",
        "FrozenMargin": "0",
        "FrozenCash": "0",
        "FrozenCommission": "0",
        "CashIn": "0",
        "Commission": "0",
        "CloseProfit": "0",
        "PositionProfit": "0",
        "WithdrawQuota": "100000",
        "Reserve": "0",
    }
    events = []
    for event in capture.events:
        data = event.data
        if data is not None:
            data = dict(data)
            if event.callback == "OnRspQryTradingAccount":
                data.update(amounts)
                data.update(money or {})
            if day is not None and "TradingDay" in data:
                data["TradingDay"] = day
        events.append(replace(event, data=data, received_at=capture.started_at))
    records.finish(
        identifier,
        replace(
            capture,
            events=tuple(events),
            failure_code=failure,
            finished_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        ),
    )
    return identifier


def test_baseline_and_later_comparison_survive_retry_without_rewriting_query(
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    del clean_database
    records, baselines = BrokerRecords(postgres_engine), BrokerBaselines(postgres_engine)
    source = saved_query(postgres_engine)
    original = records.get(source)
    command = uuid4()
    baseline = baselines.establish(source, request_id=command)
    assert baseline["status"] == "BASELINE_RECORDED"
    assert baseline["opening"]["funds"]["Balance"] == "100000"
    assert baseline["opening"]["positions"] == []
    assert baselines.establish(source, request_id=command) == baseline
    later = saved_query(postgres_engine)
    check_id = uuid4()
    result = baselines.compare(command, later, request_id=check_id)
    assert result["status"] == "MATCHED"
    assert result["scope"] == "BASELINE_COMPARISON_ONLY"
    assert result["reconciliation"] == "UNRECONCILED"
    assert result["execution"] == {"order_sending": False, "cancel_sending": False}
    assert all(field["delta"] == "0" for field in result["funds"])
    assert baselines.compare(command, later, request_id=check_id) == result
    restarted = BrokerBaselines(postgres_engine)
    assert restarted.get_check(check_id) == result
    assert restarted.context(later)["baseline"] == baseline
    assert restarted.context(later)["checks"] == [result]
    assert restarted.verify_all() == {"baselines_count": 1, "checks_count": 1}
    assert records.get(source) == original
    with pytest.raises(ValueError, match="already bound"):
        baselines.establish(later, request_id=command)
    with pytest.raises(ValueError, match="already bound"):
        baselines.compare(command, source, request_id=check_id)


def test_same_source_or_query_started_before_fixed_baseline_cannot_prove_independence(
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    del clean_database
    baselines = BrokerBaselines(postgres_engine)
    source, earlier = saved_query(postgres_engine), saved_query(postgres_engine)
    baseline_id = uuid4()
    baselines.establish(source, request_id=baseline_id)
    with pytest.raises(ValueError, match="own source"):
        baselines.compare(baseline_id, source, request_id=uuid4())
    with pytest.raises(ValueError, match="independent query"):
        baselines.compare(baseline_id, earlier, request_id=uuid4())
    foreign = saved_query(postgres_engine, profile="simnow_trading")
    with pytest.raises(ValueError, match="same broker environment"):
        baselines.compare(baseline_id, foreign, request_id=uuid4())
    foreign = saved_query(postgres_engine, account="654321")
    with pytest.raises(ValueError, match="same broker environment"):
        baselines.compare(baseline_id, foreign, request_id=uuid4())
    assert baselines.context(source)["checks"] == []


def test_changed_cash_and_other_contract_position_remain_unexplained_differences(
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    del clean_database
    baselines = BrokerBaselines(postgres_engine)
    source = saved_query(postgres_engine)
    baseline_id = uuid4()
    opening = baselines.establish(source, request_id=baseline_id)
    later = saved_query(postgres_engine, money={"Balance": "99999.9"}, position=True)
    comparison = baselines.compare(baseline_id, later, request_id=uuid4())
    assert comparison["status"] == "DIFFERENCES"
    balance = next(field for field in comparison["funds"] if field["field"] == "Balance")
    assert balance == {
        "field": "Balance",
        "expected": "100000",
        "observed": "99999.9",
        "delta": "-0.1",
    }
    assert comparison["activity"]["positions"][0]["InstrumentID"] == "cu2610"
    assert comparison["activity"]["positions"][0]["TodayPosition"] == 1
    assert comparison["reconciliation"] == "UNRECONCILED"
    assert baselines.get_baseline(baseline_id) == opening
    assert not baselines.context(later)["eligibility"]["allowed"]
    with pytest.raises(ValueError, match="cannot establish"):
        baselines.establish(later, request_id=uuid4())


@pytest.mark.parametrize(
    "change",
    [
        {"failure": "QUERY_TIMEOUT"},
        {"money": {"Available": None}},
        {"day": "20260908"},
    ],
)
def test_incomplete_fields_failures_and_new_trading_days_are_unknown_not_zero(
    postgres_engine: Engine,
    clean_database: None,
    change: dict[str, Any],
) -> None:
    del clean_database
    baselines = BrokerBaselines(postgres_engine)
    source = saved_query(postgres_engine)
    baseline_id = uuid4()
    baselines.establish(source, request_id=baseline_id)
    later = saved_query(postgres_engine, **change)
    result = baselines.compare(baseline_id, later, request_id=uuid4())
    assert result["status"] == "UNKNOWN"
    assert result["reasons"]
    assert result["reconciliation"] == "UNRECONCILED"
    if "money" in change:
        available = next(item for item in result["funds"] if item["field"] == "Available")
        assert available["observed"] is None and available["delta"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"money": {"Balance": "NaN"}},
        {"money": {"FrozenMargin": "1"}},
        {"money": {"Available": None}},
        {"position": True},
        {"failure": "DISCONNECTED"},
    ],
)
def test_unknown_or_nonflat_observations_cannot_create_an_opening_baseline(
    postgres_engine: Engine,
    clean_database: None,
    change: dict[str, Any],
) -> None:
    del clean_database
    baselines = BrokerBaselines(postgres_engine)
    source = saved_query(postgres_engine, **change)
    assert not baselines.context(source)["eligibility"]["allowed"]
    with pytest.raises(ValueError, match="cannot establish"):
        baselines.establish(source, request_id=uuid4())
    assert baselines.verify_all() == {"baselines_count": 0, "checks_count": 0}


def test_concurrent_opening_cannot_rebase_and_immutable_evidence_is_checked(
    postgres_engine: Engine,
    clean_database: None,
) -> None:
    del clean_database
    baselines = BrokerBaselines(postgres_engine)
    source = saved_query(postgres_engine)

    def establish() -> dict[str, Any] | None:
        try:
            return baselines.establish(source, request_id=uuid4())
        except ValueError as error:
            assert "already has a fixed baseline" in str(error)
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: establish(), range(2)))
    assert sum(result is not None for result in results) == 1
    baseline = next(result for result in results if result is not None)
    for command in (
        "DELETE FROM broker_account_baselines",
        "UPDATE broker_account_baselines SET sha256 = repeat('0', 64)",
    ):
        with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
            connection.execute(text(command))
    with postgres_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(text("UPDATE broker_account_baselines SET sha256 = repeat('0', 64)"))
    with pytest.raises(ValueError, match="damaged"):
        baselines.get_baseline(UUID(baseline["baseline_id"]))
    with pytest.raises(ValueError, match="damaged"):
        baselines.verify_all()


@pytest.mark.parametrize(
    "table, column",
    [
        ("broker_account_baselines", "source_batch_id"),
        ("broker_baseline_checks", "query_batch_id"),
    ],
)
def test_restore_must_verify_lookup_identity_as_well_as_json(
    postgres_engine: Engine,
    clean_database: None,
    table: str,
    column: str,
) -> None:
    del clean_database
    baselines = BrokerBaselines(postgres_engine)
    source = saved_query(postgres_engine)
    baseline_id = uuid4()
    baselines.establish(source, request_id=baseline_id)
    later = saved_query(postgres_engine)
    baselines.compare(baseline_id, later, request_id=uuid4())
    with postgres_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        # Fault injection into known test-only columns, leaving hashed JSON intact.
        connection.execute(text(f"UPDATE {table} SET {column} = :other"), {"other": uuid4()})
    with pytest.raises(ValueError, match="identity differs"):
        baselines.verify_all()

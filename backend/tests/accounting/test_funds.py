"""Synthetic saved account observations, never broker or real-money acceptance."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text

from northstar_quant.accounting.baselines import BrokerBaselines
from northstar_quant.accounting.funds import BrokerFunds
from northstar_quant.accounting.ledger import BrokerLedger
from northstar_quant.broker.events import QueryCapture
from northstar_quant.broker.records import BrokerRecords
from northstar_quant.broker.settings import get_profile
from northstar_quant.data_management.files import SourceFiles
from northstar_quant.data_management.library import DataLibrary
from tests.accounting.test_baselines import saved_query
from tests.accounting.test_ledger import ledger_query, position_baseline, trade
from tests.apps.browser import ProtocolClient as TestClient
from tests.apps.browser import login_response


def money_query(
    engine: Engine,
    *,
    money: dict[str, object] | None = None,
    day: str | None = None,
    profile: str = "simnow_dev",
) -> UUID:
    """Give the existing synthetic account writer an explicit futures settlement scope."""
    return saved_query(
        engine,
        money={"BizType": "1", "SettlementID": 1, **(money or {})},
        day=day,
        profile=profile,
    )


def money_baseline(engine: Engine) -> UUID:
    identifier = uuid4()
    BrokerBaselines(engine).establish(money_query(engine), request_id=identifier)
    return identifier


def test_cumulative_fees_survive_concurrent_retry_without_being_deducted_twice(
    postgres_engine: Engine, clean_database: None
) -> None:
    del clean_database
    baseline = money_baseline(postgres_engine)
    source = money_query(postgres_engine, money={"Commission": "5", "Balance": "99995"})
    command = uuid4()
    barrier = Barrier(2)

    def observe() -> dict[str, object]:
        barrier.wait(timeout=10)
        return BrokerFunds(postgres_engine).observe(baseline, source, request_id=command)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(observe) for _ in range(2)]
        first, retried = [future.result(timeout=30) for future in futures]
    funds = BrokerFunds(postgres_engine)
    assert first == retried
    assert first["status"] == "OBSERVED"
    assert first["interval"]["deltas"]["Commission"] == "5"
    assert first["observation"]["amounts"]["Balance"] == "99995"
    assert funds.verify_all() == 1
    with pytest.raises(ValueError, match="already has a money entry"):
        funds.observe(baseline, source, request_id=uuid4())

    later = money_query(postgres_engine, money={"Commission": "7", "Balance": "99993"})
    second = funds.observe(baseline, later, request_id=uuid4())
    assert second["interval"]["deltas"]["Commission"] == "2"
    assert second["since_baseline"]["deltas"]["Commission"] == "7"
    assert second["interval"]["deltas"]["Balance"] == "-2"
    assert second["observation"]["amounts"]["Balance"] == "99993"
    assert second["previous_entry_id"] == str(command)
    assert second["reconciliation"] == "UNRECONCILED"
    assert second["execution"] == {"order_sending": False, "cancel_sending": False}
    restarted = BrokerFunds(postgres_engine)
    assert restarted.get(command) == first
    assert restarted.context(source)["source_entry"] == first
    assert restarted.context(source)["current"] == second
    assert restarted.verify_all() == 2
    with pytest.raises(ValueError, match="different fixed inputs"):
        restarted.observe(baseline, later, request_id=command)


def test_missing_fee_is_unknown_without_erasing_other_reported_amounts(
    postgres_engine: Engine, clean_database: None
) -> None:
    del clean_database
    baseline = money_baseline(postgres_engine)
    records = BrokerRecords(postgres_engine)
    template = records.get(
        money_query(postgres_engine, money={"Balance": "99991", "Deposit": "12"})
    )
    capture = QueryCapture.from_dict(template["capture"])
    source = uuid4()
    records.begin(get_profile("simnow_dev").identity(), "123456", "rb2610", request_id=source)
    started = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    events = []
    for event in capture.events:
        data = None if event.data is None else dict(event.data)
        if event.callback == "OnRspQryTradingAccount":
            assert data is not None
            del data["Commission"]
        events.append(replace(event, data=data, received_at=started))
    records.finish(
        source,
        replace(
            capture,
            started_at=started,
            finished_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            events=tuple(events),
        ),
    )
    entry = BrokerFunds(postgres_engine).observe(baseline, source, request_id=uuid4())
    assert entry["status"] == "UNKNOWN"
    assert entry["observation"]["scope_confirmed"] is True
    assert "Commission" not in entry["observation"]["amounts"]
    assert entry["interval"]["deltas"]["Commission"] is None
    assert entry["since_baseline"]["deltas"]["Commission"] is None
    assert "ACCOUNT_COMMISSION_MISSING_CURRENT" in entry["problems"]
    assert entry["interval"]["deltas"]["Balance"] == "-9"
    assert entry["interval"]["net_deposit_delta"] == "12"
    assert records.get(UUID(str(template["batch_id"]))) == template


def test_settlement_or_trading_day_change_never_subtracts_cumulative_amounts(
    postgres_engine: Engine, clean_database: None
) -> None:
    del clean_database
    baseline = money_baseline(postgres_engine)
    funds = BrokerFunds(postgres_engine)
    for day in ("20260907", "20260908"):
        source = money_query(
            postgres_engine,
            money={"SettlementID": 2, "Commission": "3", "Balance": "99997"},
            day=day,
        )
        entry = funds.observe(baseline, source, request_id=uuid4())
        assert entry["status"] == "UNKNOWN"
        assert entry["observation"]["scope_confirmed"] is True
        assert entry["observation"]["amounts"]["Balance"] == "99997"
        assert entry["interval"]["net_deposit_delta"] is None
        assert all(value is None for value in entry["interval"]["deltas"].values())
        assert all(value is None for value in entry["since_baseline"]["deltas"].values())
        assert "ACCOUNT_SCOPE_CHANGED" in entry["problems"]


def test_same_account_number_in_another_environment_cannot_enter_the_book(
    postgres_engine: Engine, clean_database: None
) -> None:
    del clean_database
    baseline = money_baseline(postgres_engine)
    source = money_query(postgres_engine, profile="simnow_trading")
    funds = BrokerFunds(postgres_engine)
    with pytest.raises(ValueError, match="same environment and account"):
        funds.observe(baseline, source, request_id=uuid4())
    assert funds.verify_all() == 0
    assert funds.context(source)["baseline_id"] is None


def test_money_entry_keeps_the_position_reference_visible_when_it_was_recorded(
    postgres_engine: Engine, clean_database: None
) -> None:
    del clean_database
    baseline = position_baseline(postgres_engine)
    ledger, funds = BrokerLedger(postgres_engine), BrokerFunds(postgres_engine)
    first = ledger.ingest(
        baseline, ledger_query(postgres_engine, trades=(trade(),)), request_id=uuid4()
    )
    source = money_query(postgres_engine)
    entry = funds.observe(baseline, source, request_id=uuid4())
    assert entry["position_reference"]["entry_id"] == first["entry_id"]
    assert entry["position_reference"]["ordinal"] == 1
    later = ledger.ingest(
        baseline,
        ledger_query(postgres_engine, trades=(trade(), trade("T2", OrderSysID="O2"))),
        request_id=uuid4(),
    )
    assert ledger.context(source)["current"]["entry_id"] == later["entry_id"]
    assert later["ordinal"] == 2
    assert BrokerFunds(postgres_engine).get(UUID(entry["entry_id"])) == entry
    assert entry["reconciliation"] == "UNRECONCILED"


@pytest.mark.parametrize("damaged_parent", [False, True], ids=["entry-hash", "source-hash"])
def test_changed_money_or_source_evidence_is_refused_on_read(
    postgres_engine: Engine, clean_database: None, damaged_parent: bool
) -> None:
    del clean_database
    baseline = money_baseline(postgres_engine)
    source = money_query(postgres_engine)
    funds = BrokerFunds(postgres_engine)
    entry = funds.observe(baseline, source, request_id=uuid4())
    # Deliberate storage corruption, not an application write or source amendment.
    with postgres_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        if damaged_parent:
            connection.execute(
                text(
                    "UPDATE broker_query_batches SET result_hash = repeat('0', 64) "
                    "WHERE batch_id = :id"
                ),
                {"id": source},
            )
        else:
            connection.execute(
                text(
                    "UPDATE broker_funds_entries SET sha256 = repeat('0', 64) WHERE entry_id = :id"
                ),
                {"id": UUID(entry["entry_id"])},
            )
    with pytest.raises(ValueError):
        BrokerFunds(postgres_engine).get(UUID(entry["entry_id"]))
    with pytest.raises(ValueError):
        BrokerFunds(postgres_engine).verify_all()


def test_browser_money_registration_requires_session_csrf_and_saved_inputs_only(
    live_web_app, live_engine: Engine, tmp_path: Path
) -> None:
    baseline = money_baseline(live_engine)
    source = money_query(live_engine, money={"Commission": "5", "Balance": "99995"})
    command = uuid4()
    payload = {
        "baseline_id": str(baseline),
        "source_batch_id": str(source),
        "request_id": str(command),
    }
    application = live_web_app(
        live_engine, DataLibrary(live_engine, SourceFiles(tmp_path / "archive"))
    )
    with TestClient(application, base_url="http://127.0.0.1") as client:
        path = f"/api/broker/funds-entries/{command}"
        assert client.get(path).status_code == 401
        assert client.post("/api/broker/funds-entries", json=payload).status_code == 401
        page = login_response(client)
        token = page.json()["csrf"]
        assert client.post("/api/broker/funds-entries", json=payload).status_code == 403
        client.headers["X-Northstar-CSRF"] = token
        client.headers["X-Live-Runtime-ID"] = client.get("/api/live/status").json()["runtime_id"]
        assert (
            client.post(
                "/api/broker/funds-entries", json={**payload, "Balance": "1000000"}
            ).status_code
            == 422
        )
        assert client.get(path).status_code == 404
        posted = client.post("/api/broker/funds-entries", json=payload)
        assert posted.status_code == 200, posted.text
        result = posted.json()
        assert client.get(path).json() == result
        assert client.post("/api/broker/funds-entries", json=payload).json() == result
        page = login_response(client)
        assert BrokerFunds(live_engine).verify_all() == 1


@pytest.mark.parametrize("damage", ["amount", "delta", "status", "authority", "receipt"])
def test_recovery_recomputes_money_evidence_even_when_projection_hash_is_valid(
    live_engine: Engine, damage: str
) -> None:
    import hashlib
    import json

    baseline = money_baseline(live_engine)
    funds = BrokerFunds(live_engine)
    first = money_query(live_engine, money={"Commission": "8", "Balance": "99992"})
    funds.observe(baseline, first, request_id=uuid4())
    source = money_query(live_engine, money={"Commission": "3", "Balance": "99997"})
    command = uuid4()
    entry = funds.observe(baseline, source, request_id=command)
    assert entry["status"] == "UNKNOWN"
    assert "CUMULATIVE_COMMISSION_ADJUSTMENT_UNRESOLVED" in entry["problems"]
    if damage == "amount":
        entry["observation"]["amounts"]["Balance"] = "1000000"
    elif damage == "delta":
        entry["interval"]["deltas"]["Commission"] = "0"
    elif damage == "status":
        entry["status"], entry["problems"] = "OBSERVED", []
    elif damage == "authority":
        entry["reconciliation"] = "MATCHED"
        entry["execution"]["order_sending"] = True
    else:
        entry["interval_start"]["account_receipts"] = []
    content = json.dumps(entry, sort_keys=True, allow_nan=False, separators=(",", ":"))
    digest = hashlib.sha256(content.encode()).hexdigest()
    # Simulate a corrupt restored projection with an internally consistent checksum.
    # The original query remains intact and must remain the reconstruction authority.
    with live_engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER immutable_broker_funds_entries_UPDATE")
        connection.exec_driver_sql(
            "UPDATE broker_funds_entries SET document=?, sha256=? WHERE entry_id=?",
            (content, digest, command.hex),
        )
    restarted = BrokerFunds(live_engine)
    for operation in (
        lambda: restarted.get(command),
        lambda: restarted.context(source),
        lambda: restarted.observe(baseline, source, request_id=command),
        restarted.verify_all,
    ):
        with pytest.raises(ValueError, match="projection differs"):
            operation()
    assert BrokerRecords(live_engine).get(source)["capture"] is not None

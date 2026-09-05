"""Synthetic broker facts exercise money and evidence, never external execution."""

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError
from test_broker_records import _capture
from test_broker_streams import Clock, logins, prepare, start
from test_live import OPEN, tick

from northstar_quant.broker import budgets as budget_module
from northstar_quant.broker.baselines import BrokerBaselines
from northstar_quant.broker.budgets import BrokerOpeningBudgets
from northstar_quant.broker.ledger import BrokerLedger
from northstar_quant.broker.records import BrokerRecords
from northstar_quant.broker.settings import get_profile
from northstar_quant.broker.streams import BrokerStreams
from northstar_quant.data.library import DataLibrary
from northstar_quant.web import create_app


class AccountClock(datetime):
    """Keep the synthetic query/ledger chronology independent of the test run date."""

    at = OPEN - timedelta(minutes=10)

    @classmethod
    def now(cls, tz: Any = None) -> datetime:
        cls.at += timedelta(milliseconds=1)
        return cls.at if tz is not None else cls.at.replace(tzinfo=None)


def budget_query(engine: Engine, changes: dict[str, dict[str, Any]] | None = None) -> UUID:
    """Copy explicit synthetic scope and rates through the real evidence writer."""
    records, identifier = BrokerRecords(engine), uuid4()
    records.begin(get_profile("simnow_dev").identity(), "123456", "rb2610", request_id=identifier)
    capture = _capture()
    additions: dict[str, dict[str, Any]] = {
        "OnRspQryTradingAccount": {
            "BizType": "1",
            "Balance": "100000",
            "Available": "100000",
            "CurrMargin": "0",
            "FrozenMargin": "0",
            "FrozenCash": "0",
            "FrozenCommission": "0",
            "PositionProfit": "0",
        },
        "OnRspQryInstrument": {
            "ProductClass": "1",
            "ProductID": "rb",
            "DeliveryYear": 2026,
            "DeliveryMonth": 10,
            "OpenDate": "20251016",
            "ExpireDate": "20261015",
            "IsTrading": 1,
            "MinLimitOrderVolume": 1,
            "MaxLimitOrderVolume": 100,
        },
        "OnRspQryInstrumentMarginRate": {
            "InvestorRange": "3",
            "InvestUnitID": "",
            "ExchangeID": "SHFE",
            "IsRelative": 0,
            "LongMarginRatioByMoney": "0.12",
            "LongMarginRatioByVolume": "2",
            "ShortMarginRatioByMoney": "0.15",
            "ShortMarginRatioByVolume": "3",
        },
        "OnRspQryInstrumentCommissionRate": {
            "InvestorRange": "3",
            "InvestUnitID": "",
            "ExchangeID": "SHFE",
            "BizType": "1",
            "OpenRatioByMoney": "0.0001",
            "OpenRatioByVolume": "1.001",
        },
    }
    events = []
    for event in capture.events:
        data = (
            None
            if event.data is None
            else {
                **event.data,
                **additions.get(event.callback, {}),
                **(changes or {}).get(event.callback, {}),
            }
        )
        events.append(replace(event, data=data, received_at=capture.started_at))
    records.finish(
        identifier,
        replace(
            capture,
            events=tuple(events),
            finished_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        ),
    )
    return identifier


def budget_case(
    engine: Engine,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    changes: dict[str, dict[str, Any]] | None = None,
    receipt_delay: timedelta = timedelta(0),
) -> tuple[DataLibrary, UUID, UUID, int]:
    AccountClock.at = OPEN - timedelta(minutes=10)
    for module in (
        "northstar_quant.broker.records",
        "northstar_quant.broker.baselines",
        "northstar_quant.broker.ledger",
        "test_broker_records",
        "test_broker_baselines",
        "test_broker_ledger",
        __name__,
    ):
        monkeypatch.setattr(f"{module}.datetime", AccountClock)
    library, first, configuration, calls = prepare(engine, root, monkeypatch)
    baseline = UUID(BrokerBaselines(engine).context(first)["baseline"]["baseline_id"])
    ledger = BrokerLedger(engine)
    entry = ledger.ingest(baseline, first, request_id=uuid4())
    source = budget_query(engine, changes)
    comparison = ledger.compare(UUID(entry["entry_id"]), source, request_id=uuid4())
    checked = ledger.check_orders(UUID(comparison["check_id"]), request_id=uuid4())
    streams, identifier = BrokerStreams(engine, library), uuid4()
    try:
        start(streams, source, configuration, identifier)
        assert calls["ready"].wait(3)
        logins(calls["accept"])
        for sequence, seconds in enumerate(range(0, 181, 4), 3):
            event = tick(
                sequence,
                OPEN + timedelta(seconds=seconds),
                price="3100" if seconds < 120 else "3110",
                volume=100 + sequence,
            )
            event = replace(
                event,
                received_at=(datetime.fromisoformat(event.received_at) + receipt_delay)
                .isoformat()
                .replace("+00:00", "Z"),
                data={
                    **event.data,
                    "UpperLimitPrice": "3500",
                    "LowerLimitPrice": "2800",
                    "PreSettlementPrice": "3200",
                },
            )
            Clock.at = datetime.fromisoformat(event.received_at)
            calls["accept"](event)
    finally:
        streams.close()
    assert calls["count"] == 1
    return library, identifier, UUID(checked["check_id"]), sequence


def test_fixed_shadow_to_one_lot_budget_is_idempotent_immutable_and_not_a_send(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    library, stream, order, sequence = budget_case(postgres_engine, tmp_path, monkeypatch)
    budgets, command = BrokerOpeningBudgets(postgres_engine, library), uuid4()
    original = BrokerStreams(postgres_engine, library).get(stream)

    def create() -> dict[str, Any]:
        return budgets.create(
            stream, sequence, order, limit_price=Decimal("3110"), request_id=command
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: create(), range(2)))
    assert first == second
    assert first["status"] == "WITHIN_BUDGET", first
    assert first["budget"]["side"] == "BUY" and first["budget"]["quantity_lots"] == 1
    assert first["budget"]["notional"] == "31100"
    assert first["budget"]["margin_budget"] == "4202"
    assert first["budget"]["fee_budget"] == "4.12"
    assert first["budget"]["capital_budget"] == "4206.12"
    assert first["inputs"]["decision"]["result"]["intent"]["target_fraction"] == "0.5"
    assert "ACCOUNT_QUERY_NOT_CURRENT_AT_TARGET" in first["execution_blockers"]
    assert first["execution"] == {"order_sending": False, "cancel_sending": False}
    assert BrokerOpeningBudgets(postgres_engine, library).get(command) == first
    assert budgets.verify_all() == 1
    assert len(budgets.context(stream)["budgets"]) == 1
    assert BrokerStreams(postgres_engine, library).get(stream) == original
    with pytest.raises(ValueError, match="already bound"):
        budgets.create(stream, sequence, order, limit_price=Decimal("3111"), request_id=command)
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM broker_opening_budgets WHERE budget_id=:id"), {"id": command}
        )
    # A later query/ledger append cannot rebind an already saved budget's parents.
    parent = BrokerLedger(postgres_engine).get_order_check(order)
    entry = BrokerLedger(postgres_engine).get(UUID(parent["entry_id"]))
    BrokerLedger(postgres_engine).ingest(
        UUID(entry["baseline_id"]), budget_query(postgres_engine), request_id=uuid4()
    )
    assert budgets.get(command) == first
    with postgres_engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role = replica"))
        connection.execute(
            text(
                "UPDATE broker_stream_steps SET committed_at=committed_at+interval '1 second' "
                "WHERE stream_id=:id AND sequence=:sequence"
            ),
            {"id": stream, "sequence": sequence},
        )
    with pytest.raises(ValueError, match="source differs"):
        budgets.get(command)


def test_budget_freshness_checks_source_clock_not_only_later_receipt(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    library, stream, order, sequence = budget_case(
        postgres_engine, tmp_path, monkeypatch, receipt_delay=timedelta(seconds=4)
    )
    Clock.at += timedelta(seconds=2)
    monkeypatch.setattr(budget_module, "datetime", Clock)
    result = BrokerOpeningBudgets(postgres_engine, library).create(
        stream, sequence, order, limit_price=Decimal("3110"), request_id=uuid4()
    )
    assert result["status"] == "WITHIN_BUDGET"
    assert "MARKET_OBSERVATION_NOT_CURRENT" in result["execution_blockers"]
    assert result["inputs"]["market_source_time"] == "2026-09-07T01:03:00+00:00"


@pytest.mark.parametrize(
    "changes, status, reason",
    [
        (
            {"OnRspQryInstrumentMarginRate": {"IsRelative": 1}},
            "UNKNOWN",
            "ABSOLUTE_SPECULATION_MARGIN_REQUIRED",
        ),
        (
            {"OnRspQryInstrumentCommissionRate": {"InvestUnitID": None}},
            "UNKNOWN",
            "ACCOUNT_SPECIFIC_FEE_OR_MARGIN_SCOPE_NOT_CONFIRMED",
        ),
        (
            {"OnRspQryTradingAccount": {"FrozenMargin": "1"}},
            "UNKNOWN",
            "FIRST_OPENING_REQUIRES_ZERO_MARGIN_FREEZES_AND_POSITION_PROFIT",
        ),
        ({"OnRspQryTradingAccount": {"Available": "4206.11"}}, "REJECT", "INSUFFICIENT_AVAILABLE"),
    ],
)
def test_missing_scope_relative_rates_freezes_and_one_cent_short_cannot_pass(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, dict[str, Any]],
    status: str,
    reason: str,
) -> None:
    del clean_database
    library, stream, order, sequence = budget_case(
        postgres_engine, tmp_path, monkeypatch, changes=changes
    )
    result = BrokerOpeningBudgets(postgres_engine, library).create(
        stream,
        sequence,
        order,
        limit_price=Decimal("3110"),
        request_id=uuid4(),
    )
    assert result["status"] == status, result
    assert reason in result["reasons"]
    assert result["execution"] == {"order_sending": False, "cancel_sending": False}


def test_browser_budget_uses_saved_inputs_rejects_account_injection_and_shows_unknown(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    library, stream, order, sequence = budget_case(postgres_engine, tmp_path, monkeypatch)
    with TestClient(create_app(postgres_engine, library), base_url="http://127.0.0.1") as client:
        page = client.get(f"/streams/{stream}")
        assert page.status_code == 200
        csrf = re.search(r'<meta name="northstar-csrf" content="([^"]+)"', page.text).group(1)
        payload = {
            "sequence": sequence,
            "order_check_id": str(order),
            "limit_price": "3110",
            "request_id": str(uuid4()),
        }
        url, headers = f"/api/streams/{stream}/opening-budgets", {"X-Northstar-CSRF": csrf}
        assert client.post(url, json=payload).status_code == 403
        for change in (
            {"limit_price": 3110},
            {"sequence": True},
            {"available": "1000000"},
            {"side": "SELL"},
        ):
            assert client.post(url, json={**payload, **change}, headers=headers).status_code == 422
        response = client.post(url, json=payload, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "WITHIN_BUDGET"
        assert client.post(url, json=payload, headers=headers).json() == response.json()
        saved = payload["request_id"]
        assert client.get(f"/broker/opening-budgets/{saved}").status_code == 200
        assert client.get(f"/api/broker/opening-budgets/{saved}").json() == response.json()
        # A warming-up callback is not an opening target; preserve a readable UNKNOWN.
        payload.update(sequence=3, request_id=str(uuid4()))
        unknown = client.post(url, json=payload, headers=headers)
        assert unknown.status_code == 200 and unknown.json()["status"] == "UNKNOWN"
        assert client.get(f"/broker/opening-budgets/{payload['request_id']}").status_code == 200

"""Synthetic broker facts exercise money and evidence, never external execution."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from northstar_quant.accounting.baselines import BrokerBaselines
from northstar_quant.accounting.ledger import BrokerLedger
from northstar_quant.accounting.observations import ACCOUNT_AMOUNT_FIELDS
from northstar_quant.broker.events import BrokerEvent, QueryCapture
from northstar_quant.broker.records import BrokerRecords
from northstar_quant.broker.settings import get_profile
from northstar_quant.data_management.library import DataLibrary
from northstar_quant.live import opening_budgets as budget_module
from northstar_quant.live.opening_budgets import BrokerOpeningBudgets
from northstar_quant.live.streams import LiveStreams
from tests.apps.browser import ProtocolClient as TestClient
from tests.apps.browser import login_response
from tests.broker.test_records import _capture
from tests.live.test_market import OPEN, tick
from tests.live.test_streams import Clock, prepare, start


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
            **dict.fromkeys(ACCOUNT_AMOUNT_FIELDS, "0"),
            "PreBalance": "100000",
            "WithdrawQuota": "100000",
            "SettlementID": 1,
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
    finish_after_market: bool = False,
) -> tuple[DataLibrary, UUID, UUID, UUID, int]:
    AccountClock.at = OPEN - timedelta(minutes=10)
    for module in (
        "northstar_quant.broker.records",
        "northstar_quant.accounting.baselines",
        "northstar_quant.accounting.ledger",
        "tests.broker.test_records",
        "tests.accounting.test_baselines",
        "tests.accounting.test_ledger",
        __name__,
    ):
        monkeypatch.setattr(f"{module}.datetime", AccountClock)

    def scoped_capture(**kwargs):
        capture = _capture(**kwargs)
        return replace(
            capture,
            events=tuple(
                replace(event, data={**event.data, "BizType": "1", "SettlementID": 1})
                if event.callback == "OnRspQryTradingAccount"
                else event
                for event in capture.events
            ),
        )

    monkeypatch.setattr("tests.accounting.test_baselines._capture", scoped_capture)
    library, first, configuration, calls = prepare(engine, root, monkeypatch)
    baseline = UUID(BrokerBaselines(engine).context(first)["baseline"]["baseline_id"])
    ledger = BrokerLedger(engine)
    entry = ledger.ingest(baseline, first, request_id=uuid4())
    source = budget_query(engine, changes)
    streams, identifier = LiveStreams(engine, library), uuid4()
    try:
        start(streams, source, configuration, identifier)
        assert calls["ready"].wait(3)
        events = QueryCapture.from_dict(BrokerRecords(engine).get(source)["capture"]).events
        # The receiver retains its own startup and refreshed account facts.
        at = (OPEN - timedelta(seconds=2)).isoformat().replace("+00:00", "Z")
        for event in events[:-1]:
            calls["accept"](replace(event, received_at=at))
        sequence = len(events) - 1
        query_id = uuid4()
        at = (OPEN - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        sequence += 1
        calls["accept"](
            BrokerEvent(
                sequence,
                "TD",
                "AccountQueryStarted",
                None,
                None,
                at,
                0,
                {"query_id": str(query_id)},
            )
        )
        for event in events:
            if event.callback.startswith("OnRspQry") or (
                event.callback == "RequestSent"
                and str((event.data or {}).get("method", "")).startswith("ReqQry")
            ):
                sequence += 1
                calls["accept"](
                    replace(
                        event, sequence=sequence, request_id=event.request_id + 1000, received_at=at
                    )
                )

        def finish(number: int, received_at: str) -> None:
            calls["accept"](
                BrokerEvent(
                    number,
                    "TD",
                    "AccountQueryFinished",
                    None,
                    None,
                    received_at,
                    0,
                    {"query_id": str(query_id), "status": "COMPLETE", "reason": None},
                )
            )

        if not finish_after_market:
            sequence += 1
            finish(sequence, at)
        # Keep every prior source quote fresh while the real idle poll runs.
        # The delayed case becomes stale only after its final explicit clock advance.
        interval_ms = 500 if receipt_delay else 4000
        for sequence, elapsed_ms in enumerate(range(0, 180_001, interval_ms), sequence + 1):
            event = tick(
                sequence,
                OPEN + timedelta(milliseconds=elapsed_ms),
                price="3100" if elapsed_ms < 120_000 else "3110",
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
        if finish_after_market:
            finish(sequence + 1, Clock.at.isoformat().replace("+00:00", "Z"))
    finally:
        streams.close()
    assert calls["count"] == 1
    return library, identifier, query_id, UUID(entry["entry_id"]), sequence


def test_fixed_shadow_to_one_lot_budget_is_idempotent_immutable_and_not_a_send(
    postgres_engine: Engine,
    clean_database: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del clean_database
    library, stream, query_id, entry_id, sequence = budget_case(
        postgres_engine, tmp_path, monkeypatch
    )
    budgets, command = BrokerOpeningBudgets(postgres_engine, library), uuid4()
    original = LiveStreams(postgres_engine, library).get(stream)

    def create() -> dict[str, Any]:
        return budgets.create(
            stream, sequence, query_id, entry_id, limit_price=Decimal("3110"), request_id=command
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
    assert "ACCOUNT_QUERY_NOT_CURRENT_AT_RISK" in first["execution_blockers"]
    assert first["execution"] == {"order_sending": False, "cancel_sending": False}
    assert BrokerOpeningBudgets(postgres_engine, library).get(command) == first
    assert budgets.verify_all() == 1
    assert len(budgets.context(stream)["budgets"]) == 1
    assert LiveStreams(postgres_engine, library).get(stream) == original
    with pytest.raises(ValueError, match="already bound"):
        budgets.create(
            stream, sequence, query_id, entry_id, limit_price=Decimal("3111"), request_id=command
        )
    with pytest.raises(DBAPIError), postgres_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM broker_opening_budgets WHERE budget_id=:id"), {"id": command}
        )
    # A later query/ledger append cannot rebind an already saved budget's parents.
    entry = BrokerLedger(postgres_engine).get(entry_id)
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
    library, stream, query_id, entry_id, sequence = budget_case(
        postgres_engine, tmp_path, monkeypatch, receipt_delay=timedelta(seconds=4)
    )
    Clock.at += timedelta(seconds=2)
    monkeypatch.setattr(budget_module, "datetime", Clock)
    result = BrokerOpeningBudgets(postgres_engine, library).create(
        stream, sequence, query_id, entry_id, limit_price=Decimal("3110"), request_id=uuid4()
    )
    assert result["status"] == "WITHIN_BUDGET", result
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
    library, stream, query_id, entry_id, sequence = budget_case(
        postgres_engine, tmp_path, monkeypatch, changes=changes
    )
    result = BrokerOpeningBudgets(postgres_engine, library).create(
        stream,
        sequence,
        query_id,
        entry_id,
        limit_price=Decimal("3110"),
        request_id=uuid4(),
    )
    assert result["status"] == status, result
    assert reason in result["reasons"]
    assert result["execution"] == {"order_sending": False, "cancel_sending": False}


def test_browser_budget_uses_saved_inputs_rejects_account_injection_and_shows_unknown(
    live_web_app,
    live_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library, stream, query_id, entry_id, sequence = budget_case(live_engine, tmp_path, monkeypatch)
    with TestClient(live_web_app(live_engine, library), base_url="http://127.0.0.1") as client:
        page = login_response(client)
        assert page.status_code == 200
        csrf = page.json()["csrf"]
        payload = {
            "sequence": sequence,
            "query_id": str(query_id),
            "entry_id": str(entry_id),
            "limit_price": "3110",
            "request_id": str(uuid4()),
        }
        url, headers = (
            f"/api/streams/{stream}/opening-budgets",
            {
                "X-Northstar-CSRF": csrf,
                "X-Live-Runtime-ID": client.get("/api/live/status").json()["runtime_id"],
            },
        )
        assert client.post(url, json=payload).status_code == 403
        for change in (
            {"available": "1000000"},
            {"side": "SELL"},
        ):
            assert client.post(url, json={**payload, **change}, headers=headers).status_code == 422
        response = client.post(url, json=payload, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "WITHIN_BUDGET"
        assert client.post(url, json=payload, headers=headers).json() == response.json()
        saved = payload["request_id"]
        assert client.get(f"/broker/opening-budgets/{saved}").status_code == 404
        assert client.get(f"/api/broker/opening-budgets/{saved}").json() == response.json()
        # A warming-up callback is not an opening target; preserve a readable UNKNOWN.
        payload.update(sequence=3, request_id=str(uuid4()))
        unknown = client.post(url, json=payload, headers=headers)
        assert unknown.status_code == 200 and unknown.json()["status"] == "UNKNOWN"
        assert client.get(f"/broker/opening-budgets/{payload['request_id']}").status_code == 404


def test_fixed_receiver_budget_survives_empty_database_restore(
    live_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from northstar_quant.apps.live.maintenance import backup, restore
    from northstar_quant.data_management.files import SourceFiles
    from tests.live.test_recovery import _empty_restore_database

    library, stream, query_id, entry_id, sequence = budget_case(live_engine, tmp_path, monkeypatch)
    identifier = uuid4()
    saved = BrokerOpeningBudgets(live_engine, library).create(
        stream, sequence, query_id, entry_id, limit_price=Decimal("3110"), request_id=identifier
    )
    backup(live_engine, SourceFiles(tmp_path / "archive"), tmp_path / "backup")
    with _empty_restore_database(tmp_path) as target:
        result = restore(target, tmp_path / "restored", tmp_path / "backup")
        restored_library = DataLibrary(target, SourceFiles(tmp_path / "restored"))
        assert result["execution"] == "RECONCILIATION_REQUIRED"
        assert BrokerOpeningBudgets(target, restored_library).get(identifier) == saved
        assert LiveStreams(target, restored_library).account_query(stream, query_id) == (
            LiveStreams(live_engine, library).account_query(stream, query_id)
        )


def test_late_query_completion_does_not_refresh_an_earlier_account_observation(
    live_engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library, stream, query_id, entry_id, sequence = budget_case(
        live_engine, tmp_path, monkeypatch, finish_after_market=True
    )
    monkeypatch.setattr(budget_module, "datetime", Clock)
    saved = BrokerOpeningBudgets(live_engine, library).create(
        stream, sequence, query_id, entry_id, limit_price=Decimal("3110"), request_id=uuid4()
    )
    assert saved["status"] == "WITHIN_BUDGET", saved
    assert "MARKET_OBSERVATION_NOT_CURRENT" not in saved["execution_blockers"]
    assert "ACCOUNT_QUERY_NOT_CURRENT_AT_RISK" in saved["execution_blockers"]
    assert datetime.fromisoformat(saved["inputs"]["query_window"]["finished_at"]) == Clock.at
    assert saved["execution"]["order_sending"] is False


@pytest.mark.parametrize("settlement_id, expected", [(1, "UNCHANGED"), (2, "UNKNOWN")])
def test_budget_comparison_requires_same_settlement_scope_without_granting_permission(
    live_engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    settlement_id: int,
    expected: str,
) -> None:
    library, stream, query_id, entry_id, sequence = budget_case(
        live_engine,
        tmp_path,
        monkeypatch,
        changes={"OnRspQryTradingAccount": {"SettlementID": settlement_id}},
    )
    saved = BrokerOpeningBudgets(live_engine, library).create(
        stream, sequence, query_id, entry_id, limit_price=Decimal("3110"), request_id=uuid4()
    )
    assert saved["account_check"]["status"] == expected, saved["account_check"]
    assert saved["account_check"]["scope"] == "OBSERVATIONS_ONLY_NOT_ACCOUNT_RECONCILIATION"
    assert "PRECHECK_ONLY_NO_EXECUTION_AUTHORIZATION" in saved["execution_blockers"]
    assert saved["execution"]["order_sending"] is False

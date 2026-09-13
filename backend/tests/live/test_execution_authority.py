"""Owner HTTP consent and same-transaction order gates, with no external dispatch."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from northstar_quant.execution.journal import OrderJournal
from northstar_quant.live.execution_authority import ExecutionAuthority
from tests.execution.test_journal import request
from tests.live.test_streams import prepare, start


@pytest.fixture
def consent_context(live_engine, live_client, tmp_path, monkeypatch, request):
    library, source, config, calls = prepare(live_engine, tmp_path, monkeypatch)
    client = live_client(live_engine, library)
    stream_id = uuid4()
    start(client.streams, source, config, stream_id)
    assert calls["ready"].wait(3)
    from northstar_quant.broker.events import BrokerEvent
    from northstar_quant.broker.records import BrokerRecords

    events = BrokerRecords(live_engine).get(source)["capture"]["events"]
    for saved in events:
        event = BrokerEvent.from_dict(saved)
        if not getattr(request, "param", True) and event.callback == "OnRspQryTradingAccount":
            break
        calls["accept"](event)
        if event.callback == "OnRspSubMarketData" and event.is_last:
            break
    if getattr(request, "param", None) == "relogin":
        login = next(
            BrokerEvent.from_dict(saved)
            for saved in events
            if saved["channel"] == "TD" and saved["callback"] == "OnRspUserLogin"
        )
        calls["accept"](
            replace(
                login,
                sequence=event.sequence + 1,
                received_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )
        )
    if getattr(request, "param", None) == "refresh":
        calls["accept"](
            BrokerEvent(
                event.sequence + 1,
                "TD",
                "AccountQueryStarted",
                None,
                None,
                datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                0,
                {"query_id": str(uuid4())},
            )
        )
    runtime = UUID(client.status()["runtime_id"])
    body = dict(
        stream_id=str(stream_id),
        expires_at=(datetime.now(UTC) + timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
        max_order_lots=3,
        max_total_lots=3,
        max_order_budget=dict(fee="6", margin="303", gross="3030", loss="30"),
    )
    authority = ExecutionAuthority(live_engine, runtime, lambda: client.status())
    return client, stream_id, body, authority, library


def grant(context):
    client, _, body, _, _ = context
    identifier = uuid4()
    result = client.for_operator("owner").mutate("/execution/authorizations", body, identifier)
    assert result["status"] == "CONSENTED" and result["requires_current_admission"] is True
    return identifier


def order_for(context):
    client, stream_id, _, _, _ = context
    return replace(
        request(), contract_id=UUID(client.streams.get(stream_id)["binding"]["contract_id"])
    )


def test_maintenance_cannot_grant_and_changed_duplicate_is_rejected(consent_context):
    client, _, body, authority, _ = consent_context
    with pytest.raises(ValueError):
        client.mutate("/execution/authorizations", body, uuid4())
    identifier = grant(consent_context)
    assert authority.verify_all() == 1
    owner = client.for_operator("owner")
    assert owner.mutate("/execution/authorizations", body, identifier)["authorization_id"] == str(
        identifier
    )
    with pytest.raises(ValueError, match="different input"):
        owner.mutate("/execution/authorizations", {**body, "max_order_lots": 4}, identifier)


def test_consent_does_not_substitute_for_current_account_and_commits_with_order(
    live_engine, consent_context
):
    _, stream_id, _, authority, _ = consent_context
    identifier, order = grant(consent_context), order_for(consent_context)
    journal = OrderJournal(live_engine, authority.runtime_id)
    sent = []

    def not_reconciled(connection):
        raise ValueError("ACCOUNT_NOT_RECONCILED")

    with pytest.raises(ValueError, match="NOT_RECONCILED"):
        journal.submit(
            order,
            identifier,
            admit=lambda c: authority.admit(
                c, identifier, stream_id, order, check_current_account=not_reconciled
            ),
            dispatch=sent.append,
        )
    assert sent == []
    with pytest.raises(LookupError):
        journal.get(order.order_id)
    # Synthetic account gate only: this fixture neither connects nor sends to CTP.
    journal.submit(
        order,
        identifier,
        admit=lambda c: authority.admit(
            c, identifier, stream_id, order, check_current_account=lambda _: None
        ),
        dispatch=sent.append,
    )
    assert len(sent) == 1 and journal.get(order.order_id)["status"] == "UNKNOWN"
    journal.report(order.order_id, evidence_id=uuid4(), state="REJECTED", cumulative_lots=0)
    another = replace(order, order_id=str(uuid4()))
    with pytest.raises(ValueError, match="lot budget"):
        journal.submit(
            another,
            identifier,
            admit=lambda c: authority.admit(
                c, identifier, stream_id, another, check_current_account=lambda _: None
            ),
            dispatch=sent.append,
        )
    assert (
        len(sent) == 1
    )  # Rejection does not silently replenish the operator's total-attempt limit.


def test_retained_unprocessed_callback_blocks_new_order_before_account_admission(
    live_engine, consent_context
):
    from northstar_quant.broker.events import BrokerEvent
    from northstar_quant.broker.stream_records import append_stream_event
    from northstar_quant.persistence.sql import write_transaction

    client, stream_id, _, authority, _ = consent_context
    identifier, order = grant(consent_context), order_for(consent_context)
    # The source commit can survive process failure before account/strategy
    # processing. An old READY account must not hide this retained information.
    event = BrokerEvent(
        client.streams.get(stream_id)["received"] + 1,
        "TD",
        "OnFrontDisconnected",
        None,
        None,
        datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        0,
        {"Reason": 4097},
    )
    with write_transaction(live_engine) as connection:
        append_stream_event(connection, stream_id, event, receiving=True)
    checked, sent = [], []
    journal = OrderJournal(live_engine, authority.runtime_id)
    with pytest.raises(ValueError, match="every retained receiver callback"):
        journal.submit(
            order,
            identifier,
            admit=lambda connection: authority.admit(
                connection,
                identifier,
                stream_id,
                order,
                check_current_account=checked.append,
            ),
            dispatch=sent.append,
        )
    assert checked == sent == []
    with pytest.raises(LookupError):
        journal.get(order.order_id)


@pytest.mark.parametrize("consent_context", [False], indirect=True)
def test_prior_query_cannot_replace_receiver_query_at_durable_admission(
    live_engine, consent_context
):
    from northstar_quant.broker.records import BrokerRecords

    client, stream_id, _, authority, _ = consent_context
    identifier, order = grant(consent_context), order_for(consent_context)
    stream = client.streams.get(stream_id)
    prior = UUID(stream["binding"]["request"]["query_batch_id"])
    assert BrokerRecords(live_engine).get(prior)["status"] == "COMPLETE"
    assert stream["startup_query"]["status"] == "INCOMPLETE"
    journal = OrderJournal(live_engine, authority.runtime_id)
    checked, sent = [], []
    with pytest.raises(ValueError, match="receiver's complete startup query"):
        journal.submit(
            order,
            identifier,
            admit=lambda connection: authority.admit(
                connection,
                identifier,
                stream_id,
                order,
                check_current_account=checked.append,
            ),
            dispatch=sent.append,
        )
    assert checked == sent == []
    with pytest.raises(LookupError):
        journal.get(order.order_id)


@pytest.mark.parametrize("consent_context", ["relogin"], indirect=True)
def test_new_login_without_disconnect_cannot_reuse_old_startup_query(live_engine, consent_context):
    client, stream_id, _, authority, _ = consent_context
    identifier, order = grant(consent_context), order_for(consent_context)
    stream = client.streams.get(stream_id)
    assert stream["startup_query"]["status"] == "COMPLETE"
    assert stream["received"] == stream["cursor"]
    assert stream["received"] > stream["startup_query"]["through_sequence"]
    journal = OrderJournal(live_engine, authority.runtime_id)
    checked, sent = [], []
    with pytest.raises(ValueError, match="interrupted receiver session"):
        journal.submit(
            order,
            identifier,
            admit=lambda connection: authority.admit(
                connection,
                identifier,
                stream_id,
                order,
                check_current_account=checked.append,
            ),
            dispatch=sent.append,
        )
    assert checked == sent == []
    with pytest.raises(LookupError):
        journal.get(order.order_id)


@pytest.mark.parametrize(
    "change", ["contract", "expiry", "money", "quantity", "runtime", "pause", "revoked"]
)
def test_wrong_scope_or_inactive_consent_cannot_dispatch(live_engine, consent_context, change):
    client, stream_id, _, authority, _ = consent_context
    identifier, order = grant(consent_context), order_for(consent_context)
    if change == "contract":
        order = replace(order, contract_id=uuid4())
    elif change == "expiry":
        order = replace(order, expires_at=datetime.now(UTC) + timedelta(minutes=3))
    elif change == "money":
        order = replace(order, budget=replace(order.budget, margin=order.budget.margin + 1))
    elif change == "quantity":
        order = replace(order, quantity_lots=4)
    elif change == "runtime":
        authority = ExecutionAuthority(live_engine, uuid4(), lambda: client.status())
    elif change == "pause":
        client.streams.control(stream_id, "PAUSE", request_id=uuid4())
    else:
        client.for_operator("owner").mutate(
            f"/execution/authorizations/{identifier}/revoke",
            {"authorization_id": str(identifier)},
            uuid4(),
        )
        assert authority.get(identifier)["status"] == "REVOKED"
    with pytest.raises(ValueError):
        OrderJournal(live_engine, authority.runtime_id).submit(
            order,
            identifier,
            admit=lambda c: authority.admit(
                c, identifier, stream_id, order, check_current_account=lambda _: None
            ),
            dispatch=lambda _: pytest.fail("invalid consent dispatched"),
        )


def test_restored_consent_never_reactivates_in_new_runtime(live_engine, consent_context, tmp_path):
    from northstar_quant.apps.live.maintenance import backup, restore
    from northstar_quant.data_management.files import SourceFiles
    from northstar_quant.live.storage import open_store

    _, _, _, _, _ = consent_context
    identifier = grant(consent_context)
    destination = tmp_path / "consent-backup"
    assert (
        backup(live_engine, SourceFiles(tmp_path / "archive"), destination)["evidence"][
            "authorizations_count"
        ]
        == 1
    )
    target = open_store(tmp_path / "restored.sqlite")
    try:
        restore(target, tmp_path / "restored-sources", destination)
        authority = ExecutionAuthority(
            target, uuid4(), lambda: pytest.fail("restore obtained owner")
        )
        assert authority.verify_all() == 1
        assert authority.get(identifier)["status"] == "PREVIOUS_RUNTIME"
    finally:
        target.dispose()


def test_new_runtime_cannot_authorize_an_old_receiving_row(live_engine, consent_context):
    from northstar_quant.execution.orders import OrderBudget
    from northstar_quant.live.execution_authority import ExecutionLimits

    client, stream_id, body, _, _ = consent_context
    authority = ExecutionAuthority(live_engine, uuid4(), lambda: client.status())
    with pytest.raises(ValueError, match="runtime's receiver"):
        authority.grant(
            stream_id,
            ExecutionLimits(3, 3, OrderBudget.from_dict(body["max_order_budget"])),
            datetime.fromisoformat(body["expires_at"]),
            request_id=uuid4(),
            operator="owner",
        )


def test_browser_protobuf_consent_and_revocation_use_owned_runtime(
    consent_context, live_web_app, live_engine
):
    from tests.apps.browser import ProtocolClient, login_response

    _, stream_id, body, authority, library = consent_context
    with ProtocolClient(live_web_app(live_engine, library), base_url="http://127.0.0.1") as browser:
        login = login_response(browser)
        assert login.status_code == 200
        browser.headers["X-Northstar-CSRF"] = login.json()["csrf"]
        browser.headers["X-Live-Runtime-ID"] = str(authority.runtime_id)
        identifier = uuid4()
        payload = {
            key: value
            for key, value in body.items()
            if key not in {"stream_id", "max_order_budget"}
        }
        payload.update(body["max_order_budget"], request_id=str(identifier))
        response = browser.post(f"/api/streams/{stream_id}/authorizations", json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["source"]["runtime_id"] == str(authority.runtime_id)
        assert response.json()["status"] == "CONSENTED"
        page = browser.get(f"/api/streams/{stream_id}/authorizations").json()
        assert len(page["authorizations"]) == 1 and page["next_before"] is None
        command = {"request_id": str(uuid4())}
        for _ in range(2):
            revoked = browser.post(f"/api/authorizations/{identifier}/revoke", json=command)
            assert revoked.status_code == 200, revoked.text
            assert revoked.json()["status"] == "REVOKED"
        assert browser.get(f"/api/authorizations/{identifier}").json()["status"] == "REVOKED"


@pytest.mark.parametrize("consent_context", ["refresh"], indirect=True)
def test_refresh_in_progress_cannot_use_old_account_permission(live_engine, consent_context):
    _, stream_id, _, authority, _ = consent_context
    identifier, order = grant(consent_context), order_for(consent_context)
    journal = OrderJournal(live_engine, authority.runtime_id)
    checked, sent = [], []
    with pytest.raises(ValueError, match="completing the receiver account refresh"):
        journal.submit(
            order,
            identifier,
            admit=lambda c: authority.admit(
                c, identifier, stream_id, order, check_current_account=checked.append
            ),
            dispatch=sent.append,
        )
    assert checked == sent == []
    with pytest.raises(LookupError):
        journal.get(order.order_id)


def test_operator_pause_allows_only_position_checked_reduction(live_engine, consent_context):
    from northstar_quant.execution.orders import AdmissionRejected, Offset

    client, stream_id, _, authority, _ = consent_context
    identifier = grant(consent_context)
    client.streams.control(stream_id, "PAUSE", request_id=uuid4())
    opening = order_for(consent_context)
    order = replace(
        opening,
        offset=Offset.CLOSE_TODAY,
        budget=replace(opening.budget, margin=Decimal(0), gross=Decimal(0)),
    )
    journal = OrderJournal(live_engine, authority.runtime_id)
    checked, sent = [], []

    def unavailable_position(connection):
        checked.append(connection)
        raise AdmissionRejected("position does not cover requested close")

    with pytest.raises(AdmissionRejected, match="position does not cover"):
        journal.submit(
            order,
            identifier,
            admit=lambda connection: authority.admit(
                connection,
                identifier,
                stream_id,
                order,
                check_current_account=unavailable_position,
            ),
            dispatch=sent.append,
        )
    assert len(checked) == 1 and sent == []
    with pytest.raises(LookupError):
        journal.get(order.order_id)
    # The synthetic account gate supplies confirmed inventory only for this
    # transaction test. Real CTP closing must prove it from the bound ledger.
    result = journal.submit(
        order,
        identifier,
        admit=lambda connection: authority.admit(
            connection,
            identifier,
            stream_id,
            order,
            check_current_account=checked.append,
        ),
        dispatch=sent.append,
    )
    assert len(checked) == 2 and len(sent) == 1
    assert result["reservation"]["reserved_close_lots"] == 3
    assert result["status"] == "UNKNOWN"


def test_fault_pause_cannot_be_bypassed_by_close_flag(live_engine, consent_context):
    from northstar_quant.execution.orders import AdmissionRejected, Offset
    from northstar_quant.persistence.sql import write_transaction

    _, stream_id, _, authority, _ = consent_context
    identifier = grant(consent_context)
    opening = order_for(consent_context)
    order = replace(
        opening,
        offset=Offset.CLOSE_TODAY,
        budget=replace(opening.budget, margin=Decimal(0), gross=Decimal(0)),
    )
    with write_transaction(live_engine) as connection:
        from northstar_quant.broker.stream_records import text

        connection.execute(
            text("UPDATE broker_streams SET paused=1, reason='MARKET_STALE' WHERE stream_id=:id"),
            {"id": stream_id},
        )
    with pytest.raises(AdmissionRejected, match="stopped or paused"):
        OrderJournal(live_engine, authority.runtime_id).submit(
            order,
            identifier,
            admit=lambda connection: authority.admit(
                connection,
                identifier,
                stream_id,
                order,
                check_current_account=lambda _: pytest.fail("fault bypassed"),
            ),
            dispatch=lambda _: pytest.fail("fault dispatched"),
        )

"""Owner HTTP consent and same-transaction order gates, with no external dispatch."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from northstar_quant.execution.journal import OrderJournal
from northstar_quant.live.execution_authority import ExecutionAuthority
from tests.execution.test_journal import request
from tests.live.test_streams import logins, prepare, start


@pytest.fixture
def consent_context(live_engine, live_client, tmp_path, monkeypatch):
    library, source, config, calls = prepare(live_engine, tmp_path, monkeypatch)
    client = live_client(live_engine, library)
    stream_id = uuid4()
    start(client.streams, source, config, stream_id)
    assert calls["ready"].wait(3)
    logins(calls["accept"])
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

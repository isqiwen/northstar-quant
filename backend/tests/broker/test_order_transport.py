"""The actual local journal/CTP boundary, without credentials or a native connection."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from northstar_quant.broker.order_transport import CtpExecution, CtpSession, verify_all
from northstar_quant.execution.orders import Offset, Side
from tests.execution.test_journal import request


def session():
    return CtpSession("simnow_dev", "9999", "123456", date(2026, 9, 11), 7, 99, 500)


def instrument(order):
    return dict(
        contract_id=str(order.contract_id),
        ExchangeID="SHFE",
        InstrumentID="rb2610",
        PriceTick="1",
        ProductClass="1",
        MinLimitOrderVolume=1,
        MaxLimitOrderVolume=500,
    )


def test_ctp_attempt_is_committed_before_send_and_never_resubmitted(live_engine):
    runtime, permit, order = uuid4(), uuid4(), request()
    adapter = CtpExecution(live_engine, runtime, session())
    calls = []

    def send(method, fields, request_id, expires_at):
        assert adapter.journal.get(order.order_id)["status"] == "UNKNOWN"
        assert verify_all(live_engine) == 1
        assert fields["OrderRef"] == "501" and request_id > 100_000
        assert fields["LimitPrice"] == "100" and fields["VolumeTotalOriginal"] == 3
        calls.append((method, fields, request_id))
        raise KeyboardInterrupt("crash after the wire attempt")

    with pytest.raises(KeyboardInterrupt):
        adapter.submit(
            order,
            permit,
            instrument(order),
            Decimal(100),
            admit=lambda c: None,
            send=send,
            check_owner=lambda: None,
        )
    recovered = CtpExecution(live_engine, uuid4(), replace(session(), front_id=8, session_id=100))
    result = recovered.submit(
        order,
        permit,
        instrument(order),
        Decimal(100),
        admit=lambda c: pytest.fail("no repeated admission"),
        send=lambda *args: pytest.fail("no repeated send"),
        check_owner=lambda: pytest.fail("no old runtime dispatch"),
    )
    assert result["status"] == "UNKNOWN"
    assert result["reservation"]["reserved_margin"] == "303"
    assert len(calls) == 1
    with pytest.raises(ValueError, match="different wire input"):
        recovered.submit(
            order,
            permit,
            instrument(order),
            Decimal(101),
            admit=lambda c: None,
            send=lambda *args: 0,
            check_owner=lambda: None,
        )
    cancel_id = uuid4()
    canceled = []

    def cancel(method, fields, native_id, expires_at):
        assert (fields["FrontID"], fields["SessionID"], fields["OrderRef"]) == (7, 99, "501")
        canceled.append((method, native_id))
        return 0

    result = recovered.cancel(
        order.order_id, cancel_id, admit=lambda c: None, send=cancel, check_owner=lambda: None
    )
    assert result["status"] == "UNKNOWN" and result["reservation"]["reserved_margin"] == "303"
    recovered.cancel(
        order.order_id,
        cancel_id,
        admit=lambda c: None,
        send=lambda *args: pytest.fail("no duplicate cancel"),
        check_owner=lambda: None,
    )
    assert len(canceled) == 1 and verify_all(live_engine) == 1


@pytest.mark.parametrize("stage", ["admission", "ownership", "native_return"])
def test_ctp_failures_preserve_transaction_and_uncertain_order(live_engine, stage):
    adapter, order, calls = CtpExecution(live_engine, uuid4(), session()), request(), []

    def admit(connection):
        if stage == "admission":
            raise ValueError("not authorized")

    def owner():
        if stage == "ownership":
            raise ValueError("owner lease lost")

    def send(*args):
        calls.append(args)
        return -1

    if stage == "admission":
        with pytest.raises(ValueError, match="not authorized"):
            adapter.submit(
                order,
                uuid4(),
                instrument(order),
                Decimal(100),
                admit=admit,
                send=send,
                check_owner=owner,
            )
        assert verify_all(live_engine) == 0
        with pytest.raises(LookupError):
            adapter.journal.get(order.order_id)
    else:
        result = adapter.submit(
            order,
            uuid4(),
            instrument(order),
            Decimal(100),
            admit=admit,
            send=send,
            check_owner=owner,
        )
        assert result["status"] == "UNKNOWN"
        assert result["reservation"]["reserved_margin"] == "303"
        assert verify_all(live_engine) == 1
    assert len(calls) == (1 if stage == "native_return" else 0)


@pytest.mark.parametrize(
    "offset,wire", [(Offset.OPEN, "0"), (Offset.CLOSE_TODAY, "3"), (Offset.CLOSE_YESTERDAY, "4")]
)
def test_ctp_close_age_and_exact_limit_are_preserved(live_engine, offset, wire):
    from northstar_quant.execution.orders import OrderBudget

    adapter = CtpExecution(live_engine, uuid4(), session())
    order = replace(
        request(),
        offset=offset,
        side=Side.SELL,
        budget=OrderBudget(Decimal(2), Decimal(0), Decimal(0), Decimal(10)),
    )
    calls = []
    adapter.submit(
        order,
        uuid4(),
        instrument(order),
        Decimal(100),
        admit=lambda c: None,
        send=lambda *args: calls.append(args) or 0,
        check_owner=lambda: None,
    )
    assert calls[0][1]["CombOffsetFlag"] == wire and calls[0][1]["Direction"] == "1"
    changed = replace(request(), contract_id=uuid4())
    bad = dict(instrument(changed), PriceTick="3")
    with pytest.raises(ValueError, match="off tick"):
        adapter.submit(
            changed,
            uuid4(),
            bad,
            Decimal(100),
            admit=lambda c: None,
            send=lambda *args: pytest.fail("bad native order"),
            check_owner=lambda: None,
        )
    assert verify_all(live_engine) == 1


def test_ctp_references_increase_across_runtime_and_login(live_engine):
    adapter = CtpExecution(live_engine, uuid4(), session())
    orders, refs = [request(), request()], []
    for order in orders:
        adapter.submit(
            order,
            uuid4(),
            instrument(order),
            Decimal(100),
            admit=lambda c: None,
            send=lambda _, f, n, deadline: refs.append(f["OrderRef"]) or 0,
            check_owner=lambda: None,
        )
        adapter = CtpExecution(live_engine, uuid4(), replace(session(), max_order_ref=900))
    assert refs == ["501", "901"] and verify_all(live_engine) == 2


def test_ctp_binding_and_parent_are_verified_on_live_backup(live_engine, tmp_path):
    from northstar_quant.apps.live.maintenance import backup, restore
    from northstar_quant.data_management.files import SourceFiles
    from northstar_quant.live.storage import open_store

    adapter, order = CtpExecution(live_engine, uuid4(), session()), request()
    adapter.submit(
        order,
        uuid4(),
        instrument(order),
        Decimal(100),
        admit=lambda c: None,
        send=lambda *args: 0,
        check_owner=lambda: None,
    )
    destination = tmp_path / "backup"
    assert (
        backup(live_engine, SourceFiles(tmp_path / "sources"), destination)["evidence"][
            "ctp_orders_count"
        ]
        == 1
    )
    target = open_store(tmp_path / "restored.sqlite")
    try:
        restored = restore(target, tmp_path / "restored-sources", destination)
        assert restored["execution"] == "RECONCILIATION_REQUIRED"
        assert verify_all(target) == 1
    finally:
        target.dispose()


def test_login_identity_and_rejection_callbacks_retain_only_permitted_fields():
    from types import SimpleNamespace

    from northstar_quant.broker import _ctp_worker
    from northstar_quant.broker.events import BrokerEvent

    login = BrokerEvent(
        1,
        "TD",
        "OnRspUserLogin",
        2,
        True,
        "2026-09-11T01:00:00Z",
        0,
        dict(
            BrokerID="9999",
            UserID="123456",
            TradingDay="20260911",
            FrontID=7,
            SessionID=99,
            MaxOrderRef="0000500",
        ),
    )
    assert CtpSession.from_login("simnow_dev", "9999", "123456", login) == session()
    with pytest.raises(ValueError, match="matching TD login"):
        CtpSession.from_login("simnow_dev", "9999", "other", login)
    copied = _ctp_worker._copy_fields(
        "OnErrRtnOrderInsert",
        SimpleNamespace(
            OrderRef="501",
            BrokerID="9999",
            InvestorID="123456",
            Password="private",
            LimitPrice=100.0,
            VolumeTotalOriginal=1,
        ),
    )
    assert copied["OrderRef"] == "501" and copied["LimitPrice"] == "100.0"
    assert "Password" not in copied


def test_native_order_building_never_connects_or_sends(live_engine):
    from types import SimpleNamespace

    from northstar_quant.broker.order_transport import native_request

    adapter, order, built = CtpExecution(live_engine, uuid4(), session()), request(), []
    structures = SimpleNamespace(
        InputOrderField=SimpleNamespace, InputOrderActionField=SimpleNamespace
    )

    def construct(method, fields, native_id, expires_at):
        value = native_request(structures, method, fields)
        built.append((method, value, native_id))
        return 0

    adapter.submit(
        order,
        uuid4(),
        instrument(order),
        Decimal(100),
        admit=lambda c: None,
        send=construct,
        check_owner=lambda: None,
    )
    adapter.cancel(
        order.order_id, uuid4(), admit=lambda c: None, send=construct, check_owner=lambda: None
    )
    assert built[0][1].LimitPrice == 100.0
    assert built[1][1].FrontID == 7 and built[1][1].SessionID == 99
    assert built[0][2] != built[1][2]

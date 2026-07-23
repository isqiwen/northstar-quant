from dataclasses import replace
from types import SimpleNamespace

import pytest

from northstar_quant.common.order_identity import build_chase_policy_fingerprint
from northstar_quant.execution.broker_base import BrokerAdapter
from northstar_quant.execution.limit_chase_executor import LimitChaseExecutor
from northstar_quant.execution.models import (
    BrokerStateSnapshot,
    FillSnapshot,
    OrderRequest,
    OrderResult,
)
from northstar_quant.risk.models import OrderRiskContext, RiskLimits


class _RecordingBroker(BrokerAdapter):
    def __init__(self, *, cancel_result: bool = True) -> None:
        self.cancel_result = cancel_result
        self.orders: list[OrderRequest] = []
        self.cancel_requests: list[str] = []

    def submit_order(self, order: OrderRequest) -> OrderResult:
        self.orders.append(order)
        return OrderResult(
            accepted=True,
            broker_order_id=f"order-{len(self.orders)}",
            status="Submitted",
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        self.cancel_requests.append(broker_order_id)
        return self.cancel_result

    def get_name(self) -> str:
        return "fake"


class _ScriptedChaseExecutor(LimitChaseExecutor):
    def __init__(self, *args, resolutions: list[dict | None], **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.resolutions = list(resolutions)

    def _wait_for_terminal_or_timeout(
        self,
        broker_order_id: str,
        *,
        expected_qty: float,
        expected_identity: dict | None = None,
    ) -> dict | None:
        del broker_order_id, expected_qty, expected_identity
        return self.resolutions.pop(0)


def _settings(
    *,
    max_steps: int = 3,
    fallback_mode: str = "cancel",
    offset_bps: float = 15.0,
):
    return SimpleNamespace(
        limit_chase_max_steps=max_steps,
        limit_chase_fallback_mode=fallback_mode,
        limit_chase_per_step_timeout_seconds=1,
        limit_chase_sleep_seconds=0.2,
        limit_price_offset_bps=offset_bps,
    )


def _policy_fingerprint(
    *,
    max_steps: int,
    fallback_mode: str,
    offset_bps: float = 15.0,
) -> str:
    return build_chase_policy_fingerprint(
        max_steps=max_steps,
        fallback_mode=fallback_mode,
        limit_price_offset_bps=offset_bps,
    )


def _base_order() -> OrderRequest:
    return OrderRequest(
        strategy_id="test",
        symbol="510300.SS",
        side="BUY",
        qty=10.0,
        reference_price=100.0,
        planned_trade_value=1000.0,
    )


def test_limit_chase_resubmits_only_remaining_qty_after_confirmed_partial_cancel():
    broker = _RecordingBroker()
    context = OrderRiskContext(available_cash=2000.0)
    executor = _ScriptedChaseExecutor(
        broker,
        RiskLimits(max_order_notional=None),
        risk_context=context,
        resolutions=[
            None,
            {"status": "Cancelled", "filled_qty": 4.0},
            {"status": "Filled", "filled_qty": 6.0},
        ],
    )
    executor.settings = _settings(max_steps=2)

    result = executor.execute(_base_order(), reference_price=100.0)

    assert [order.qty for order in broker.orders] == [10.0, 6.0]
    assert broker.cancel_requests == ["order-1"]
    assert result.final_mode == "limit_filled"
    # 资金预留按每轮实际限价计价，不能退回较低的原始计划金额。
    assert context.reserved_buy_notional == pytest.approx(1002.4)


def test_limit_chase_stops_when_cancel_is_not_confirmed():
    broker = _RecordingBroker()
    executor = _ScriptedChaseExecutor(
        broker,
        RiskLimits(max_order_notional=None),
        resolutions=[None, None],
    )
    executor.settings = _settings()

    result = executor.execute(_base_order(), reference_price=100.0)

    assert len(broker.orders) == 1
    assert broker.cancel_requests == ["order-1"]
    assert result.final_mode == "uncertain_stop"
    assert result.final_result.status == "cancel_unconfirmed"


def test_limit_chase_stops_on_unknown_terminal_without_retry_or_market_fallback():
    broker = _RecordingBroker()
    executor = _ScriptedChaseExecutor(
        broker,
        RiskLimits(max_order_notional=None),
        resolutions=[{"status": "UnknownTerminal", "filled_qty": 2.0}],
    )
    executor.settings = _settings(fallback_mode="market")

    result = executor.execute(_base_order(), reference_price=100.0)

    assert len(broker.orders) == 1
    assert broker.cancel_requests == []
    assert result.final_mode == "uncertain_stop"
    assert result.final_result.status == "unknown_terminal"


def test_limit_chase_market_fallback_uses_only_confirmed_remaining_qty():
    broker = _RecordingBroker()
    executor = _ScriptedChaseExecutor(
        broker,
        RiskLimits(max_order_notional=None),
        resolutions=[{"status": "Cancelled", "filled_qty": 4.0}],
    )
    executor.settings = _settings(max_steps=1, fallback_mode="market")

    result = executor.execute(_base_order(), reference_price=100.0)

    assert [(order.order_type, order.qty) for order in broker.orders] == [
        ("LMT", 10.0),
        ("MKT", 6.0),
    ]
    assert result.final_mode == "fallback_market"


@pytest.mark.parametrize(
    ("persisted_attempts", "max_steps", "fallback_mode"),
    [
        (
            [
                {"attempt_no": 1, "order_type": "LMT"},
                {"attempt_no": 2, "order_type": "LMT"},
                {"attempt_no": 3, "order_type": "LMT"},
                {"attempt_no": 4, "order_type": "MKT"},
            ],
            1,
            "cancel",
        ),
        (
            [
                {"attempt_no": 1, "order_type": "LMT"},
                {"attempt_no": 2, "order_type": "MKT"},
            ],
            3,
            "market",
        ),
    ],
)
def test_limit_chase_blocks_persisted_attempt_config_drift_before_route(
    persisted_attempts,
    max_steps,
    fallback_mode,
):
    current_fingerprint = _policy_fingerprint(
        max_steps=max_steps,
        fallback_mode=fallback_mode,
    )
    persisted_attempts = [
        {
            **row,
            "execution_policy_fingerprint": current_fingerprint,
        }
        for row in persisted_attempts
    ]

    class _PersistedAttemptsBroker(_RecordingBroker):
        def list_order_plan_attempts(self, order: OrderRequest) -> list[dict]:
            del order
            return list(persisted_attempts)

    broker = _PersistedAttemptsBroker()
    executor = LimitChaseExecutor(
        broker,
        RiskLimits(max_order_notional=None),
    )
    executor.settings = _settings(
        max_steps=max_steps,
        fallback_mode=fallback_mode,
    )

    with pytest.raises(RuntimeError, match="IDEMPOTENCY_CONFIG_CONFLICT"):
        executor.execute(_base_order(), reference_price=100.0)

    assert broker.orders == []


def test_limit_chase_blocks_gapped_persisted_attempt_sequence_before_route():
    current_fingerprint = _policy_fingerprint(
        max_steps=3,
        fallback_mode="cancel",
    )

    class _GappedAttemptsBroker(_RecordingBroker):
        def list_order_plan_attempts(self, order: OrderRequest) -> list[dict]:
            del order
            return [
                {
                    "attempt_no": 2,
                    "order_type": "LMT",
                    "execution_policy_fingerprint": current_fingerprint,
                }
            ]

    broker = _GappedAttemptsBroker()
    executor = LimitChaseExecutor(
        broker,
        RiskLimits(max_order_notional=None),
    )
    executor.settings = _settings(max_steps=3)

    with pytest.raises(
        RuntimeError,
        match="IDEMPOTENCY_CONFIG_CONFLICT.*序列存在缺口",
    ):
        executor.execute(_base_order(), reference_price=100.0)

    assert broker.orders == []


def test_limit_chase_blocks_legacy_attempt_without_policy_fingerprint():
    class _LegacyAttemptBroker(_RecordingBroker):
        def list_order_plan_attempts(self, order: OrderRequest) -> list[dict]:
            del order
            return [
                {
                    "attempt_no": 1,
                    "order_type": "LMT",
                    "execution_policy_fingerprint": None,
                }
            ]

    broker = _LegacyAttemptBroker()
    executor = LimitChaseExecutor(
        broker,
        RiskLimits(max_order_notional=None),
    )
    executor.settings = _settings(max_steps=1, fallback_mode="cancel")

    with pytest.raises(
        RuntimeError,
        match="IDEMPOTENCY_CONFIG_CONFLICT.*策略指纹缺失",
    ):
        executor.execute(_base_order(), reference_price=100.0)

    assert broker.orders == []


@pytest.mark.parametrize(
    ("old_max_steps", "old_fallback", "new_max_steps", "new_fallback"),
    [
        (1, "cancel", 2, "cancel"),
        (1, "cancel", 1, "market"),
    ],
)
def test_limit_chase_blocks_policy_expansion_before_route(
    old_max_steps,
    old_fallback,
    new_max_steps,
    new_fallback,
):
    old_fingerprint = _policy_fingerprint(
        max_steps=old_max_steps,
        fallback_mode=old_fallback,
    )

    class _OldPolicyBroker(_RecordingBroker):
        def list_order_plan_attempts(self, order: OrderRequest) -> list[dict]:
            del order
            return [
                {
                    "attempt_no": 1,
                    "order_type": "LMT",
                    "execution_policy_fingerprint": old_fingerprint,
                }
            ]

    broker = _OldPolicyBroker()
    executor = LimitChaseExecutor(
        broker,
        RiskLimits(max_order_notional=None),
    )
    executor.settings = _settings(
        max_steps=new_max_steps,
        fallback_mode=new_fallback,
    )

    with pytest.raises(
        RuntimeError,
        match="IDEMPOTENCY_CONFIG_CONFLICT.*策略指纹",
    ):
        executor.execute(_base_order(), reference_price=100.0)

    assert broker.orders == []


def test_limit_chase_attaches_policy_fingerprint_to_every_attempt():
    broker = _RecordingBroker()
    executor = _ScriptedChaseExecutor(
        broker,
        RiskLimits(max_order_notional=None),
        resolutions=[{"status": "Cancelled", "filled_qty": 0.0}],
    )
    executor.settings = _settings(max_steps=1, fallback_mode="market")

    executor.execute(_base_order(), reference_price=100.0)

    expected = _policy_fingerprint(
        max_steps=1,
        fallback_mode="market",
    )
    assert [order.execution_policy_fingerprint for order in broker.orders] == [
        expected,
        expected,
    ]


def test_limit_chase_replay_does_not_release_unowned_risk_reservation():
    class _ReplayBroker(_RecordingBroker):
        def submit_order(self, order: OrderRequest) -> OrderResult:
            self.orders.append(order)
            return OrderResult(
                accepted=True,
                broker_order_id="existing-1",
                status="Submitted",
                replayed=True,
            )

        def get_order_attempt_state(self, order: OrderRequest) -> dict:
            return {
                "account": "DU123456",
                "client_id": 7,
                "order_ref": "NSQ-existing",
                "perm_id": 101,
                "con_id": 756733,
                "status": "Submitted",
                "qty": float(order.qty),
                "filled_qty": 0.0,
                "remaining_qty": float(order.qty),
            }

    broker = _ReplayBroker()
    context = OrderRiskContext(
        available_cash=5000.0,
        reserved_buy_notional=250.0,
    )
    executor = _ScriptedChaseExecutor(
        broker,
        RiskLimits(max_order_notional=None),
        risk_context=context,
        resolutions=[{"status": "Cancelled", "filled_qty": 4.0}],
    )
    executor.settings = _settings(max_steps=1)

    result = executor.execute(_base_order(), reference_price=100.0)

    assert result.final_mode == "cancel_after_chase"
    assert context.reserved_buy_notional == pytest.approx(250.0)


def test_limit_chase_restart_does_not_deduct_historical_fill_twice():
    class _RestartBroker(_RecordingBroker):
        def restore_order_attempt(self, order: OrderRequest) -> OrderRequest:
            if order.attempt_no == 1:
                return replace(order, qty=10.0)
            return order

        def submit_order(self, order: OrderRequest) -> OrderResult:
            self.orders.append(order)
            if order.attempt_no == 1:
                return OrderResult(
                    accepted=True,
                    broker_order_id="existing-1",
                    status="Submitted",
                    replayed=True,
                )
            return OrderResult(
                accepted=True,
                broker_order_id="new-2",
                status="Submitted",
            )

        def get_order_attempt_state(self, order: OrderRequest) -> dict | None:
            if order.attempt_no != 1:
                return None
            return {
                "account": "DU123456",
                "client_id": 7,
                "order_ref": "NSQ-existing",
                "perm_id": 101,
                "con_id": 756733,
                "status": "Submitted",
                "qty": 10.0,
                "filled_qty": 4.0,
                "remaining_qty": 6.0,
            }

    broker = _RestartBroker()
    executor = _ScriptedChaseExecutor(
        broker,
        RiskLimits(max_order_notional=None),
        resolutions=[
            {"status": "Cancelled", "filled_qty": 4.0},
            {"status": "Filled", "filled_qty": 6.0},
        ],
    )
    executor.settings = _settings(max_steps=2)
    restarted_order = replace(
        _base_order(),
        qty=6.0,
        planned_trade_value=600.0,
    )

    result = executor.execute(restarted_order, reference_price=100.0)

    assert [order.qty for order in broker.orders] == [10.0, 6.0]
    assert result.attempts[0]["newly_filled_qty"] == 0.0
    assert result.final_mode == "limit_filled"


def test_limit_chase_uses_persisted_terminal_without_broker_order_id():
    class _TerminalReplayBroker(_RecordingBroker):
        def submit_order(self, order: OrderRequest) -> OrderResult:
            self.orders.append(order)
            return OrderResult(
                accepted=True,
                broker_order_id="",
                status="Filled",
                replayed=True,
            )

        def get_order_attempt_state(self, order: OrderRequest) -> dict:
            return {
                "account": "DU123456",
                "client_id": 7,
                "order_ref": "NSQ-terminal",
                "perm_id": 101,
                "con_id": 756733,
                "status": "Filled",
                "qty": float(order.qty),
                "filled_qty": float(order.qty),
                "remaining_qty": 0.0,
            }

    broker = _TerminalReplayBroker()
    executor = LimitChaseExecutor(
        broker,
        RiskLimits(max_order_notional=None),
    )
    executor.settings = _settings(max_steps=1)

    result = executor.execute(_base_order(), reference_price=100.0)

    assert result.final_mode == "limit_filled"
    assert result.final_result.broker_order_id == ""
    assert broker.cancel_requests == []


def test_snapshot_fallback_requires_exact_persisted_order_identity():
    expected = {
        "account": "DU123456",
        "client_id": 7,
        "order_ref": "NSQ-plan-attempt",
        "perm_id": 101,
        "con_id": 756733,
    }
    exact = {
        "broker_order_id": "42",
        **expected,
        "status": "Filled",
    }
    reused_by_other_client = {
        **exact,
        "client_id": 8,
        "order_ref": "NSQ-other",
    }

    assert (
        LimitChaseExecutor._find_snapshot_order(
            [reused_by_other_client, exact],
            "42",
            expected_identity=expected,
        )
        == exact
    )
    with pytest.raises(
        RuntimeError,
        match="BROKER_ORDER_IDENTITY_MISMATCH",
    ):
        LimitChaseExecutor._find_snapshot_order(
            [reused_by_other_client],
            "42",
            expected_identity=expected,
        )


def test_fill_fallback_requires_exact_persisted_broker_identity():
    expected = {
        "account": "DU123456",
        "client_id": 7,
        "order_ref": "NSQ-plan-attempt",
        "perm_id": 101,
        "con_id": 756733,
    }
    exact = FillSnapshot(
        broker_order_id="42",
        symbol="SPY",
        qty=2.0,
        price=500.0,
        side="BUY",
        account="DU123456",
        client_id=7,
        order_ref="NSQ-plan-attempt",
        perm_id=101,
        con_id=756733,
    )
    reused_by_other_client = replace(exact, client_id=8, qty=9.0)
    wrong_order_ref = replace(exact, order_ref="NSQ-other")

    assert LimitChaseExecutor._filled_qty_from_fills(
        [reused_by_other_client, exact],
        "42",
        expected_identity=expected,
    ) == pytest.approx(2.0)
    with pytest.raises(RuntimeError, match="BROKER_FILL_IDENTITY_MISMATCH"):
        LimitChaseExecutor._filled_qty_from_fills(
            [reused_by_other_client, wrong_order_ref],
            "42",
            expected_identity=expected,
        )


def test_wait_fallback_reads_completed_order_only_with_exact_identity():
    identity = {
        "account": "DU123456",
        "client_id": 7,
        "order_ref": "NSQ-plan-attempt",
        "perm_id": 101,
        "con_id": 756733,
    }

    class _SnapshotBroker(_RecordingBroker):
        def sync_state(self) -> BrokerStateSnapshot:
            return BrokerStateSnapshot(
                completed_orders=[
                    {
                        "broker_order_id": "42",
                        **identity,
                        "status": "Filled",
                        "filled_qty": 10.0,
                    }
                ]
            )

    executor = LimitChaseExecutor(
        _SnapshotBroker(),
        RiskLimits(max_order_notional=None),
    )
    executor.settings = _settings()

    terminal = executor._wait_for_terminal_or_timeout(
        "42",
        expected_qty=10.0,
        expected_identity=identity,
    )

    assert terminal is not None
    assert terminal["status"] == "Filled"

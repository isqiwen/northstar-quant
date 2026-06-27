from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from northstar_quant.config.settings import get_settings
from northstar_quant.config.trading_profile import load_trading_profile
from northstar_quant.execution.models import BrokerStateSnapshot, MarketQuoteSnapshot, PositionSnapshot
from northstar_quant.live import service as live_service
from northstar_quant.live.preflight import build_preflight_result
from northstar_quant.risk.models import SymbolTradeState


def _market_frame(asof: date) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [asof],
            "symbol": ["510300.SS"],
            "close": [500.0],
        }
    )


def test_build_preflight_result_allows_trade_with_non_blocking_execution_alert():
    profile = load_trading_profile("cn_etf_daily")
    asof = date.today() - timedelta(days=1)
    frame = _market_frame(asof)
    state = BrokerStateSnapshot(
        positions=[],
        open_orders=[],
        fills=[],
        account_values={"NetLiquidation": 100000.0},
        asof=datetime.now(UTC),
    )

    result = build_preflight_result(
        profile=profile,
        raw_market_df=frame,
        signal_market_df=frame,
        output_frame=pl.DataFrame({"date": [asof], "symbol": ["510300.SS"], "target_weight": [0.5]}),
        output_time_column="date",
        broker_state=state,
        execution_symbols=["510300.SS"],
        execution_reference_prices={"510300.SS": 500.0},
        execution_price_sources={"510300.SS": "broker_snapshot"},
        equity=100000.0,
        live_account_attribution={
            "alert_items": [
                {
                    "tag": "执行异常",
                    "message": "执行损耗达到 40.00，约 25.0 bps。",
                }
            ]
        },
    ).to_dict()

    assert result["can_trade"] is True
    assert result["blocking_failure_count"] == 0
    assert result["warning_count"] == 1
    assert any("非阻断异常" in message for message in result["warning_messages"])


def test_build_preflight_result_blocks_on_open_orders_fallback_quote_and_ledger_alert():
    profile = load_trading_profile("cn_etf_daily")
    asof = date.today() - timedelta(days=1)
    frame = _market_frame(asof)
    state = BrokerStateSnapshot(
        positions=[],
        open_orders=[
            {
                "broker_order_id": "paper-1",
                "symbol": "510300.SS",
                "side": "BUY",
                "remaining_qty": 5.0,
                "status": "Submitted",
            }
        ],
        fills=[],
        account_values={"NetLiquidation": 100000.0},
        asof=datetime.now(UTC),
    )

    result = build_preflight_result(
        profile=profile,
        raw_market_df=frame,
        signal_market_df=frame,
        output_frame=pl.DataFrame({"date": [asof], "symbol": ["510300.SS"], "target_weight": [0.5]}),
        output_time_column="date",
        broker_state=state,
        execution_symbols=["510300.SS"],
        execution_reference_prices={"510300.SS": 500.0},
        execution_price_sources={"510300.SS": "local_valuation_fallback"},
        equity=100000.0,
        live_account_attribution={
            "alert_items": [
                {
                    "tag": "账本异常",
                    "message": "未解释剩余达到 200.00。",
                }
            ]
        },
    ).to_dict()

    assert result["can_trade"] is False
    assert result["blocking_failure_count"] == 3
    assert any("未完成订单" in message for message in result["blocking_messages"])
    assert any("本地估值回退价" in message for message in result["blocking_messages"])
    assert any("账本异常" in message for message in result["blocking_messages"])


def test_run_live_once_returns_without_order_submission_when_preflight_fails(monkeypatch):
    profile = load_trading_profile("cn_etf_daily")
    asof = date.today() - timedelta(days=1)
    frame = _market_frame(asof)
    alerts: list[tuple[str, str]] = []

    class _FakeBroker:
        account = "paper"

        def connect(self) -> None:
            return None

        def disconnect(self) -> None:
            return None

        def sync_state(self) -> BrokerStateSnapshot:
            return BrokerStateSnapshot(
                positions=[],
                open_orders=[
                    {
                        "broker_order_id": "paper-1",
                        "symbol": "510300.SS",
                        "side": "BUY",
                        "remaining_qty": 5.0,
                        "status": "Submitted",
                    }
                ],
                fills=[],
                account_values={"NetLiquidation": 100000.0},
                asof=datetime.now(UTC),
            )

        def get_market_quotes(self, symbols: list[str]) -> list[MarketQuoteSnapshot]:
            del symbols
            return [
                MarketQuoteSnapshot(
                    symbol="510300.SS",
                    last=500.0,
                    close=500.0,
                    market_price=500.0,
                    asof=datetime.now(UTC),
                    source="paper_state",
                )
            ]

        def get_name(self) -> str:
            return "paper"

    monkeypatch.setattr(live_service, "load_trading_profile", lambda *_: profile)
    monkeypatch.setattr(live_service, "load_profile_market_data", lambda *_: frame)
    monkeypatch.setattr(live_service, "load_profile_signal_data", lambda *_: frame)
    monkeypatch.setattr(
        live_service,
        "run_profile_strategy_pipeline",
        lambda *_args, **_kwargs: SimpleNamespace(
            frame=pl.DataFrame({"date": [asof], "symbol": ["510300.SS"], "target_weight": [0.5]}),
            strategy_id="portfolio",
            output_type=live_service.StrategyOutputType.TARGET_WEIGHT,
            time_column="date",
        ),
    )
    monkeypatch.setattr(live_service, "_pick_broker", lambda service=None: _FakeBroker())
    monkeypatch.setattr(live_service, "SessionLocal", lambda: nullcontext(object()))
    monkeypatch.setattr(live_service, "save_strategy_run_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        live_service,
        "reconcile_broker_state",
        lambda *args, **kwargs: {"positions_synced": 0, "fills_synced": 0},
    )
    monkeypatch.setattr(live_service, "latest_live_account_attribution_summary", lambda **_: None)
    monkeypatch.setattr(
        live_service,
        "_record_run_health",
        lambda *args, **kwargs: {"id": 1, "mode": kwargs["mode"]},
    )
    monkeypatch.setattr(
        live_service,
        "resolve_execution_planner",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("planner should not run")),
    )
    monkeypatch.setattr(
        live_service,
        "send_alert",
        lambda message, level="info": alerts.append((level, message)),
    )

    messages = live_service.run_live_once(profile.profile_id)

    assert messages[0] == "PRECHECK_BLOCKED: 实盘 preflight 未通过，本次只同步不下单。"
    assert any("未完成订单" in message for message in messages[1:])
    assert alerts and alerts[0][0] == "warning"
    assert "只同步，不下单" in alerts[0][1]


def test_build_order_risk_context_reserves_existing_open_orders():
    state = BrokerStateSnapshot(
        positions=[
            PositionSnapshot(symbol="510300.SS", qty=100.0, sellable_qty=80.0),
        ],
        open_orders=[
            {
                "broker_order_id": "buy-1",
                "symbol": "510500.SS",
                "side": "BUY",
                "remaining_qty": 3.0,
                "status": "Submitted",
            },
            {
                "broker_order_id": "sell-1",
                "symbol": "510300.SS",
                "side": "SELL",
                "remaining_qty": 20.0,
                "status": "Submitted",
            },
        ],
        account_values={"AvailableFunds": 1000.0},
    )

    context = live_service._build_order_risk_context(
        state,
        {"510500.SS": 100.0},
        {
            "510300.SS": SymbolTradeState(
                limit_up_price=5.5,
                limit_down_price=4.5,
            )
        },
    )

    assert context.available_cash == 1000.0
    assert context.position_qty_by_symbol["510300.SS"] == 100.0
    assert context.sellable_qty_by_symbol["510300.SS"] == 80.0
    assert context.trade_state_by_symbol["510300.SS"].limit_up_price == 5.5
    assert context.reserved_buy_notional == 300.0
    assert context.reserved_sell_qty_by_symbol["510300.SS"] == 20.0
    assert context.unresolved_open_order_count == 0


def test_latest_trade_state_by_symbol_reads_optional_market_fields():
    frame = pl.DataFrame(
        {
            "date": ["2026-01-02", "2026-01-03", "2026-01-03"],
            "symbol": ["510300.SS", "510300.SS", "510500.SS"],
            "close": [5.0, 5.5, 6.0],
            "is_suspended": [False, True, False],
            "limit_up": [5.5, 6.05, 6.6],
            "limit_down": [4.5, 4.95, 5.4],
        }
    )

    state_by_symbol = live_service._latest_trade_state_by_symbol(frame)

    assert state_by_symbol["510300.SS"].is_suspended is True
    assert state_by_symbol["510300.SS"].limit_up_price == 6.05
    assert state_by_symbol["510300.SS"].limit_down_price == 4.95
    assert state_by_symbol["510500.SS"].is_suspended is False
    assert state_by_symbol["510500.SS"].limit_up_price == 6.6


def test_live_preflight_rejects_non_production_profile():
    with pytest.raises(ValueError, match="production 画像"):
        live_service.run_live_preflight("cn_stock_intraday_1m")


def test_run_live_once_blocks_when_kill_switch_is_enabled(monkeypatch):
    monkeypatch.setenv("NORTHSTAR_BROKER", "paper")
    monkeypatch.setenv("NORTHSTAR_KILL_SWITCH_ENABLED", "true")
    get_settings.cache_clear()
    alerts: list[tuple[str, str]] = []

    monkeypatch.setattr(
        live_service,
        "load_profile_market_data",
        lambda *_: (_ for _ in ()).throw(AssertionError("market data should not load")),
    )
    monkeypatch.setattr(
        live_service,
        "send_alert",
        lambda message, level="info": alerts.append((level, message)),
    )

    try:
        messages = live_service.run_live_once("cn_etf_daily")
    finally:
        get_settings.cache_clear()

    assert messages == ["KILL_SWITCH_ENABLED: 交易 kill switch 已开启，本次不下单。"]
    assert alerts and alerts[0][0] == "warning"


def test_run_live_once_blocks_real_broker_when_live_trading_switch_is_off(monkeypatch):
    monkeypatch.setenv("NORTHSTAR_BROKER", "ibkr")
    monkeypatch.setenv("NORTHSTAR_LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("NORTHSTAR_KILL_SWITCH_ENABLED", "false")
    get_settings.cache_clear()
    alerts: list[tuple[str, str]] = []

    monkeypatch.setattr(
        live_service,
        "IBKRService",
        lambda: (_ for _ in ()).throw(AssertionError("IBKR service should not be created")),
    )
    monkeypatch.setattr(
        live_service,
        "load_profile_market_data",
        lambda *_: (_ for _ in ()).throw(AssertionError("market data should not load")),
    )
    monkeypatch.setattr(
        live_service,
        "send_alert",
        lambda message, level="info": alerts.append((level, message)),
    )

    try:
        messages = live_service.run_live_once("cn_etf_daily")
    finally:
        get_settings.cache_clear()

    assert messages == [
        "LIVE_TRADING_DISABLED: 真实券商下单开关未开启；"
        "需要显式设置 NORTHSTAR_LIVE_TRADING_ENABLED=true。"
    ]
    assert alerts and alerts[0][0] == "warning"


def test_run_shadow_once_builds_plan_but_never_submits_orders(monkeypatch):
    profile = load_trading_profile("cn_etf_daily")
    asof = date.today() - timedelta(days=1)
    frame = _market_frame(asof)
    captured: dict[str, object] = {}

    class _FakeBroker:
        account = "paper"

        def connect(self) -> None:
            return None

        def disconnect(self) -> None:
            return None

        def sync_state(self) -> BrokerStateSnapshot:
            return BrokerStateSnapshot(
                positions=[],
                open_orders=[],
                fills=[],
                account_values={"NetLiquidation": 100000.0},
                asof=datetime.now(UTC),
            )

        def get_market_quotes(self, symbols: list[str]) -> list[MarketQuoteSnapshot]:
            del symbols
            return [
                MarketQuoteSnapshot(
                    symbol="510300.SS",
                    last=500.0,
                    close=500.0,
                    market_price=500.0,
                    asof=datetime.now(UTC),
                    source="paper_state",
                )
            ]

        def get_name(self) -> str:
            return "paper"

    fake_plan = SimpleNamespace(
        symbol="510300.SS",
        side="BUY",
        qty=10.0,
        target_weight=0.5,
        current_qty=0.0,
        target_qty=10.0,
        latest_price=500.0,
        execution_reference_price=500.0,
        estimated_trade_value=5000.0,
        strategy_id="core_portfolio",
        order_semantic=None,
        reason="1d_rebalance",
        order_type="MKT",
        limit_price=None,
        plan_id=None,
    )

    monkeypatch.setattr(live_service, "load_trading_profile", lambda *_: profile)
    monkeypatch.setattr(live_service, "load_profile_market_data", lambda *_: frame)
    monkeypatch.setattr(live_service, "load_profile_signal_data", lambda *_: frame)
    monkeypatch.setattr(
        live_service,
        "run_profile_strategy_pipeline",
        lambda *_args, **_kwargs: SimpleNamespace(
            frame=pl.DataFrame({"date": [asof], "symbol": ["510300.SS"], "target_weight": [0.5]}),
            strategy_id="portfolio",
            output_type=live_service.StrategyOutputType.TARGET_WEIGHT,
            time_column="date",
        ),
    )
    monkeypatch.setattr(live_service, "_pick_broker", lambda service=None: _FakeBroker())
    monkeypatch.setattr(live_service, "SessionLocal", lambda: nullcontext(object()))
    monkeypatch.setattr(live_service, "save_strategy_run_snapshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        live_service,
        "reconcile_broker_state",
        lambda *args, **kwargs: {"positions_synced": 0, "fills_synced": 0},
    )
    monkeypatch.setattr(live_service, "latest_live_account_attribution_summary", lambda **_: None)
    monkeypatch.setattr(
        live_service,
        "resolve_execution_planner",
        lambda *args, **kwargs: SimpleNamespace(planner_id="bar_close_rebalance"),
    )
    monkeypatch.setattr(live_service, "build_execution_plan", lambda *args, **kwargs: [fake_plan])
    monkeypatch.setattr(
        live_service,
        "save_execution_plan_records",
        lambda *args, **kwargs: 1,
    )
    monkeypatch.setattr(
        live_service,
        "_record_run_health",
        lambda *args, **kwargs: (
            captured.update({"health_mode": kwargs["mode"], "health_plan_count": len(kwargs["plans"])})
            or {"id": 1, "mode": kwargs["mode"]}
        ),
    )
    monkeypatch.setattr(
        live_service,
        "save_order_result",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("shadow run must not submit orders")),
    )

    result = live_service.run_shadow_once(profile.profile_id)

    assert result["mode"] == "shadow_run"
    assert result["plan_count"] == 1
    assert result["planned_order_count"] == 1
    assert captured == {"health_mode": "shadow_run", "health_plan_count": 1}

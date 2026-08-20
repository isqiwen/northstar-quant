"""实盘服务批次级失败关闭测试。"""

from datetime import date
from types import SimpleNamespace

import pytest
import polars as pl

from northstar_quant.platform.common.enums import AssetType
from northstar_quant.trading_execution.execution.models import OrderRequest, OrderResult
from northstar_quant.application.live_service import (
    _collect_execution_symbols,
    _latest_valuation_price_map,
    _route_order_batch_fail_closed,
)


class _RecordingLogger:
    def bind(self, **_kwargs):
        return self

    def info(self, *_args, **_kwargs):
        return None

    def exception(self, *_args, **_kwargs):
        return None


class _FailingSecondRouter:
    def __init__(self) -> None:
        self.symbols: list[str] = []

    def route(self, order: OrderRequest) -> OrderResult:
        self.symbols.append(order.symbol)
        if len(self.symbols) == 2:
            raise RuntimeError("券商拒绝测试订单")
        return OrderResult(
            accepted=True,
            broker_order_id=f"order-{len(self.symbols)}",
            status="Submitted",
            message=f"accepted:{order.symbol}",
        )


def test_order_batch_stops_after_first_failure_and_reports_remaining_count():
    router = _FailingSecondRouter()
    orders = [
        OrderRequest(
            strategy_id="test",
            symbol=symbol,
            side="BUY",
            qty=1.0,
        )
        for symbol in ("AAA", "BBB", "CCC")
    ]

    messages, halted_reason = _route_order_batch_fail_closed(
        router,
        orders,
        run_logger=_RecordingLogger(),
    )

    assert router.symbols == ["AAA", "BBB"]
    assert messages[0] == "accepted:AAA"
    assert halted_reason is not None
    assert "已停止剩余 1 笔订单" in halted_reason
    assert messages[-1] == halted_reason


def test_latest_valuation_price_is_selected_by_market_date():
    market_df = pl.DataFrame(
        {
            "date": [
                date(2026, 7, 28),
                date(2026, 7, 27),
                date(2026, 7, 28),
            ],
            "symbol": ["AAA", "AAA", "BBB"],
            "close": [12.0, 99.0, 23.0],
        }
    )

    assert _latest_valuation_price_map(market_df) == {
        "AAA": 12.0,
        "BBB": 23.0,
    }


def test_live_service_rejects_continuous_research_symbol_before_ctp_lookup():
    profile = SimpleNamespace(
        asset_type=AssetType.FUTURES,
        futures=SimpleNamespace(ctp_contract_mapping_path="unused.yaml"),
    )

    with pytest.raises(ValueError, match="FUTURES_CONTINUOUS_CONTRACT_FORBIDDEN"):
        _collect_execution_symbols(
            profile,
            pl.DataFrame({"symbol": ["RB_CONT"]}),
            SimpleNamespace(positions=(), open_orders=()),
            broker_name="ctp_sim",
        )

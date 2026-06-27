from northstar_quant.config.settings import get_settings
from northstar_quant.execution.models import OrderRequest
from northstar_quant.execution.paper_broker import PaperBrokerAdapter


def _make_paper_broker(tmp_path, monkeypatch, *, default_cash: float = 100000.0) -> PaperBrokerAdapter:
    monkeypatch.setenv("NORTHSTAR_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("NORTHSTAR_DEFAULT_CASH", str(default_cash))
    monkeypatch.setenv("NORTHSTAR_PAPER_FILL_PRICE_MODE", "reference")
    get_settings.cache_clear()
    return PaperBrokerAdapter()


def test_paper_broker_market_order_persists_positions_fills_and_quotes(tmp_path, monkeypatch):
    try:
        broker = _make_paper_broker(tmp_path, monkeypatch)
        result = broker.submit_order(
            OrderRequest(
                strategy_id="paper-test",
                symbol="AAPL",
                side="BUY",
                qty=10.0,
                reference_price=100.0,
            )
        )

        assert result.accepted is True

        snapshot = broker.sync_state()
        positions = {row.symbol: row for row in snapshot.positions}

        assert snapshot.open_orders == []
        assert len(snapshot.fills) == 1
        assert positions["AAPL"].qty == 10.0
        assert positions["AAPL"].avg_cost == 100.0
        assert positions["AAPL"].market_price == 100.0
        assert snapshot.account_values["CashBalance"] == 99000.0
        assert snapshot.account_values["NetLiquidation"] == 100000.0

        reloaded_broker = _make_paper_broker(tmp_path, monkeypatch)
        reloaded_snapshot = reloaded_broker.sync_state()
        reloaded_positions = {row.symbol: row for row in reloaded_snapshot.positions}
        quotes = reloaded_broker.get_market_quotes(["AAPL", "MSFT"])

        assert reloaded_positions["AAPL"].qty == 10.0
        assert len(reloaded_snapshot.fills) == 1
        assert len(quotes) == 1
        assert quotes[0].symbol == "AAPL"
        assert quotes[0].market_price == 100.0
        assert quotes[0].source == "paper_state"
    finally:
        get_settings.cache_clear()


def test_paper_broker_limit_order_can_partial_fill_then_complete_on_next_sync(tmp_path, monkeypatch):
    try:
        broker = _make_paper_broker(tmp_path, monkeypatch)
        broker.submit_order(
            OrderRequest(
                strategy_id="paper-test",
                symbol="QQQ",
                side="BUY",
                qty=10.0,
                order_type="LMT",
                limit_price=101.0,
                reference_price=100.0,
            )
        )

        first_snapshot = broker.sync_state()
        first_positions = {row.symbol: row for row in first_snapshot.positions}

        assert len(first_snapshot.open_orders) == 1
        assert first_snapshot.open_orders[0]["status"] == "PartiallyFilled"
        assert first_snapshot.open_orders[0]["filled_qty"] == 5.0
        assert first_snapshot.open_orders[0]["remaining_qty"] == 5.0
        assert len(first_snapshot.fills) == 1
        assert first_positions["QQQ"].qty == 5.0

        reloaded_broker = _make_paper_broker(tmp_path, monkeypatch)
        second_snapshot = reloaded_broker.sync_state()
        second_positions = {row.symbol: row for row in second_snapshot.positions}

        assert second_snapshot.open_orders == []
        assert len(second_snapshot.fills) == 2
        assert second_positions["QQQ"].qty == 10.0
        assert second_positions["QQQ"].avg_cost == 100.0
        assert second_snapshot.account_values["CashBalance"] == 99000.0
        assert second_snapshot.account_values["NetLiquidation"] == 100000.0
    finally:
        get_settings.cache_clear()


def test_paper_broker_cancel_removes_unmarketable_open_order(tmp_path, monkeypatch):
    try:
        broker = _make_paper_broker(tmp_path, monkeypatch)
        result = broker.submit_order(
            OrderRequest(
                strategy_id="paper-test",
                symbol="SPY",
                side="BUY",
                qty=10.0,
                order_type="LMT",
                limit_price=95.0,
                reference_price=100.0,
            )
        )

        pending_snapshot = broker.sync_state()

        assert pending_snapshot.positions == []
        assert pending_snapshot.fills == []
        assert len(pending_snapshot.open_orders) == 1
        assert pending_snapshot.open_orders[0]["status"] == "Submitted"

        reloaded_broker = _make_paper_broker(tmp_path, monkeypatch)

        assert reloaded_broker.cancel_order(result.broker_order_id) is True

        cancelled_snapshot = reloaded_broker.sync_state()

        assert cancelled_snapshot.open_orders == []
        assert cancelled_snapshot.positions == []
        assert cancelled_snapshot.fills == []
        assert cancelled_snapshot.account_values["CashBalance"] == 100000.0
    finally:
        get_settings.cache_clear()


def test_paper_broker_updates_avg_cost_and_equity_across_multiple_fills(tmp_path, monkeypatch):
    try:
        broker = _make_paper_broker(tmp_path, monkeypatch)
        broker.submit_order(
            OrderRequest(
                strategy_id="paper-test",
                symbol="IWM",
                side="BUY",
                qty=10.0,
                reference_price=100.0,
            )
        )
        broker.sync_state()

        reloaded_broker = _make_paper_broker(tmp_path, monkeypatch)
        reloaded_broker.submit_order(
            OrderRequest(
                strategy_id="paper-test",
                symbol="IWM",
                side="BUY",
                qty=10.0,
                reference_price=110.0,
            )
        )

        snapshot = reloaded_broker.sync_state()
        position = {row.symbol: row for row in snapshot.positions}["IWM"]

        assert position.qty == 20.0
        assert position.avg_cost == 105.0
        assert position.market_price == 110.0
        assert snapshot.account_values["CashBalance"] == 97900.0
        assert snapshot.account_values["GrossPositionValue"] == 2200.0
        assert snapshot.account_values["NetLiquidation"] == 100100.0
    finally:
        get_settings.cache_clear()

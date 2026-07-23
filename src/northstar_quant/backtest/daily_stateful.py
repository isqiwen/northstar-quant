"""日频持仓账本与成交状态机回测器。

本模块是纯内存、确定性模拟器：收盘时读取目标权重并创建订单，订单最早在下一
交易日开盘成交。它不调用券商、不复用实盘的提交状态，也不会写数据库。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from numbers import Real

import polars as pl

from northstar_quant.backtest.event_engine import BacktestResult


class DailyOrderStatus(StrEnum):
    """日频模拟订单的有限终态集合。"""

    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class DailyBacktestConfig:
    """日频成交模拟的显式假设。

    目标在收盘生成，并在 ``execution_delay_sessions`` 个交易日后的 ``open`` 成交。
    ``commission_bps`` 和 ``slippage_bps`` 均为基点；买单滑点抬高价格，卖单滑点
    压低价格。该版本只支持无杠杆、无做空的现金账户。
    """

    initial_cash: float = 100_000.0
    commission_bps: float = 0.0
    min_commission: float = 0.0
    slippage_bps: float = 0.0
    lot_size: int = 1
    execution_delay_sessions: int = 1
    sellable_after_sessions: int = 0

    def __post_init__(self) -> None:
        if not math.isfinite(self.initial_cash) or self.initial_cash <= 0:
            raise ValueError("initial_cash 必须是大于 0 的有限数值")
        for field_name, value in (
            ("commission_bps", self.commission_bps),
            ("min_commission", self.min_commission),
            ("slippage_bps", self.slippage_bps),
        ):
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{field_name} 必须是大于等于 0 的有限数值")
        if self.slippage_bps >= 10_000:
            raise ValueError("slippage_bps 必须小于 10000")
        if isinstance(self.lot_size, bool) or not isinstance(self.lot_size, int) or self.lot_size <= 0:
            raise ValueError("lot_size 必须是大于 0 的整数")
        if (
            isinstance(self.execution_delay_sessions, bool)
            or not isinstance(self.execution_delay_sessions, int)
            or self.execution_delay_sessions < 1
        ):
            raise ValueError("execution_delay_sessions 必须是大于等于 1 的整数")
        if (
            isinstance(self.sellable_after_sessions, bool)
            or not isinstance(self.sellable_after_sessions, int)
            or self.sellable_after_sessions < 0
        ):
            raise ValueError("sellable_after_sessions 必须是大于等于 0 的整数")


@dataclass(slots=True)
class DailyOrder:
    """一次目标仓位调整产生的模拟订单。"""

    order_id: str
    decision_date: date
    scheduled_date: date | None
    symbol: str
    side: str
    requested_qty: int
    target_weight: float
    status: DailyOrderStatus = DailyOrderStatus.PENDING
    filled_qty: int = 0
    unfilled_qty: int = 0
    reason: str = "target_rebalance"


@dataclass(frozen=True, slots=True)
class DailyFill:
    """一次模拟成交，保留成交价、费用和已实现盈亏。"""

    order_id: str
    trade_date: date
    symbol: str
    side: str
    qty: int
    reference_price: float
    execution_price: float
    notional: float
    commission: float
    realized_pnl: float


@dataclass(frozen=True, slots=True)
class DailyPositionSnapshot:
    """收盘后的单标的持仓快照。"""

    date: date
    symbol: str
    qty: int
    sellable_qty: int
    avg_cost: float
    close_price: float
    market_value: float
    unrealized_pnl: float


@dataclass(frozen=True, slots=True)
class DailyPortfolioSnapshot:
    """收盘后的组合账本快照。"""

    date: date
    cash: float
    positions_value: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    traded_notional: float


@dataclass(frozen=True, slots=True)
class DailyStatefulBacktestResult:
    """日频状态机回测结果及其可审计账本。"""

    summary: BacktestResult
    orders: tuple[DailyOrder, ...]
    fills: tuple[DailyFill, ...]
    portfolio_snapshots: tuple[DailyPortfolioSnapshot, ...]
    position_snapshots: tuple[DailyPositionSnapshot, ...]


@dataclass(slots=True)
class _Lot:
    qty: int
    unit_cost: float
    acquired_session: int


def run_daily_stateful_backtest(
    market_df: pl.DataFrame,
    targets: pl.DataFrame,
    *,
    config: DailyBacktestConfig | None = None,
    periods_per_year: int = 252,
) -> DailyStatefulBacktestResult:
    """运行逐日持仓、现金和撮合状态机回测。

    ``market_df`` 必须有 ``date/symbol/open/close``；每个 ``date + symbol`` 只能有
    一条 bar。``targets`` 必须有 ``date/symbol/target_weight``，某个目标日期未出现
    的标的按目标权重 0 处理，因此可表达清仓。单日目标权重总和不得超过 1，当前
    实现不模拟杠杆或做空。

    每个交易日的顺序固定为：先按开盘价处理已排期订单（卖单先于买单），再按收盘
    价估值，最后根据当天的目标权重生成未来订单。这个顺序保证收盘信号不能在同一
    根日 bar 成交。订单因现金、可卖数量或缺失价格不能完整成交时会进入明确终态，
    不会隐式沿用到未来日期。
    """

    active_config = config or DailyBacktestConfig()
    _validate_periods_per_year(periods_per_year)
    if market_df.is_empty():
        return _empty_result()

    market_by_date, sessions, universe = _parse_market_data(market_df)
    target_by_date = _parse_targets(targets, market_by_date=market_by_date, universe=universe)

    cash = float(active_config.initial_cash)
    lots_by_symbol: dict[str, list[_Lot]] = {symbol: [] for symbol in universe}
    pending_by_session: dict[int, list[DailyOrder]] = {}
    orders: list[DailyOrder] = []
    fills: list[DailyFill] = []
    portfolio_snapshots: list[DailyPortfolioSnapshot] = []
    position_snapshots: list[DailyPositionSnapshot] = []
    realized_pnl = 0.0
    order_sequence = 0

    for session_index, session_date in enumerate(sessions):
        price_rows = market_by_date[session_date]
        traded_notional = 0.0
        for order in sorted(
            pending_by_session.pop(session_index, []),
            key=lambda item: (item.side != "SELL", item.symbol, item.order_id),
        ):
            fill, cash_delta, realized_delta = _execute_order(
                order,
                session_date=session_date,
                session_index=session_index,
                price_row=price_rows.get(order.symbol),
                lots_by_symbol=lots_by_symbol,
                cash=cash,
                config=active_config,
            )
            cash += cash_delta
            realized_pnl += realized_delta
            if fill is not None:
                fills.append(fill)
                traded_notional += fill.notional

        positions_value, unrealized_pnl, daily_positions = _mark_to_market(
            session_date=session_date,
            session_index=session_index,
            price_rows=price_rows,
            lots_by_symbol=lots_by_symbol,
            sellable_after_sessions=active_config.sellable_after_sessions,
        )
        equity = cash + positions_value
        portfolio_snapshots.append(
            DailyPortfolioSnapshot(
                date=session_date,
                cash=cash,
                positions_value=positions_value,
                equity=equity,
                realized_pnl=realized_pnl,
                unrealized_pnl=unrealized_pnl,
                traded_notional=traded_notional,
            )
        )
        position_snapshots.extend(daily_positions)

        if session_date not in target_by_date:
            continue
        target_weights = target_by_date[session_date]
        scheduled_index = session_index + active_config.execution_delay_sessions
        for symbol in universe:
            target_weight = target_weights.get(symbol, 0.0)
            close_price = price_rows[symbol]["close"]
            target_qty = _round_down_to_lot(
                equity * target_weight / close_price,
                active_config.lot_size,
            )
            current_qty = _position_qty(lots_by_symbol[symbol])
            delta = target_qty - current_qty
            if delta == 0:
                continue
            order_sequence += 1
            side = "BUY" if delta > 0 else "SELL"
            order = DailyOrder(
                order_id=f"daily-{session_date.isoformat()}-{order_sequence:04d}",
                decision_date=session_date,
                scheduled_date=sessions[scheduled_index] if scheduled_index < len(sessions) else None,
                symbol=symbol,
                side=side,
                requested_qty=abs(delta),
                target_weight=target_weight,
                unfilled_qty=abs(delta),
            )
            orders.append(order)
            if scheduled_index < len(sessions):
                pending_by_session.setdefault(scheduled_index, []).append(order)
            else:
                order.status = DailyOrderStatus.EXPIRED
                order.reason = "no_future_session"

    return DailyStatefulBacktestResult(
        summary=_build_summary(portfolio_snapshots, periods_per_year=periods_per_year),
        orders=tuple(orders),
        fills=tuple(fills),
        portfolio_snapshots=tuple(portfolio_snapshots),
        position_snapshots=tuple(position_snapshots),
    )


def _parse_market_data(
    market_df: pl.DataFrame,
) -> tuple[dict[date, dict[str, dict[str, float]]], list[date], tuple[str, ...]]:
    required_columns = {"date", "symbol", "open", "close"}
    missing = sorted(required_columns.difference(market_df.columns))
    if missing:
        raise ValueError(f"日频状态机回测缺少行情列: {', '.join(missing)}")

    market_by_date: dict[date, dict[str, dict[str, float]]] = {}
    for row in market_df.select("date", "symbol", "open", "close").iter_rows(named=True):
        session_date = _normalize_date(row["date"], field_name="market.date")
        symbol = str(row["symbol"])
        if not symbol:
            raise ValueError("行情 symbol 不能为空")
        if session_date in market_by_date and symbol in market_by_date[session_date]:
            raise ValueError(f"行情存在重复 date + symbol: {session_date} / {symbol}")
        open_price = _positive_price(row["open"], field_name=f"{session_date} {symbol} open")
        close_price = _positive_price(row["close"], field_name=f"{session_date} {symbol} close")
        market_by_date.setdefault(session_date, {})[symbol] = {
            "open": open_price,
            "close": close_price,
        }

    sessions = sorted(market_by_date)
    universe = tuple(sorted({symbol for rows in market_by_date.values() for symbol in rows}))
    universe_set = set(universe)
    for session_date, price_rows in market_by_date.items():
        missing_symbols = sorted(universe_set.difference(price_rows))
        if missing_symbols:
            raise ValueError(
                f"行情交易日缺少标的，无法可靠估值: {session_date} / {', '.join(missing_symbols)}"
            )
    return market_by_date, sessions, universe


def _parse_targets(
    targets: pl.DataFrame,
    *,
    market_by_date: dict[date, dict[str, dict[str, float]]],
    universe: tuple[str, ...],
) -> dict[date, dict[str, float]]:
    if targets.is_empty():
        return {}
    required_columns = {"date", "symbol", "target_weight"}
    missing = sorted(required_columns.difference(targets.columns))
    if missing:
        raise ValueError(f"日频状态机回测缺少目标列: {', '.join(missing)}")

    universe_set = set(universe)
    target_by_date: dict[date, dict[str, float]] = {}
    for row in targets.select("date", "symbol", "target_weight").iter_rows(named=True):
        decision_date = _normalize_date(row["date"], field_name="targets.date")
        symbol = str(row["symbol"])
        if decision_date not in market_by_date:
            raise ValueError(f"目标日期不在行情交易日中: {decision_date}")
        if symbol not in universe_set:
            raise ValueError(f"目标标的未出现在行情中: {symbol}")
        if symbol not in market_by_date[decision_date]:
            raise ValueError(f"目标标的当天缺少行情: {decision_date} / {symbol}")
        weight = float(row["target_weight"])
        if not math.isfinite(weight) or not 0 <= weight <= 1:
            raise ValueError(f"目标权重必须在 [0, 1] 内: {decision_date} / {symbol}")
        daily_targets = target_by_date.setdefault(decision_date, {})
        if symbol in daily_targets:
            raise ValueError(f"目标存在重复 date + symbol: {decision_date} / {symbol}")
        daily_targets[symbol] = weight

    for decision_date, daily_targets in target_by_date.items():
        if sum(daily_targets.values()) > 1.0 + 1e-9:
            raise ValueError(f"目标权重总和不能超过 1: {decision_date}")
    return target_by_date


def _execute_order(
    order: DailyOrder,
    *,
    session_date: date,
    session_index: int,
    price_row: dict[str, float] | None,
    lots_by_symbol: dict[str, list[_Lot]],
    cash: float,
    config: DailyBacktestConfig,
) -> tuple[DailyFill | None, float, float]:
    if price_row is None:
        order.status = DailyOrderStatus.REJECTED
        order.reason = "missing_execution_price"
        return None, 0.0, 0.0

    reference_price = price_row["open"]
    slippage_ratio = config.slippage_bps / 10_000.0
    execution_price = reference_price * (1.0 + slippage_ratio if order.side == "BUY" else 1.0 - slippage_ratio)
    if execution_price <= 0:
        order.status = DailyOrderStatus.REJECTED
        order.reason = "invalid_execution_price"
        return None, 0.0, 0.0

    if order.side == "BUY":
        qty = _affordable_buy_qty(
            requested_qty=order.requested_qty,
            price=execution_price,
            cash=cash,
            config=config,
        )
        if qty == 0:
            order.status = DailyOrderStatus.REJECTED
            order.reason = "insufficient_cash"
            return None, 0.0, 0.0
        notional = execution_price * qty
        commission = _commission(notional, config)
        lots_by_symbol[order.symbol].append(
            _Lot(
                qty=qty,
                unit_cost=(notional + commission) / qty,
                acquired_session=session_index,
            )
        )
        cash_delta = -(notional + commission)
        realized_delta = 0.0
    else:
        sellable_qty = _sellable_qty(
            lots_by_symbol[order.symbol],
            session_index=session_index,
            sellable_after_sessions=config.sellable_after_sessions,
        )
        qty = min(order.requested_qty, sellable_qty)
        qty = _round_down_to_lot(qty, config.lot_size)
        if qty == 0:
            order.status = DailyOrderStatus.REJECTED
            order.reason = "insufficient_sellable_qty"
            return None, 0.0, 0.0
        notional = execution_price * qty
        commission = _commission(notional, config)
        cost_basis = _consume_lots(
            lots_by_symbol[order.symbol],
            qty=qty,
            session_index=session_index,
            sellable_after_sessions=config.sellable_after_sessions,
        )
        cash_delta = notional - commission
        realized_delta = notional - commission - cost_basis

    order.filled_qty = qty
    order.unfilled_qty = order.requested_qty - qty
    if order.unfilled_qty == 0:
        order.status = DailyOrderStatus.FILLED
        order.reason = "filled"
    else:
        order.status = DailyOrderStatus.PARTIALLY_FILLED
        order.reason = "cash_limited" if order.side == "BUY" else "sellable_qty_limited"

    return (
        DailyFill(
            order_id=order.order_id,
            trade_date=session_date,
            symbol=order.symbol,
            side=order.side,
            qty=qty,
            reference_price=reference_price,
            execution_price=execution_price,
            notional=notional,
            commission=commission,
            realized_pnl=realized_delta,
        ),
        cash_delta,
        realized_delta,
    )


def _mark_to_market(
    *,
    session_date: date,
    session_index: int,
    price_rows: dict[str, dict[str, float]],
    lots_by_symbol: dict[str, list[_Lot]],
    sellable_after_sessions: int,
) -> tuple[float, float, list[DailyPositionSnapshot]]:
    positions_value = 0.0
    unrealized_pnl = 0.0
    snapshots: list[DailyPositionSnapshot] = []
    for symbol, lots in sorted(lots_by_symbol.items()):
        qty = _position_qty(lots)
        if qty == 0:
            continue
        if symbol not in price_rows:
            raise ValueError(f"持仓标的当天缺少收盘价: {session_date} / {symbol}")
        close_price = price_rows[symbol]["close"]
        total_cost = sum(lot.qty * lot.unit_cost for lot in lots)
        market_value = qty * close_price
        position_unrealized_pnl = market_value - total_cost
        positions_value += market_value
        unrealized_pnl += position_unrealized_pnl
        snapshots.append(
            DailyPositionSnapshot(
                date=session_date,
                symbol=symbol,
                qty=qty,
                sellable_qty=_sellable_qty(
                    lots,
                    session_index=session_index,
                    sellable_after_sessions=sellable_after_sessions,
                ),
                avg_cost=total_cost / qty,
                close_price=close_price,
                market_value=market_value,
                unrealized_pnl=position_unrealized_pnl,
            )
        )
    return positions_value, unrealized_pnl, snapshots


def _build_summary(
    snapshots: list[DailyPortfolioSnapshot],
    *,
    periods_per_year: int,
) -> BacktestResult:
    if not snapshots:
        return BacktestResult(0.0, 0.0, 0.0, 0.0)

    first_equity = snapshots[0].equity
    final_equity = snapshots[-1].equity
    total_return = final_equity / first_equity - 1.0 if first_equity else 0.0
    annualized_return = (
        (final_equity / first_equity) ** (periods_per_year / len(snapshots)) - 1.0
        if first_equity > 0 and final_equity > 0
        else -1.0
    )
    running_max = -math.inf
    max_drawdown = 0.0
    drawdown_curve: list[dict[str, float | str]] = []
    equity_curve: list[dict[str, float | str]] = []
    monthly_products: dict[str, float] = {}
    turnover_ratios: list[float] = []
    previous_equity: float | None = None
    for snapshot in snapshots:
        running_max = max(running_max, snapshot.equity)
        drawdown = snapshot.equity / running_max - 1.0 if running_max > 0 else 0.0
        max_drawdown = min(max_drawdown, drawdown)
        equity_curve.append({"date": snapshot.date.isoformat(), "equity": snapshot.equity})
        drawdown_curve.append({"date": snapshot.date.isoformat(), "drawdown": drawdown})
        if previous_equity is not None and previous_equity != 0:
            month_key = snapshot.date.strftime("%Y-%m")
            monthly_products[month_key] = monthly_products.get(month_key, 1.0) * (
                snapshot.equity / previous_equity
            )
        turnover_ratios.append(snapshot.traded_notional / max(snapshot.equity, 1.0))
        previous_equity = snapshot.equity

    return BacktestResult(
        total_return=total_return,
        annualized_return=annualized_return,
        max_drawdown=max_drawdown,
        turnover_estimate=sum(turnover_ratios) / len(turnover_ratios),
        equity_curve=equity_curve,
        drawdown_curve=drawdown_curve,
        monthly_returns=[
            {"month": month, "return": value - 1.0}
            for month, value in sorted(monthly_products.items())
        ],
    )


def _empty_result() -> DailyStatefulBacktestResult:
    return DailyStatefulBacktestResult(
        summary=BacktestResult(0.0, 0.0, 0.0, 0.0),
        orders=(),
        fills=(),
        portfolio_snapshots=(),
        position_snapshots=(),
    )


def _normalize_date(value: object, *, field_name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise ValueError(f"{field_name} 必须是 date 或 datetime")


def _positive_price(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field_name} 必须是大于 0 的有限价格")
    price = float(value)
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"{field_name} 必须是大于 0 的有限价格")
    return price


def _validate_periods_per_year(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("periods_per_year 必须是大于 0 的整数")


def _round_down_to_lot(qty: float | int, lot_size: int) -> int:
    return max(math.floor(float(qty) / lot_size) * lot_size, 0)


def _position_qty(lots: list[_Lot]) -> int:
    return sum(lot.qty for lot in lots)


def _sellable_qty(
    lots: list[_Lot],
    *,
    session_index: int,
    sellable_after_sessions: int,
) -> int:
    return sum(
        lot.qty
        for lot in lots
        if session_index - lot.acquired_session >= sellable_after_sessions
    )


def _consume_lots(
    lots: list[_Lot],
    *,
    qty: int,
    session_index: int,
    sellable_after_sessions: int,
) -> float:
    remaining = qty
    cost_basis = 0.0
    retained: list[_Lot] = []
    for lot in lots:
        sellable = session_index - lot.acquired_session >= sellable_after_sessions
        consumed = min(lot.qty, remaining) if sellable else 0
        remaining -= consumed
        cost_basis += consumed * lot.unit_cost
        if lot.qty > consumed:
            retained.append(
                _Lot(
                    qty=lot.qty - consumed,
                    unit_cost=lot.unit_cost,
                    acquired_session=lot.acquired_session,
                )
            )
    if remaining != 0:
        raise RuntimeError("模拟卖出数量超过可卖持仓")
    lots[:] = retained
    return cost_basis


def _commission(notional: float, config: DailyBacktestConfig) -> float:
    return max(notional * config.commission_bps / 10_000.0, config.min_commission)


def _affordable_buy_qty(
    *,
    requested_qty: int,
    price: float,
    cash: float,
    config: DailyBacktestConfig,
) -> int:
    qty = min(
        requested_qty,
        _round_down_to_lot(cash / (price * (1.0 + config.commission_bps / 10_000.0)), config.lot_size),
    )
    while qty > 0 and price * qty + _commission(price * qty, config) > cash + 1e-9:
        qty -= config.lot_size
    return max(qty, 0)

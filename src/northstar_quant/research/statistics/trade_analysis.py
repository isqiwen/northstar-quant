"""将标准化成交按 FIFO 归并为已完成交易。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from northstar_quant.research.statistics.metrics import calculate_trade_metrics
from northstar_quant.research.statistics.models import ExecutionFill, TradeAnalysis, TradeRecord

_EPSILON = 1e-9


@dataclass(slots=True)
class _OpenLot:
    fill: ExecutionFill
    remaining_qty: float
    remaining_commission: float


def analyze_long_only_fills(fills: list[ExecutionFill] | tuple[ExecutionFill, ...]) -> TradeAnalysis:
    """将只做多成交按 FIFO 原则归并为交易明细。

    BUY 会创建入场 lot，SELL 依次关闭最早的同标的 lot。入场和出场手续费都按
    实际关闭数量比例分摊。若 SELL 超过可用数量则立即报错，避免把错误成交静默
    当作做空。未平仓 lot 不会伪造已完成交易，数量会返回在 ``open_qty_by_symbol``。
    """

    lots_by_symbol: dict[str, list[_OpenLot]] = {}
    trades: list[TradeRecord] = []

    for sequence, fill in enumerate(fills):
        _validate_fill(fill)
        side = fill.side.upper()
        lots = lots_by_symbol.setdefault(fill.symbol, [])
        if side == "BUY":
            lots.append(
                _OpenLot(
                    fill=fill,
                    remaining_qty=fill.qty,
                    remaining_commission=fill.commission,
                )
            )
            continue

        remaining_qty = fill.qty
        remaining_exit_commission = fill.commission
        while remaining_qty > _EPSILON:
            if not lots:
                raise ValueError(
                    f"成交 {fill.fill_id} 的 SELL 数量超过可用多头持仓: {fill.symbol}"
                )
            entry_lot = lots[0]
            closed_qty = min(remaining_qty, entry_lot.remaining_qty)
            entry_commission = _proportional_amount(
                entry_lot.remaining_commission,
                closed_qty,
                entry_lot.remaining_qty,
            )
            exit_commission = _proportional_amount(
                remaining_exit_commission,
                closed_qty,
                remaining_qty,
            )
            gross_pnl = (fill.price - entry_lot.fill.price) * closed_qty
            net_pnl = gross_pnl - entry_commission - exit_commission
            initial_risk = _initial_risk(entry_lot.fill, closed_qty)
            pnl_r = net_pnl / initial_risk if initial_risk is not None else None
            trades.append(
                TradeRecord(
                    trade_id=f"{entry_lot.fill.fill_id}:{fill.fill_id}:{sequence}:{len(trades) + 1}",
                    symbol=fill.symbol,
                    strategy_id=entry_lot.fill.strategy_id,
                    entry_fill_id=entry_lot.fill.fill_id,
                    exit_fill_id=fill.fill_id,
                    entry_time=entry_lot.fill.timestamp,
                    exit_time=fill.timestamp,
                    qty=closed_qty,
                    entry_price=entry_lot.fill.price,
                    exit_price=fill.price,
                    initial_stop_price=entry_lot.fill.initial_stop_price,
                    target_r=entry_lot.fill.target_r,
                    entry_commission=entry_commission,
                    exit_commission=exit_commission,
                    gross_pnl=gross_pnl,
                    net_pnl=net_pnl,
                    initial_risk=initial_risk,
                    pnl_r=pnl_r,
                    entry_reason=entry_lot.fill.reason,
                    exit_reason=fill.reason,
                )
            )
            entry_lot.remaining_qty -= closed_qty
            entry_lot.remaining_commission -= entry_commission
            remaining_qty -= closed_qty
            remaining_exit_commission -= exit_commission
            if entry_lot.remaining_qty <= _EPSILON:
                lots.pop(0)

    open_qty_by_symbol = {
        symbol: sum(lot.remaining_qty for lot in lots)
        for symbol, lots in lots_by_symbol.items()
        if sum(lot.remaining_qty for lot in lots) > _EPSILON
    }
    trade_tuple = tuple(trades)
    return TradeAnalysis(
        trades=trade_tuple,
        metrics=calculate_trade_metrics(trade_tuple),
        open_qty_by_symbol=open_qty_by_symbol,
    )


def _validate_fill(fill: ExecutionFill) -> None:
    if not fill.fill_id:
        raise ValueError("fill_id 不能为空")
    if not fill.symbol:
        raise ValueError("symbol 不能为空")
    if fill.side.upper() not in {"BUY", "SELL"}:
        raise ValueError(f"成交 {fill.fill_id} 的 side 仅支持 BUY / SELL")
    for field_name, value, allow_zero in (
        ("qty", fill.qty, False),
        ("price", fill.price, False),
        ("commission", fill.commission, True),
    ):
        if not math.isfinite(value) or (value < 0 if allow_zero else value <= 0):
            comparator = "大于等于 0" if allow_zero else "大于 0"
            raise ValueError(f"成交 {fill.fill_id} 的 {field_name} 必须是{comparator}的有限数值")
    if fill.initial_stop_price is not None:
        if not math.isfinite(fill.initial_stop_price) or fill.initial_stop_price <= 0:
            raise ValueError(f"成交 {fill.fill_id} 的 initial_stop_price 必须是大于 0 的有限数值")
        if fill.side.upper() == "BUY" and fill.initial_stop_price >= fill.price:
            raise ValueError(f"成交 {fill.fill_id} 的 initial_stop_price 必须低于入场价格")
    if fill.target_r is not None and (not math.isfinite(fill.target_r) or fill.target_r <= 0):
        raise ValueError(f"成交 {fill.fill_id} 的 target_r 必须是大于 0 的有限数值")


def _initial_risk(entry_fill: ExecutionFill, qty: float) -> float | None:
    if entry_fill.initial_stop_price is None:
        return None
    return (entry_fill.price - entry_fill.initial_stop_price) * qty


def _proportional_amount(amount: float, qty: float, total_qty: float) -> float:
    if qty >= total_qty - _EPSILON:
        return amount
    return amount * qty / total_qty

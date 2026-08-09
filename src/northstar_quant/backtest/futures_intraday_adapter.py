"""实际合约分钟数据到订单回放状态机的适配器。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
import math
from typing import Any, cast

import pandas as pd  # type: ignore[import-untyped]
import polars as pl

from northstar_quant.backtest.event_engine import (
    BacktestResult,
    _drawdown_from_initial_equity,
)
from northstar_quant.backtest.futures_daily import FuturesInstrumentSpec
from northstar_quant.backtest.futures_intraday import (
    FuturesIntradayBar,
    IntradayWeightTarget,
    run_intraday_futures_replay,
)
from northstar_quant.config.product_cards import load_product_cards
from northstar_quant.config.trading_profile import TradingProfile
from northstar_quant.data.futures_intraday import intraday_active_contract_rows
from northstar_quant.data.schema import validate_market_dataset


def run_actual_futures_intraday_replay(
    profile: TradingProfile,
    market_df: pl.DataFrame,
    targets: pl.DataFrame,
) -> BacktestResult:
    """将日频连续目标映射到分钟实际合约并回放完整委托生命周期。"""

    validate_market_dataset(profile, market_df)
    _validate_targets(targets)
    normalized = market_df.with_columns(
        pl.col("symbol").cast(pl.String).str.strip_chars().str.to_uppercase(),
        pl.col("product").cast(pl.String).str.strip_chars().str.to_uppercase(),
        pl.col("exchange").cast(pl.String).str.strip_chars().str.to_uppercase(),
        pl.col("active_contract").cast(pl.String).str.strip_chars().str.to_uppercase(),
        pl.col("session").cast(pl.String).str.strip_chars().str.to_lowercase(),
    )
    cards = {card.product: card for card in load_product_cards()}
    specs: dict[str, FuturesInstrumentSpec] = {}
    bars: list[FuturesIntradayBar] = []
    for row in normalized.sort(["timestamp", "product", "symbol"]).to_dicts():
        instrument_id = str(row["symbol"])
        product = str(row["product"])
        card = cards[product]
        specs.setdefault(
            instrument_id,
            FuturesInstrumentSpec(
                instrument_id=instrument_id,
                product=product,
                exchange_id=str(row["exchange"]),
                multiplier=card.multiplier,
                tick_size=card.tick_size,
                slippage_ticks=profile.backtest.slippage_ticks,
            ),
        )
        bars.append(_bar_from_row(row))

    sessions = sorted(normalized.get_column("date").unique().to_list())
    active_rows = intraday_active_contract_rows(normalized)
    active_by_day = {
        (cast(date, row["date"]), str(row["product"])): str(row["active_contract"])
        for row in active_rows
    }
    weight_targets = _build_weight_targets(
        targets,
        sessions,
        active_by_day,
        execution_delay_sessions=profile.backtest.execution_delay_sessions,
    )
    replay = run_intraday_futures_replay(
        bars=bars,
        instrument_specs=specs.values(),
        weight_targets=weight_targets,
        initial_cash=profile.backtest.initial_cash,
        max_volume_participation=profile.backtest.max_volume_participation,
        queue_ahead_ratio=profile.backtest.queue_ahead_ratio,
        generated_order_ttl_bars=profile.backtest.order_ttl_bars,
    )
    return _to_backtest_result(
        replay,
        specs,
        initial_cash=profile.backtest.initial_cash,
    )


def _bar_from_row(row: dict[str, Any]) -> FuturesIntradayBar:
    return FuturesIntradayBar(
        trading_day=cast(date, row["date"]),
        timestamp=row["timestamp"],
        instrument_id=str(row["symbol"]),
        session=str(row["session"]),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
        open_interest=float(row["open_interest"]),
        bid_price=float(row["bid_price"]),
        ask_price=float(row["ask_price"]),
        bid_volume=float(row["bid_volume"]),
        ask_volume=float(row["ask_volume"]),
        settlement=float(row["settlement"]),
        pre_settlement=float(row["pre_settlement"]),
        upper_limit=float(row["upper_limit"]),
        lower_limit=float(row["lower_limit"]),
        margin_rate=float(row["margin_rate"]),
        commission_open_per_lot=float(row["commission_open_per_lot"]),
        commission_open_rate=float(row["commission_open_rate"]),
        commission_close_per_lot=float(row["commission_close_per_lot"]),
        commission_close_rate=float(row["commission_close_rate"]),
        commission_close_today_per_lot=float(
            row["commission_close_today_per_lot"]
        ),
        commission_close_today_rate=float(row["commission_close_today_rate"]),
        max_position_lots=int(row["max_position_lots"]),
        is_trading_day_end=bool(row["is_trading_day_end"]),
        session_complete=bool(row["session_complete"]),
    )


def _build_weight_targets(
    targets: pl.DataFrame,
    sessions: list[date],
    active_by_day: dict[tuple[date, str], str],
    *,
    execution_delay_sessions: int,
) -> list[IntradayWeightTarget]:
    target_pd = targets.select("date", "symbol", "target_weight").to_pandas()
    target_pd["date"] = pd.to_datetime(target_pd["date"]).dt.date
    target_pd["product"] = (
        target_pd["symbol"].astype(str).str.upper().str.removesuffix("_CONT")
    )
    if target_pd.duplicated(["date", "product"]).any():
        raise ValueError("分钟回放目标在 date/product 上存在重复记录")
    products = sorted({product for _, product in active_by_day})
    unknown_dates = sorted(set(target_pd["date"]).difference(sessions))
    if unknown_dates:
        raise ValueError(
            "分钟回放目标包含交易日历外的决策日："
            + ", ".join(str(item) for item in unknown_dates)
        )
    unknown = sorted(set(target_pd["product"]).difference(products))
    if unknown:
        raise ValueError("目标权重包含分钟合约链中不存在的品种：" + ", ".join(unknown))
    weights = (
        target_pd.pivot(index="date", columns="product", values="target_weight")
        .reindex(index=sessions, columns=products)
        .ffill()
        .fillna(0.0)
    )
    result: list[IntradayWeightTarget] = []
    for decision_offset, decision_day in enumerate(sessions):
        execution_offset = decision_offset + execution_delay_sessions
        if execution_offset >= len(sessions):
            continue
        execution_day = sessions[execution_offset]
        for product in products:
            instrument_id = active_by_day.get((execution_day, product))
            if instrument_id is None:
                raise ValueError(f"{execution_day}/{product} 缺少执行日主力合约映射")
            result.append(
                IntradayWeightTarget(
                    decision_day=decision_day,
                    execution_day=execution_day,
                    instrument_id=instrument_id,
                    target_weight=float(weights.at[decision_day, product]),
                )
            )
    return result


def _to_backtest_result(
    replay,
    specs: dict[str, FuturesInstrumentSpec],
    *,
    initial_cash: float,
) -> BacktestResult:
    curve = pd.DataFrame(replay.equity_curve)
    curve["date"] = pd.to_datetime(curve["date"])
    curve = curve.set_index("date")
    normalized = curve["equity"].astype(float) / initial_cash
    returns = normalized.pct_change().fillna(normalized.iloc[0] - 1.0)
    drawdown = _drawdown_from_initial_equity(normalized)
    total_return = float(normalized.iloc[-1] - 1.0)
    annualized_return = (
        -1.0
        if normalized.iloc[-1] <= 0
        else float(normalized.iloc[-1] ** (252 / max(len(normalized), 1)) - 1.0)
    )

    turnover_notional: dict[date, float] = {}
    for trade in replay.trades:
        spec = specs[trade.instrument_id]
        turnover_notional[trade.trading_day] = (
            turnover_notional.get(trade.trading_day, 0.0)
            + trade.qty * trade.price * spec.multiplier
        )
    turnover_rows: list[dict[str, float | str]] = []
    turnover_values: list[float] = []
    for timestamp, equity in curve["equity"].items():
        current_day = timestamp.date()
        turnover = turnover_notional.get(current_day, 0.0) / max(
            abs(float(equity)),
            1e-12,
        )
        turnover_values.append(turnover)
        turnover_rows.append({"date": current_day.isoformat(), "turnover": turnover})
    monthly = returns.resample("ME").apply(lambda values: (1.0 + values).prod() - 1.0)

    return BacktestResult(
        total_return=total_return,
        annualized_return=annualized_return,
        max_drawdown=float(drawdown.min()),
        turnover_estimate=float(sum(turnover_values) / len(turnover_values)),
        equity_curve=[
            {
                "date": timestamp.date().isoformat(),
                "equity": float(row["equity"]) / initial_cash,
                "margin": float(row["margin"]),
                "available_funds": float(row["available_funds"]),
                "margin_ratio": float(row["margin"])
                / max(abs(float(row["equity"])), 1e-12),
                "available_funds_ratio": float(row["available_funds"])
                / max(abs(float(row["equity"])), 1e-12),
            }
            for timestamp, row in curve.iterrows()
        ],
        drawdown_curve=[
            {
                "date": timestamp.date().isoformat(),
                "drawdown": float(value),
            }
            for timestamp, value in drawdown.items()
        ],
        monthly_returns=[
            {"month": timestamp.strftime("%Y-%m"), "return": float(value)}
            for timestamp, value in monthly.items()
        ],
        turnover_curve=turnover_rows,
        trades=[
            {
                **asdict(trade),
                "trading_day": trade.trading_day.isoformat(),
                "timestamp": trade.timestamp.isoformat(),
                "notional": float(
                    trade.qty * trade.price * specs[trade.instrument_id].multiplier
                ),
            }
            for trade in replay.trades
        ],
        orders=[
            {
                "order_id": order.request.order_id,
                "submitted_at": order.request.submitted_at.isoformat(),
                "instrument_id": order.request.instrument_id,
                "side": order.request.side.value,
                "offset": order.request.offset.value,
                "order_type": order.request.order_type.value,
                "limit_price": order.request.limit_price,
                "requested_qty": order.request.qty,
                "filled_qty": order.filled_qty,
                "average_fill_price": order.average_fill_price,
                "commission": order.commission,
                "status": order.status.value,
                "reason": order.request.reason,
                "message": order.message,
                "updated_at": (
                    order.updated_at.isoformat() if order.updated_at else None
                ),
            }
            for order in replay.orders
        ],
        rejected_orders=list(replay.rejected_orders),
    )


def _validate_targets(targets: pl.DataFrame) -> None:
    if targets.is_empty():
        raise ValueError("分钟实际合约回放目标权重不能为空")
    missing = sorted({"date", "symbol", "target_weight"}.difference(targets.columns))
    if missing:
        raise ValueError("分钟回放目标缺少字段：" + ", ".join(missing))
    invalid = sum(
        1
        for value in targets.get_column("target_weight").to_list()
        if not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or abs(float(value)) > 1
    )
    if invalid:
        raise ValueError("分钟回放 target_weight 必须位于 [-1, 1]")

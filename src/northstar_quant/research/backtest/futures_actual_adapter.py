"""实际合约数据集到逐日状态机的适配器。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
import math
from typing import Any, cast

import pandas as pd  # type: ignore[import-untyped]
import polars as pl

from northstar_quant.research.backtest.event_engine import _drawdown_from_initial_equity
from northstar_quant.research.backtest.models import BacktestEngine, BacktestResult
from northstar_quant.research.backtest.futures_daily import (
    FuturesDailyBar,
    FuturesInstrumentSpec,
    FuturesRollover,
    FuturesWeightTarget,
    run_daily_futures_backtest,
)
from northstar_quant.research.backtest.metrics import periods_per_year_for_frequency
from northstar_quant.data.contracts.product_cards import load_product_cards
from northstar_quant.foundation.config.trading_profile import TradingProfile
from northstar_quant.data.market.futures_actual import active_contract_rows
from northstar_quant.data.quality.schema import validate_market_dataset


def run_actual_futures_backtest(
    profile: TradingProfile,
    market_df: pl.DataFrame,
    targets: pl.DataFrame,
) -> BacktestResult:
    """将连续信号目标映射到实际合约并运行逐日保证金账户回测。"""

    validate_market_dataset(profile, market_df)
    _validate_targets(targets)
    normalized = market_df.with_columns(
        pl.col("symbol").cast(pl.String).str.strip_chars().str.to_uppercase(),
        pl.col("product").cast(pl.String).str.strip_chars().str.to_uppercase(),
        pl.col("exchange").cast(pl.String).str.strip_chars().str.to_uppercase(),
        pl.col("active_contract").cast(pl.String).str.strip_chars().str.to_uppercase(),
        pl.col("first_session").cast(pl.String).str.strip_chars().str.to_lowercase(),
    )
    sessions = sorted(normalized.get_column("date").unique().to_list())
    cards = {card.product: card for card in load_product_cards()}
    rows = normalized.sort(["date", "product", "symbol"]).to_dicts()

    specs_by_symbol: dict[str, FuturesInstrumentSpec] = {}
    bars: list[FuturesDailyBar] = []
    for row in rows:
        instrument_id = str(row["symbol"])
        product = str(row["product"])
        card = cards[product]
        specs_by_symbol.setdefault(
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

    active_rows = active_contract_rows(normalized)
    active_by_day = {
        (row["date"], str(row["product"])): str(row["active_contract"])
        for row in active_rows
    }
    rollovers = _build_rollovers(active_rows)
    weight_targets = _build_weight_targets(
        targets,
        sessions,
        active_by_day,
        execution_delay_sessions=profile.backtest.execution_delay_sessions,
    )
    state_result = run_daily_futures_backtest(
        bars=bars,
        instrument_specs=specs_by_symbol.values(),
        weight_targets=weight_targets,
        rollovers=rollovers,
        initial_cash=profile.backtest.initial_cash,
        trading_calendar=sessions,
        execution_delay_sessions=profile.backtest.execution_delay_sessions,
        max_volume_participation=profile.backtest.max_volume_participation,
    )
    return _to_backtest_result(
        state_result,
        specs_by_symbol,
        initial_cash=profile.backtest.initial_cash,
        periods_per_year=periods_per_year_for_frequency(profile.data_frequency),
    )


def _bar_from_row(row: dict[str, Any]) -> FuturesDailyBar:
    return FuturesDailyBar(
        trading_day=cast(date, row["date"]),
        instrument_id=str(row["symbol"]),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        settlement=float(row["settlement"]),
        pre_settlement=float(row["pre_settlement"]),
        volume=float(row["volume"]),
        open_interest=float(row["open_interest"]),
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
        first_session=str(row["first_session"]),
        session_complete=bool(row["session_complete"]),
    )


def _build_rollovers(
    rows: list[dict[str, Any]],
) -> list[FuturesRollover]:
    previous_by_product: dict[str, str] = {}
    result: list[FuturesRollover] = []
    for row in rows:
        product = str(row["product"])
        current = str(row["active_contract"])
        previous = previous_by_product.get(product)
        if previous is not None and previous != current:
            result.append(
                FuturesRollover(
                    trading_day=cast(date, row["date"]),
                    from_instrument_id=previous,
                    to_instrument_id=current,
                )
            )
        previous_by_product[product] = current
    return result


def _build_weight_targets(
    targets: pl.DataFrame,
    sessions: list[date],
    active_by_day: dict[tuple[date, str], str],
    *,
    execution_delay_sessions: int,
) -> list[FuturesWeightTarget]:
    """复现收益型引擎的目标前向填充，再绑定执行日实际合约。"""

    target_pd = targets.select("date", "symbol", "target_weight").to_pandas()
    target_pd["date"] = pd.to_datetime(target_pd["date"]).dt.date
    target_pd["product"] = (
        target_pd["symbol"].astype(str).str.upper().str.removesuffix("_CONT")
    )
    duplicate_count = int(target_pd.duplicated(["date", "product"]).sum())
    if duplicate_count:
        raise ValueError("实际合约回测目标在 date/product 上存在重复记录")

    products = sorted({product for _, product in active_by_day})
    unknown_dates = sorted(set(target_pd["date"]).difference(sessions))
    if unknown_dates:
        raise ValueError(
            "目标权重包含交易日历外的决策日："
            + ", ".join(str(item) for item in unknown_dates)
        )
    unknown_products = sorted(set(target_pd["product"]).difference(products))
    if unknown_products:
        raise ValueError(
            "目标权重包含实际合约链中不存在的品种：" + ", ".join(unknown_products)
        )
    weights = target_pd.pivot(index="date", columns="product", values="target_weight")
    weights = (
        weights.reindex(index=sessions, columns=products)
        .ffill()
        .fillna(0.0)
    )

    result: list[FuturesWeightTarget] = []
    for decision_offset, decision_day in enumerate(sessions):
        execution_offset = decision_offset + execution_delay_sessions
        if execution_offset >= len(sessions):
            continue
        execution_day = sessions[execution_offset]
        for product in products:
            instrument_id = active_by_day.get((execution_day, product))
            if instrument_id is None:
                raise ValueError(
                    f"{execution_day}/{product} 缺少执行日主力合约映射"
                )
            result.append(
                FuturesWeightTarget(
                    decision_day=decision_day,
                    instrument_id=instrument_id,
                    target_weight=float(weights.at[decision_day, product]),
                )
            )
    return result


def _to_backtest_result(
    state_result,
    specs: dict[str, FuturesInstrumentSpec],
    *,
    initial_cash: float,
    periods_per_year: int,
) -> BacktestResult:
    curve = pd.DataFrame(state_result.equity_curve)
    curve["date"] = pd.to_datetime(curve["date"])
    curve = curve.set_index("date")
    normalized = curve["equity"].astype(float) / float(initial_cash)
    returns = normalized.pct_change().fillna(normalized.iloc[0] - 1.0)
    drawdown = _drawdown_from_initial_equity(normalized)
    total_return = float(normalized.iloc[-1] - 1.0)
    if normalized.iloc[-1] <= 0:
        annualized_return = -1.0
    else:
        annualized_return = float(
            normalized.iloc[-1] ** (periods_per_year / max(len(normalized), 1)) - 1.0
        )

    turnover_notional: dict[date, float] = {}
    for trade in state_result.trades:
        spec = specs[trade.instrument_id]
        turnover_notional[trade.trading_day] = (
            turnover_notional.get(trade.trading_day, 0.0)
            + trade.qty * trade.price * spec.multiplier
        )
    turnover_rows: list[dict[str, float | str]] = []
    turnover_values: list[float] = []
    for current_day, equity in curve["equity"].items():
        day = current_day.date()
        turnover = turnover_notional.get(day, 0.0) / max(abs(float(equity)), 1e-12)
        turnover_values.append(turnover)
        turnover_rows.append({"date": day.isoformat(), "turnover": turnover})
    monthly = returns.resample("ME").apply(lambda values: (1.0 + values).prod() - 1.0)

    return BacktestResult(
        engine=BacktestEngine.FUTURES_DAILY,
        total_return=total_return,
        annualized_return=annualized_return,
        max_drawdown=float(drawdown.min()),
        turnover_estimate=float(sum(turnover_values) / len(turnover_values)),
        equity_curve=[
            {
                "date": current_day.date().isoformat(),
                "equity": float(row["equity"]) / float(initial_cash),
                "margin": float(row["margin"]),
                "available_funds": float(row["available_funds"]),
                "margin_ratio": float(row["margin"]) / max(abs(float(row["equity"])), 1e-12),
                "available_funds_ratio": float(row["available_funds"])
                / max(abs(float(row["equity"])), 1e-12),
            }
            for current_day, row in curve.iterrows()
        ],
        drawdown_curve=[
            {
                "date": current_day.date().isoformat(),
                "drawdown": float(value),
            }
            for current_day, value in drawdown.items()
        ],
        monthly_returns=[
            {"month": current_day.strftime("%Y-%m"), "return": float(value)}
            for current_day, value in monthly.items()
        ],
        turnover_curve=turnover_rows,
        trades=[
            {
                **asdict(trade),
                "trading_day": trade.trading_day.isoformat(),
                "notional": float(
                    trade.qty * trade.price * specs[trade.instrument_id].multiplier
                ),
            }
            for trade in state_result.trades
        ],
        rejected_orders=list(state_result.rejected_targets),
    )


def _validate_targets(targets: pl.DataFrame) -> None:
    if targets.is_empty():
        raise ValueError("实际合约回测目标权重不能为空")
    missing = sorted({"date", "symbol", "target_weight"}.difference(targets.columns))
    if missing:
        raise ValueError("实际合约回测目标缺少字段：" + ", ".join(missing))
    invalid = sum(
        1
        for value in targets.get_column("target_weight").to_list()
        if not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or abs(float(value)) > 1
    )
    if invalid:
        raise ValueError("实际合约回测 target_weight 必须位于 [-1, 1]")

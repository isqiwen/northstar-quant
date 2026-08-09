"""画像驱动离线回测的唯一编排入口。

本模块把已发布数据制品、策略管线、目标权重回测器和可审计运行清单固定为同一次
运行。它只读取已发布数据制品，绝不生成订单或调用券商。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import subprocess
from typing import Any

import polars as pl

from northstar_quant import __version__
from northstar_quant.backtest.event_engine import BacktestResult
from northstar_quant.backtest.metrics import periods_per_year_for_frequency
from northstar_quant.backtest.registry import resolve_target_backtester
from northstar_quant.common.enums import StrategyOutputType
from northstar_quant.common.types import StrategyOutputBundle
from northstar_quant.config.trading_profile import TradingProfile, load_trading_profile
from northstar_quant.data.schema import to_signal_market_data
from northstar_quant.data.storage import (
    dataset_manifest_path,
    load_json,
    load_profile_market_data,
    profile_config_sha256,
    profile_market_data_path,
)
from northstar_quant.strategies.pipeline import (
    build_profile_risk_limits,
    latest_pipeline_output,
    resolve_selected_profile_strategy_ids,
    run_profile_strategy_pipeline,
)


BACKTEST_MANIFEST_SCHEMA_VERSION = "northstar_backtest_manifest_v1"
_EPSILON = 1e-12
_MIN_STATISTICAL_OBSERVATIONS = 20


@dataclass(frozen=True, slots=True)
class BacktestRun:
    """一次完整历史回测的冻结运行视图。

    ``manifest`` 仅包含可安全归档的配置、数据和代码指纹；不写入本地绝对路径、环境
    变量或下载器扩展选项，避免报告制品泄露运行环境信息。
    """

    profile: TradingProfile
    selected_strategy_ids: tuple[str, ...]
    backtester_id: str
    pipeline: StrategyOutputBundle
    latest_holdings: pl.DataFrame
    result: BacktestResult
    metrics: dict[str, object]
    analytics: dict[str, object]
    manifest: dict[str, object]

    @property
    def run_id(self) -> str:
        return str(self.manifest["run_id"])

    @property
    def artifact_period(self) -> str:
        curve = _report_equity_curve(self.analytics)
        if not curve:
            raise ValueError("回测结果缺少净值曲线，无法确定报告周期")
        start = str(curve[0]["date"])[:10].replace("-", "")
        end = str(curve[-1]["date"])[:10].replace("-", "")
        return f"{start}-{end}"

    @property
    def period_label(self) -> str:
        curve = _report_equity_curve(self.analytics)
        if not curve:
            raise ValueError("回测结果缺少净值曲线，无法确定报告周期")
        start = str(curve[0]["date"])[:10]
        end = str(curve[-1]["date"])[:10]
        return f"{start} 至 {end}"


def run_profile_backtest_run(
    profile_id: str | None = None,
    *,
    strategy_ids: Sequence[str] | None = None,
) -> BacktestRun:
    """运行一次完整、可归档的历史回测。

    数据必须先通过 ``load_profile_market_data`` 的内容哈希和 schema 校验。策略使用完整
    历史生成信号，再交由画像唯一匹配的目标权重回测器执行；因此研究、CLI 回测和
    周期报告可以引用同一运行语义，而不会各自拼装流程。
    """

    profile = load_trading_profile(profile_id)
    raw_market_df = load_profile_market_data(profile)
    source_manifest = _load_safe_source_manifest(profile)
    signal_market_df = to_signal_market_data(profile, raw_market_df)
    selected_strategy_ids = resolve_selected_profile_strategy_ids(profile, strategy_ids)
    pipeline = run_profile_strategy_pipeline(
        signal_market_df,
        profile,
        strategy_ids=selected_strategy_ids if strategy_ids is not None else None,
        latest_only=False,
    )
    if pipeline.output_type != StrategyOutputType.TARGET_WEIGHT:
        raise ValueError(
            f"策略输出类型为 {pipeline.output_type.value}，不能运行目标权重回测；"
            "当前正式回测器仅接受 target_weight。"
        )
    if pipeline.frame.is_empty():
        raise ValueError("策略管线没有产生任何历史目标权重，已拒绝生成空回测报告")

    backtester = resolve_target_backtester(profile)
    result = backtester.backtester(profile, raw_market_df, pipeline.frame)
    if not result.equity_curve:
        raise ValueError("回测器未返回净值曲线，已拒绝生成不可审计的回测结果")

    latest_holdings = latest_pipeline_output(pipeline)
    periods_per_year = _result_periods_per_year(profile)
    evaluation = _build_evaluation_view(
        result,
        targets=pipeline.frame,
        time_column=pipeline.time_column,
        execution_delay_sessions=profile.backtest.execution_delay_sessions,
    )
    evaluation_curve = _report_equity_curve(evaluation)
    performance = _calculate_equity_performance(
        evaluation_curve,
        periods_per_year=periods_per_year,
    )
    benchmark = _build_benchmark_analytics(
        signal_market_df,
        benchmark_symbol=profile.benchmark_symbol,
        equity_curve=evaluation_curve,
        periods_per_year=periods_per_year,
        sample_is_sufficient=bool(performance["sample_is_sufficient"]),
    )
    execution = _build_execution_analytics(result, backtester_id=backtester.backtester_id)
    analytics: dict[str, object] = {
        "equity_curve": evaluation_curve,
        "drawdown_curve": evaluation["drawdown_curve"],
        "monthly_returns": evaluation["monthly_returns"],
        "turnover_curve": evaluation["turnover_curve"],
        "full_equity_curve": result.equity_curve,
        "full_drawdown_curve": result.drawdown_curve,
        "evaluation": evaluation["metadata"],
        "trades": result.trades,
        "orders": result.orders,
        "rejected_orders": result.rejected_orders,
        "performance": performance,
        "benchmark": benchmark,
        "execution": execution,
    }
    metrics = _build_report_metrics(
        result,
        performance=performance,
        benchmark=benchmark,
        execution=execution,
        backtester_id=backtester.backtester_id,
        evaluation=evaluation["metadata"],
    )
    manifest = _build_run_manifest(
        profile=profile,
        source_manifest=source_manifest,
        selected_strategy_ids=selected_strategy_ids,
        pipeline=pipeline,
        result=result,
        backtester_id=backtester.backtester_id,
        periods_per_year=periods_per_year,
        analytics=analytics,
        evaluation=evaluation["metadata"],
    )
    return BacktestRun(
        profile=profile,
        selected_strategy_ids=selected_strategy_ids,
        backtester_id=backtester.backtester_id,
        pipeline=pipeline,
        latest_holdings=latest_holdings,
        result=result,
        metrics=metrics,
        analytics=analytics,
        manifest=manifest,
    )


def run_profile_backtest(profile_id: str | None = None) -> dict[str, Any]:
    """保留研究摘要 API，并委托给唯一的完整回测工作流。"""

    run = run_profile_backtest_run(profile_id)
    return {
        "profile_id": run.profile.profile_id,
        "run_id": run.run_id,
        "price_field": run.profile.data.price_field,
        "output_type": run.pipeline.output_type.value,
        "selected_strategy_ids": list(run.selected_strategy_ids),
        "total_return": run.result.total_return,
        "annualized_return": run.result.annualized_return,
        "max_drawdown": run.result.max_drawdown,
        "turnover_estimate": run.result.turnover_estimate,
        "symbols": sorted(set(run.latest_holdings["symbol"].to_list()))
        if "symbol" in run.latest_holdings.columns
        else [],
        "latest_holdings": run.latest_holdings.to_dicts(),
        "trade_count": len(run.result.trades),
        "rejected_order_count": len(run.result.rejected_orders),
    }


def _load_safe_source_manifest(profile: TradingProfile) -> dict[str, object]:
    """从已校验的数据 manifest 提取不含环境路径和下载参数的归档字段。"""

    manifest_path = dataset_manifest_path(profile_market_data_path(profile))
    raw = load_json(manifest_path)
    schema = raw.get("schema")
    return {
        "manifest_version": raw.get("manifest_version"),
        "dataset_id": raw.get("dataset_id"),
        "data_source": raw.get("data_source"),
        "content_sha256": raw.get("content_sha256"),
        "profile_config_sha256": raw.get("profile_config_sha256"),
        "schema_version": schema.get("schema_version") if isinstance(schema, dict) else None,
        "row_count": raw.get("row_count"),
        "symbol_count": raw.get("symbol_count"),
        "symbols": raw.get("symbols"),
        "columns": raw.get("columns"),
        "start": raw.get("start"),
        "end": raw.get("end"),
        "quality": raw.get("quality"),
        "versions": raw.get("versions"),
    }


def _result_periods_per_year(profile: TradingProfile) -> int:
    """返回净值曲线实际采样频率的年化周期数。

    分钟订单回放虽读取分钟 bar，但对外输出的是日终权益曲线，风险统计必须按 252 个
    交易日年化，不能误用分钟 bar 的 98,280 周期。
    """

    if profile.backtest.engine in {"futures_daily", "futures_intraday_replay"}:
        return 252
    return periods_per_year_for_frequency(profile.data_frequency)


def _build_evaluation_view(
    result: BacktestResult,
    *,
    targets: pl.DataFrame,
    time_column: str,
    execution_delay_sessions: int,
) -> dict[str, object]:
    """剔除信号热身期，并以首次可执行非零目标前的权益作为评估基线。

    策略的 lookback 期间没有可交易信号，若把这段零暴露直接混进年化样本，会让短样本
    的收益、波动和比率看起来比实际更稳定。原始完整曲线仍保留在 ``BacktestResult``
    和报告的 ``full_equity_curve`` 中，供审计追溯。
    """

    full_curve = result.equity_curve
    points = _normalized_equity_points(full_curve)
    target_start = _first_nonzero_target_date(targets, time_column=time_column)
    metadata: dict[str, object] = {
        "source_equity_observation_count": len(points),
        "first_nonzero_target_date": target_start,
        "execution_delay_sessions": execution_delay_sessions,
    }
    if target_start is None:
        normalized_curve = [dict(row) for row in full_curve]
        metadata.update(
            {
                "status": "no_active_target",
                "evaluation_start": str(normalized_curve[0]["date"])[:10],
                "warmup_excluded_observation_count": 0,
                "evaluation_observation_count": len(normalized_curve),
                "note": "回测期间没有非零目标权重；绩效仅表示空仓基线。",
            }
        )
        return _evaluation_payload(result, normalized_curve, metadata)

    decision_index = next(
        (
            index
            for index, (point_date, _) in enumerate(points)
            if point_date >= target_start
        ),
        None,
    )
    if decision_index is None:
        raise ValueError("首个非零目标不在回测净值曲线范围内")
    evaluation_index = decision_index + execution_delay_sessions
    if evaluation_index >= len(points):
        normalized_curve = [dict(row) for row in full_curve]
        metadata.update(
            {
                "status": "no_executable_target",
                "evaluation_start": str(normalized_curve[0]["date"])[:10],
                "warmup_excluded_observation_count": 0,
                "evaluation_observation_count": len(normalized_curve),
                "note": "非零目标出现得过晚，样本中没有后续可执行交易日。",
            }
        )
        return _evaluation_payload(result, normalized_curve, metadata)

    baseline_equity = points[evaluation_index - 1][1] if evaluation_index > 0 else 1.0
    normalized_curve = []
    for row in full_curve[evaluation_index:]:
        normalized_row = dict(row)
        normalized_row["equity"] = _as_finite_float(
            row.get("equity"),
            default=math.nan,
        ) / baseline_equity
        normalized_curve.append(normalized_row)
    metadata.update(
        {
            "status": "active_target_evaluation",
            "evaluation_start": str(normalized_curve[0]["date"])[:10],
            "baseline_date": points[evaluation_index - 1][0]
            if evaluation_index > 0
            else None,
            "warmup_excluded_observation_count": evaluation_index,
            "evaluation_observation_count": len(normalized_curve),
        }
    )
    return _evaluation_payload(result, normalized_curve, metadata)


def _evaluation_payload(
    result: BacktestResult,
    equity_curve: Sequence[Mapping[str, object]],
    metadata: dict[str, object],
) -> dict[str, object]:
    """从评估权益切片重建回撤、月度收益和同期换手曲线。"""

    normalized_equity_curve = [dict(row) for row in equity_curve]
    points = _normalized_equity_points(normalized_equity_curve)
    returns = _returns_from_normalized_equities([equity for _, equity in points])
    peak = 1.0
    drawdown_curve: list[dict[str, float | str]] = []
    for (point_date, equity) in points:
        peak = max(peak, equity)
        drawdown_curve.append({"date": point_date, "drawdown": equity / peak - 1.0})
    monthly_products: dict[str, float] = {}
    for (point_date, _), value in zip(points, returns, strict=True):
        month = point_date[:7]
        monthly_products[month] = monthly_products.get(month, 1.0) * (1.0 + value)
    monthly_returns = [
        {"month": month, "return": product - 1.0}
        for month, product in sorted(monthly_products.items())
    ]
    start_date = points[0][0]
    turnover_curve = [
        dict(row)
        for row in result.turnover_curve
        if str(row.get("date") or "")[:10] >= start_date
    ]
    return {
        "equity_curve": normalized_equity_curve,
        "drawdown_curve": drawdown_curve,
        "monthly_returns": monthly_returns,
        "turnover_curve": turnover_curve,
        "metadata": metadata,
    }


def _first_nonzero_target_date(
    targets: pl.DataFrame,
    *,
    time_column: str,
) -> str | None:
    required = {time_column, "target_weight"}
    if not required.issubset(targets.columns):
        raise ValueError("策略目标缺少评估期所需的时间列或 target_weight")
    active = targets.filter(pl.col("target_weight").abs() > _EPSILON)
    if active.is_empty():
        return None
    value = active.select(pl.col(time_column).min()).item()
    return str(value)[:10]


def _report_equity_curve(payload: dict[str, object]) -> list[dict[str, object]]:
    curve = payload.get("equity_curve")
    if not isinstance(curve, list):
        raise ValueError("回测运行缺少评估净值曲线")
    rows = [row for row in curve if isinstance(row, dict)]
    if len(rows) != len(curve):
        raise ValueError("评估净值曲线包含无效记录")
    return rows


def _calculate_equity_performance(
    equity_curve: Sequence[Mapping[str, object]],
    *,
    periods_per_year: int,
) -> dict[str, object]:
    """从日终归一化权益计算收益、风险与回撤指标。

    首期收益以初始权益 1.0 为基准，避免首日亏损被忽略。无风险利率固定为 0，并写入
    输出，防止把 Sharpe/Sortino 的假设藏在实现细节里。
    """

    if periods_per_year <= 0:
        raise ValueError("年化周期数必须大于 0")
    points = _normalized_equity_points(equity_curve)
    if not points:
        raise ValueError("净值曲线不能为空")
    equities = [equity for _, equity in points]
    returns: list[float] = []
    previous = 1.0
    peak = 1.0
    drawdowns: list[float] = []
    for equity in equities:
        returns.append(equity / previous - 1.0)
        previous = equity
        peak = max(peak, equity)
        drawdowns.append(equity / peak - 1.0)

    total_return = equities[-1] - 1.0
    sample_is_sufficient = len(returns) >= _MIN_STATISTICAL_OBSERVATIONS
    annualized_return = (
        (
            -1.0
            if equities[-1] <= 0
            else equities[-1] ** (periods_per_year / len(equities)) - 1.0
        )
        if sample_is_sufficient
        else None
    )
    volatility = _sample_standard_deviation(returns)
    annualized_volatility = (
        volatility * math.sqrt(periods_per_year)
        if sample_is_sufficient and volatility is not None
        else None
    )
    average_return = sum(returns) / len(returns)
    sharpe_ratio = (
        average_return / volatility * math.sqrt(periods_per_year)
        if sample_is_sufficient and volatility is not None and volatility > _EPSILON
        else None
    )
    downside_deviation = math.sqrt(
        sum(min(value, 0.0) ** 2 for value in returns) / len(returns)
    )
    sortino_ratio = (
        average_return / downside_deviation * math.sqrt(periods_per_year)
        if sample_is_sufficient and downside_deviation > _EPSILON
        else None
    )
    max_drawdown = min(drawdowns, default=0.0)
    calmar_ratio = (
        annualized_return / abs(max_drawdown)
        if annualized_return is not None and max_drawdown < -_EPSILON
        else None
    )
    return {
        "return_observation_count": len(returns),
        "minimum_statistical_observation_count": _MIN_STATISTICAL_OBSERVATIONS,
        "sample_is_sufficient": sample_is_sufficient,
        "sample_status": (
            "统计样本充足"
            if sample_is_sufficient
            else f"样本不足（至少需要 {_MIN_STATISTICAL_OBSERVATIONS} 个执行后权益观测）"
        ),
        "periods_per_year": periods_per_year,
        "annual_risk_free_rate": 0.0,
        "total_return": total_return,
        "annualized_return": annualized_return,
        "annualized_volatility": annualized_volatility,
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": sortino_ratio,
        "max_drawdown": max_drawdown,
        "calmar_ratio": calmar_ratio,
        "positive_return_ratio": sum(value > 0.0 for value in returns) / len(returns),
    }


def _build_benchmark_analytics(
    signal_market_df: pl.DataFrame,
    *,
    benchmark_symbol: str,
    equity_curve: Sequence[Mapping[str, object]],
    periods_per_year: int,
    sample_is_sufficient: bool = True,
) -> dict[str, object]:
    """以同一信号价格口径构造基准曲线；缺失时明确标注不可计算。"""

    required = {"date", "symbol", "close"}
    if not required.issubset(signal_market_df.columns):
        return {
            "status": "unavailable",
            "symbol": benchmark_symbol,
            "reason": "信号行情缺少 date/symbol/close，无法计算基准",
        }
    benchmark_rows = signal_market_df.filter(pl.col("symbol") == benchmark_symbol)
    if benchmark_rows.is_empty():
        return {
            "status": "unavailable",
            "symbol": benchmark_symbol,
            "reason": "数据集不含画像指定的基准标的",
        }
    duplicate_dates = benchmark_rows.group_by("date").len().filter(pl.col("len") != 1)
    if not duplicate_dates.is_empty():
        return {
            "status": "unavailable",
            "symbol": benchmark_symbol,
            "reason": "基准价格在同一日期存在重复记录",
        }

    prices = {
        str(row["date"])[:10]: float(row["close"])
        for row in benchmark_rows.select("date", "close").to_dicts()
        if _is_finite_positive(row["close"])
    }
    points = _normalized_equity_points(equity_curve)
    missing_dates = [point_date for point_date, _ in points if point_date not in prices]
    if missing_dates:
        return {
            "status": "unavailable",
            "symbol": benchmark_symbol,
            "reason": "回测净值日期缺少基准价格：" + ", ".join(missing_dates[:3]),
        }
    base_price = prices[points[0][0]]
    if not _is_finite_positive(base_price):
        return {
            "status": "unavailable",
            "symbol": benchmark_symbol,
            "reason": "基准起始价格无效",
        }

    benchmark_equities = [prices[point_date] / base_price for point_date, _ in points]
    strategy_equities = [equity for _, equity in points]
    strategy_returns = _returns_from_normalized_equities(strategy_equities)
    benchmark_returns = _returns_from_normalized_equities(benchmark_equities)
    active_returns = [
        strategy_return - benchmark_return
        for strategy_return, benchmark_return in zip(
            strategy_returns,
            benchmark_returns,
            strict=True,
        )
    ]
    tracking_error = _sample_standard_deviation(active_returns)
    information_ratio = (
        (sum(active_returns) / len(active_returns))
        / tracking_error
        * math.sqrt(periods_per_year)
        if sample_is_sufficient
        and tracking_error is not None
        and tracking_error > _EPSILON
        else None
    )
    benchmark_total_return = benchmark_equities[-1] - 1.0
    benchmark_annualized_return = (
        -1.0
        if benchmark_equities[-1] <= 0
        else benchmark_equities[-1] ** (periods_per_year / len(benchmark_equities)) - 1.0
    ) if sample_is_sufficient else None
    return {
        "status": "available",
        "symbol": benchmark_symbol,
        "total_return": benchmark_total_return,
        "annualized_return": benchmark_annualized_return,
        "excess_total_return": strategy_equities[-1] - benchmark_equities[-1],
        "tracking_error": (
            tracking_error * math.sqrt(periods_per_year)
            if sample_is_sufficient and tracking_error is not None
            else None
        ),
        "information_ratio": information_ratio,
        "sample_is_sufficient": sample_is_sufficient,
        "equity_curve": [
            {"date": point_date, "equity": equity}
            for (point_date, _), equity in zip(points, benchmark_equities, strict=True)
        ],
    }


def _build_execution_analytics(
    result: BacktestResult,
    *,
    backtester_id: str,
) -> dict[str, object]:
    """按回测器能力汇总成交与约束事件，避免把 fill 伪称为闭合交易。"""

    if backtester_id == "continuous_futures_research_backtest":
        return {
            "detail_level": "not_modeled",
            "message": "连续收益研究引擎未模拟逐笔订单、成交、保证金或成交约束。",
        }

    filled_quantity = _sum_numeric_field(result.trades, "qty")
    commission_total = _sum_numeric_field(result.trades, "commission")
    notional_total = _sum_numeric_field(result.trades, "notional")
    reason_counts = dict(sorted(Counter(str(row.get("reason") or "unknown") for row in result.trades).items()))
    payload: dict[str, object] = {
        "detail_level": (
            "orders_and_fills"
            if backtester_id == "actual_futures_intraday_replay_backtest"
            else "fills_and_target_events"
        ),
        "fill_event_count": len(result.trades),
        "filled_quantity": filled_quantity,
        "commission_total": commission_total,
        "traded_notional_total": notional_total,
        "fill_reason_counts": reason_counts,
    }
    margin_ratios = _finite_curve_values(result.equity_curve, "margin_ratio")
    available_funds_ratios = _finite_curve_values(
        result.equity_curve,
        "available_funds_ratio",
    )
    if margin_ratios:
        payload["max_margin_ratio"] = max(margin_ratios)
    if available_funds_ratios:
        payload["min_available_funds_ratio"] = min(available_funds_ratios)
    if backtester_id == "actual_futures_intraday_replay_backtest":
        status_counts = Counter(str(row.get("status") or "unknown") for row in result.orders)
        payload.update(
            {
                "order_count": len(result.orders),
                "order_status_counts": dict(sorted(status_counts.items())),
                "partial_fill_order_count": sum(
                    0 < _as_finite_float(row.get("filled_qty"), default=0.0)
                    < _as_finite_float(row.get("requested_qty"), default=0.0)
                    for row in result.orders
                ),
                "rejected_order_count": sum(
                    count for status, count in status_counts.items() if status == "REJECTED"
                ),
            }
        )
    else:
        payload["target_constraint_event_count"] = len(result.rejected_orders)
        payload["target_constraint_events"] = list(result.rejected_orders)
    return payload


def _build_report_metrics(
    result: BacktestResult,
    *,
    performance: dict[str, object],
    benchmark: dict[str, object],
    execution: dict[str, object],
    backtester_id: str,
    evaluation: object,
) -> dict[str, object]:
    """构造报告展示指标，并在键名中保留口径与不可用状态。"""

    metrics: dict[str, object] = {
        "回测器": backtester_id,
        "总收益率": performance["total_return"],
        "年化收益率": performance["annualized_return"],
        "年化波动率": performance["annualized_volatility"],
        "夏普比率（无风险利率=0）": performance["sharpe_ratio"],
        "索提诺比率（无风险利率=0）": performance["sortino_ratio"],
        "最大回撤": performance["max_drawdown"],
        "卡玛比率": performance["calmar_ratio"],
        "正收益期占比": performance["positive_return_ratio"],
        "权益收益观测数": performance["return_observation_count"],
        "统计样本状态": performance["sample_status"],
        "最小统计观测数": performance["minimum_statistical_observation_count"],
        "年化周期数": performance["periods_per_year"],
        "平均换手（引擎口径）": result.turnover_estimate,
    }
    if isinstance(evaluation, dict):
        metrics["评估起始日"] = evaluation.get("evaluation_start")
        metrics["热身排除权益观测数"] = evaluation.get(
            "warmup_excluded_observation_count"
        )
    if benchmark.get("status") == "available":
        metrics.update(
            {
                "基准总收益率": benchmark.get("total_return"),
                "相对基准总超额收益": benchmark.get("excess_total_return"),
                "跟踪误差": benchmark.get("tracking_error"),
                "信息比率": benchmark.get("information_ratio"),
            }
        )
    else:
        metrics["基准比较"] = "N/A（" + str(benchmark.get("reason") or "不可计算") + "）"

    detail_level = str(execution.get("detail_level") or "not_modeled")
    if detail_level == "not_modeled":
        metrics["成交与订单模拟"] = "未建模（连续收益研究）"
    else:
        metrics["成交事件数"] = execution.get("fill_event_count")
        metrics["累计成交数量"] = execution.get("filled_quantity")
        metrics["累计手续费"] = execution.get("commission_total")
        metrics["累计成交名义金额"] = execution.get("traded_notional_total")
        if detail_level == "orders_and_fills":
            metrics["订单事件数"] = execution.get("order_count")
            metrics["拒绝订单数"] = execution.get("rejected_order_count")
        else:
            metrics["目标约束/未成交事件数"] = execution.get(
                "target_constraint_event_count"
            )
        if "max_margin_ratio" in execution:
            metrics["最大保证金/权益"] = execution.get("max_margin_ratio")
        if "min_available_funds_ratio" in execution:
            metrics["最低可用资金/权益"] = execution.get(
                "min_available_funds_ratio"
            )
    return metrics


def _build_run_manifest(
    *,
    profile: TradingProfile,
    source_manifest: dict[str, object],
    selected_strategy_ids: tuple[str, ...],
    pipeline: StrategyOutputBundle,
    result: BacktestResult,
    backtester_id: str,
    periods_per_year: int,
    analytics: dict[str, object],
    evaluation: object,
) -> dict[str, object]:
    """建立输入指纹和输出校验和，不依赖报告写入时的当前时间。"""

    config_by_id = {item.strategy_id: item for item in profile.enabled_strategies}
    selected_configs = [asdict(config_by_id[strategy_id]) for strategy_id in selected_strategy_ids]
    code = _source_control_metadata()
    profile_fingerprint = profile_config_sha256(profile)
    strategy = {
        "selected_strategy_ids": list(selected_strategy_ids),
        "selected_configs": selected_configs,
        "output_type": pipeline.output_type.value,
        "time_column": pipeline.time_column,
        "target_frame_sha256": _frame_sha256(pipeline.frame),
        "target_row_count": pipeline.frame.height,
    }
    engine = {
        "backtester_id": backtester_id,
        "profile_engine": profile.backtest.engine,
        "return_frequency": "D1_EOD",
        "periods_per_year": periods_per_year,
        "evaluation": evaluation,
    }
    effective_configuration = {
        "execution": asdict(profile.execution),
        "backtest": asdict(profile.backtest),
        "risk_limits": asdict(build_profile_risk_limits(profile)),
        "versions": asdict(profile.versions),
        "data_price_field": profile.data.price_field,
        "data_adjusted": profile.data.adjusted,
        "calendar": profile.calendar,
    }
    fingerprint_inputs = {
        "schema_version": BACKTEST_MANIFEST_SCHEMA_VERSION,
        "code": code,
        "profile": {
            "profile_id": profile.profile_id,
            "profile_config_sha256": profile_fingerprint,
            "dimension_key": profile.dimension_key,
        },
        "data": source_manifest,
        "strategy": strategy,
        "engine": engine,
        "effective_configuration": effective_configuration,
    }
    fingerprint = _json_sha256(fingerprint_inputs)
    result_payload = asdict(result)
    output_checksums = {
        "result_sha256": _json_sha256(result_payload),
        "analytics_sha256": _json_sha256(analytics),
    }
    return {
        "schema_version": BACKTEST_MANIFEST_SCHEMA_VERSION,
        "run_id": f"bt-{fingerprint[:16]}",
        "run_fingerprint": f"sha256:{fingerprint}",
        "code": code,
        "profile": {
            "profile_id": profile.profile_id,
            "profile_config_sha256": profile_fingerprint,
            "dimensions": asdict(profile.dimensions),
            "versions": asdict(profile.versions),
        },
        "data": source_manifest,
        "strategy": strategy,
        "engine": engine,
        "effective_configuration": effective_configuration,
        "output_checksums": output_checksums,
        "reproducibility_note": _reproducibility_note(code),
    }


def _normalized_equity_points(
    equity_curve: Sequence[Mapping[str, object]],
) -> list[tuple[str, float]]:
    points: list[tuple[str, float]] = []
    for row in equity_curve:
        point_date = str(row.get("date") or "")[:10]
        equity = _as_finite_float(row.get("equity"), default=math.nan)
        if not point_date or not math.isfinite(equity) or equity <= 0:
            raise ValueError("净值曲线包含空日期、非有限值或非正权益")
        points.append((point_date, equity))
    points.sort(key=lambda item: item[0])
    dates = [point_date for point_date, _ in points]
    if len(set(dates)) != len(dates):
        raise ValueError("净值曲线包含重复日期，无法计算可靠绩效")
    return points


def _returns_from_normalized_equities(equities: list[float]) -> list[float]:
    if not equities:
        return []
    returns: list[float] = []
    previous = 1.0
    for equity in equities:
        returns.append(equity / previous - 1.0)
        previous = equity
    return returns


def _sample_standard_deviation(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _sum_numeric_field(rows: list[dict[str, object]], field_name: str) -> float | None:
    values = [
        _as_finite_float(row.get(field_name), default=math.nan)
        for row in rows
        if field_name in row
    ]
    finite_values = [value for value in values if math.isfinite(value)]
    return sum(finite_values) if finite_values else None


def _finite_curve_values(
    curve: Sequence[Mapping[str, object]],
    field_name: str,
) -> list[float]:
    values: list[float] = []
    for row in curve:
        if field_name not in row:
            continue
        value = _as_finite_float(row[field_name], default=math.nan)
        if math.isfinite(value):
            values.append(value)
    return values


def _as_finite_float(value: object, *, default: float) -> float:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return numeric if math.isfinite(numeric) else default


def _is_finite_positive(value: object) -> bool:
    return _as_finite_float(value, default=math.nan) > 0.0


def _frame_sha256(frame: pl.DataFrame) -> str:
    serialized_rows = sorted(
        json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            default=_canonical_json_default,
            allow_nan=False,
        )
        for row in frame.to_dicts()
    )
    return hashlib.sha256("\n".join(serialized_rows).encode("utf-8")).hexdigest()


def _json_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_canonical_json_default,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_json_default(value: object) -> str:
    """仅允许归档模型中明确支持的非 JSON 标量。"""

    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"回测归档包含不支持的类型：{type(value).__name__}")


def _source_control_metadata() -> dict[str, object]:
    """读取当前代码版本；不可读取 Git 时不阻断离线研究。"""

    project_root = Path(__file__).resolve().parents[3]
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        diff = subprocess.run(
            ["git", "diff", "HEAD", "--binary", "--no-ext-diff"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=False,
            timeout=5,
        ).stdout
        return {
            "package_version": __version__,
            "git_commit": commit or None,
            "git_dirty": bool(status.strip()),
            "tracked_diff_sha256": hashlib.sha256(diff).hexdigest(),
        }
    except (OSError, subprocess.SubprocessError):
        return {
            "package_version": __version__,
            "git_commit": None,
            "git_dirty": None,
            "tracked_diff_sha256": None,
        }


def _reproducibility_note(code: dict[str, object]) -> str:
    if code.get("git_dirty") is True:
        return "运行时工作树含未提交变更；已记录差异哈希，正式比较前应固定到提交版本。"
    if code.get("git_commit"):
        return "数据、有效配置、策略目标和代码提交均已写入本清单。"
    return "无法读取 Git 提交信息；请在受版本控制的环境中复跑以获得完整代码溯源。"

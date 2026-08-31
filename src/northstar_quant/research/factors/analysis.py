"""因子暴露的到期后统计分析。

forward return 只在一个决策已经到期后进入本模块。这里绝不生成组合提案，也绝不向因子
引擎返回参数，因此分析结果无法反哺同一期的历史目标。
"""

from __future__ import annotations

from collections import defaultdict
import math

from northstar_quant.research.factors.models import (
    FactorAnalysisPeriod,
    FactorAnalysisResult,
    FactorExposure,
    FactorForwardOutcome,
    FactorResearchError,
)


def analyze_factor(
    *,
    factor_id: str,
    exposures: tuple[FactorExposure, ...],
    outcomes: tuple[FactorForwardOutcome, ...],
    quantile_count: int,
    min_cross_section: int,
) -> FactorAnalysisResult:
    """计算 Pearson IC、Spearman Rank-IC、分位收益和 top/bottom turnover。"""

    if not isinstance(factor_id, str) or not factor_id:
        raise FactorResearchError("factor_id 必须是非空文本")
    if isinstance(quantile_count, bool) or not isinstance(quantile_count, int) or quantile_count < 2:
        raise FactorResearchError("quantile_count 必须是不小于 2 的整数")
    if isinstance(min_cross_section, bool) or not isinstance(min_cross_section, int):
        raise FactorResearchError("min_cross_section 必须是整数")
    if min_cross_section < quantile_count:
        raise FactorResearchError("min_cross_section 必须不小于 quantile_count")
    selected_exposures = tuple(item for item in exposures if item.factor_id == factor_id)
    if not selected_exposures:
        raise FactorResearchError("没有该 factor 的 exposure")
    if len({item.factor_definition_hash for item in selected_exposures}) != 1:
        raise FactorResearchError("factor analysis 不能混合不同的 FactorDefinition")
    if len({item.config_hash for item in selected_exposures}) != 1:
        raise FactorResearchError("factor analysis 不能混合不同的 FactorPipelineConfig")
    by_checkpoint: dict[str, dict[str, FactorExposure]] = defaultdict(dict)
    for exposure in selected_exposures:
        existing_exposure = by_checkpoint[exposure.checkpoint_hash].get(exposure.symbol)
        if existing_exposure is not None:
            raise FactorResearchError("factor exposure 不能重复 checkpoint/symbol")
        by_checkpoint[exposure.checkpoint_hash][exposure.symbol] = exposure
    outcome_by_checkpoint: dict[str, dict[str, FactorForwardOutcome]] = defaultdict(dict)
    for outcome in outcomes:
        existing_outcome = outcome_by_checkpoint[outcome.origin_checkpoint_hash].get(outcome.symbol)
        if existing_outcome is not None:
            raise FactorResearchError("forward outcome 不能重复 origin checkpoint/symbol")
        outcome_by_checkpoint[outcome.origin_checkpoint_hash][outcome.symbol] = outcome

    raw_periods: list[tuple[FactorAnalysisPeriod, dict[str, float]]] = []
    for checkpoint_hash, exposure_by_symbol in by_checkpoint.items():
        outcome_by_symbol = outcome_by_checkpoint.get(checkpoint_hash)
        if outcome_by_symbol is None:
            # 最后一个尚未到期的决策没有 outcome，是允许的；它不会进入 ex-post 分析。
            continue
        common = tuple(sorted(set(exposure_by_symbol).intersection(outcome_by_symbol)))
        if len(common) < min_cross_section:
            continue
        if set(common) != set(exposure_by_symbol) or set(common) != set(outcome_by_symbol):
            raise FactorResearchError("到期因子分析不得静默丢弃单个 symbol")
        decision_sessions = {exposure_by_symbol[symbol].decision_session for symbol in common}
        if len(decision_sessions) != 1:
            raise FactorResearchError("同一 checkpoint 的 exposure 决策日必须一致")
        x = {symbol: exposure_by_symbol[symbol].value for symbol in common}
        y = {symbol: outcome_by_symbol[symbol].forward_return for symbol in common}
        ic = _pearson(tuple(x.values()), tuple(y.values()))
        rank_ic = _pearson(
            tuple(_average_ranks(x)[symbol] for symbol in common),
            tuple(_average_ranks(y)[symbol] for symbol in common),
        )
        if ic is None or rank_ic is None:
            # 常量截面没有有效相关性；不伪造 0 IC，直接不形成统计 period。
            continue
        buckets = _quantile_buckets(x, quantile_count)
        quantile_returns: list[tuple[int, float]] = []
        for bucket in range(1, quantile_count + 1):
            members = tuple(symbol for symbol in common if buckets[symbol] == bucket)
            if not members:
                break
            quantile_returns.append((bucket, sum(y[symbol] for symbol in members) / len(members)))
        if len(quantile_returns) != quantile_count:
            # 并列导致空分位时，这个截面没有明确的分组经济含义。
            continue
        raw_periods.append(
            (
                FactorAnalysisPeriod(
                    decision_session=next(iter(decision_sessions)),
                    ic=ic,
                    rank_ic=rank_ic,
                    quantile_returns=tuple(quantile_returns),
                ),
                _long_short_membership(buckets, quantile_count),
            )
        )
    if not raw_periods:
        raise FactorResearchError("没有足够的已到期、有效横截面可用于 factor analysis")
    raw_periods.sort(key=lambda item: item[0].decision_session)
    if len({item[0].decision_session for item in raw_periods}) != len(raw_periods):
        raise FactorResearchError("factor analysis 不支持同一 decision session 的多个 checkpoint")
    turnovers = [
        _turnover(previous, current)
        for (_, previous), (_, current) in zip(raw_periods, raw_periods[1:])
    ]
    return FactorAnalysisResult(
        factor_id=factor_id,
        quantile_count=quantile_count,
        periods=tuple(item[0] for item in raw_periods),
        mean_turnover=sum(turnovers) / len(turnovers) if turnovers else 0.0,
    )


def _pearson(left: tuple[float, ...], right: tuple[float, ...]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        raise FactorResearchError("Pearson IC 需要至少两个配对观测")
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    left_delta = tuple(value - left_mean for value in left)
    right_delta = tuple(value - right_mean for value in right)
    denominator = math.sqrt(sum(value * value for value in left_delta) * sum(value * value for value in right_delta))
    if denominator <= 1e-12:
        return None
    result = sum(a * b for a, b in zip(left_delta, right_delta, strict=True)) / denominator
    return max(-1.0, min(1.0, result))


def _average_ranks(values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
    result: dict[str, float] = {}
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        average = (start + 1 + end) / 2.0
        for symbol, _ in ordered[start:end]:
            result[symbol] = average
        start = end
    return result


def _quantile_buckets(values: dict[str, float], quantile_count: int) -> dict[str, int]:
    size = len(values)
    ranks = _average_ranks(values)
    return {
        symbol: min(quantile_count, max(1, math.ceil(rank * quantile_count / size)))
        for symbol, rank in ranks.items()
    }


def _long_short_membership(buckets: dict[str, int], quantile_count: int) -> dict[str, float]:
    lower = tuple(symbol for symbol, bucket in buckets.items() if bucket == 1)
    upper = tuple(symbol for symbol, bucket in buckets.items() if bucket == quantile_count)
    if not lower or not upper:
        raise FactorResearchError("factor quantile membership 不完整")
    return {
        **{symbol: -1.0 / len(lower) for symbol in lower},
        **{symbol: 1.0 / len(upper) for symbol in upper},
    }


def _turnover(previous: dict[str, float], current: dict[str, float]) -> float:
    symbols = set(previous).union(current)
    return 0.5 * sum(abs(current.get(symbol, 0.0) - previous.get(symbol, 0.0)) for symbol in symbols)


__all__ = ["analyze_factor"]

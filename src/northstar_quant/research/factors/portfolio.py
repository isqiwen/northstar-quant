"""Research-only 横截面因子组合构造。

这里实现的是显式的对角波动率风险预算：没有协方差矩阵时，绝不把它称为完整
risk-parity。输出仅为 :class:`FactorPortfolioProposal`，不会进入 portfolio approval 或执行。
"""

from __future__ import annotations

import math

from northstar_quant.research.factors.models import (
    FactorCheckpointData,
    FactorPipelineConfig,
    FactorPortfolioProposal,
    FactorPortfolioWeight,
    FactorResearchError,
    ProposalStatus,
)


def build_factor_portfolio_proposal(
    *,
    config: FactorPipelineConfig,
    checkpoint_data: FactorCheckpointData,
) -> FactorPortfolioProposal:
    """在单一 checkpoint 的同期严格暴露上构造风险受限的连续序列权重。"""

    if not isinstance(config, FactorPipelineConfig):
        raise FactorResearchError("config 必须是 FactorPipelineConfig")
    if not isinstance(checkpoint_data, FactorCheckpointData):
        raise FactorResearchError("checkpoint_data 必须是 FactorCheckpointData")
    if checkpoint_data.config_hash != config.config_hash:
        raise FactorResearchError("checkpoint_data.config_hash 与 pipeline config 不一致")
    expected_factor_ids = {item.factor_id for item in config.factors}
    materialized_factor_ids = {item.factor_id for item in checkpoint_data.materializations}
    if expected_factor_ids != materialized_factor_ids:
        raise FactorResearchError("checkpoint factor materializations 与 pipeline config 不一致")

    market_symbols = {item.symbol for item in checkpoint_data.market_slices}
    exposures: dict[str, dict[str, float]] = {factor_id: {} for factor_id in expected_factor_ids}
    for exposure in checkpoint_data.exposures:
        if exposure.factor_id not in exposures:
            raise FactorResearchError("checkpoint exposure 包含未配置 factor")
        if exposure.symbol in exposures[exposure.factor_id]:
            raise FactorResearchError("checkpoint exposure 不能重复 factor/symbol")
        exposures[exposure.factor_id][exposure.symbol] = exposure.value

    # 任一同期期货序列没有所有 alpha / vol 输入时直接保持 no-proposal；绝不悄悄缩小品种池。
    all_factor_symbols = set.intersection(*(set(values) for values in exposures.values()))
    valid_symbols = all_factor_symbols.intersection(market_symbols)
    volatility_values = exposures[config.volatility_factor_id]
    if valid_symbols != market_symbols or any(volatility_values[symbol] <= 0 for symbol in valid_symbols):
        return _no_proposal(config, checkpoint_data, "factor_or_volatility_input_incomplete")
    if len(valid_symbols) < config.min_cross_section:
        return _no_proposal(config, checkpoint_data, "insufficient_cross_section")

    symbols = tuple(sorted(valid_symbols))
    aggregate_weights = {symbol: 0.0 for symbol in symbols}
    composite_scores = {symbol: 0.0 for symbol in symbols}
    for definition in config.alpha_factors:
        ranked = _z_scores({symbol: exposures[definition.factor_id][symbol] for symbol in symbols})
        directional = {symbol: definition.direction * ranked[symbol] for symbol in symbols}
        inverse_vol = {
            symbol: directional[symbol] / volatility_values[symbol]
            for symbol in symbols
        }
        gross = sum(abs(value) for value in inverse_vol.values())
        if gross <= 1e-12:
            return _no_proposal(config, checkpoint_data, "zero_alpha_cross_section")
        for symbol in symbols:
            sleeve_weight = definition.risk_budget * inverse_vol[symbol] / gross
            aggregate_weights[symbol] += sleeve_weight
            composite_scores[symbol] += definition.risk_budget * directional[symbol]

    estimated_volatility = math.sqrt(
        sum((aggregate_weights[symbol] * volatility_values[symbol]) ** 2 for symbol in symbols)
    )
    if not math.isfinite(estimated_volatility) or estimated_volatility <= 1e-12:
        return _no_proposal(config, checkpoint_data, "zero_or_invalid_estimated_volatility")
    volatility_scale = min(1.0, config.target_volatility / estimated_volatility)
    constrained = {
        symbol: max(
            -config.max_abs_weight,
            min(config.max_abs_weight, aggregate_weights[symbol] * volatility_scale),
        )
        for symbol in symbols
    }
    gross_after_cap = sum(abs(value) for value in constrained.values())
    if gross_after_cap > config.max_gross_exposure:
        scaling = config.max_gross_exposure / gross_after_cap
        constrained = {symbol: value * scaling for symbol, value in constrained.items()}
    if any(abs(value) > config.max_abs_weight + 1e-12 for value in constrained.values()):
        raise FactorResearchError("portfolio cap 内部校验失败")
    if sum(abs(value) for value in constrained.values()) > config.max_gross_exposure + 1e-12:
        raise FactorResearchError("portfolio gross limit 内部校验失败")

    weights = tuple(
        FactorPortfolioWeight(
            symbol=symbol,
            composite_score=composite_scores[symbol],
            target_weight=constrained[symbol],
        )
        for symbol in symbols
    )
    return FactorPortfolioProposal(
        checkpoint_hash=checkpoint_data.checkpoint_hash,
        decision_at=checkpoint_data.decision_at,
        decision_session=checkpoint_data.decision_session,
        snapshot_id=checkpoint_data.snapshot_id,
        checkpoint_data_hash=checkpoint_data.checkpoint_data_hash,
        config_hash=config.config_hash,
        status=ProposalStatus.PROPOSAL,
        weights=weights,
        estimated_volatility=estimated_volatility,
        volatility_scale=volatility_scale,
    )


def _no_proposal(
    config: FactorPipelineConfig,
    checkpoint_data: FactorCheckpointData,
    reason: str,
) -> FactorPortfolioProposal:
    """显式 no-target 状态，避免用旧值、前填或猜测风险参数继续研究。"""

    return FactorPortfolioProposal(
        checkpoint_hash=checkpoint_data.checkpoint_hash,
        decision_at=checkpoint_data.decision_at,
        decision_session=checkpoint_data.decision_session,
        snapshot_id=checkpoint_data.snapshot_id,
        checkpoint_data_hash=checkpoint_data.checkpoint_data_hash,
        config_hash=config.config_hash,
        status=ProposalStatus.NO_PROPOSAL_WARMUP,
        weights=(),
        estimated_volatility=None,
        volatility_scale=None,
        no_proposal_reason=reason,
    )


def _z_scores(values: dict[str, float]) -> dict[str, float]:
    """以平均秩稳定处理并列，再标准化到均值 0、样本方差 1。"""

    if len(values) < 2:
        raise FactorResearchError("横截面至少需要两个 factor value")
    ranks = _average_ranks(values)
    mean_rank = sum(ranks.values()) / len(ranks)
    variance = sum((rank - mean_rank) ** 2 for rank in ranks.values()) / (len(ranks) - 1)
    if variance <= 1e-12:
        raise FactorResearchError("横截面 factor value 没有可用于排序的方差")
    standard_deviation = math.sqrt(variance)
    return {symbol: (ranks[symbol] - mean_rank) / standard_deviation for symbol in values}


def _average_ranks(values: dict[str, float]) -> dict[str, float]:
    """升序平均秩；同值按 symbol 排序但赋予同一个平均 rank。"""

    ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
    ranks: dict[str, float] = {}
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        average_rank = (start + 1 + end) / 2.0
        for symbol, _ in ordered[start:end]:
            ranks[symbol] = average_rank
        start = end
    return ranks


__all__ = ["build_factor_portfolio_proposal"]

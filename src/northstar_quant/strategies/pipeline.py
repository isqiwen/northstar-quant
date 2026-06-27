"""Canonical profile strategy pipeline shared by research, backtest, and live."""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl

from northstar_quant.common.enums import StrategyOutputType
from northstar_quant.common.types import StrategyOutputBundle
from northstar_quant.config.trading_profile import TradingProfile
from northstar_quant.logging_.logger import get_logger
from northstar_quant.portfolio.multi_strategy import (
    build_target_weight_portfolio,
    build_target_weight_portfolio_history,
    combine_strategy_execution_intents,
)
from northstar_quant.risk.models import RiskLimits
from northstar_quant.strategies.base import StrategyBase
from northstar_quant.strategies.registry import build_strategy

logger = get_logger(__name__)


def build_profile_risk_limits(profile: TradingProfile) -> RiskLimits:
    """Build risk limits from profile risk overrides."""

    supported_fields = set(RiskLimits.__dataclass_fields__)
    risk_overrides = {
        key: value
        for key, value in profile.risk.items()
        if key in supported_fields
    }
    if (
        "min_order_notional" not in risk_overrides
        and profile.execution.rebalance_min_trade_value is not None
    ):
        risk_overrides["min_order_notional"] = profile.execution.rebalance_min_trade_value
    return RiskLimits(**risk_overrides)


def parse_strategy_selection(strategy_name: str | None) -> tuple[str, ...] | None:
    """Parse a CLI-style strategy selector.

    ``None`` / ``portfolio`` means "use all enabled strategies in the profile".
    Otherwise returns the requested strategy IDs.
    """

    if strategy_name is None:
        return None

    parts = tuple(
        item.strip()
        for item in str(strategy_name).split(",")
        if item.strip()
    )
    if not parts:
        return None
    if len(parts) == 1 and parts[0].lower() in {"portfolio", "profile", "all"}:
        return None
    return parts


def latest_pipeline_output(bundle: StrategyOutputBundle) -> pl.DataFrame:
    """Return the latest slice from a pipeline result."""

    if bundle.frame.is_empty() or bundle.time_column not in bundle.frame.columns:
        return bundle.frame
    latest_value = bundle.frame[bundle.time_column].max()
    return bundle.frame.filter(pl.col(bundle.time_column) == latest_value)


def resolve_selected_profile_strategy_ids(
    profile: TradingProfile,
    strategy_ids: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """Resolve selected strategy IDs within a profile."""

    enabled_ids = tuple(item.strategy_id for item in profile.enabled_strategies)
    if not enabled_ids:
        raise ValueError(f"交易画像 {profile.profile_id} 未配置任何启用中的策略。")
    if strategy_ids is None:
        return enabled_ids

    requested = tuple(str(item).strip() for item in strategy_ids if str(item).strip())
    missing = sorted(set(requested).difference(enabled_ids))
    if missing:
        raise ValueError(
            f"交易画像 {profile.profile_id} 未启用策略：{', '.join(missing)}。"
            f"当前启用策略：{', '.join(enabled_ids)}"
        )
    return requested


def build_selected_profile_strategies(
    profile: TradingProfile,
    strategy_ids: Sequence[str] | None = None,
) -> tuple[list[tuple[StrategyBase, float]], tuple[str, ...]]:
    """Build selected strategies from the profile.

    When a subset is requested, the subset capital weights are re-normalized so the
    selected strategies still represent a full portfolio by themselves.
    """

    selected_ids = resolve_selected_profile_strategy_ids(profile, strategy_ids)
    config_by_id = {
        item.strategy_id: item
        for item in profile.enabled_strategies
    }
    selected_configs = [config_by_id[strategy_id] for strategy_id in selected_ids]

    capital_weights = [float(item.capital_weight) for item in selected_configs]
    if strategy_ids is not None:
        total_weight = sum(capital_weights)
        if total_weight <= 0:
            raise ValueError(
                f"交易画像 {profile.profile_id} 选中策略的 capital_weight 总和必须大于 0。"
            )
        capital_weights = [weight / total_weight for weight in capital_weights]

    built = [
        (
            build_strategy(config.strategy_id, params=config.params),
            float(weight),
        )
        for config, weight in zip(selected_configs, capital_weights, strict=False)
    ]
    return built, selected_ids


def _ensure_constant_time_column(
    frame: pl.DataFrame,
    *,
    time_column: str,
    time_value: object | None,
) -> pl.DataFrame:
    if time_column in frame.columns or time_value is None:
        return frame
    return frame.with_columns(pl.lit(time_value).alias(time_column))


def run_profile_strategy_pipeline(
    market_df: pl.DataFrame,
    profile: TradingProfile,
    *,
    strategy_ids: Sequence[str] | None = None,
    latest_only: bool = False,
) -> StrategyOutputBundle:
    """Run the canonical strategy pipeline for a profile."""

    strategies, selected_ids = build_selected_profile_strategies(
        profile,
        strategy_ids=strategy_ids,
    )
    output_types = {strategy.output_type for strategy, _ in strategies}
    if len(output_types) != 1:
        output_text = ", ".join(sorted(item.value for item in output_types))
        raise ValueError(
            f"交易画像 {profile.profile_id} 同时包含多种策略输出类型：{output_text}。"
            "当前版本暂不支持同一画像混用 target_weight 和 execution_intent。"
        )

    output_type = next(iter(output_types))
    time_column = strategies[0][0].time_column
    strategy_frames: list[pl.DataFrame] = []
    weights: list[float] = []
    latest_time_value: object | None = None

    for strategy, capital_weight in strategies:
        output = strategy.build_output_bundle(market_df).frame
        current_output = strategy.latest_output(output) if latest_only else output
        if current_output.is_empty():
            continue
        if "strategy_id" not in current_output.columns:
            current_output = current_output.with_columns(
                pl.lit(strategy.strategy_id).alias("strategy_id")
            )
        if time_column in current_output.columns:
            candidate_time = current_output[time_column].max()
            if latest_time_value is None or candidate_time > latest_time_value:
                latest_time_value = candidate_time
        strategy_frames.append(current_output)
        weights.append(float(capital_weight))

    if output_type == StrategyOutputType.TARGET_WEIGHT:
        limits = build_profile_risk_limits(profile)
        if latest_only:
            combined = build_target_weight_portfolio(strategy_frames, weights, limits)
            combined = _ensure_constant_time_column(
                combined,
                time_column=time_column,
                time_value=latest_time_value,
            )
        else:
            combined = build_target_weight_portfolio_history(
                strategy_frames,
                weights,
                limits,
                time_column=time_column,
            )
    else:
        combined = combine_strategy_execution_intents(
            strategy_frames,
            weights,
            time_column=time_column,
        )

    logger.bind(
        command="strategy.pipeline",
        profile=profile.profile_id,
        output_type=output_type.value,
        strategy_ids=list(selected_ids),
        latest_only=latest_only,
    ).info("Canonical strategy pipeline executed, rows=%s", combined.height)

    return StrategyOutputBundle(
        strategy_id="portfolio" if strategy_ids is None else ",".join(selected_ids),
        output_type=output_type,
        time_column=time_column,
        frame=combined,
    )

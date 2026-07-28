"""研究、回测和实盘共用的策略输出管线。

管线只负责“标准行情 → 已注册策略 → 统一输出 → 组合与权重风控”；它不直接调用券商。
不同输出类型不能混合，避免把交易计划误当作权重或把执行意图用于收益回测。
"""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl

from northstar_quant.common.enums import StrategyOutputType
from northstar_quant.common.types import StrategyOutputBundle
from northstar_quant.config.settings import get_settings
from northstar_quant.config.trading_profile import TradingProfile
from northstar_quant.logging_.logger import get_logger
from northstar_quant.portfolio.multi_strategy import (
    build_target_weight_portfolio,
    build_target_weight_portfolio_history,
    combine_strategy_execution_intents,
)
from northstar_quant.risk.models import RiskLimits
from northstar_quant.strategies.base import StrategyBase
from northstar_quant.strategies.registry import build_profile_strategy

logger = get_logger(__name__)


def build_profile_risk_limits(profile: TradingProfile) -> RiskLimits:
    """由画像风险覆盖与执行数量约束构造统一 RiskLimits。

未知字段直接拒绝，避免 YAML 拼写错误使风险限制悄悄失效。画像显式风险优先；若未提供
最小订单金额或数量步长，则从 execution 段补入相应默认值。
    """

    supported_fields = set(RiskLimits.__dataclass_fields__)
    unknown_fields = sorted(set(profile.risk).difference(supported_fields))
    if unknown_fields:
        raise ValueError(
            f"交易画像 {profile.profile_id} 包含不支持的风控字段："
            f"{', '.join(unknown_fields)}"
        )
    risk_overrides = dict(profile.risk)
    boolean_fields = {
        "enforce_available_cash",
        "enforce_sellable_qty",
        "enforce_tradeable_state",
        "enforce_price_limit",
        "long_only",
    }
    for field_name in boolean_fields.intersection(risk_overrides):
        risk_overrides[field_name] = _strict_risk_bool(
            risk_overrides[field_name],
            field_name=field_name,
        )
    numeric_fields = {
        "max_single_weight",
        "max_gross_exposure",
        "min_cash_buffer",
        "min_order_notional",
        "max_order_notional",
        "max_order_qty",
        "order_qty_step",
        "buy_qty_step",
        "sell_qty_step",
    }
    for field_name in numeric_fields.intersection(risk_overrides):
        if risk_overrides[field_name] is not None:
            risk_overrides[field_name] = float(risk_overrides[field_name])
    if (
        "min_order_notional" not in risk_overrides
    ):
        risk_overrides["min_order_notional"] = (
            profile.execution.rebalance_min_trade_value
            if profile.execution.rebalance_min_trade_value is not None
            else get_settings().rebalance_min_trade_value
        )
    for risk_key in ("order_qty_step", "buy_qty_step", "sell_qty_step"):
        execution_value = getattr(profile.execution, risk_key)
        if risk_key not in risk_overrides and execution_value is not None:
            risk_overrides[risk_key] = execution_value
    risk_overrides["long_only"] = profile.execution.long_only
    limits = RiskLimits(**risk_overrides)
    if profile.is_production:
        required_flags = [
            "enforce_available_cash",
            "enforce_tradeable_state",
            "enforce_price_limit",
        ]
        if limits.long_only:
            required_flags.append("enforce_sellable_qty")
        missing_flags = [
            field_name
            for field_name in required_flags
            if not getattr(limits, field_name)
        ]
        if missing_flags:
            raise ValueError(
                f"production 画像 {profile.profile_id} 必须显式启用动态风控："
                + ", ".join(missing_flags)
            )
    return limits


def _strict_risk_bool(value: object, *, field_name: str) -> bool:
    """严格解析 risk 段布尔值，拒绝 Python 字符串真值语义。"""

    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError(f"风控字段 {field_name} 必须是明确的布尔值")


def enforce_profile_target_policy(
    frame: pl.DataFrame,
    profile: TradingProfile,
) -> pl.DataFrame:
    """执行画像级目标方向政策；违规时失败关闭，不自动改写策略意图。"""

    if frame.is_empty() or not profile.execution.long_only:
        return frame
    if "target_weight" not in frame.columns:
        raise ValueError("long_only 目标检查缺少 target_weight 字段")
    short_count = frame.filter(pl.col("target_weight") < -1e-12).height
    if short_count:
        raise ValueError(
            f"画像 {profile.profile_id} 配置 long_only=true，"
            f"但策略产生了 {short_count} 条负目标权重"
        )
    return frame


def parse_strategy_selection(strategy_name: str | None) -> tuple[str, ...] | None:
    """解析 CLI 风格策略选择器。

    ``None``、``portfolio``、``profile`` 或 ``all`` 表示画像中全部启用策略；逗号分隔
    字符串表示明确子集。子集是否启用由后续解析再次校验。
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
    """从画像构建选中策略及其资本权重。

    当只选择策略子集时，会将子集权重重新归一到 1，使研究结果代表完整子组合，而非
    保留原组合的一小部分名义风险。全量运行时保持画像原始权重，交由组合层处理。
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
            build_profile_strategy(profile, config),
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
    """运行完整策略管线并返回带输出语义的 StrategyOutputBundle。

    所有启用策略必须产生同一种输出：target_weight 可按资本权重合并并做组合风险缩放；
    execution_intent 只合并意图；trade_plan 当前每个画像只允许一条策略，防止未经定义的
    候选冲突和资金分配。``latest_only`` 只保留最新时点，但仍使用完整历史生成信号。
    """

    strategies, selected_ids = build_selected_profile_strategies(
        profile,
        strategy_ids=strategy_ids,
    )
    output_types = {strategy.output_type for strategy, _ in strategies}
    if len(output_types) != 1:
        output_text = ", ".join(sorted(item.value for item in output_types))
        raise ValueError(
            f"交易画像 {profile.profile_id} 同时包含多种策略输出类型：{output_text}。"
            "当前版本暂不支持同一画像混用 target_weight、execution_intent 和 trade_plan。"
        )

    output_type = next(iter(output_types))
    time_column = strategies[0][0].time_column
    strategy_frames: list[pl.DataFrame] = []
    weights: list[float] = []

    for strategy, capital_weight in strategies:
        output = strategy.build_output_bundle(market_df).frame
        current_output = strategy.latest_output(output) if latest_only else output
        if current_output.is_empty():
            continue
        if "strategy_id" not in current_output.columns:
            current_output = current_output.with_columns(
                pl.lit(strategy.strategy_id).alias("strategy_id")
            )
        strategy_frames.append(current_output)
        weights.append(float(capital_weight))

    latest_time_value: object | None = None
    if latest_only and strategy_frames and time_column in strategy_frames[0].columns:
        latest_time_value = (
            pl.concat(
                [frame.select(time_column) for frame in strategy_frames],
                how="vertical",
            )
            .select(pl.col(time_column).max())
            .item()
        )

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
        combined = enforce_profile_target_policy(combined, profile)
    elif output_type == StrategyOutputType.EXECUTION_INTENT:
        combined = combine_strategy_execution_intents(
            strategy_frames,
            weights,
            time_column=time_column,
        )
    elif output_type == StrategyOutputType.TRADE_PLAN:
        if len(strategies) != 1:
            raise ValueError(
                f"交易画像 {profile.profile_id} 配置了多个 TradePlan 策略。"
                "TradePlan 的资金分配与候选冲突必须先由风险层明确处理，当前每个画像仅支持一个。"
            )
        combined = strategy_frames[0] if strategy_frames else strategies[0][0].empty_output()
    else:  # pragma: no cover - 枚举新增值时的安全兜底
        raise ValueError(f"暂不支持的策略输出类型：{output_type.value}")

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

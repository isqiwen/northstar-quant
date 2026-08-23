"""Strategy base classes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

import polars as pl

from northstar_quant.foundation.common.enums import (
    DataFrequency,
    OrderSemantic,
    StrategyOutputType,
)
from northstar_quant.foundation.common.types import StrategyOutputBundle


class StrategyBase(ABC):
    """Unified strategy interface."""

    strategy_id: str = "base"
    output_type: StrategyOutputType = StrategyOutputType.TARGET_WEIGHT
    time_column: str = "date"
    supported_data_frequencies: tuple[DataFrequency, ...] = ()
    required_market_columns: tuple[str, ...] = ("symbol", "close")
    required_output_columns: tuple[str, ...] = ("symbol", "signal_value")

    def validate_market_data(self, market_df: pl.DataFrame) -> None:
        required_columns = {self.time_column, *self.required_market_columns}
        missing = sorted(required_columns.difference(market_df.columns))
        if missing:
            raise ValueError(
                f"策略 {self.strategy_id} 缺少必需行情列: {', '.join(missing)}"
            )

    def empty_output(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                self.time_column: [],
                **{column: [] for column in self.required_output_columns},
            }
        )

    def normalize_output(self, output: pl.DataFrame) -> pl.DataFrame:
        if output.is_empty():
            return self.empty_output()
        required_columns = {self.time_column, *self.required_output_columns}
        missing = sorted(required_columns.difference(output.columns))
        if missing:
            raise ValueError(
                f"策略 {self.strategy_id} 输出缺少字段: {', '.join(missing)}"
            )
        return output

    def to_output_frame(self, rows: Iterable[dict]) -> pl.DataFrame:
        rows = list(rows)
        if not rows:
            return self.empty_output()
        return self.normalize_output(pl.DataFrame(rows))

    def latest_output(self, output: pl.DataFrame) -> pl.DataFrame:
        normalized = self.normalize_output(output)
        if normalized.is_empty():
            return normalized
        latest_value = normalized[self.time_column].max()
        return normalized.filter(pl.col(self.time_column) == latest_value)

    @abstractmethod
    def generate_output(self, market_df: pl.DataFrame) -> pl.DataFrame:
        """Generate strategy output from market data."""

    def build_output_bundle(self, market_df: pl.DataFrame) -> StrategyOutputBundle:
        output = self.generate_output(market_df)
        return StrategyOutputBundle(
            strategy_id=self.strategy_id,
            output_type=self.output_type,
            time_column=self.time_column,
            frame=self.normalize_output(output),
        )


class TargetWeightStrategyBase(StrategyBase):
    """Base class for target-weight strategies."""

    output_type = StrategyOutputType.TARGET_WEIGHT
    required_output_columns = ("symbol", "signal_value", "target_weight")

    @abstractmethod
    def generate_targets(self, market_df: pl.DataFrame) -> pl.DataFrame:
        """Generate target weights from market data."""

    def generate_output(self, market_df: pl.DataFrame) -> pl.DataFrame:
        return self.generate_targets(market_df)

    def to_targets_frame(self, rows: Iterable[dict]) -> pl.DataFrame:
        return self.to_output_frame(rows)

    def latest_targets(self, targets: pl.DataFrame) -> pl.DataFrame:
        return self.latest_output(targets)


class ExecutionIntentStrategyBase(StrategyBase):
    """Base class for execution-intent strategies."""

    output_type = StrategyOutputType.EXECUTION_INTENT
    required_output_columns = (
        "symbol",
        "signal_value",
        "side",
        "size_fraction",
        "order_semantic",
    )

    @abstractmethod
    def generate_execution_intents(self, market_df: pl.DataFrame) -> pl.DataFrame:
        """Generate execution intents from market data."""

    def generate_output(self, market_df: pl.DataFrame) -> pl.DataFrame:
        return self.generate_execution_intents(market_df)

    def to_intents_frame(self, rows: Iterable[dict]) -> pl.DataFrame:
        return self.to_output_frame(rows)

    def normalize_output(self, output: pl.DataFrame) -> pl.DataFrame:
        normalized = super().normalize_output(output)
        if normalized.is_empty():
            return normalized

        if "order_type" not in normalized.columns:
            normalized = normalized.with_columns(pl.lit("MKT").alias("order_type"))
        if "order_semantic" not in normalized.columns:
            normalized = normalized.with_columns(
                pl.lit(OrderSemantic.ENTRY.value).alias("order_semantic")
            )
        if "reason" not in normalized.columns:
            normalized = normalized.with_columns(pl.col("order_semantic").alias("reason"))
        if "limit_price" not in normalized.columns:
            normalized = normalized.with_columns(
                pl.lit(None, dtype=pl.Float64).alias("limit_price")
            )

        normalized = normalized.with_columns(
            pl.col("side").cast(pl.String).str.to_uppercase().alias("side"),
            pl.col("order_semantic")
            .cast(pl.String)
            .str.to_lowercase()
            .alias("order_semantic"),
            pl.col("order_type").cast(pl.String).str.to_uppercase().alias("order_type"),
            pl.col("size_fraction").cast(pl.Float64).alias("size_fraction"),
        )

        invalid_sides = normalized.filter(~pl.col("side").is_in(["BUY", "SELL"]))
        if invalid_sides.height:
            raise ValueError(
                f"策略 {self.strategy_id} 的执行意图 side 仅支持 BUY / SELL"
            )

        supported_semantics = [member.value for member in OrderSemantic]
        invalid_semantics = normalized.filter(
            ~pl.col("order_semantic").is_in(supported_semantics)
        )
        if invalid_semantics.height:
            supported = ", ".join(supported_semantics)
            raise ValueError(
                f"策略 {self.strategy_id} 的执行意图 order_semantic 仅支持 {supported}"
            )

        invalid_sizes = normalized.filter(pl.col("size_fraction") <= 0)
        if invalid_sizes.height:
            raise ValueError(
                f"策略 {self.strategy_id} 的执行意图 size_fraction 必须大于 0"
            )
        return normalized

    def latest_intents(self, intents: pl.DataFrame) -> pl.DataFrame:
        return self.latest_output(intents)


class TradePlanStrategyBase(StrategyBase):
    """输出单笔候选交易计划的策略基类。

    策略只给出交易方向、入场与退出价格假设及其理由；账户资金、风险预算
    和数量由后续风险、执行模块计算。这样可保证同一信号在研究、模拟和实盘
    都经过一致的资金与风控约束。
    """

    output_type = StrategyOutputType.TRADE_PLAN
    required_output_columns = (
        "symbol",
        "signal_value",
        "side",
        "planned_entry_price",
        "initial_stop_price",
        "entry_condition",
    )

    @abstractmethod
    def generate_trade_plans(self, market_df: pl.DataFrame) -> pl.DataFrame:
        """根据行情生成候选交易计划。"""

    def generate_output(self, market_df: pl.DataFrame) -> pl.DataFrame:
        return self.generate_trade_plans(market_df)

    def to_trade_plans_frame(self, rows: Iterable[dict]) -> pl.DataFrame:
        return self.to_output_frame(rows)

    def normalize_output(self, output: pl.DataFrame) -> pl.DataFrame:
        normalized = super().normalize_output(output)
        if normalized.is_empty():
            return normalized

        defaults: dict[str, pl.Expr] = {
            "target_price": pl.lit(None, dtype=pl.Float64),
            "target_r": pl.lit(None, dtype=pl.Float64),
            "cancel_condition": pl.lit(None, dtype=pl.String),
            "reason": pl.lit("trade_plan"),
            "trend": pl.lit(None, dtype=pl.String),
            "support_price": pl.lit(None, dtype=pl.Float64),
            "resistance_price": pl.lit(None, dtype=pl.Float64),
        }
        normalized = normalized.with_columns(
            expression.alias(column)
            for column, expression in defaults.items()
            if column not in normalized.columns
        ).with_columns(
            pl.col("side").cast(pl.String).str.to_uppercase().alias("side"),
            pl.col("signal_value").cast(pl.Float64).alias("signal_value"),
            pl.col("planned_entry_price").cast(pl.Float64).alias("planned_entry_price"),
            pl.col("initial_stop_price").cast(pl.Float64).alias("initial_stop_price"),
            pl.col("target_price").cast(pl.Float64).alias("target_price"),
            pl.col("target_r").cast(pl.Float64).alias("target_r"),
            pl.col("entry_condition").cast(pl.String).alias("entry_condition"),
        )

        invalid_side = normalized.filter(~pl.col("side").is_in(["BUY", "SELL"]))
        if invalid_side.height:
            raise ValueError(f"策略 {self.strategy_id} 的 TradePlan side 仅支持 BUY / SELL")
        invalid_prices = normalized.filter(
            (pl.col("planned_entry_price") <= 0) | (pl.col("initial_stop_price") <= 0)
        )
        if invalid_prices.height:
            raise ValueError(f"策略 {self.strategy_id} 的 TradePlan 入场价和止损价必须大于 0")
        invalid_stop = normalized.filter(
            ((pl.col("side") == "BUY") & (pl.col("initial_stop_price") >= pl.col("planned_entry_price")))
            | ((pl.col("side") == "SELL") & (pl.col("initial_stop_price") <= pl.col("planned_entry_price")))
        )
        if invalid_stop.height:
            raise ValueError(f"策略 {self.strategy_id} 的 TradePlan 初始止损方向不合理")
        invalid_target = normalized.filter(
            pl.col("target_price").is_not_null()
            & (
                (pl.col("target_price") <= 0)
                | ((pl.col("side") == "BUY") & (pl.col("target_price") <= pl.col("planned_entry_price")))
                | ((pl.col("side") == "SELL") & (pl.col("target_price") >= pl.col("planned_entry_price")))
            )
        )
        if invalid_target.height:
            raise ValueError(f"策略 {self.strategy_id} 的 TradePlan 目标价方向不合理")
        invalid_target_r = normalized.filter(
            pl.col("target_r").is_not_null() & (pl.col("target_r") <= 0)
        )
        if invalid_target_r.height:
            raise ValueError(f"策略 {self.strategy_id} 的 TradePlan target_r 必须大于 0")
        risk_per_unit = (pl.col("planned_entry_price") - pl.col("initial_stop_price")).abs()
        derived_target_r = (pl.col("target_price") - pl.col("planned_entry_price")).abs() / risk_per_unit
        inconsistent_target = normalized.filter(
            pl.col("target_price").is_not_null()
            & pl.col("target_r").is_not_null()
            & ((derived_target_r - pl.col("target_r")).abs() > 1e-8)
        )
        if inconsistent_target.height:
            raise ValueError(f"策略 {self.strategy_id} 的 TradePlan 目标价与 target_r 不一致")
        empty_condition = normalized.filter(pl.col("entry_condition").str.strip_chars() == "")
        if empty_condition.height:
            raise ValueError(f"策略 {self.strategy_id} 的 TradePlan 入场条件不能为空")

        derived_target_price = pl.when(pl.col("side") == "BUY").then(
            pl.col("planned_entry_price") + risk_per_unit * pl.col("target_r")
        ).otherwise(pl.col("planned_entry_price") - risk_per_unit * pl.col("target_r"))
        normalized = normalized.with_columns(
            risk_per_unit.alias("risk_per_unit"),
            pl.when(pl.col("target_price").is_null() & pl.col("target_r").is_not_null())
            .then(derived_target_price)
            .otherwise(pl.col("target_price"))
            .alias("target_price"),
            pl.when(pl.col("target_r").is_null() & pl.col("target_price").is_not_null())
            .then(derived_target_r)
            .otherwise(pl.col("target_r"))
            .alias("target_r"),
        ).with_columns(
            pl.when(pl.col("target_price").is_not_null())
            .then((pl.col("target_price") - pl.col("planned_entry_price")).abs())
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .alias("reward_per_unit"),
            pl.col("target_r").alias("risk_reward_ratio"),
        )
        return normalized

    def latest_trade_plans(self, trade_plans: pl.DataFrame) -> pl.DataFrame:
        """返回最新决策时点的候选交易计划。"""

        return self.latest_output(trade_plans)


class DateBasedStrategyBase(TargetWeightStrategyBase):
    """Target-weight strategy driven by ``date``."""

    time_column = "date"
    required_market_columns = ("date", "symbol", "open", "high", "low", "close", "volume")
    supported_data_frequencies: tuple[DataFrequency, ...] = (DataFrequency.D1, DataFrequency.W1)


class DailyStrategyBase(DateBasedStrategyBase):
    """Daily target-weight strategy."""

    supported_data_frequencies = (DataFrequency.D1,)


class WeeklyStrategyBase(DateBasedStrategyBase):
    """Weekly target-weight strategy."""

    supported_data_frequencies = (DataFrequency.W1,)


class DateBasedTradePlanStrategyBase(TradePlanStrategyBase):
    """以 ``date`` 驱动的交易计划策略基类。"""

    time_column = "date"
    required_market_columns = ("date", "symbol", "open", "high", "low", "close", "volume")
    supported_data_frequencies: tuple[DataFrequency, ...] = (DataFrequency.D1, DataFrequency.W1)


class DailyTradePlanStrategyBase(DateBasedTradePlanStrategyBase):
    """日线交易计划策略基类。"""

    supported_data_frequencies = (DataFrequency.D1,)


class WeeklyTradePlanStrategyBase(DateBasedTradePlanStrategyBase):
    """周线交易计划策略基类。"""

    supported_data_frequencies = (DataFrequency.W1,)


class IntradayStrategyBase(ExecutionIntentStrategyBase):
    """Execution-intent strategy driven by ``timestamp``."""

    time_column = "timestamp"
    required_market_columns = ("timestamp", "symbol", "open", "high", "low", "close", "volume")
    supported_data_frequencies: tuple[DataFrequency, ...] = (
        DataFrequency.M1,
        DataFrequency.M5,
        DataFrequency.M15,
        DataFrequency.H1,
    )

    def normalize_output(self, output: pl.DataFrame) -> pl.DataFrame:
        normalized = super().normalize_output(output)
        if normalized.is_empty():
            return normalized
        if "date" not in normalized.columns and "timestamp" in normalized.columns:
            normalized = normalized.with_columns(pl.col("timestamp").dt.date().alias("date"))
        return normalized


class MinuteStrategyBase(IntradayStrategyBase):
    """Minute-level execution-intent strategy."""

    supported_data_frequencies = (
        DataFrequency.M1,
        DataFrequency.M5,
        DataFrequency.M15,
    )

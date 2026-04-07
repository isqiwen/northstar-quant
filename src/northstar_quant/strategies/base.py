"""Strategy base classes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable

import polars as pl

from northstar_quant.common.enums import (
    DataFrequency,
    OrderSemantic,
    StrategyOutputType,
)
from northstar_quant.common.types import StrategyOutputBundle


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


class DateBasedStrategyBase(TargetWeightStrategyBase):
    """Target-weight strategy driven by ``date``."""

    time_column = "date"
    required_market_columns = ("date", "symbol", "open", "high", "low", "close", "volume")
    supported_data_frequencies = (DataFrequency.D1, DataFrequency.W1)


class DailyStrategyBase(DateBasedStrategyBase):
    """Daily target-weight strategy."""

    supported_data_frequencies = (DataFrequency.D1,)


class WeeklyStrategyBase(DateBasedStrategyBase):
    """Weekly target-weight strategy."""

    supported_data_frequencies = (DataFrequency.W1,)


class IntradayStrategyBase(ExecutionIntentStrategyBase):
    """Execution-intent strategy driven by ``timestamp``."""

    time_column = "timestamp"
    required_market_columns = ("timestamp", "symbol", "open", "high", "low", "close", "volume")
    supported_data_frequencies = (
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

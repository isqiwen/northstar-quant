"""收盘后生成、冻结并读取日频目标仓位。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import math
from zoneinfo import ZoneInfo

import polars as pl

from northstar_quant.platform.common.enums import DataFrequency, StrategyOutputType
from northstar_quant.platform.common.time import ensure_utc, utc_now
from northstar_quant.platform.common.types import StrategyOutputBundle
from northstar_quant.platform.config.settings import get_settings
from northstar_quant.platform.config.trading_profile import (
    TradingProfile,
    ensure_broker_profile,
    load_trading_profile,
)
from northstar_quant.data_platform.artifacts.storage import (
    load_profile_market_data,
    load_profile_signal_data,
)
from northstar_quant.platform.db.models import StrategyRunRecord
from northstar_quant.platform.db.repositories import (
    get_strategy_run_by_run_id,
    latest_strategy_run,
    list_strategy_snapshots_for_run,
    save_strategy_run_snapshot,
)
from northstar_quant.platform.db.session import SessionLocal
from northstar_quant.platform.observability.logging.logger import get_logger
from northstar_quant.portfolio_risk.portfolio.strategy_pipeline import run_profile_strategy_pipeline

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PersistedDailyTargets:
    """一份已经冻结、可以交给执行层读取的日频目标。"""

    run_id: str
    profile_id: str
    market_data_asof: datetime | None
    signal_data_asof: datetime | None
    output_asof: datetime
    created_at: datetime
    bundle: StrategyOutputBundle

    def to_dict(self, *, reused: bool | None = None) -> dict[str, object]:
        result: dict[str, object] = {
            "run_id": self.run_id,
            "profile_id": self.profile_id,
            "output_type": self.bundle.output_type.value,
            "output_asof": self.output_asof.isoformat(),
            "target_count": self.bundle.frame.height,
            "market_data_asof": (
                self.market_data_asof.isoformat()
                if self.market_data_asof is not None
                else None
            ),
            "signal_data_asof": (
                self.signal_data_asof.isoformat()
                if self.signal_data_asof is not None
                else None
            ),
            "created_at": self.created_at.isoformat(),
        }
        if reused is not None:
            result["reused"] = reused
        return result


def _latest_value(frame: pl.DataFrame, columns: tuple[str, ...]) -> object | None:
    if frame.is_empty():
        return None
    for column in columns:
        if column in frame.columns:
            return frame.get_column(column).max()
    return None


def _calendar_date(value: object | None) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None
    return None


def _assert_daily_inputs_complete(
    profile: TradingProfile,
    raw_market_df: pl.DataFrame,
    signal_market_df: pl.DataFrame,
    pipeline: StrategyOutputBundle,
) -> None:
    """证明目标只依赖已经结束的完整日线，缺少证据时失败关闭。"""

    if profile.data_frequency != DataFrequency.D1:
        raise ValueError("DAILY_TARGET_FREQUENCY_REQUIRED: 日频目标只接受 1d 原始行情。")
    if profile.strategy_data_frequency != DataFrequency.D1:
        raise ValueError("DAILY_SIGNAL_FREQUENCY_REQUIRED: 策略信号频率必须为 1d。")
    if pipeline.output_type != StrategyOutputType.TARGET_WEIGHT:
        raise ValueError("DAILY_TARGET_WEIGHT_REQUIRED: 日频执行层只接受 target_weight。")
    if raw_market_df.is_empty() or signal_market_df.is_empty() or pipeline.frame.is_empty():
        raise ValueError("DAILY_TARGET_INPUT_EMPTY: 行情、信号或目标为空，禁止冻结。")

    raw_time_column = "timestamp" if "timestamp" in raw_market_df.columns else "date"
    raw_latest = _latest_value(raw_market_df, (raw_time_column,))
    signal_latest = _latest_value(signal_market_df, ("date", "timestamp"))
    output_latest = _latest_value(pipeline.frame, (pipeline.time_column,))
    dates = {
        "market": _calendar_date(raw_latest),
        "signal": _calendar_date(signal_latest),
        "output": _calendar_date(output_latest),
    }
    if None in dates.values() or len(set(dates.values())) != 1:
        raise ValueError(
            "DAILY_TARGET_ASOF_MISMATCH: 市场数据、信号数据与目标日期不一致，"
            f"market={dates['market']}，signal={dates['signal']}，output={dates['output']}。"
        )

    if "session_complete" not in raw_market_df.columns:
        raise ValueError(
            "DAILY_SESSION_COMPLETENESS_REQUIRED: production 日频行情必须提供 "
            "session_complete，禁止根据无法证明已封盘的数据生成目标。"
        )
    latest_rows = raw_market_df.filter(pl.col(raw_time_column) == raw_latest)
    if latest_rows.is_empty() or not bool(
        latest_rows.get_column("session_complete").fill_null(False).all()
    ):
        raise ValueError(
            "DAILY_SESSION_INCOMPLETE: 最新交易日仍有未完成时段，禁止生成日频目标。"
        )


def _daily_target_run_id(
    profile: TradingProfile,
    pipeline: StrategyOutputBundle,
) -> str:
    output_value = _latest_value(pipeline.frame, (pipeline.time_column,))
    if output_value is None:
        raise ValueError("DAILY_TARGET_ASOF_REQUIRED: 目标缺少稳定决策日期。")
    digest = sha256(
        (
            f"daily-target-v1:{profile.profile_id}:{pipeline.strategy_id}:"
            f"{output_value}"
        ).encode("utf-8")
    ).hexdigest()
    return f"daily-target-{digest[:32]}"


def _snapshot_frame(rows, *, output_asof: datetime) -> pl.DataFrame:
    payload = [
        {
            "date": row.asof.date(),
            "symbol": row.symbol,
            "signal_value": row.signal_value,
            "target_weight": row.target_weight,
        }
        for row in rows
    ]
    if not payload:
        raise RuntimeError(
            "DAILY_TARGET_SNAPSHOT_EMPTY: 策略快照没有逐标的目标，禁止执行。"
        )
    frame = pl.DataFrame(payload).sort(["date", "symbol"])
    latest_date = frame.get_column("date").max()
    if latest_date != output_asof.date():
        raise RuntimeError(
            "DAILY_TARGET_SNAPSHOT_ASOF_MISMATCH: 快照明细与运行头日期不一致。"
        )
    return frame


def _persisted_from_record(session, record: StrategyRunRecord) -> PersistedDailyTargets:
    if record.output_asof is None:
        raise RuntimeError(
            "DAILY_TARGET_SNAPSHOT_ASOF_REQUIRED: 已保存目标缺少 output_asof。"
        )
    output_type = StrategyOutputType.parse(record.output_type)
    if output_type != StrategyOutputType.TARGET_WEIGHT:
        raise RuntimeError(
            "DAILY_TARGET_SNAPSHOT_TYPE_INVALID: 已保存目标不是 target_weight。"
        )
    rows = list_strategy_snapshots_for_run(session, run_id=record.run_id)
    if len(rows) != record.snapshot_count:
        raise RuntimeError(
            "DAILY_TARGET_SNAPSHOT_COUNT_MISMATCH: 策略运行头与明细数量不一致。"
        )
    frame = _snapshot_frame(rows, output_asof=record.output_asof)
    return PersistedDailyTargets(
        run_id=record.run_id,
        profile_id=record.profile_id,
        market_data_asof=record.market_data_asof,
        signal_data_asof=record.signal_data_asof,
        output_asof=record.output_asof,
        created_at=record.created_at,
        bundle=StrategyOutputBundle(
            strategy_id=record.pipeline_strategy_id,
            output_type=StrategyOutputType.TARGET_WEIGHT,
            time_column="date",
            frame=frame,
        ),
    )


def _comparable_targets(frame: pl.DataFrame) -> list[tuple[str, float | None, float]]:
    rows: list[tuple[str, float | None, float]] = []
    for row in frame.sort("symbol").to_dicts():
        signal_value = row.get("signal_value")
        target_weight = row.get("target_weight")
        if target_weight is None or not math.isfinite(float(target_weight)):
            raise ValueError("DAILY_TARGET_WEIGHT_INVALID: 目标权重必须是有限数。")
        rows.append(
            (
                str(row.get("symbol") or "").strip().upper(),
                (
                    round(float(signal_value), 12)
                    if signal_value is not None and math.isfinite(float(signal_value))
                    else None
                ),
                round(float(target_weight), 12),
            )
        )
    return rows


def generate_daily_targets_once(
    profile_id: str | None = None,
) -> PersistedDailyTargets:
    """在日线封盘后计算目标，并按决策日冻结为不可变快照。"""

    profile = ensure_broker_profile(
        load_trading_profile(profile_id),
        broker=get_settings().broker,
        context="live.signal",
    )
    raw_market_df = load_profile_market_data(profile)
    signal_market_df = load_profile_signal_data(profile)
    pipeline = run_profile_strategy_pipeline(
        signal_market_df,
        profile,
        latest_only=True,
    )
    _assert_daily_inputs_complete(
        profile,
        raw_market_df,
        signal_market_df,
        pipeline,
    )
    run_id = _daily_target_run_id(profile, pipeline)

    with SessionLocal() as session:
        existing = get_strategy_run_by_run_id(session, run_id)
        if existing is not None:
            persisted = _persisted_from_record(session, existing)
            if _comparable_targets(persisted.bundle.frame) != _comparable_targets(
                pipeline.frame
            ):
                raise RuntimeError(
                    "DAILY_TARGET_ALREADY_FROZEN: 同一决策日的目标已经冻结，"
                    "但当前重算结果发生变化；禁止静默覆盖，请人工核验数据修订。"
                )
            logger.bind(
                command="live.signal",
                profile=profile.profile_id,
                run_id=run_id,
            ).info("日频目标已存在，本次复用冻结快照")
            return persisted

        save_strategy_run_snapshot(
            session,
            run_id=run_id,
            profile_id=profile.profile_id,
            pipeline_strategy_id=pipeline.strategy_id,
            output_type=pipeline.output_type,
            time_column=pipeline.time_column,
            output_frame=pipeline.frame,
            selected_strategy_ids=[
                item.strategy_id for item in profile.enabled_strategies
            ],
            strategy_params={
                item.strategy_id: dict(item.params)
                for item in profile.enabled_strategies
            },
            risk_limits=dict(profile.risk),
            market_data_frame=raw_market_df,
            signal_data_frame=signal_market_df,
        )
        record = get_strategy_run_by_run_id(session, run_id)
        if record is None:
            raise RuntimeError("DAILY_TARGET_PERSIST_FAILED: 日频目标写入后无法读取。")
        persisted = _persisted_from_record(session, record)

    logger.bind(
        command="live.signal",
        profile=profile.profile_id,
        run_id=run_id,
        output_asof=persisted.output_asof.isoformat(),
    ).info("日频目标已经冻结，targets=%s", persisted.bundle.frame.height)
    return persisted


def load_latest_daily_targets(
    profile: TradingProfile,
    *,
    require_fresh: bool = True,
    now: datetime | None = None,
) -> PersistedDailyTargets:
    """读取执行层唯一允许消费的最新日频目标快照。"""

    with SessionLocal() as session:
        record = latest_strategy_run(
            session,
            profile_id=profile.profile_id,
            run_id_prefix="daily-target-",
        )
        if record is None:
            raise RuntimeError(
                "DAILY_TARGET_NOT_FOUND: 尚未生成日频目标；请先运行 northstar live signal。"
            )
        persisted = _persisted_from_record(session, record)

    if require_fresh:
        current = ensure_utc(now or utc_now()).astimezone(ZoneInfo(profile.timezone))
        age_days = (current.date() - persisted.output_asof.date()).days
        max_age_days = get_settings().daily_target_max_age_days
        if age_days < 0 or age_days > max_age_days:
            raise RuntimeError(
                "DAILY_TARGET_STALE: 最新目标已过期或日期异常，"
                f"output_asof={persisted.output_asof.date()}，age_days={age_days}，"
                f"max_age_days={max_age_days}。"
            )
    return persisted

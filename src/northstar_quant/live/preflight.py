"""实盘下单前的硬门禁检查。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

import polars as pl

from northstar_quant.common.enums import DataFrequency
from northstar_quant.common.time import ensure_utc, utc_now
from northstar_quant.config.settings import get_settings
from northstar_quant.config.trading_profile import TradingProfile
from northstar_quant.execution.models import BrokerStateSnapshot

_ACCEPTED_EXECUTION_SOURCES = {
    "broker_snapshot",
    "broker_snapshot_delayed",
    "paper_state",
}
_BLOCKING_ACCOUNT_ALERT_TAGS = {"账本异常", "资金异常"}


@dataclass(slots=True)
class PreflightCheck:
    """单项 preflight 检查结果。"""

    code: str
    status: str
    blocking: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status == "fail"


@dataclass(slots=True)
class PreflightResult:
    """整轮 preflight 汇总结果。"""

    profile_id: str
    checked_at: datetime
    checks: list[PreflightCheck] = field(default_factory=list)

    @property
    def can_trade(self) -> bool:
        return not any(check.failed and check.blocking for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        blocking_checks = [
            check
            for check in self.checks
            if check.failed and check.blocking
        ]
        warning_checks = [check for check in self.checks if check.status == "warn"]
        return {
            "profile_id": self.profile_id,
            "checked_at": ensure_utc(self.checked_at).isoformat(),
            "can_trade": self.can_trade,
            "blocking_failure_count": len(blocking_checks),
            "warning_count": len(warning_checks),
            "blocking_messages": [check.message for check in blocking_checks],
            "warning_messages": [check.message for check in warning_checks],
            "checks": [
                {
                    **asdict(check),
                    "failed": check.failed,
                }
                for check in self.checks
            ],
        }


def _coerce_snapshot_time(value: object | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return ensure_utc(value)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=utc_now().tzinfo)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return ensure_utc(parsed)
    return None


def _latest_frame_asof(
    frame: pl.DataFrame | None,
    *,
    preferred_columns: tuple[str, ...] = ("date", "timestamp", "ts", "datetime", "asof"),
) -> datetime | None:
    if frame is None or frame.is_empty():
        return None
    for column in preferred_columns:
        if column not in frame.columns:
            continue
        return _coerce_snapshot_time(frame[column].max())
    return None


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "N/A"
    return ensure_utc(value).isoformat()


def _aligned_to_frequency(
    left: datetime | None,
    right: datetime | None,
    frequency: DataFrequency,
) -> bool:
    if left is None or right is None:
        return False
    left_utc = ensure_utc(left)
    right_utc = ensure_utc(right)
    if frequency in {DataFrequency.D1, DataFrequency.W1}:
        return left_utc.date() == right_utc.date()
    if frequency == DataFrequency.H1:
        return left_utc.replace(minute=0, second=0, microsecond=0) == right_utc.replace(
            minute=0,
            second=0,
            microsecond=0,
        )
    if frequency == DataFrequency.M15:
        return (
            left_utc.replace(minute=(left_utc.minute // 15) * 15, second=0, microsecond=0)
            == right_utc.replace(minute=(right_utc.minute // 15) * 15, second=0, microsecond=0)
        )
    if frequency == DataFrequency.M5:
        return (
            left_utc.replace(minute=(left_utc.minute // 5) * 5, second=0, microsecond=0)
            == right_utc.replace(minute=(right_utc.minute // 5) * 5, second=0, microsecond=0)
        )
    if frequency == DataFrequency.M1:
        return left_utc.replace(second=0, microsecond=0) == right_utc.replace(
            second=0,
            microsecond=0,
        )
    return left_utc == right_utc


def _max_data_age(profile: TradingProfile) -> timedelta:
    settings = get_settings()
    if profile.data_frequency == DataFrequency.W1:
        return timedelta(days=settings.live_preflight_weekly_data_max_age_days)
    if profile.data_frequency == DataFrequency.D1:
        return timedelta(days=settings.live_preflight_daily_data_max_age_days)
    return timedelta(minutes=settings.live_preflight_intraday_data_max_age_minutes)


def _append_check(
    result: PreflightResult,
    *,
    code: str,
    status: str,
    blocking: bool,
    message: str,
    **details: Any,
) -> None:
    result.checks.append(
        PreflightCheck(
            code=code,
            status=status,
            blocking=blocking,
            message=message,
            details=details,
        )
    )


def build_preflight_result(
    *,
    profile: TradingProfile,
    raw_market_df: pl.DataFrame,
    signal_market_df: pl.DataFrame,
    output_frame: pl.DataFrame,
    output_time_column: str,
    broker_state: BrokerStateSnapshot,
    execution_symbols: list[str],
    execution_reference_prices: dict[str, float],
    execution_price_sources: dict[str, str],
    equity: float | None,
    live_account_attribution: dict[str, Any] | None = None,
    checked_at: datetime | None = None,
) -> PreflightResult:
    """构建实盘 preflight 汇总结果。"""

    checked_at = ensure_utc(checked_at or utc_now())
    result = PreflightResult(profile_id=profile.profile_id, checked_at=checked_at)
    max_data_age = _max_data_age(profile)

    market_asof = _latest_frame_asof(raw_market_df)
    signal_asof = _latest_frame_asof(signal_market_df)
    output_asof = _latest_frame_asof(
        output_frame,
        preferred_columns=(output_time_column, "asof", "timestamp", "date", "datetime", "ts"),
    )

    for code, label, asof in (
        ("market_data_freshness", "市场数据", market_asof),
        ("signal_data_freshness", "信号数据", signal_asof),
    ):
        if asof is None:
            _append_check(
                result,
                code=code,
                status="fail",
                blocking=True,
                message=f"{label}缺少可识别的 asof 字段，本次只同步不交易。",
            )
            continue
        age = checked_at - ensure_utc(asof)
        if age > max_data_age:
            _append_check(
                result,
                code=code,
                status="fail",
                blocking=True,
                message=(
                    f"{label}最新时间 {_format_dt(asof)}，已超过 {max_data_age} 的新鲜度门限，本次只同步不交易。"
                ),
                latest_asof=_format_dt(asof),
                age_seconds=int(age.total_seconds()),
            )
            continue
        _append_check(
            result,
            code=code,
            status="pass",
            blocking=True,
            message=f"{label}最新时间 {_format_dt(asof)}，新鲜度检查通过。",
            latest_asof=_format_dt(asof),
            age_seconds=int(age.total_seconds()),
        )

    signal_output_aligned = _aligned_to_frequency(signal_asof, output_asof, profile.data_frequency)
    if signal_output_aligned:
        _append_check(
            result,
            code="signal_output_alignment",
            status="pass",
            blocking=True,
            message=(
                f"信号数据与策略输出对齐，signal={_format_dt(signal_asof)}，output={_format_dt(output_asof)}。"
            ),
            signal_asof=_format_dt(signal_asof),
            output_asof=_format_dt(output_asof),
        )
    else:
        _append_check(
            result,
            code="signal_output_alignment",
            status="fail",
            blocking=True,
            message=(
                f"信号数据与策略输出未对齐，signal={_format_dt(signal_asof)}，output={_format_dt(output_asof)}，本次只同步不交易。"
            ),
            signal_asof=_format_dt(signal_asof),
            output_asof=_format_dt(output_asof),
        )

    state_asof = broker_state.asof
    state_age_seconds: int | None = None
    if state_asof is not None:
        state_age_seconds = int((checked_at - ensure_utc(state_asof)).total_seconds())
    max_state_age_seconds = int(get_settings().live_preflight_max_state_age_seconds)
    broker_state_ok = (
        state_asof is not None
        and state_age_seconds is not None
        and state_age_seconds <= max_state_age_seconds
        and equity is not None
    )
    if broker_state_ok:
        _append_check(
            result,
            code="broker_state_completeness",
            status="pass",
            blocking=True,
            message=(
                f"券商状态快照完整，state_asof={_format_dt(state_asof)}，equity={float(equity):,.2f}。"
            ),
            state_asof=_format_dt(state_asof),
            state_age_seconds=state_age_seconds,
            equity=float(equity),
        )
    else:
        _append_check(
            result,
            code="broker_state_completeness",
            status="fail",
            blocking=True,
            message=(
                f"券商状态不完整，state_asof={_format_dt(state_asof)}，equity={equity!r}，本次只同步不交易。"
            ),
            state_asof=_format_dt(state_asof),
            state_age_seconds=state_age_seconds,
            equity=equity,
            max_state_age_seconds=max_state_age_seconds,
        )

    open_order_count = len(broker_state.open_orders)
    if open_order_count > 0:
        _append_check(
            result,
            code="working_orders_clear",
            status="fail",
            blocking=True,
            message=f"当前仍有 {open_order_count} 笔未完成订单，本次只同步不交易。",
            open_order_count=open_order_count,
            broker_order_ids=[
                str(row.get("broker_order_id") or "")
                for row in broker_state.open_orders
                if str(row.get("broker_order_id") or "").strip()
            ],
        )
    else:
        _append_check(
            result,
            code="working_orders_clear",
            status="pass",
            blocking=True,
            message="当前没有未完成挂单，可继续进入执行计划。",
            open_order_count=0,
        )

    allow_fallback = bool(get_settings().live_preflight_allow_valuation_price_fallback)
    missing_symbols = [
        symbol
        for symbol in execution_symbols
        if symbol not in execution_reference_prices
    ]
    fallback_symbols = sorted(
        symbol
        for symbol in execution_symbols
        if execution_price_sources.get(symbol) == "local_valuation_fallback"
    )
    unknown_source_symbols = sorted(
        symbol
        for symbol in execution_symbols
        if symbol in execution_reference_prices
        and execution_price_sources.get(symbol) not in _ACCEPTED_EXECUTION_SOURCES
        and execution_price_sources.get(symbol) != "local_valuation_fallback"
    )
    quote_failed = bool(missing_symbols or unknown_source_symbols or (fallback_symbols and not allow_fallback))
    if quote_failed:
        fragments: list[str] = []
        if missing_symbols:
            fragments.append(f"缺少执行报价: {', '.join(sorted(missing_symbols))}")
        if fallback_symbols and not allow_fallback:
            fragments.append(f"仅有本地估值回退价: {', '.join(fallback_symbols)}")
        if unknown_source_symbols:
            fragments.append(f"执行价来源不受信任: {', '.join(unknown_source_symbols)}")
        _append_check(
            result,
            code="execution_quotes_available",
            status="fail",
            blocking=True,
            message="；".join(fragments) + "，本次只同步不交易。",
            execution_symbol_count=len(execution_symbols),
            missing_symbols=sorted(missing_symbols),
            fallback_symbols=fallback_symbols,
            unknown_source_symbols=unknown_source_symbols,
        )
    else:
        _append_check(
            result,
            code="execution_quotes_available",
            status="pass",
            blocking=True,
            message=(
                f"执行参考价检查通过，共覆盖 {len(execution_symbols)} 个标的。"
            ),
            execution_symbol_count=len(execution_symbols),
            price_sources={symbol: execution_price_sources.get(symbol) for symbol in execution_symbols},
        )

    alert_items = [
        item
        for item in (live_account_attribution or {}).get("alert_items", [])
        if isinstance(item, dict)
    ]
    blocking_alerts = [
        item
        for item in alert_items
        if str(item.get("tag") or "").strip() in _BLOCKING_ACCOUNT_ALERT_TAGS
    ]
    warning_alerts = [
        item
        for item in alert_items
        if str(item.get("tag") or "").strip()
        and str(item.get("tag") or "").strip() not in _BLOCKING_ACCOUNT_ALERT_TAGS
    ]
    if blocking_alerts:
        _append_check(
            result,
            code="account_anomaly_gate",
            status="fail",
            blocking=True,
            message=(
                "最近账户归因存在阻断型异常："
                + "；".join(f"[{item['tag']}] {item['message']}" for item in blocking_alerts)
                + "，本次只同步不交易。"
            ),
            blocking_alerts=blocking_alerts,
        )
    elif warning_alerts:
        _append_check(
            result,
            code="account_anomaly_gate",
            status="warn",
            blocking=False,
            message=(
                "最近账户归因存在非阻断异常："
                + "；".join(f"[{item['tag']}] {item['message']}" for item in warning_alerts)
            ),
            warning_alerts=warning_alerts,
        )
    else:
        _append_check(
            result,
            code="account_anomaly_gate",
            status="pass",
            blocking=True,
            message="最近账户归因没有触发阻断型异常。",
        )

    return result

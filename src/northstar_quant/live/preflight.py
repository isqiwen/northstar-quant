"""实盘下单前的硬门禁检查。"""

from __future__ import annotations

import math
from collections.abc import Mapping
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
    """一项实盘前检查的可审计结果。

    ``blocking`` 为 true 的 fail 会使整轮 ``can_trade`` 为 false；warn 只记录风险提示，
    不应被用于绕过真正的安全门禁。
    """

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
    """一轮实盘前检查的不可变结论视图。

    只有所有阻断项均未失败时才允许后续提交。它不自行下单，也不能替代订单路由时的
    最后一层预交易风控，因为行情、账户状态可能在两者之间变化。
    """

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
    available_cash: float | None = None,
    live_account_attribution: dict[str, Any] | None = None,
    broker_name: str | None = None,
    expected_account: str | None = None,
    data_manifest: Mapping[str, Any] | None = None,
    checked_at: datetime | None = None,
) -> PreflightResult:
    """构建实盘前硬门禁结果。

    检查账户身份与状态完整性、数据 manifest 与时效、信号和行情时间对齐、执行价格
    来源、资金与持仓归因等条件。任一必需输入缺失时应生成阻断项，而不是填充默认值；
    这使 production 路径保持失败关闭。
    """

    checked_at = ensure_utc(checked_at or utc_now())
    result = PreflightResult(profile_id=profile.profile_id, checked_at=checked_at)
    max_data_age = _max_data_age(profile)

    normalized_broker = str(broker_name or "").strip().lower()
    if normalized_broker and normalized_broker != "paper":
        normalized_expected_account = str(expected_account or "").strip()
        normalized_state_account = str(broker_state.account or "").strip()
        identity_issues: list[str] = []
        if not normalized_expected_account:
            identity_issues.append("未提供目标券商账户")
        if not normalized_state_account:
            identity_issues.append("券商状态未声明账户")
        elif (
            normalized_expected_account
            and normalized_state_account != normalized_expected_account
        ):
            identity_issues.append(
                "券商状态账户与目标账户不一致："
                f"{normalized_state_account} != {normalized_expected_account}"
            )
        if not broker_state.state_complete:
            identity_issues.append("券商状态快照未证明完整")
        if broker_state.state_errors:
            identity_issues.append(
                "券商状态包含错误：" + "；".join(broker_state.state_errors)
            )

        if identity_issues:
            _append_check(
                result,
                code="broker_account_identity",
                status="fail",
                blocking=True,
                message=(
                    "真实券商账户身份门禁未通过："
                    + "；".join(identity_issues)
                    + "，本次只同步不交易。"
                ),
                expected_account=normalized_expected_account or None,
                state_account=normalized_state_account or None,
                state_complete=broker_state.state_complete,
                state_errors=list(broker_state.state_errors),
            )
        else:
            _append_check(
                result,
                code="broker_account_identity",
                status="pass",
                blocking=True,
                message=f"券商账户身份检查通过，account={normalized_state_account}。",
                expected_account=normalized_expected_account,
                state_account=normalized_state_account,
                state_complete=True,
            )

        provenance_issues: list[str] = []
        approved_live_providers = {
            item.strip().lower()
            for item in get_settings().approved_live_data_providers.split(",")
            if item.strip()
        }
        manifest_source = (
            str(data_manifest.get("data_source") or "").strip().lower()
            if data_manifest is not None
            else ""
        )
        if not profile.data.live_trading_eligible:
            provenance_issues.append("画像未显式声明数据可用于真实交易")
        if data_manifest is None:
            provenance_issues.append("缺少数据 manifest")
        else:
            manifest_profile_id = str(data_manifest.get("profile_id") or "").strip()
            manifest_dataset_id = str(data_manifest.get("dataset_id") or "").strip()
            manifest_live_eligible = data_manifest.get("live_trading_eligible")
            if manifest_profile_id != profile.profile_id:
                provenance_issues.append(
                    f"manifest profile_id 不匹配: {manifest_profile_id or 'N/A'}"
                )
            if manifest_dataset_id != profile.data.dataset_id:
                provenance_issues.append(
                    f"manifest dataset_id 不匹配: {manifest_dataset_id or 'N/A'}"
                )
            if manifest_live_eligible is not True:
                provenance_issues.append(
                    "manifest 未记录生成时的数据实盘资格"
                )
            if not manifest_source:
                provenance_issues.append("manifest 缺少 data_source")
            elif not approved_live_providers:
                provenance_issues.append("未配置真实交易数据提供器白名单")
            elif manifest_source not in approved_live_providers:
                provenance_issues.append(
                    f"数据来源未进入真实交易白名单：{manifest_source}"
                )
            content_sha256 = str(data_manifest.get("content_sha256") or "")
            if len(content_sha256) != 64:
                provenance_issues.append("manifest 缺少有效的数据内容哈希")
            if str(data_manifest.get("manifest_version") or "") != "data_manifest_v2":
                provenance_issues.append("manifest 版本不受支持")

        if provenance_issues:
            _append_check(
                result,
                code="data_provenance",
                status="fail",
                blocking=True,
                message=(
                    "真实券商数据来源门禁未通过："
                    + "；".join(provenance_issues)
                    + "，本次只同步不交易。"
                ),
                broker=normalized_broker,
                configured_provider=profile.data.provider,
                configured_download_provider=profile.data.download.provider,
                live_trading_eligible=profile.data.live_trading_eligible,
                manifest_data_source=manifest_source or None,
                approved_live_data_providers=sorted(approved_live_providers),
            )
        else:
            _append_check(
                result,
                code="data_provenance",
                status="pass",
                blocking=True,
                message=f"真实券商数据来源门禁通过，data_source={manifest_source}。",
                broker=normalized_broker,
                manifest_data_source=manifest_source,
                approved_live_data_providers=sorted(approved_live_providers),
            )

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
        if age < timedelta(0):
            _append_check(
                result,
                code=code,
                status="fail",
                blocking=True,
                message=(
                    f"{label}最新时间 {_format_dt(asof)} 晚于检查时间，"
                    "疑似时钟或时区异常，本次只同步不交易。"
                ),
                latest_asof=_format_dt(asof),
                age_seconds=int(age.total_seconds()),
            )
            continue
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
    equity_ok = (
        equity is not None
        and math.isfinite(float(equity))
        and float(equity) > 0
    )
    broker_state_ok = (
        state_asof is not None
        and state_age_seconds is not None
        and state_age_seconds >= 0
        and state_age_seconds <= max_state_age_seconds
        and equity_ok
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

    if normalized_broker and normalized_broker != "paper":
        available_cash_ok = (
            available_cash is not None
            and math.isfinite(float(available_cash))
            and float(available_cash) >= 0
        )
        if available_cash_ok:
            _append_check(
                result,
                code="broker_available_cash",
                status="pass",
                blocking=True,
                message=f"券商可用资金检查通过，available_cash={float(available_cash):,.2f}。",
                available_cash=float(available_cash),
            )
        else:
            _append_check(
                result,
                code="broker_available_cash",
                status="fail",
                blocking=True,
                message=(
                    f"真实券商可用资金无效，available_cash={available_cash!r}，"
                    "本次只同步不交易。"
                ),
                available_cash=available_cash,
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

    attribution_unavailable = (
        normalized_broker not in {"", "paper"}
        and live_account_attribution is None
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
    if attribution_unavailable:
        _append_check(
            result,
            code="account_anomaly_gate",
            status="fail",
            blocking=True,
            message="真实券商账户归因不可用，无法确认账本与资金状态，本次只同步不交易。",
        )
    elif blocking_alerts:
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

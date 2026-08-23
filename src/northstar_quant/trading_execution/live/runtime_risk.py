"""盘中账户、持仓和行情风险的独立评估。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import math
from typing import Any

from northstar_quant.foundation.common.time import ensure_utc, utc_now
from northstar_quant.foundation.config.settings import Settings
from northstar_quant.trading_execution.execution.models import (
    BrokerStateSnapshot,
    MarketQuoteSnapshot,
)
from northstar_quant.trading_execution.execution.pricing import (
    execution_reference_price_from_quote,
    normalize_symbols,
)


@dataclass(frozen=True, slots=True)
class RuntimeRiskCheck:
    """一项盘中风控检查。"""

    code: str
    status: str
    blocking: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status == "fail"


@dataclass(frozen=True, slots=True)
class RuntimeRiskAssessment:
    """一次可持久化的盘中风险结论。"""

    profile_id: str
    broker: str
    account: str | None
    checked_at: datetime
    checks: tuple[RuntimeRiskCheck, ...]

    @property
    def can_submit(self) -> bool:
        return not any(check.failed and check.blocking for check in self.checks)

    @property
    def blocking_checks(self) -> tuple[RuntimeRiskCheck, ...]:
        return tuple(
            check for check in self.checks if check.failed and check.blocking
        )

    @property
    def warning_checks(self) -> tuple[RuntimeRiskCheck, ...]:
        return tuple(check for check in self.checks if check.status == "warn")

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "broker": self.broker,
            "account": self.account,
            "checked_at": self.checked_at.isoformat(),
            "can_submit": self.can_submit,
            "blocking_failure_count": len(self.blocking_checks),
            "warning_count": len(self.warning_checks),
            "blocking_messages": [
                check.message for check in self.blocking_checks
            ],
            "warning_messages": [
                check.message for check in self.warning_checks
            ],
            "checks": [asdict(check) for check in self.checks],
        }


def runtime_risk_symbols(
    state: BrokerStateSnapshot,
    target_symbols: list[str] | None = None,
) -> list[str]:
    """返回持仓、挂单和待执行目标涉及的全部标的。"""

    return normalize_symbols(
        [
            *(target_symbols or []),
            *(item.symbol for item in state.positions),
            *(str(row.get("symbol") or "") for row in state.open_orders),
        ]
    )


def _number(account_values: dict[str, float | str], *keys: str) -> float | None:
    for key in keys:
        value = account_values.get(key)
        if value is None:
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(parsed):
            return parsed
    return None


def _age_seconds(value: datetime | None, checked_at: datetime) -> float | None:
    if value is None:
        return None
    return (checked_at - ensure_utc(value)).total_seconds()


def _check(
    checks: list[RuntimeRiskCheck],
    *,
    code: str,
    status: str,
    blocking: bool,
    message: str,
    **details: Any,
) -> None:
    checks.append(
        RuntimeRiskCheck(
            code=code,
            status=status,
            blocking=blocking,
            message=message,
            details=details,
        )
    )


def assess_runtime_risk(
    *,
    profile_id: str,
    broker: str,
    account: str | None,
    state: BrokerStateSnapshot,
    quotes: list[MarketQuoteSnapshot],
    required_symbols: list[str],
    settings: Settings,
    checked_at: datetime | None = None,
) -> RuntimeRiskAssessment:
    """评估盘中账户和行情状态，不生成订单，也不自动平仓。"""

    now = ensure_utc(checked_at or utc_now())
    normalized_broker = str(broker).strip().lower()
    is_real_broker = normalized_broker != "paper"
    checks: list[RuntimeRiskCheck] = []

    _check(
        checks,
        code="kill_switch",
        status="fail" if settings.kill_switch_enabled else "pass",
        blocking=True,
        message=(
            "KILL_SWITCH_ENABLED: 交易停止开关已开启。"
            if settings.kill_switch_enabled
            else "交易停止开关未开启。"
        ),
    )

    state_issues = list(state.state_errors)
    if not state.state_complete:
        state_issues.append("券商状态未声明完整")
    state_age = _age_seconds(state.asof, now)
    if state_age is None:
        state_issues.append("券商状态缺少时间戳")
    elif state_age < -5:
        state_issues.append(f"券商状态时间位于未来 {abs(state_age):.1f} 秒")
    elif state_age > settings.runtime_risk_max_state_age_seconds:
        state_issues.append(f"券商状态已过期 {state_age:.1f} 秒")
    _check(
        checks,
        code="broker_state",
        status="fail" if state_issues else "pass",
        blocking=True,
        message=(
            "BROKER_STATE_UNSAFE: " + "；".join(state_issues)
            if state_issues
            else "券商账户状态完整且新鲜。"
        ),
        state_age_seconds=state_age,
        state_complete=state.state_complete,
    )

    equity = _number(
        state.account_values,
        "NetLiquidation",
        "EquityWithLoanValue",
        "Balance",
        "DynamicEquity",
    )
    available = _number(
        state.account_values,
        "AvailableFunds",
        "Available",
        "CashBalance",
        "BuyingPower",
    )
    account_issues: list[str] = []
    if equity is None or equity <= 0:
        account_issues.append("账户权益缺失或不为正")
    if available is None:
        account_issues.append("可用资金缺失")
    elif available < 0:
        account_issues.append("可用资金为负")
    available_ratio = (
        available / equity
        if equity is not None and equity > 0 and available is not None
        else None
    )
    if (
        available_ratio is not None
        and available_ratio < settings.runtime_risk_min_available_funds_ratio
    ):
        account_issues.append(
            "可用资金比例过低："
            f"{available_ratio:.2%} < "
            f"{settings.runtime_risk_min_available_funds_ratio:.2%}"
        )
    _check(
        checks,
        code="account_funds",
        status="fail" if account_issues else "pass",
        blocking=True,
        message=(
            "ACCOUNT_FUNDS_UNSAFE: " + "；".join(account_issues)
            if account_issues
            else "账户权益与可用资金检查通过。"
        ),
        equity=equity,
        available_funds=available,
        available_funds_ratio=available_ratio,
    )

    margin = _number(
        state.account_values,
        "CurrMargin",
        "CurrentMargin",
        "InitialMargin",
        "InitialMarginRequirement",
        "MaintMarginReq",
        "Margin",
    )
    margin_ratio = (
        margin / equity
        if margin is not None and equity is not None and equity > 0
        else None
    )
    if margin_ratio is None and state.positions:
        margin_status = "fail" if is_real_broker else "warn"
        margin_message = "MARGIN_DATA_REQUIRED: 持仓账户缺少保证金占用数据。"
    elif (
        margin_ratio is not None
        and margin_ratio > settings.runtime_risk_max_margin_ratio
    ):
        margin_status = "fail"
        margin_message = (
            "MARGIN_RATIO_EXCEEDED: 保证金占用比例 "
            f"{margin_ratio:.2%} > {settings.runtime_risk_max_margin_ratio:.2%}。"
        )
    else:
        margin_status = "pass"
        margin_message = "保证金占用检查通过。"
    _check(
        checks,
        code="margin_usage",
        status=margin_status,
        blocking=True,
        message=margin_message,
        margin=margin,
        margin_ratio=margin_ratio,
    )

    invalid_positions = [
        item.symbol
        for item in state.positions
        if not math.isfinite(float(item.qty))
    ]
    _check(
        checks,
        code="positions",
        status="fail" if invalid_positions else "pass",
        blocking=True,
        message=(
            "POSITION_STATE_INVALID: 持仓数量异常："
            + ", ".join(sorted(invalid_positions))
            if invalid_positions
            else f"持仓状态检查通过，position_count={len(state.positions)}。"
        ),
        position_count=len(state.positions),
        invalid_symbols=sorted(invalid_positions),
    )

    open_order_count = len(state.open_orders)
    open_order_exceeded = (
        open_order_count > settings.runtime_risk_max_open_orders
    )
    _check(
        checks,
        code="open_orders",
        status="fail" if open_order_exceeded else "pass",
        blocking=True,
        message=(
            "OPEN_ORDER_LIMIT_EXCEEDED: 活跃订单数 "
            f"{open_order_count} > {settings.runtime_risk_max_open_orders}。"
            if open_order_exceeded
            else f"活跃订单数量检查通过，open_order_count={open_order_count}。"
        ),
        open_order_count=open_order_count,
        max_open_orders=settings.runtime_risk_max_open_orders,
    )

    quote_by_symbol = {
        str(quote.symbol).strip().upper(): quote
        for quote in quotes
        if str(quote.symbol).strip()
    }
    quote_issues: list[str] = []
    for symbol in normalize_symbols(required_symbols):
        quote = quote_by_symbol.get(symbol)
        if quote is None:
            quote_issues.append(f"{symbol} 缺少券商行情")
            continue
        if execution_reference_price_from_quote(quote) is None:
            quote_issues.append(f"{symbol} 缺少有效价格")
        quote_age = _age_seconds(quote.asof, now)
        if quote_age is None:
            quote_issues.append(f"{symbol} 行情缺少时间戳")
        elif quote_age < -5:
            quote_issues.append(f"{symbol} 行情时间位于未来")
        elif quote_age > settings.runtime_risk_max_quote_age_seconds:
            quote_issues.append(f"{symbol} 行情已过期 {quote_age:.1f} 秒")
        bid = float(quote.bid) if quote.bid is not None else None
        ask = float(quote.ask) if quote.ask is not None else None
        if bid is not None and ask is not None:
            if (
                not math.isfinite(bid)
                or not math.isfinite(ask)
                or bid <= 0
                or ask <= 0
            ):
                quote_issues.append(f"{symbol} 买卖价非法")
            elif bid > ask:
                quote_issues.append(f"{symbol} 买价高于卖价")
            else:
                spread_bps = (ask - bid) / ((ask + bid) * 0.5) * 10000.0
                if spread_bps > settings.runtime_risk_max_quote_spread_bps:
                    quote_issues.append(
                        f"{symbol} 买卖价差 {spread_bps:.1f} bps 超限"
                    )

    if quote_issues and not is_real_broker:
        quote_status = "warn"
    else:
        quote_status = "fail" if quote_issues else "pass"
    _check(
        checks,
        code="market_quotes",
        status=quote_status,
        blocking=True,
        message=(
            "MARKET_QUOTE_UNSAFE: " + "；".join(quote_issues)
            if quote_issues
            else "目标、持仓和挂单标的的实时行情检查通过。"
        ),
        required_symbols=normalize_symbols(required_symbols),
        issue_count=len(quote_issues),
    )

    return RuntimeRiskAssessment(
        profile_id=str(profile_id).strip(),
        broker=normalized_broker,
        account=str(account).strip() if account else None,
        checked_at=now,
        checks=tuple(checks),
    )

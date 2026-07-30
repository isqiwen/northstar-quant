"""订单幂等身份生成。

这里的身份必须只依赖稳定业务字段，不能包含进程号、随机数或当前时间。
同一执行计划的同一次尝试在进程重启后应生成完全相同的值。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json


def build_order_ref(plan_id: str, attempt_no: int) -> str:
    """生成可发送给券商的稳定订单引用。"""

    normalized_plan_id = str(plan_id or "").strip()
    if not normalized_plan_id:
        raise ValueError("生成 orderRef 前必须提供 plan_id。")
    normalized_attempt = int(attempt_no)
    if normalized_attempt < 1:
        raise ValueError("生成 orderRef 时 attempt_no 必须大于等于 1。")
    digest = sha256(
        f"{normalized_plan_id}:{normalized_attempt}".encode("utf-8")
    ).hexdigest()
    return f"NSQ-{digest[:24]}"


def build_order_idempotency_key(
    *,
    broker: str,
    account: str,
    plan_id: str,
    attempt_no: int,
) -> str:
    """生成数据库侧幂等键。

    券商和账户必须进入摘要，避免不同账户之间错误复用订单结果。
    """

    normalized_broker = str(broker or "").strip().lower()
    normalized_account = str(account or "").strip()
    if not normalized_broker:
        raise ValueError("生成订单幂等键前必须提供 broker。")
    if not normalized_account:
        raise ValueError("生成订单幂等键前必须提供 account。")
    order_ref = build_order_ref(plan_id, attempt_no)
    digest = sha256(
        f"v1:{normalized_broker}:{normalized_account}:{order_ref}".encode("utf-8")
    ).hexdigest()
    return f"nsq-v1-{digest}"


def build_order_request_fingerprint(
    *,
    account: str,
    strategy_id: str,
    symbol: str,
    side: str,
    qty: float,
    order_type: str,
    limit_price: float | None,
    plan_id: str,
    attempt_no: int,
    instrument_id: str | None = None,
    exchange_id: str | None = None,
    ctp_offset: str | None = None,
    volume_multiple: int | None = None,
    margin_rate: float | None = None,
    currency: str | None = None,
    execution_policy_fingerprint: str | None = None,
) -> str:
    """为实际券商载荷生成稳定摘要，防止同一幂等键复用不同订单。"""

    payload = {
        "account": str(account).strip(),
        "strategy_id": str(strategy_id).strip(),
        "symbol": str(symbol).strip().upper(),
        "side": str(side).strip().upper(),
        "qty": _canonical_decimal(qty),
        "order_type": str(order_type).strip().upper(),
        "limit_price": (
            _canonical_decimal(limit_price)
            if limit_price is not None
            else None
        ),
        "plan_id": str(plan_id).strip(),
        "attempt_no": int(attempt_no),
        "instrument_id": (
            str(instrument_id).strip().lower()
            if instrument_id is not None
            else None
        ),
        "exchange_id": (
            str(exchange_id).strip().upper()
            if exchange_id is not None
            else None
        ),
        "ctp_offset": (
            str(ctp_offset).strip().lower()
            if ctp_offset is not None
            else None
        ),
        "volume_multiple": (
            int(volume_multiple)
            if volume_multiple is not None
            else None
        ),
        "margin_rate": (
            _canonical_decimal(margin_rate)
            if margin_rate is not None
            else None
        ),
        "currency": str(currency).strip().upper() if currency is not None else None,
        "execution_policy_fingerprint": (
            str(execution_policy_fingerprint).strip()
            if execution_policy_fingerprint is not None
            else None
        ),
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def build_chase_policy_fingerprint(
    *,
    max_steps: int,
    fallback_mode: str,
    limit_price_offset_bps: float,
) -> str:
    """锁定同一执行 plan 可生成的 attempt 集合与限价阶梯语义。"""

    normalized_max_steps = int(max_steps)
    normalized_fallback_mode = str(fallback_mode or "").strip().lower()
    if normalized_max_steps < 1:
        raise ValueError("追价策略 max_steps 必须大于等于 1。")
    if normalized_fallback_mode not in {"cancel", "market"}:
        raise ValueError("追价策略 fallback_mode 必须是 cancel 或 market。")
    payload = {
        "version": 1,
        "max_steps": normalized_max_steps,
        "fallback_mode": normalized_fallback_mode,
        "limit_price_offset_bps": _canonical_decimal(
            limit_price_offset_bps
        ),
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def _canonical_decimal(value: float) -> str:
    """把数量和价格规范为不丢精度的十进制文本。"""

    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"订单数值无法规范化：{value!r}") from exc
    if not decimal_value.is_finite():
        raise ValueError(f"订单数值必须是有限数：{value!r}")
    normalized = decimal_value.normalize()
    return format(normalized, "f")


def build_execution_batch_id(
    *,
    broker: str,
    account: str,
    profile_id: str,
    strategy_id: str,
    output_asof: object,
) -> str:
    """按策略输出周期生成重启后稳定的执行批次身份。"""

    components = (
        str(broker).strip().lower(),
        str(account).strip(),
        str(profile_id).strip(),
        str(strategy_id).strip(),
        str(output_asof).strip(),
    )
    if any(not value for value in components):
        raise ValueError("生成执行批次身份所需字段不能为空。")
    digest = sha256(":".join(components).encode("utf-8")).hexdigest()
    return f"order-batch-{digest[:20]}"


def build_execution_plan_id(
    *,
    batch_id: str,
    strategy_id: str,
    symbol: str,
    side: str,
    order_semantic: str | None,
    ctp_offset: str | None = None,
) -> str:
    """生成与列表顺序无关的稳定执行计划身份。"""

    identity = ":".join(
        (
            str(batch_id).strip(),
            str(strategy_id).strip(),
            str(symbol).strip().upper(),
            str(side).strip().upper(),
            str(order_semantic or "").strip().lower(),
            str(ctp_offset or "").strip().lower(),
        )
    )
    return f"plan-{sha256(identity.encode('utf-8')).hexdigest()[:24]}"

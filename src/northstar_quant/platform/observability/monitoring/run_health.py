"""运行健康度查询与滚动汇总。"""

from __future__ import annotations

from datetime import datetime, timedelta

from northstar_quant.platform.common.time import utc_now
from northstar_quant.platform.db.repositories import count_anomaly_events, list_run_health_records
from northstar_quant.platform.db.session import SessionLocal


def anomaly_trend(current_count: int, previous_count: int) -> str:
    """比较相邻窗口的异常事件趋势。"""

    if current_count < previous_count:
        return "down"
    if current_count > previous_count:
        return "up"
    return "flat"


def soak_summary(
    *,
    days: int = 28,
    limit: int = 20,
    profile_id: str | None = None,
    account: str | None = None,
    mode: str | None = None,
    session_factory=SessionLocal,
    now: datetime | None = None,
) -> dict:
    """汇总最近一段时间的 soak / shadow 运行稳定性。"""

    normalized_days = max(int(days), 1)
    normalized_limit = max(int(limit), 1)
    now = now or utc_now()
    since = now - timedelta(days=normalized_days)
    current_window_start = now - timedelta(days=7)
    previous_window_start = current_window_start - timedelta(days=7)

    with session_factory() as session:
        rows = list_run_health_records(
            session,
            limit=1000,
            profile_id=profile_id,
            account=account,
            mode=mode,
            since=since,
        )
        latest_rows = list_run_health_records(
            session,
            limit=normalized_limit,
            profile_id=profile_id,
            account=account,
            mode=mode,
        )
        anomaly_recent_7d = count_anomaly_events(
            session,
            profile_id=profile_id,
            account=account,
            start_at=current_window_start,
            end_at=now,
        )
        anomaly_prev_7d = count_anomaly_events(
            session,
            profile_id=profile_id,
            account=account,
            start_at=previous_window_start,
            end_at=current_window_start,
        )

    abs_shortfall_bps = [
        abs(float(row.execution_shortfall_bps))
        for row in rows
        if row.execution_shortfall_bps is not None
    ]
    abs_residuals = [
        abs(float(row.residual_pnl))
        for row in rows
        if row.residual_pnl is not None
    ]
    return {
        "profile_id": profile_id,
        "account": account,
        "mode": mode or "all",
        "days": normalized_days,
        "run_count": len(rows),
        "preflight_pass_count": sum(1 for row in rows if row.preflight_can_trade),
        "blocked_run_count": sum(1 for row in rows if not row.preflight_can_trade),
        "plan_consistency_issue_run_count": sum(
            1 for row in rows if int(row.plan_consistency_issue_count or 0) > 0
        ),
        "open_order_run_count": sum(
            1 for row in rows if int(row.open_order_count or 0) > 0
        ),
        "partial_fill_run_count": sum(
            1 for row in rows if int(row.partial_fill_count or 0) > 0
        ),
        "avg_abs_execution_shortfall_bps": (
            sum(abs_shortfall_bps) / len(abs_shortfall_bps)
            if abs_shortfall_bps
            else None
        ),
        "avg_abs_residual_pnl": (
            sum(abs_residuals) / len(abs_residuals) if abs_residuals else None
        ),
        "anomaly_events_recent_7d": anomaly_recent_7d,
        "anomaly_events_prev_7d": anomaly_prev_7d,
        "anomaly_trend": anomaly_trend(anomaly_recent_7d, anomaly_prev_7d),
        "latest_runs": [
            {
                "created_at": row.created_at.isoformat(),
                "run_id": row.run_id,
                "mode": row.mode,
                "preflight_can_trade": row.preflight_can_trade,
                "execution_plan_count": row.execution_plan_count,
                "plan_consistency_issue_count": row.plan_consistency_issue_count,
                "open_order_count": row.open_order_count,
                "partial_fill_count": row.partial_fill_count,
                "execution_shortfall_bps": row.execution_shortfall_bps,
                "residual_pnl": row.residual_pnl,
                "anomaly_trend": row.anomaly_trend,
            }
            for row in latest_rows
        ],
    }

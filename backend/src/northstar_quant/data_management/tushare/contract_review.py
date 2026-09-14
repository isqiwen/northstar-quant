"""Review an entire real contract against all supported native data requirements.

This is a read projection of retained evidence, never a new publication authority.
Unknown session/applicability evidence cannot qualify a contract or authorize deletion.
"""

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import Connection, Engine, text

from ..exploration.instruments import display_name
from .catalog import DATASETS, Dataset
from .planning import target_day


def review(engine: Engine, scope: str) -> dict[str, Any]:
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
        contract = (
            c.execute(
                text("SELECT * FROM data_sync_contracts WHERE ts_code=:scope"), {"scope": scope}
            )
            .mappings()
            .one_or_none()
        )
        if contract is None:
            raise LookupError("合约目录不存在")
        if contract["kind"] != "1":
            raise ValueError("全生命周期验收以真实合约为单位；连续序列不能代替真实合约")
        details = contract["details"]
        start = _date(details.get("list_date"))
        expiry = _date(details.get("delist_date"))
        target = target_day()
        end = min(expiry, target) if expiry else target
        reasons = []
        if start is None or expiry is None or start > end:
            reasons.append("上市或退市日期缺失/无效，不能确定完整生命周期")
        calendar_complete = False
        if start is not None and start <= end:
            calendar = (
                c.execute(
                    text("""SELECT count(*) AS days,
                max(cal_date) FILTER (WHERE is_open) AS last_open
                FROM data_sync_calendar WHERE exchange=:exchange
                AND cal_date BETWEEN :start AND :end"""),
                    dict(exchange=contract["exchange"], start=start, end=end),
                )
                .mappings()
                .one()
            )
            calendar_complete = calendar["days"] == (end - start).days + 1
            if not calendar_complete:
                reasons.append("交易日历未覆盖整个生命周期；最近已完成交易日尚不能确认")
            elif calendar["last_open"] is not None:
                end = calendar["last_open"]
            else:
                reasons.append("生命周期内没有已确认交易日")
        requirements = []
        for dataset in DATASETS:
            scopes = _scopes(c, dataset, contract)
            item = _requirement(c, dataset, scopes, start, end)
            if dataset.key == "contracts":
                item.update(
                    status="RECEIVED", reason="合约元数据已收到；上市及退市范围仍需上述核对"
                )
            elif dataset.key == "calendar":
                item.update(
                    status="VERIFIED" if calendar_complete else "UNKNOWN",
                    reason="按交易所逐自然日核对日历，包括休市日",
                )
            requirements.append(item)
        # Successful transport/row checks cannot establish sessions, native periods,
        # product reporting obligations or index applicability. Keep these explicit.
        reasons.extend(
            [
                "分钟行情：缺少全生命周期历史交易时段及供应商时间标签的已核实证据",
                "周/月线及品种级数据：应有记录、发布日与适用范围尚未完成核验",
                "主力映射、复权序列及市场指数保持独立身份；关联完整性尚未完成核验",
            ]
        )
        invalid = any(r["status"] == "INVALID" for r in requirements)
        return {
            "scope": scope,
            "display_name": display_name(
                scope, details.get("name"), contract["exchange"], contract["product"]
            ),
            "exchange": contract["exchange"],
            "product": contract["product"],
            "listing_date": start.isoformat() if start else None,
            "delisting_date": expiry.isoformat() if expiry else None,
            "required_end": end.isoformat() if calendar_complete else None,
            "status": "INVALID" if invalid else "VERIFICATION_PENDING",
            "admitted": False,
            "requirements": requirements,
            "reasons": reasons,
            "policy": (
                "全部已支持数据集参与核验；未核实适用性不算豁免。"
                "已下载、空响应和请求完成都不等于整合约完整。此报告不执行数据清理。"
            ),
        }


def _date(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) != 8:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        return None


def _scopes(c: Connection, dataset: Dataset, contract: Any) -> list[str]:
    if dataset.scope in {"catalog", "calendar"}:
        return [contract["exchange"]]
    if dataset.scope == "contract":
        return [contract["ts_code"]]
    if dataset.scope == "product":
        return [f"{contract['exchange']}:{contract['product']}"]
    if dataset.scope == "continuous":
        return list(
            c.scalars(
                text("""SELECT ts_code FROM data_sync_contracts
            WHERE exchange=:exchange AND product=:product AND kind='2' ORDER BY ts_code"""),
                dict(exchange=contract["exchange"], product=contract["product"]),
            )
        )
    from .catalog import NANHUA_CODES

    return list(NANHUA_CODES)


def _requirement(
    c: Connection, dataset: Dataset, scopes: list[str], start: date | None, end: date
) -> dict[str, Any]:
    result: dict[str, Any] = dict(
        dataset=dataset.key,
        label=dataset.label,
        native_scope=dataset.scope,
        scopes=scopes,
        status="UNKNOWN",
        received=0,
        reason="尚无完整生命周期的记录核验依据",
    )
    if start is None or end < start or not scopes:
        return result
    records = (
        c.execute(
            text("""SELECT j.scope,j.start_at,j.end_at,j.status,
        r.receipt_id,v.receipt_id AS covered_receipt,
        coalesce((r.quality->>'excluded_rows')::integer,0)>0 AS has_exclusions,
        coalesce((a.quality->>'issue_count')::integer,0)>0 AS has_failures
        FROM data_sync_jobs j LEFT JOIN data_sync_receipts r ON r.receipt_id=j.receipt_id
        LEFT JOIN data_sync_coverage v ON v.request_id=j.request_id
        LEFT JOIN data_sync_attempts a ON a.generation=j.generation
        WHERE j.dataset=:dataset AND j.scope=ANY(:scopes) AND j.status<>'SPLIT'
        AND (j.start_at='' OR (j.start_at<=:end AND j.end_at>=:start))
        ORDER BY j.scope,j.start_at,j.end_at"""),
            dict(dataset=dataset.key, scopes=scopes, start=start.isoformat(), end=end.isoformat()),
        )
        .mappings()
        .all()
    )
    result["received"] = sum(r["receipt_id"] is not None for r in records)
    if any(
        r["has_exclusions"] or (r["status"] == "BLOCKED" and r["has_failures"]) for r in records
    ):
        result.update(status="INVALID", reason="当前响应存在异常行或旧部分发布，不能通过整合约验收")
        return result
    if dataset.scope in {"catalog", "calendar"}:
        return result
    for scope in scopes:
        cursor = start
        for r in records:
            if (
                r["scope"] != scope
                or r["status"] != "VALIDATED"
                or r["receipt_id"] is None
                or r["covered_receipt"] != r["receipt_id"]
            ):
                continue
            left, right = date.fromisoformat(r["start_at"]), date.fromisoformat(r["end_at"])
            if left <= cursor:
                cursor = max(cursor, right + timedelta(days=1))
        if cursor <= end:
            result.update(
                status="COLLECTING",
                reason=f"{scope} 从 {cursor} 起尚无连续已校验响应；不能据此确认源端缺失",
            )
            return result
    result.update(status="RECEIVED", reason="生命周期请求范围已覆盖；还需核验应有记录和字段语义")
    return result

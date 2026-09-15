"""Review an entire real contract against all supported native data requirements.

This is a read projection of retained evidence, never a new publication authority.
Unknown session/applicability evidence cannot qualify a contract or authorize deletion.
"""

from datetime import date, timedelta
from typing import Any

from sqlalchemy import Connection, Engine, text

from ..contract_data import minute_review, record_review, weekly_review
from ..contract_data.lifecycle import completed, describe
from ..contract_data.requirements import classify, record_checks, requirement
from ..exploration.instruments import display_name
from .catalog import DATASETS, Dataset


def review(engine: Engine, scope: str) -> dict[str, Any]:
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as c, c.begin():
        return review_connection(c, scope)


def review_connection(c: Connection, scope: str) -> dict[str, Any]:
    contract = (
        c.execute(text("SELECT * FROM data_sync_contracts WHERE ts_code=:scope"), {"scope": scope})
        .mappings()
        .one_or_none()
    )
    if contract is None:
        raise LookupError("合约目录不存在")
    if contract["kind"] != "1":
        raise ValueError("全生命周期验收以真实合约为单位；连续序列不能代替真实合约")
    details = contract["details"]
    facts = describe(contract)
    profile = classify(contract)
    facts["contract_type"] = profile.public()
    reasons = []
    try:
        lifetime = completed(contract)
    except ValueError as error:
        return dict(
            scope=scope,
            display_name=display_name(
                scope, details.get("name"), contract["exchange"], contract["product"]
            ),
            exchange=contract["exchange"],
            product=contract["product"],
            **facts,
            required_end=None,
            status="UNKNOWN" if facts["lifecycle_status"] == "UNKNOWN" else "NOT_ELIGIBLE",
            admitted=False,
            requirements=[],
            reasons=[str(error)],
            policy="仅最后交易日和最后交割日均已完成的真实合约参与下载及发布",
        )
    start, end = lifetime.start, lifetime.end
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
            reasons.append("交易日历未覆盖上市至最后交易日")
        elif calendar["last_open"] is not None:
            end = calendar["last_open"]
        else:
            reasons.append("生命周期内没有已确认交易日")
    requirements = []
    for dataset in DATASETS:
        rule = requirement(profile, dataset.key)
        if not rule.collect:
            requirements.append(
                dict(
                    dataset=dataset.key,
                    label=dataset.label,
                    native_scope=dataset.scope,
                    scopes=[],
                    received=0,
                    applicability=rule.applicability,
                    status=rule.applicability,
                    reason=rule.reason,
                    reference=rule.reference,
                )
            )
            continue
        scopes = _scopes(c, dataset, contract)
        item = _requirement(c, dataset, scopes, start, end, scope)
        item.update(applicability=rule.applicability, reference=rule.reference)
        if item["status"] == "RECEIVED":
            if calendar_complete and dataset.key == "weekly_detail":
                item.update(
                    weekly_review.verify(
                        c, scope, contract["exchange"], contract["product"], start, end
                    )
                )
            elif calendar_complete and dataset.key in record_review.SUPPORTED:
                item.update(
                    record_review.verify(c, scope, contract["exchange"], dataset.key, start, end)
                )
            elif calendar_complete and dataset.key.endswith("min"):
                item.update(
                    minute_review.inspect(c, scope, contract["exchange"], dataset.key, start, end)
                )
            else:
                item["reason"] = "生命周期请求已覆盖；" + record_checks(dataset.key, profile)
        if dataset.key == "contracts":
            item.update(
                status="VERIFIED" if profile.category != "UNKNOWN" else "UNKNOWN",
                reason="真实合约类型与生命周期元数据已核对"
                if profile.category != "UNKNOWN"
                else profile.basis,
            )
        elif dataset.key == "calendar":
            item.update(
                status="VERIFIED" if calendar_complete else "UNKNOWN",
                reason="按交易所逐自然日核对日历，包括休市日",
            )
        requirements.append(item)
    if profile.category == "UNKNOWN":
        reasons.append(profile.basis)
    invalid = any(r["status"] == "INVALID" for r in requirements)
    # Lead with observed failures/unfinished collection, not a universal rule disclaimer.
    reasons = [
        f"{r['label']}：{r['reason']}"
        for r in sorted(requirements, key=lambda r: r["status"] != "INVALID")
        if r["status"] not in {"VERIFIED", "NOT_APPLICABLE", "RELATED"}
    ] + reasons
    admitted = not reasons and all(
        r["status"] in {"VERIFIED", "NOT_APPLICABLE", "RELATED"} for r in requirements
    )
    return {
        "scope": scope,
        "display_name": display_name(
            scope, details.get("name"), contract["exchange"], contract["product"]
        ),
        "exchange": contract["exchange"],
        "product": contract["product"],
        **facts,
        "required_end": end.isoformat() if calendar_complete else None,
        "status": "INVALID" if invalid else "VERIFIED" if admitted else "VERIFICATION_PENDING",
        "admitted": admitted,
        "requirements": requirements,
        "reasons": reasons,
        "policy": (
            "按合约类型核验全部适用必需数据；已确认不适用的资料和独立研究序列不阻塞发布。"
            "适用性未知仍阻塞发布，且不盲目发起请求。"
            "已下载、空响应和请求完成都不等于整合约完整。此报告不执行数据清理。"
        ),
    }


def _scopes(c: Connection, dataset: Dataset, contract: Any) -> list[str]:
    if dataset.scope in {"catalog", "calendar"}:
        return [contract["exchange"]]
    if dataset.scope == "contract":
        return [contract["ts_code"]]
    if dataset.scope == "product":
        return [f"{contract['exchange']}:{contract['product']}"]
    raise ValueError("独立研究序列不能作为真实合约的必需请求")


def _requirement(
    c: Connection, dataset: Dataset, scopes: list[str], start: date | None, end: date, owner: str
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
        r.receipt_id,v.receipt_id AS covered_receipt,j.error,a.quality AS attempt_quality,
        coalesce((r.quality->>'excluded_rows')::integer,0)>0 AS has_exclusions,
        coalesce((a.quality->>'issue_count')::integer,0)>0 AS has_failures
        FROM data_sync_jobs j LEFT JOIN data_sync_receipts r ON r.receipt_id=j.receipt_id
        LEFT JOIN data_sync_coverage v ON v.request_id=j.request_id
        LEFT JOIN data_sync_attempts a ON a.generation=j.generation
        WHERE j.dataset=:dataset AND j.scope=ANY(:scopes) AND j.status<>'SPLIT'
        AND EXISTS (SELECT 1 FROM data_contract_requests cr
                    WHERE cr.request_id=j.request_id AND cr.scope=:owner)
        AND (j.start_at='' OR (j.start_at<=:end AND j.end_at>=:start))
        ORDER BY j.scope,j.start_at,j.end_at"""),
            dict(
                dataset=dataset.key,
                scopes=scopes,
                start=start.isoformat(),
                end=end.isoformat(),
                owner=owner,
            ),
        )
        .mappings()
        .all()
    )
    result["received"] = sum(r["receipt_id"] is not None for r in records)
    for record in records:
        if record["has_exclusions"] or (record["status"] == "BLOCKED" and record["has_failures"]):
            quality = record["attempt_quality"] or {}
            issues = quality.get("issues") or []
            detail = issues[0]["reason"] if issues else "响应存在被排除的异常行"
            result.update(
                status="INVALID",
                reason=f"{record['scope']} {record['start_at']} 至 {record['end_at']}："
                f"{detail}；异常 {quality.get('issue_count', '未知')} 行；"
                "响应已收到但未通过校验，不代表源端缺少行情",
            )
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
                reason=(
                    f"{scope} 从 {cursor} 起尚无连续已校验响应；不能据此确认源端缺失"
                    + (
                        "。Tushare 概述声明结算参数从 2012-01 开始，但已观察到更早的品种记录；"
                        "须按本合约实际响应核查，不能据概述判定此前永久缺失或缩短合约历史"
                        if dataset.key == "settlement" and cursor < date(2012, 1, 1)
                        else ""
                    )
                ),
            )
            return result
    result.update(status="RECEIVED", reason="生命周期请求范围已覆盖；还需核验应有记录和字段语义")
    return result

"""Compare reported order transitions with independently recorded individual fills."""

from typing import Any

from northstar_quant.broker.order_reports import decode_order, order_key, order_observations


def _terms(order: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in order.items()
        if key
        not in {
            "reported_traded_lots",
            "reported_remaining_lots",
            "order_state",
            "submit_state",
            "active",
            "problems",
            "client_identity",
        }
    }


def inspect_orders(
    check: dict[str, Any], batches: list[dict[str, Any]], fills: list[dict[str, Any]]
) -> dict[str, Any]:
    """Use fixed query/stream sources and a final independent query, never newer fills.

    Stream callbacks have their original local order, unlike rows gathered by a
    query. Absence in a stream segment is not an absent queried order. All observed
    states remain visible, including those preceding the latest cumulative value.
    """
    latest = batches[-1]
    problems = list(check["problems"])
    observations: dict[str, list[dict[str, Any]]] = {}
    previous: dict[str, dict[str, Any]] = {}
    latest_orders: dict[str, dict[str, Any]] = {}
    queried_latest: set[str] = set()
    aliases: dict[tuple[object, ...], str] = {}
    unresolved = []
    count = 0
    for batch in batches:
        current: dict[str, dict[str, Any]] = {}
        for locator, raw in order_observations(batch):
            count += 1
            if count > 10000:
                raise ValueError("order review exceeds 10000 bounded observations")
            try:
                order = decode_order(raw, batch)
            except ValueError:
                unresolved.append({**locator, "reported_fields": raw})
                problems.append({"code": "ORDER_FIELDS_NOT_CONFIRMED", **locator})
                continue
            key = order["order_id"]
            if batch is latest and locator["callback"] == "OnRspQryOrder":
                queried_latest.add(key)
            observations.setdefault(key, []).append(
                {
                    **locator,
                    "order_state": order["order_state"],
                    "submit_state": order["submit_state"],
                    "reported_traded_lots": order["reported_traded_lots"],
                    "reported_remaining_lots": order["reported_remaining_lots"],
                }
            )
            for problem in order["problems"]:
                problems.append({**problem, "order_id": key, **locator})
            earlier = current.get(key)
            if earlier is not None and earlier != order and "stream_id" not in batch:
                problems.append(
                    {"code": "ORDER_OBSERVATIONS_AMBIGUOUS", "order_id": key, **locator}
                )
                # Keep the first observation; neither arrival order nor maxima resolve ambiguity.
                continue
            client = order["client_identity"]
            if client is not None:
                alias = tuple(client)
                if alias in aliases and aliases[alias] != key:
                    problems.append(
                        {"code": "CLIENT_ORDER_IDENTITY_CONFLICT", "order_id": key, **locator}
                    )
                else:
                    aliases[alias] = key
            prior = earlier if "stream_id" in batch and earlier is not None else previous.get(key)
            if prior is not None:
                if _terms(prior) != _terms(order):
                    problems.append({"code": "ORDER_IDENTITY_CONFLICT", "order_id": key, **locator})
                if (
                    prior["client_identity"] is not None
                    and client is not None
                    and prior["client_identity"] != client
                ):
                    problems.append(
                        {"code": "CLIENT_ORDER_IDENTITY_CHANGED", "order_id": key, **locator}
                    )
                if prior["reported_traded_lots"] > order["reported_traded_lots"]:
                    problems.append(
                        {"code": "ORDER_CUMULATIVE_VOLUME_REGRESSED", "order_id": key, **locator}
                    )
                if prior["active"] is False and (
                    order["active"] is not False
                    or (prior["order_state"], prior["reported_traded_lots"])
                    != (order["order_state"], order["reported_traded_lots"])
                ):
                    problems.append(
                        {"code": "ORDER_TERMINAL_STATE_CHANGED", "order_id": key, **locator}
                    )
            current[key] = order
        for key in sorted(set(previous) - set(current)) if "stream_id" not in batch else ():
            problems.append(
                {
                    "code": "PREVIOUS_ORDER_MISSING_FROM_QUERY",
                    "order_id": key,
                    "source_batch_id": batch["batch_id"],
                }
            )
        if batch is latest:
            latest_orders = current
        previous.update(current)
    missing = set(latest_orders) - queried_latest
    for key in sorted(missing):
        problems.append({"code": "PREVIOUS_ORDER_MISSING_FROM_QUERY", "order_id": key})

    linked: dict[str, list[dict[str, Any]]] = {}
    unlinked = []
    for fill in fills:
        key = order_key(fill["exchange"], fill["order_sys_id"], latest)
        matched_order = previous.get(key)
        if matched_order is None or (
            fill["symbol"],
            fill["direction"],
            fill["offset_flag"],
            fill["hedge_flag"],
        ) != (
            matched_order["symbol"],
            matched_order["direction"],
            matched_order["offset_flag"],
            matched_order["hedge_flag"],
        ):
            unlinked.append(fill)
            if matched_order is not None:
                problems.append(
                    {
                        "code": "ORDER_FILL_TERMS_CONFLICT",
                        "order_id": key,
                        "fill_id": fill["fill_id"],
                    }
                )
            else:
                problems.append({"code": "RECORDED_FILL_WITHOUT_ORDER", "fill_id": fill["fill_id"]})
            continue
        if fill["trading_day"] != latest["completeness"]["trading_day"]:
            unlinked.append(fill)
            problems.append({"code": "ORDER_FILL_TRADING_DAY_CONFLICT", "fill_id": fill["fill_id"]})
            continue
        linked.setdefault(key, []).append(fill)
    result = []
    for key, order in sorted(previous.items()):
        matched = linked.get(key, [])
        recorded = sum(fill["quantity_lots"] for fill in matched)
        row_problems = [problem for problem in problems if problem.get("order_id") == key]
        seen = key in queried_latest
        result.append(
            {
                **order,
                "contract_id": next(
                    (fill["contract_id"] for fill in matched if fill["contract_id"] is not None),
                    None,
                ),
                "ledger_filled_lots": recorded,
                "fill_gap_lots": order["reported_traded_lots"] - recorded,
                "ledger_fill_ids": [fill["fill_id"] for fill in matched],
                "unrecorded_fill_ids": [
                    fill["fill_id"]
                    for fill in check["unrecorded_fills"]
                    if order_key(fill["exchange"], fill["order_sys_id"], latest) == key
                ],
                "seen_in_query": seen,
                "active": order["active"] if seen and not problems else None,
                "observations": observations[key],
                "problems": row_problems,
                "ownership": "EXTERNAL_NOT_OWNED",
                "reservation_release": "NOT_AUTHORIZED",
            }
        )
    changed = unlinked or check["unrecorded_fills"] or any(row["fill_gap_lots"] for row in result)
    return {
        "status": "UNKNOWN" if problems else "DIFFERENCES" if changed else "MATCHED",
        "orders": result,
        "unlinked_fills": unlinked,
        "unresolved_observations": unresolved,
        "unrecorded_fills": check["unrecorded_fills"],
        "problems": problems,
    }

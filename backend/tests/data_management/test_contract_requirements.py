"""Typed collection must neither request inapplicable data nor waive unknown facts."""

import json

import pytest
from sqlalchemy import text

from northstar_quant.data_management.contract_data.requirements import classify, requirement
from northstar_quant.data_management.tushare import planning
from northstar_quant.data_management.tushare.contract_review import review
from tests.data_management import test_contract_review

automatic = test_contract_review.automatic
lifetime = test_contract_review.lifetime


@pytest.mark.parametrize(
    "exchange,product,name,delivery,category,warehouse",
    [
        ("DCE", "V", "PVC2609", "实物交割", "PHYSICAL_COMMODITY", "REQUIRED"),
        ("DCE", "V_F", "PVC月均价2609F", "现金交割", "MONTHLY_AVERAGE", "NOT_APPLICABLE"),
        ("CFFEX", "IF", "沪深300", "现金交割", "EQUITY_INDEX", "NOT_APPLICABLE"),
        ("CFFEX", "T", "十年国债", "实物交割", "GOVERNMENT_BOND", "NOT_APPLICABLE"),
        ("INE", "EC", "集运指数", "现金交割", "FREIGHT_INDEX", "NOT_APPLICABLE"),
        ("DCE", "V_F", "PVC月均价2609F", "实物交割", "UNKNOWN", "UNKNOWN"),
        ("DCE", "UNKNOWN_F", "未核实", "现金交割", "UNKNOWN", "UNKNOWN"),
    ],
)
def test_type_drives_requirements(exchange, product, name, delivery, category, warehouse):
    profile = classify(
        dict(
            kind="1",
            exchange=exchange,
            product=product,
            details=dict(name=name, d_mode_desc=delivery),
        )
    )
    assert profile.category == category
    assert requirement(profile, "warehouse").applicability == warehouse
    for dataset in ("1min", "5min", "daily", "settlement", "limits"):
        assert requirement(profile, dataset).collect
    for dataset in ("mapping", "adjusted", "index"):
        assert not requirement(profile, dataset).collect


def test_monthly_average_planning_and_review_use_same_policy(automatic):
    lifetime(automatic)
    details = dict(
        name="PVC月均价2609F",
        list_date="20260901",
        delist_date="20260902",
        last_ddate="20260902",
        d_mode_desc="现金交割",
    )
    with automatic._engine.begin() as c:
        c.execute(text("DELETE FROM data_sync_jobs"))
        c.execute(text("DELETE FROM data_contract_collections"))
        c.execute(
            text(
                """UPDATE data_sync_contracts SET ts_code='V2609F.DCE',exchange='DCE',product='V_F',
                details=CAST(:d AS jsonb),planned_revision=0"""
            ),
            {"d": json.dumps(details)},
        )
        c.execute(
            text("UPDATE data_sync_settings SET enabled=true,selected_products=ARRAY['DCE:V_F']")
        )
    test_contract_review.test_tushare.calendar_for_planning(automatic, exchange="DCE")
    planning.plan(automatic._engine)
    with automatic._engine.connect() as c:
        datasets = set(c.scalars(text("SELECT DISTINCT dataset FROM data_sync_jobs")))
    assert {"1min", "daily", "settlement", "limits"} <= datasets
    assert not datasets & {"warehouse", "mapping", "adjusted", "index", "holdings", "weekly_detail"}
    result = review(automatic._engine, "V2609F.DCE")
    found = {r["dataset"]: r for r in result["requirements"]}
    assert result["contract_type"]["category"] == "MONTHLY_AVERAGE"
    assert found["warehouse"]["status"] == "NOT_APPLICABLE"
    assert found["holdings"]["status"] == "UNKNOWN"
    assert found["index"]["status"] == "RELATED"
    assert result["admitted"] is False
    assert not any(reason.startswith("仓单日报：") for reason in result["reasons"])
    assert not any(reason.startswith("持仓排名：") for reason in result["reasons"])
    assert any(reason.startswith("持仓排名：") for reason in result["quality"]["warnings"])

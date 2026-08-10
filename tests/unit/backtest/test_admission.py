"""候选策略研究准入评估测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import polars as pl

from northstar_quant.backtest.admission import evaluate_research_admission
from northstar_quant.config.data_sources import (
    ExchangeAuthorizationEvidence,
    data_source_config_sha256,
    get_data_source,
)
from northstar_quant.config.instrument_universes import load_instrument_universe
from northstar_quant.config.research_admission import load_research_admission_policy
from northstar_quant.config.trading_profile import load_trading_profile


def test_current_public_short_sample_is_not_promoted_but_keeps_a_traceable_result():
    profile = load_trading_profile("cn_futures_daily_actual_offline")
    market_data = pl.DataFrame(
        {
            "date": [date(2026, 3, 26), date(2026, 3, 27)],
            "product": ["RB", "RB"],
        }
    )

    result = evaluate_research_admission(
        profile,
        source_manifest={"governance": {"source_id": profile.data.source_id}},
        raw_market_df=market_data,
        equity_curve=[{"date": "2026-03-27", "equity": 1.0}],
        performance={
            "return_observation_count": 1,
            "sharpe_ratio": None,
            "max_drawdown": 0.0,
        },
        execution={"fill_event_count": 0},
    )

    payload = result.to_dict()
    assert result.status == "INSUFFICIENT_EVIDENCE"
    assert result.eligible_for_human_review is False
    assert payload["blocking_check_count"] > 0
    assert any(check.check_id == "policy.owner_activation" for check in result.checks)
    assert any(check.check_id == "universe.product_coverage" for check in result.checks)


def test_admission_pass_requires_complete_frozen_evidence_and_never_changes_execution_permission():
    profile = load_trading_profile("cn_futures_daily_actual_offline")
    policy = replace(
        load_research_admission_policy("cn_commodity_futures_research_conservative_v1"),
        status="active",
    )
    base_source = get_data_source("wind_wds_server_v1")
    licensed_source = replace(
        base_source,
        tier="commercial_licensed",
        status="active",
        supported=replace(
            base_source.supported,
            authoritative_calendar=True,
            authoritative_dynamic_rules=True,
        ),
        license=replace(
            base_source.license,
            status="active",
            legal_entity="测试主体",
            contract_ref="TEST-CONTRACT-REF",
            order_form_ref="TEST-ORDER-FORM",
            effective_from="2026-01-01",
            expires_on="2030-12-31",
            last_verified_at="2026-08-10",
            verified_by="测试核验人",
            authorized_exchanges=("SHFE", "DCE", "CZCE"),
            authorized_products=("RB", "CU", "I", "M", "TA"),
            authorized_datasets=("actual_contract_daily",),
            authorized_frequencies=("1d",),
            authorized_environments=("internal_server",),
            permitted_purposes=("internal_research", "historical_backtest", "model_validation"),
            allows_internal_storage=True,
            retention_days=3650,
            allows_derived_data_storage=True,
            contract_document_sha256="a" * 64,
            exchange_authorization_evidence=(
                ExchangeAuthorizationEvidence(
                    exchange="SHFE",
                    evidence_ref="TEST-SHFE",
                    evidence_url=None,
                    document_sha256="b" * 64,
                    verified_at="2026-08-10",
                ),
                ExchangeAuthorizationEvidence(
                    exchange="DCE",
                    evidence_ref="TEST-DCE",
                    evidence_url=None,
                    document_sha256="c" * 64,
                    verified_at="2026-08-10",
                ),
                ExchangeAuthorizationEvidence(
                    exchange="CZCE",
                    evidence_ref="TEST-CZCE",
                    evidence_url=None,
                    document_sha256="d" * 64,
                    verified_at="2026-08-10",
                ),
            ),
        ),
    )
    universe = load_instrument_universe("cn_commodity_futures_research_core_v1")
    start = date(2015, 1, 1)
    products = [member.product for member in universe.members_for_tier("core")]
    market_data = pl.DataFrame(
        {
            "date": [start + timedelta(days=offset) for offset in range(3_000) for _ in products],
            "product": [product for _ in range(3_000) for product in products],
        }
    )
    equity_curve = [
        {"date": str(start + timedelta(days=offset * 2)), "equity": 1.0 + offset * 0.0001}
        for offset in range(800)
    ]
    evidence = {
        "unknown_missing_sessions": 0,
        "unresolved_official_mismatches": 0,
        "walk_forward_fold_count": 3,
        "positive_net_fold_count": 3,
        "completed_oos_round_trip_count": 60,
        "margin_call_count": 0,
        "forced_liquidation_count": 0,
        "cost_stress": {"1.0": True, "1.5": True, "2.0": True},
        "parameter_neighbor_count": 8,
        "passing_neighbor_fraction": 0.75,
        "immutable_trial_ledger": True,
        "secondary_source_validation": {"source_id": "ifind_quant_api_v1", "status": "PASS"},
    }

    result = evaluate_research_admission(
        profile,
        source_manifest={
            "governance": {
                "source_id": licensed_source.source_id,
                "source_config_sha256": data_source_config_sha256(licensed_source),
            },
            "schema": {"complete_trading_sessions": True},
        },
        raw_market_df=market_data,
        equity_curve=equity_curve,
        performance={
            "return_observation_count": 800,
            "sharpe_ratio": 0.8,
            "max_drawdown": -0.1,
        },
        execution={
            "max_margin_ratio": 0.25,
            "min_available_funds_ratio": 0.75,
        },
        evidence=evidence,
        policy_override=policy,
        source_override=licensed_source,
        universe_override=universe,
    )

    assert result.status == "PASS"
    assert result.eligible_for_human_review is True
    assert profile.futures is not None
    assert profile.futures.execution_allowed is False


def test_weight_return_is_not_applicable_even_when_a_policy_is_explicitly_requested():
    base_profile = load_trading_profile("cn_futures_daily_trend_offline")
    profile = replace(
        base_profile,
        research_admission=replace(
            base_profile.research_admission,
            enabled=True,
            policy_id="cn_commodity_futures_research_conservative_v1",
        ),
    )

    result = evaluate_research_admission(
        profile,
        source_manifest={},
        raw_market_df=pl.DataFrame({"date": [date(2015, 1, 1)], "product": ["RB"]}),
        equity_curve=[{"date": "2015-01-01", "equity": 1.0}],
        performance={},
        execution={},
    )

    assert result.status == "NOT_APPLICABLE"
    assert result.eligible_for_human_review is False

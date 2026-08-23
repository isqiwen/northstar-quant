from dataclasses import replace

from northstar_quant.platform.config.trading_profile import load_trading_profile


def test_simulated_profile_parses_complete_portfolio_risk_approval_authority():
    profile = load_trading_profile("cn_futures_daily_trend_simulated")
    config = profile.portfolio_risk_approval

    assert config is not None
    assert config.policy_id == "ctp-sim-portfolio-risk-v1"
    assert config.policy_version == profile.versions.risk_policy
    assert config.max_input_age_seconds == 300
    assert config.manual_approval_verifier_id == "ctp-sim-manual-risk-verifier-v1"
    assert config.authorized_approver_ids == ("risk-owner",)
    assert config.limits.margin_utilization == 0.8
    assert {scenario.kind for scenario in config.scenarios} == {
        "gap",
        "limit_move",
        "volatility_shock",
        "liquidity_collapse",
        "correlated_commodity_shock",
        "margin_increase",
        "fx_shock",
    }
    assert config.taxonomy_for("RB").correlation_cluster_id == "ferrous-steel"
    assert config.ctp_sim_execution_rule_for("sc").max_position_lots == 100
    assert len(config.config_hash) == 64


def test_manual_approval_authority_is_canonical_and_bound_into_config_hash():
    config = load_trading_profile("cn_futures_daily_trend_simulated").portfolio_risk_approval

    assert config is not None
    expanded = replace(
        config,
        authorized_approver_ids=("secondary-risk-owner", "risk-owner"),
    )
    reverse_expanded = replace(
        config,
        authorized_approver_ids=("risk-owner", "secondary-risk-owner"),
    )
    changed_verifier = replace(
        config,
        manual_approval_verifier_id="different-ctp-sim-manual-risk-verifier-v1",
    )

    assert expanded.authorized_approver_ids == (
        "risk-owner",
        "secondary-risk-owner",
    )
    assert reverse_expanded.authorized_approver_ids == expanded.authorized_approver_ids
    assert reverse_expanded.config_hash == expanded.config_hash
    assert expanded.config_hash != config.config_hash
    assert changed_verifier.config_hash != config.config_hash

import polars as pl
import pytest
from dataclasses import replace

from northstar_quant.platform.config.settings import get_settings
from northstar_quant.platform.config.trading_profile import load_trading_profile
from northstar_quant.portfolio_risk.portfolio.multi_strategy import (
    build_target_weight_portfolio,
    combine_strategy_targets,
)
from northstar_quant.portfolio_risk.limits.models import RiskLimits
import northstar_quant.portfolio_risk.portfolio.strategy_pipeline as pipeline
from northstar_quant.portfolio_risk.portfolio.strategy_pipeline import (
    build_profile_risk_limits,
    enforce_profile_target_policy,
)


def _weights_by_symbol(targets: pl.DataFrame) -> dict[str, float]:
    return {
        row["symbol"]: float(row["target_weight"])
        for row in targets.select(["symbol", "target_weight"]).to_dicts()
    }


def test_combine_strategy_targets_normalizes_each_strategy_before_scaling():
    strategy_frames = [
        pl.DataFrame(
            [
                {"symbol": "AAA", "target_weight": 2.0},
                {"symbol": "BBB", "target_weight": 1.0},
            ]
        ),
        pl.DataFrame(
            [
                {"symbol": "BBB", "target_weight": 3.0},
            ]
        ),
    ]

    combined = combine_strategy_targets(strategy_frames, [0.6, 0.4])
    weights = _weights_by_symbol(combined)

    assert weights["AAA"] == pytest.approx(0.4)
    assert weights["BBB"] == pytest.approx(0.6)
    assert float(combined["target_weight"].sum()) == pytest.approx(1.0)


def test_build_target_weight_portfolio_preserves_cash_after_risk_constraints():
    strategy_frames = [
        pl.DataFrame(
            [
                {"symbol": "AAA", "target_weight": 0.5},
                {"symbol": "BBB", "target_weight": 0.5},
            ]
        )
    ]
    limits = RiskLimits(
        max_single_weight=0.35,
        max_gross_exposure=1.0,
        min_cash_buffer=0.02,
    )

    combined = build_target_weight_portfolio(strategy_frames, [1.0], limits)
    weights = _weights_by_symbol(combined)

    assert weights["AAA"] == pytest.approx(0.35)
    assert weights["BBB"] == pytest.approx(0.35)
    assert float(combined["target_weight"].max()) == pytest.approx(0.35)
    assert float(combined["target_weight"].sum()) == pytest.approx(0.7)


def test_long_only_profile_rejects_negative_target_weights():
    profile = load_trading_profile()
    profile = replace(
        profile,
        execution=replace(profile.execution, long_only=True),
    )
    targets = pl.DataFrame(
        {
            "symbol": ["RB_CONT"],
            "target_weight": [-0.2],
        }
    )

    with pytest.raises(ValueError, match="long_only=true"):
        enforce_profile_target_policy(targets, profile)


def test_profile_risk_uses_global_minimum_trade_value_as_last_resort(monkeypatch):
    profile = load_trading_profile()
    profile = replace(
        profile,
        execution=replace(profile.execution, rebalance_min_trade_value=None),
    )
    settings = get_settings().model_copy(update={"rebalance_min_trade_value": 888.0})
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)

    limits = build_profile_risk_limits(profile)

    assert limits.min_order_notional == 888.0


def test_profile_rejects_conflicting_long_only_settings():
    profile = load_trading_profile()
    profile = replace(
        profile,
        execution=replace(profile.execution, long_only=False),
        risk={**profile.risk, "long_only": True},
    )

    with pytest.raises(ValueError, match="risk.long_only.*execution.long_only 冲突"):
        build_profile_risk_limits(profile)


def test_production_profile_requires_dynamic_risk_flags():
    profile = load_trading_profile()
    profile = replace(
        profile,
        lifecycle=replace(profile.lifecycle, role="production"),
    )

    with pytest.raises(ValueError, match="必须显式启用动态风控"):
        build_profile_risk_limits(profile)

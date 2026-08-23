from northstar_quant.portfolio_risk.limits import LimitStatus, RiskLimitSet, RiskMeasurements, evaluate_limit, evaluate_limits


def test_limit_evaluation_warns_blocks_and_fails_closed_for_unknown_measurements():
    assert evaluate_limit(limit_id="gross", observed=0.7, threshold=1).status is LimitStatus.PASS
    assert evaluate_limit(limit_id="gross", observed=0.8, threshold=1).status is LimitStatus.WARN
    assert evaluate_limit(limit_id="gross", observed=1.1, threshold=1).status is LimitStatus.BLOCK
    assert evaluate_limit(limit_id="gross", observed=None, threshold=1).status is LimitStatus.BLOCK


def test_limit_set_covers_all_required_categories_and_blocks_unknown_measurements():
    checks = evaluate_limits(
        limits=RiskLimitSet(1, 1, 1, 1, 1, 1, 2, 1, 0.8),
        measurements=RiskMeasurements(0.2, 0.3, 0.4, 0.5, 0.6, None, 1.0, 0.2, 0.7),
    )
    statuses = {check.limit_id: check.status for check in checks}
    assert set(statuses) == {"per_contract", "per_commodity", "per_sector", "per_exchange", "per_strategy", "per_account", "gross_leverage", "net_leverage", "margin_utilization"}
    assert statuses["per_account"] is LimitStatus.BLOCK

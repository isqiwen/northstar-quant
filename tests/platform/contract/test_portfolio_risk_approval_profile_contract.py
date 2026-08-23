"""P10-WP05 profile-owned portfolio-risk approval policy contracts."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from shutil import copytree
from typing import Any

import pytest
import yaml

from northstar_quant.platform.config.trading_profile import (
    load_trading_profile_uncached,
)
from tests.helpers.paths import PROJECT_ROOT


PROFILE_ID = "cn_futures_daily_trend_simulated"
_PROFILE_RELATIVE_PATH = Path("simulated") / f"{PROFILE_ID}.yaml"
_EXPECTED_SCENARIO_KINDS = {
    "gap",
    "limit_move",
    "volatility_shock",
    "liquidity_collapse",
    "correlated_commodity_shock",
    "margin_increase",
    "fx_shock",
}


def _mutated_profile(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], None],
):
    """Copy the real simulated profile, mutate one policy field, then parse it."""

    profile_dir = tmp_path / "profiles"
    copytree(PROJECT_ROOT / "configs" / "profiles", profile_dir)
    path = profile_dir / _PROFILE_RELATIVE_PATH
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    mutate(raw)
    path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return load_trading_profile_uncached(PROFILE_ID, config_dir=profile_dir)


def _approval(raw: dict[str, Any]) -> dict[str, Any]:
    value = raw["portfolio_risk_approval"]
    assert isinstance(value, dict)
    return value


def test_simulated_profile_exposes_one_complete_profile_owned_p3_policy() -> None:
    profile = load_trading_profile_uncached(PROFILE_ID)
    approval = profile.portfolio_risk_approval

    assert approval is not None
    assert approval.policy_version == profile.versions.risk_policy
    assert approval.manual_approval_verifier_id == "ctp-sim-manual-risk-verifier-v1"
    assert approval.authorized_approver_ids == ("risk-owner",)
    assert {item.kind for item in approval.scenarios} == _EXPECTED_SCENARIO_KINDS
    assert len(approval.scenarios) == len(_EXPECTED_SCENARIO_KINDS)
    assert {item.product_id for item in approval.taxonomy} == {
        item.product_id for item in approval.ctp_sim_execution_rules
    }
    assert approval.config_hash == approval.as_mapping()["config_hash"]


@pytest.mark.parametrize(
    ("mutate", "error"),
    (
        (
            lambda raw: _approval(raw)["limits"].__setitem__("unreviewed_limit", 1),
            "portfolio_risk_approval.limits has an invalid field set",
        ),
        (
            lambda raw: _approval(raw)["limits"].pop("per_contract"),
            "portfolio_risk_approval.limits has an invalid field set",
        ),
        (
            lambda raw: _approval(raw).__setitem__("policy_id", "invalid policy id"),
            "portfolio_risk_approval.policy_id must match",
        ),
        (
            lambda raw: _approval(raw).__setitem__(
                "policy_version",
                "invalid policy version",
            ),
            "portfolio_risk_approval.policy_version must match",
        ),
        (
            lambda raw: _approval(raw).__setitem__(
                "policy_version",
                "different-policy-v1",
            ),
            "portfolio_risk_approval.policy_version must equal versions.risk_policy",
        ),
        (
            lambda raw: _approval(raw)["scenarios"].__setitem__(
                0,
                {
                    **_approval(raw)["scenarios"][0],
                    "kind": "not-a-p3-scenario",
                },
            ),
            "portfolio_risk_approval.scenarios.kind must be one of",
        ),
        (
            lambda raw: _approval(raw)["scenarios"].__setitem__(
                0,
                {
                    **_approval(raw)["scenarios"][0],
                    "shock_fraction": 0,
                },
            ),
            "portfolio_risk_approval.scenarios.shock_fraction "
            "must be a positive finite number",
        ),
        (
            lambda raw: _approval(raw)["ctp_sim_execution_rules"].pop("sc"),
            "portfolio_risk_approval.taxonomy and ctp_sim_execution_rules must cover",
        ),
        (
            lambda raw: _approval(raw).pop("manual_approval_verifier_id"),
            "portfolio_risk_approval has an invalid field set",
        ),
        (
            lambda raw: _approval(raw).__setitem__("authorized_approver_ids", []),
            "authorized_approver_ids must be a non-empty list",
        ),
        (
            lambda raw: _approval(raw).__setitem__(
                "authorized_approver_ids", ["risk-owner", "risk-owner"]
            ),
            "authorized_approver_ids cannot contain duplicates",
        ),
    ),
    ids=(
        "nested-unknown-limit",
        "missing-required-limit",
        "invalid-policy-id",
        "invalid-policy-version",
        "policy-version-drift",
        "unknown-scenario-kind",
        "non-positive-shock",
        "taxonomy-rule-product-drift",
        "missing-manual-verifier",
        "empty-authorized-approvers",
        "duplicate-authorized-approvers",
    ),
)
def test_profile_owned_p3_policy_rejects_unreviewed_or_incomplete_taxonomy(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], None],
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        _mutated_profile(tmp_path, mutate)

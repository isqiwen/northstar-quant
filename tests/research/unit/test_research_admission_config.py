"""候选策略研究准入政策配置测试。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from northstar_quant.platform.config.research_admission import (
    ResearchAdmissionConfigError,
    load_research_admission_policy,
)
from northstar_quant.platform.config.yaml_loader import load_yaml
from tests.helpers.paths import PROJECT_ROOT


def test_conservative_admission_policy_is_pending_and_requires_core_actual_contract_evidence():
    policy = load_research_admission_policy("cn_commodity_futures_research_conservative_v1")

    assert policy.status == "pending_owner_approval"
    assert policy.source.required_source_id == "wind_wds_server_v1"
    assert policy.source.secondary_validation_source_id == "ifind_quant_api_v1"
    assert policy.source.require_secondary_source_validation is True
    assert policy.scope.allowed_backtest_engines == (
        "futures_daily",
        "futures_intraday_replay",
    )
    assert policy.data.min_complete_history_years == 8
    assert policy.sample.min_oos_trading_days == 720
    assert policy.risk.max_oos_drawdown_fraction == pytest.approx(0.2)
    assert policy.promotion.allow_research_to_simulated is False


def test_admission_policy_rejects_unsafe_automatic_promotion(tmp_path: Path):
    payload = deepcopy(
        load_yaml(
            PROJECT_ROOT
            / "configs"
            / "research"
            / "admission"
            / "cn_commodity_futures_research_conservative_v1.yaml"
        )
    )
    payload["status"] = "active"
    payload["promotion"]["allow_research_to_simulated"] = True
    path = tmp_path / "cn_commodity_futures_research_conservative_v1.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ResearchAdmissionConfigError, match="不得自动"):
        load_research_admission_policy(
            "cn_commodity_futures_research_conservative_v1",
            tmp_path,
        )

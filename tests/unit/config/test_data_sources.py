"""数据来源与授权边界配置测试。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from northstar_quant.config.data_sources import (
    DataSourceConfigError,
    get_data_source,
    load_data_sources,
)
from northstar_quant.config.yaml_loader import load_yaml
from tests.support.paths import PROJECT_ROOT


def test_data_source_registry_separates_public_reference_from_pending_commercial_vendor():
    sources = {source.source_id: source for source in load_data_sources()}

    assert sources["akshare_actual_daily_public_v1"].tier == "public_reference"
    assert sources["akshare_actual_daily_public_v1"].is_research_admission_eligible is False
    wind = get_data_source("wind_wds_server_v1")
    assert wind.adapter_id == "wind_wds_server"
    assert wind.status == "procurement_pending"
    assert wind.is_research_admission_eligible is False
    assert wind.license.allows_live_trading is False


def test_data_source_rejects_active_vendor_without_effective_contract_evidence(tmp_path: Path):
    payload = deepcopy(load_yaml(PROJECT_ROOT / "configs" / "data" / "sources.yaml"))
    candidate = next(item for item in payload["sources"] if item["source_id"] == "wind_wds_server_v1")
    candidate["tier"] = "commercial_licensed"
    candidate["status"] = "active"
    candidate["license"]["status"] = "active"
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    with pytest.raises(DataSourceConfigError, match="有效且未过期的合同证据"):
        load_data_sources(path)


def test_data_source_accepts_active_vendor_only_with_per_exchange_evidence(tmp_path: Path):
    payload = deepcopy(load_yaml(PROJECT_ROOT / "configs" / "data" / "sources.yaml"))
    candidate = next(item for item in payload["sources"] if item["source_id"] == "wind_wds_server_v1")
    candidate["tier"] = "commercial_licensed"
    candidate["status"] = "active"
    license_config = candidate["license"]
    license_config.update(
        {
            "status": "active",
            "legal_entity": "测试主体",
            "contract_ref": "TEST-CONTRACT",
            "order_form_ref": "TEST-ORDER",
            "effective_from": "2026-01-01",
            "expires_on": "2030-12-31",
            "last_verified_at": "2026-08-10",
            "verified_by": "测试核验人",
            "authorized_exchanges": ["SHFE"],
            "authorized_products": ["RB"],
            "authorized_datasets": ["actual_contract_daily"],
            "authorized_frequencies": ["1d"],
            "authorized_environments": ["internal_server"],
            "permitted_purposes": ["internal_research", "historical_backtest", "model_validation"],
            "allows_internal_storage": True,
            "retention_days": 3650,
            "allows_derived_data_storage": True,
            "contract_document_sha256": "a" * 64,
            "exchange_authorization_evidence": [
                {
                    "exchange": "SHFE",
                    "evidence_ref": "TEST-SHFE",
                    "evidence_url": "https://example.test/shfe",
                    "document_sha256": "b" * 64,
                    "verified_at": "2026-08-10",
                }
            ],
            "request_rate_limit_per_minute": 120,
        }
    )
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    source = next(item for item in load_data_sources(path) if item.source_id == "wind_wds_server_v1")

    assert source.license.is_active is True
    assert source.is_research_admission_eligible is True


def test_data_source_rejects_unknown_field(tmp_path: Path):
    payload = deepcopy(load_yaml(PROJECT_ROOT / "configs" / "data" / "sources.yaml"))
    payload["sources"][0]["unexpected"] = True
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")

    with pytest.raises(DataSourceConfigError, match="未知字段"):
        load_data_sources(path)

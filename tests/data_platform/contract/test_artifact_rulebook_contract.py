"""Artifact-backed Contract RuleBook 的边界契约。"""

from __future__ import annotations

import ast
from dataclasses import is_dataclass

from northstar_quant.data_platform.contracts.artifact_rulebook import (
    ArtifactBackedContractRuleReplay,
    ArtifactRuleBookError,
    ContractRuleBookPITSelector,
    RULEBOOK_DATASET_ID,
    RULEBOOK_DATASET_TRANSFORM_VERSION,
    RULEBOOK_SCHEMA_VERSION,
    RULEBOOK_TRANSFORM_VERSION,
)
from tests.helpers.paths import PROJECT_ROOT


SOURCE = (
    PROJECT_ROOT
    / "src"
    / "northstar_quant"
    / "data_platform"
    / "contracts"
    / "artifact_rulebook.py"
)


def test_artifact_rulebook_public_contract_is_immutable_and_fixed_schema() -> None:
    assert is_dataclass(ArtifactBackedContractRuleReplay)
    assert ArtifactBackedContractRuleReplay.__dataclass_params__.frozen
    assert issubclass(ArtifactRuleBookError, ValueError)
    assert RULEBOOK_DATASET_ID == "cn_futures_contract_rule_book"
    assert RULEBOOK_SCHEMA_VERSION == "cn_futures_contract_rule_book_v1"
    assert RULEBOOK_TRANSFORM_VERSION == "normalize.contract-rulebook.v1"
    assert RULEBOOK_DATASET_TRANSFORM_VERSION == "dataset.contract-rulebook.v1"
    assert callable(ContractRuleBookPITSelector.select)


def test_rulebook_is_data_platform_only_and_cannot_load_static_or_live_rules() -> None:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    forbidden_prefixes = (
        "northstar_quant.application",
        "northstar_quant.research",
        "northstar_quant.trading_execution",
        "northstar_quant.platform.db",
    )
    assert not {
        module
        for module in imported_modules
        if module.startswith(forbidden_prefixes)
    }
    assert "northstar_quant.data_platform.contracts.contract_master_loader" not in imported_modules

    source = SOURCE.read_text(encoding="utf-8")
    assert "load_contract_master" not in source
    assert "datetime.now" not in source
    assert "yaml." not in source
    assert "execution_eligible=True" not in source


def test_rulebook_replay_mapping_is_hash_only_and_never_claims_execution_eligibility() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert '"execution_eligible": False' in source
    assert '"raw_payload"' not in source
    assert '"blob_path"' not in source
    assert '"record_path"' not in source

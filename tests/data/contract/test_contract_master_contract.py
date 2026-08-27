"""P1-WP03 Contract Master 的公开边界契约。"""

from __future__ import annotations

import ast
from dataclasses import is_dataclass

import northstar_quant.data.contracts as contracts

from tests.helpers.paths import PROJECT_ROOT


PUBLIC_CONTRACT_MASTER_TYPES = frozenset(
    {
        "Commodity",
        "Exchange",
        "Instrument",
        "ContinuousResearchSeries",
        "Contract",
        "ContractRuleSnapshot",
        "ContractResolution",
        "ContractMasterPublication",
    }
)
MASTER_SOURCE = (
    PROJECT_ROOT / "src" / "northstar_quant" / "data" / "contracts" / "contract_master.py"
)
AUTHORITY_SOURCE = (
    PROJECT_ROOT
    / "src"
    / "northstar_quant"
    / "data"
    / "contracts"
    / "postgresql_contract_authority.py"
)
EXECUTION_SYMBOL_BOUNDARIES = (
    PROJECT_ROOT
    / "src"
    / "northstar_quant"
    / "trading_execution"
    / "execution"
    / "registry.py",
    PROJECT_ROOT / "src" / "northstar_quant" / "application" / "live_service.py",
)


def test_contract_master_public_models_are_frozen_value_contracts() -> None:
    assert PUBLIC_CONTRACT_MASTER_TYPES <= set(contracts.__all__)
    for name in PUBLIC_CONTRACT_MASTER_TYPES:
        model = getattr(contracts, name)
        assert is_dataclass(model), f"{name} 必须是 dataclass 值对象"
        assert model.__dataclass_params__.frozen, f"{name} 必须不可变"


def test_contract_master_is_broker_neutral_and_does_not_read_current_time() -> None:
    """Data Platform 只形成事实结论；绑定 CTP 和订单必须留在下游组合层。"""

    tree = ast.parse(MASTER_SOURCE.read_text(encoding="utf-8"), filename=str(MASTER_SOURCE))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    forbidden = {
        module
        for module in imported_modules
        if module.startswith("northstar_quant.trading_execution")
        or module.startswith("northstar_quant.application")
    }

    assert not forbidden
    source = MASTER_SOURCE.read_text(encoding="utf-8")
    assert "datetime.now" not in source
    assert "utc_now" not in source


def test_contract_master_authority_has_no_yaml_or_path_fallback() -> None:
    """执行规则只能来自带 PIT 与来源证据的 PostgreSQL 发布记录。"""

    source = AUTHORITY_SOURCE.read_text(encoding="utf-8")

    assert "contract_master_loader" not in source
    assert "yaml" not in source.lower()
    assert "pathlib" not in source
    assert "configs/" not in source
    assert "load_contract_master_publication_at" in source


def test_execution_entrypoints_never_resolve_continuous_series_to_broker_identity() -> None:
    """研究序列只能在研究域存在，不能由执行入口偷偷换月。"""

    for source_path in EXECUTION_SYMBOL_BOUNDARIES:
        source = source_path.read_text(encoding="utf-8")
        assert ".resolve_continuous(" not in source, source_path

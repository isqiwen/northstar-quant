import pytest

from northstar_quant.trading_execution.broker.ctp_contract_mapping import (
    CtpContractMappingError,
    load_ctp_contract_registry,
)


def test_ctp_contract_mapping_loads_and_rejects_disabled_contracts(tmp_path):
    mapping_path = tmp_path / "ctp.yaml"
    mapping_path.write_text(
        """version: 1
broker: ctp
contracts:
  - continuous_symbol: RB_CONT
    data_symbol: RB2601
    instrument_id: rb2601
    exchange_id: SHFE
    product_id: rb
    volume_multiple: 10
    price_tick: 1
    trading_enabled: false
""",
        encoding="utf-8",
    )

    registry = load_ctp_contract_registry(mapping_path)
    with pytest.raises(CtpContractMappingError, match="CTP_CONTINUOUS_CONTRACT_FORBIDDEN"):
        registry.resolve_continuous("rb_cont")
    with pytest.raises(CtpContractMappingError, match="CTP_CONTRACT_DISABLED"):
        registry.resolve_data_symbol("RB2601")
    with pytest.raises(CtpContractMappingError, match="CTP_CONTRACT_DISABLED"):
        registry.resolve_ctp_identity("rb2601", "SHFE")


def test_ctp_contract_mapping_requires_unambiguous_contract_identity(tmp_path):
    mapping_path = tmp_path / "ctp-invalid.yaml"
    mapping_path.write_text(
        """version: 1
broker: ctp
contracts:
  - continuous_symbol: RB_CONT
    data_symbol: RB2601
    instrument_id: rb2601
    exchange_id: SHFE
    product_id: rb
    volume_multiple: 10
    price_tick: 1
    trading_enabled: true
  - continuous_symbol: I_CONT
    data_symbol: RB2601
    instrument_id: rb2601
    exchange_id: SHFE
    product_id: rb
    volume_multiple: 10
    price_tick: 1
    trading_enabled: true
""",
        encoding="utf-8",
    )

    with pytest.raises(CtpContractMappingError, match="数据合约映射重复"):
        load_ctp_contract_registry(mapping_path)


def test_ctp_contract_mapping_resolves_enabled_reverse_identity(tmp_path):
    mapping_path = tmp_path / "ctp-enabled.yaml"
    mapping_path.write_text(
        """version: 1
broker: ctp
contracts:
  - continuous_symbol: M_CONT
    data_symbol: M2601
    instrument_id: m2601
    exchange_id: DCE
    product_id: m
    volume_multiple: 10
    price_tick: 1
    trading_enabled: true
""",
        encoding="utf-8",
    )

    registry = load_ctp_contract_registry(mapping_path)
    mapping = registry.resolve_ctp_identity("M2601", "DCE")
    assert mapping.data_symbol == "M2601"
    assert mapping.volume_multiple == 10
    assert mapping.continuous_symbol == "M_CONT"
    with pytest.raises(CtpContractMappingError, match="CTP_CONTINUOUS_CONTRACT_FORBIDDEN"):
        registry.resolve_continuous("m_cont")


def test_ctp_contract_mapping_public_resolution_apis_reject_unknown_contracts(tmp_path):
    mapping_path = tmp_path / "ctp-enabled.yaml"
    mapping_path.write_text(
        """version: 1
broker: ctp
contracts:
  - continuous_symbol: RB_CONT
    data_symbol: RB2601
    instrument_id: rb2601
    exchange_id: SHFE
    product_id: rb
    volume_multiple: 10
    price_tick: 1
    trading_enabled: true
""",
        encoding="utf-8",
    )

    registry = load_ctp_contract_registry(mapping_path)
    with pytest.raises(CtpContractMappingError, match="CTP_CONTINUOUS_CONTRACT_FORBIDDEN"):
        registry.resolve_continuous("CU_CONT")
    with pytest.raises(CtpContractMappingError, match="CTP_CONTRACT_NOT_CONFIGURED"):
        registry.resolve_data_symbol("CU2601")
    with pytest.raises(CtpContractMappingError, match="CTP_CONTRACT_NOT_CONFIGURED"):
        registry.resolve_ctp_identity("cu2601", "SHFE")


@pytest.mark.parametrize(
    ("target", "replacement", "error_message"),
    [
        (
            "data_symbol: RB2601",
            "data_symbol: RB_CONT",
            "data_symbol 不能是连续研究合约",
        ),
        (
            "data_symbol: RB2601",
            "data_symbol: RB2602",
            "data_symbol 必须与 instrument_id 忽略大小写后完全一致",
        ),
        (
            "product_id: rb",
            "product_id: i",
            "product_id 必须同时是 data_symbol 和 instrument_id 的前缀",
        ),
    ],
)
def test_ctp_contract_mapping_rejects_non_executable_or_inconsistent_identity(
    tmp_path,
    target,
    replacement,
    error_message,
):
    mapping_path = tmp_path / "ctp-invalid-identity.yaml"
    document = """version: 1
broker: ctp
contracts:
  - continuous_symbol: RB_CONT
    data_symbol: RB2601
    instrument_id: rb2601
    exchange_id: SHFE
    product_id: rb
    volume_multiple: 10
    price_tick: 1
    trading_enabled: true
"""
    mapping_path.write_text(
        document.replace(target, replacement),
        encoding="utf-8",
    )

    with pytest.raises(CtpContractMappingError, match=error_message):
        load_ctp_contract_registry(mapping_path)

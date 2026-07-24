import pytest

from northstar_quant.config.ctp_contract_mapping import (
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
    mapping = registry.resolve_continuous("rb_cont")
    assert mapping.data_symbol == "RB2601"
    assert mapping.instrument_id == "rb2601"
    with pytest.raises(CtpContractMappingError, match="CTP_CONTRACT_DISABLED"):
        registry.resolve_data_symbol("RB2601")


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
    data_symbol: I2601
    instrument_id: rb2601
    exchange_id: SHFE
    product_id: rb
    volume_multiple: 10
    price_tick: 1
    trading_enabled: true
""",
        encoding="utf-8",
    )

    with pytest.raises(CtpContractMappingError, match="CTP 合约身份重复"):
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

    mapping = load_ctp_contract_registry(mapping_path).resolve_ctp_identity("M2601", "DCE")
    assert mapping.data_symbol == "M2601"
    assert mapping.volume_multiple == 10

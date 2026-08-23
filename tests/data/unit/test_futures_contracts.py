import pytest

from northstar_quant.data.contracts.futures_contracts import (
    FuturesContractConfigError,
    load_futures_contract_catalog,
)


def test_continuous_contract_catalog_rejects_execution():
    catalog = load_futures_contract_catalog("configs/futures/cn_commodity_research.yaml")

    rb = catalog.resolve("RB_CONT")
    assert rb.multiplier == 10.0
    assert rb.tick_size == 1.0
    assert catalog.resolve("CU_CONT").multiplier == 5.0
    assert catalog.resolve("I_CONT").tick_size == 0.5
    assert catalog.resolve("M_CONT").exchange == "DCE"
    assert catalog.resolve("TA_CONT").multiplier == 5.0
    assert catalog.resolve("SA_CONT").multiplier == 20.0
    assert catalog.resolve("SI_CONT").exchange == "GFEX"
    assert catalog.resolve("SC_CONT").multiplier == 1000.0
    assert {contract.exchange for contract in catalog.contracts} == {
        "SHFE",
        "DCE",
        "CZCE",
        "GFEX",
        "INE",
    }
    with pytest.raises(FuturesContractConfigError, match="不能下单"):
        rb.require_tradable()


def test_contract_catalog_rejects_unsupported_exchange(tmp_path):
    path = tmp_path / "unsupported_exchange.yaml"
    path.write_text(
        """version: 1
contracts:
  - symbol: XX_CONT
    product: XX
    exchange: UNKNOWN
    contract_kind: continuous
    multiplier: 1
    tick_size: 1
    tradable: false
""",
        encoding="utf-8",
    )

    with pytest.raises(FuturesContractConfigError, match="不受支持"):
        load_futures_contract_catalog(path)

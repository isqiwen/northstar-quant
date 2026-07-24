"""品种卡与期货研究主数据的一致性测试。"""

from northstar_quant.config.futures_contracts import load_futures_contract_catalog
from northstar_quant.config.product_cards import (
    load_product_cards,
    validate_product_cards_against_catalog,
)


def test_all_supported_products_have_consistent_cards():
    cards = load_product_cards()
    catalog = load_futures_contract_catalog("configs/futures/cn_commodity_research.yaml")

    validate_product_cards_against_catalog(cards, catalog)

    assert {card.product for card in cards} == {"RB", "CU", "I", "M", "TA", "SA", "SI", "SC"}
    assert all(card.dynamic_snapshot_fields for card in cards)

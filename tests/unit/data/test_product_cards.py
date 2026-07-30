"""品种卡与期货研究主数据的一致性测试。"""

import shutil

import pytest

from northstar_quant.common.enums import ProductSessionRule, RolloverMethod
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
    assert next(card for card in cards if card.product == "M").delivery_months == (1, 3, 5, 7, 8, 9, 11, 12)
    assert all(card.session_rule == ProductSessionRule.EXCHANGE_DAILY_SCHEDULE for card in cards)
    assert all(card.rollover_method == RolloverMethod.EXPLICIT_DAILY_CONTRACT_CHAIN for card in cards)


def test_invalid_rule_string_is_rejected_during_product_card_load(tmp_path):
    source = "configs/instruments/products/rb.yaml"
    destination = tmp_path / "rb.yaml"
    shutil.copy(source, destination)
    destination.write_text(
        destination.read_text(encoding="utf-8").replace(
            "session_rule: exchange_daily_schedule",
            "session_rule: exchange_daily_shedule",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="trading.session_rule 取值无效"):
        load_product_cards(tmp_path)

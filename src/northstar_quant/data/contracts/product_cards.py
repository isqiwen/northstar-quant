"""国内期货品种卡的加载与一致性校验。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from northstar_quant.foundation.common.enums import (
    DynamicProductSnapshotField,
    IndividualInvestorRule,
    LastTradeDayRule,
    ProductSessionRule,
    RolloverMethod,
    RolloverReferenceSignal,
)
from northstar_quant.data.contracts.futures_contracts import FuturesContractCatalog
from northstar_quant.foundation.config.settings import get_settings
from northstar_quant.foundation.config.yaml_loader import load_yaml


@dataclass(frozen=True, slots=True)
class ProductCard:
    """一张品种卡；动态交易参数只描述来源，不保存可能过期的数值。"""

    product: str
    name: str
    exchange: str
    multiplier: float
    tick_size: float
    delivery_months: tuple[int, ...]
    has_night_session: bool
    session_rule: ProductSessionRule
    last_trade_day_rule: LastTradeDayRule
    individual_investor_rule: IndividualInvestorRule
    rollover_method: RolloverMethod
    rollover_reference_signals: tuple[RolloverReferenceSignal, ...]
    dynamic_snapshot_fields: tuple[DynamicProductSnapshotField, ...]
    payload: dict[str, Any]


_ROOT_FIELDS = frozenset(
    {"version", "product", "name", "exchange", "contract", "trading", "delivery", "roll", "risk", "research", "sources"}
)
_CONTRACT_FIELDS = frozenset({"multiplier", "tick_size", "delivery_months"})
_TRADING_FIELDS = frozenset({"timezone", "has_night_session", "session_rule"})
_DELIVERY_FIELDS = frozenset({"last_trade_day_rule", "individual_investor_rule"})
_ROLL_FIELDS = frozenset({"method", "reference_signals"})
_RISK_FIELDS = frozenset({"dynamic_snapshot"})
_RESEARCH_FIELDS = frozenset({"industry_chain", "supply_demand_drivers", "event_risks"})
_SOURCES_FIELDS = frozenset({"exchange", "daily_rule_source"})


def load_product_cards(path: str | Path = "configs/instruments/products") -> tuple[ProductCard, ...]:
    """读取全部品种卡，并拒绝空目录、重复品种或不完整动态快照声明。"""

    directory = Path(path)
    if not directory.is_absolute():
        directory = get_settings().project_root / directory
    if not directory.is_dir():
        raise ValueError(f"品种卡目录不存在：{directory}")
    cards = tuple(_parse_card(load_yaml(file), file) for file in sorted(directory.glob("*.yaml")))
    if not cards:
        raise ValueError("品种卡目录中至少需要一个 YAML 文件")
    products = [card.product for card in cards]
    if len(products) != len(set(products)):
        raise ValueError("品种卡 product 不能重复")
    return cards


def validate_product_cards_against_catalog(
    cards: tuple[ProductCard, ...],
    catalog: FuturesContractCatalog,
) -> None:
    """确保品种卡的交易所、乘数和 tick 与连续研究合约主数据一致。"""

    by_product = {card.product: card for card in cards}
    for contract in catalog.contracts:
        card = by_product.get(contract.product)
        if card is None:
            raise ValueError(f"期货主数据 {contract.product} 缺少品种卡")
        if (card.exchange, card.multiplier, card.tick_size) != (
            contract.exchange,
            contract.multiplier,
            contract.tick_size,
        ):
            raise ValueError(f"品种卡 {card.product} 与期货主数据的交易规格不一致")


def _parse_card(payload: dict[str, Any], path: Path) -> ProductCard:
    if set(payload) != _ROOT_FIELDS:
        raise ValueError(f"品种卡 {path.name} 字段不完整或包含未知字段")
    if payload["version"] != 1:
        raise ValueError(f"品种卡 {path.name} version 必须为 1")
    contract = _object(payload["contract"], path, "contract", _CONTRACT_FIELDS)
    trading = _object(payload["trading"], path, "trading", _TRADING_FIELDS)
    delivery = _object(payload["delivery"], path, "delivery", _DELIVERY_FIELDS)
    roll = _object(payload["roll"], path, "roll", _ROLL_FIELDS)
    risk = _object(payload["risk"], path, "risk", _RISK_FIELDS)
    _object(payload["research"], path, "research", _RESEARCH_FIELDS)
    _object(payload["sources"], path, "sources", _SOURCES_FIELDS)
    dynamic_fields = _enum_list(
        DynamicProductSnapshotField,
        risk["dynamic_snapshot"],
        path,
        "risk.dynamic_snapshot",
    )
    if set(dynamic_fields) != set(DynamicProductSnapshotField):
        raise ValueError(f"品种卡 {path.name} 必须完整且唯一地声明动态交易快照字段")
    return ProductCard(
        product=_text(payload["product"], path, "product"),
        name=_text(payload["name"], path, "name"),
        exchange=_text(payload["exchange"], path, "exchange"),
        multiplier=_positive(contract.get("multiplier"), path, "contract.multiplier"),
        tick_size=_positive(contract.get("tick_size"), path, "contract.tick_size"),
        delivery_months=_delivery_months(contract.get("delivery_months"), path),
        has_night_session=_boolean(trading["has_night_session"], path, "trading.has_night_session"),
        session_rule=_enum(ProductSessionRule, trading["session_rule"], path, "trading.session_rule"),
        last_trade_day_rule=_enum(
            LastTradeDayRule,
            delivery["last_trade_day_rule"],
            path,
            "delivery.last_trade_day_rule",
        ),
        individual_investor_rule=_enum(
            IndividualInvestorRule,
            delivery["individual_investor_rule"],
            path,
            "delivery.individual_investor_rule",
        ),
        rollover_method=_enum(RolloverMethod, roll["method"], path, "roll.method"),
        rollover_reference_signals=_enum_list(
            RolloverReferenceSignal,
            roll["reference_signals"],
            path,
            "roll.reference_signals",
        ),
        dynamic_snapshot_fields=dynamic_fields,
        payload=payload,
    )


def _text(value: object, path: Path, field: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"品种卡 {path.name} 的 {field} 不能为空")
    return normalized.upper() if field in {"product", "exchange"} else normalized


def _positive(value: object, path: Path, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"品种卡 {path.name} 的 {field} 必须为正数") from exc
    if parsed <= 0:
        raise ValueError(f"品种卡 {path.name} 的 {field} 必须为正数")
    return parsed


def _delivery_months(value: object, path: Path) -> tuple[int, ...]:
    """校验交割月份为升序、无重复且位于 1 至 12 的整数列表。"""

    if not isinstance(value, list) or not value:
        raise ValueError(f"品种卡 {path.name} 的 contract.delivery_months 必须是非空列表")
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise ValueError(f"品种卡 {path.name} 的 contract.delivery_months 必须是整数列表")
    months = tuple(value)
    if any(item < 1 or item > 12 for item in months) or months != tuple(sorted(set(months))):
        raise ValueError(f"品种卡 {path.name} 的 contract.delivery_months 必须升序且在 1 至 12")
    return months


def _object(value: object, path: Path, field: str, expected_fields: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise ValueError(f"品种卡 {path.name} 的 {field} 字段不完整或包含未知字段")
    return value


def _boolean(value: object, path: Path, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"品种卡 {path.name} 的 {field} 必须是布尔值")
    return value


def _enum(enum_cls, value: object, path: Path, field: str):
    try:
        return enum_cls.parse(str(value))
    except ValueError as exc:
        raise ValueError(f"品种卡 {path.name} 的 {field} 取值无效") from exc


def _enum_list(enum_cls, value: object, path: Path, field: str) -> tuple:
    if not isinstance(value, list) or not value:
        raise ValueError(f"品种卡 {path.name} 的 {field} 必须是非空列表")
    values = tuple(_enum(enum_cls, item, path, field) for item in value)
    if len(values) != len(set(values)):
        raise ValueError(f"品种卡 {path.name} 的 {field} 不能重复")
    return values

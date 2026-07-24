"""国内期货品种卡的加载与一致性校验。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from northstar_quant.config.futures_contracts import FuturesContractCatalog
from northstar_quant.config.settings import get_settings
from northstar_quant.config.yaml_loader import load_yaml


@dataclass(frozen=True, slots=True)
class ProductCard:
    """一张品种卡；动态交易参数只描述来源，不保存可能过期的数值。"""

    product: str
    name: str
    exchange: str
    multiplier: float
    tick_size: float
    has_night_session: bool
    dynamic_snapshot_fields: tuple[str, ...]
    payload: dict[str, Any]


_ROOT_FIELDS = frozenset(
    {"version", "product", "name", "exchange", "contract", "trading", "delivery", "roll", "risk", "research", "sources"}
)
_REQUIRED_DYNAMIC_FIELDS = frozenset(
    {"trading_sessions", "margin_rate", "commission", "price_limits", "position_limits", "active_contract"}
)


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
    contract, trading, risk = payload["contract"], payload["trading"], payload["risk"]
    if not all(isinstance(item, dict) for item in (contract, trading, risk)):
        raise ValueError(f"品种卡 {path.name} 的 contract、trading、risk 必须是对象")
    dynamic_fields = tuple(str(item) for item in risk.get("dynamic_snapshot", ()))
    if not _REQUIRED_DYNAMIC_FIELDS.issubset(dynamic_fields):
        raise ValueError(f"品种卡 {path.name} 未完整声明动态交易快照字段")
    return ProductCard(
        product=_text(payload["product"], path, "product"),
        name=_text(payload["name"], path, "name"),
        exchange=_text(payload["exchange"], path, "exchange"),
        multiplier=_positive(contract.get("multiplier"), path, "contract.multiplier"),
        tick_size=_positive(contract.get("tick_size"), path, "contract.tick_size"),
        has_night_session=bool(trading.get("has_night_session")),
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

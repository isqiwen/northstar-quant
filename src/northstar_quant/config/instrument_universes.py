"""期货研究品种池的严格配置与品种卡一致性校验。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from northstar_quant.config.product_cards import load_product_cards
from northstar_quant.config.settings import get_settings
from northstar_quant.config.yaml_loader import load_yaml


class InstrumentUniverseConfigError(ValueError):
    """研究品种池配置不完整或与品种卡冲突。"""


_ROOT_FIELDS = frozenset({"version", "universe_id", "name", "market", "asset_type", "status", "members"})
_MEMBER_FIELDS = frozenset({"product", "exchange", "continuous_symbol", "admission_tier"})
_STATUSES = frozenset({"active", "research_only", "retired"})
_ADMISSION_TIERS = frozenset({"core", "extension", "sample"})


@dataclass(frozen=True, slots=True)
class InstrumentUniverseMember:
    """一个稳定期货品种与其连续研究符号。"""

    product: str
    exchange: str
    continuous_symbol: str
    admission_tier: str


@dataclass(frozen=True, slots=True)
class InstrumentUniverse:
    """一个版本化研究宇宙；成员不携带具体交割月合约。"""

    universe_id: str
    name: str
    market: str
    asset_type: str
    status: str
    members: tuple[InstrumentUniverseMember, ...]

    def members_for_tier(self, tier: str) -> tuple[InstrumentUniverseMember, ...]:
        """按准入层级返回成员。"""

        normalized = _choice(tier, _ADMISSION_TIERS, "admission_tier")
        return tuple(member for member in self.members if member.admission_tier == normalized)

    @property
    def products(self) -> tuple[str, ...]:
        return tuple(member.product for member in self.members)

    @property
    def continuous_symbols(self) -> tuple[str, ...]:
        return tuple(member.continuous_symbol for member in self.members)

    def product_coverage(self, products: set[str], *, tier: str | None = None) -> float:
        """计算已观测品种对指定层级的覆盖率。"""

        members = self.members_for_tier(tier) if tier is not None else self.members
        expected = {member.product for member in members}
        if not expected:
            return 0.0
        return len(expected.intersection(products)) / len(expected)


def get_instrument_universe_directory(path: str | Path | None = None) -> Path:
    """返回期货研究品种池目录。"""

    if path is None:
        return get_settings().project_root / "configs" / "instruments" / "universes"
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = get_settings().project_root / candidate
    return candidate.resolve()


def get_instrument_universe_path(
    universe_id: str,
    directory: str | Path | None = None,
) -> Path:
    """按 universe_id 解析唯一 YAML 路径。"""

    normalized = _required_text(universe_id, "universe_id")
    return get_instrument_universe_directory(directory) / f"{normalized}.yaml"


def load_instrument_universe(
    universe_id: str,
    directory: str | Path | None = None,
) -> InstrumentUniverse:
    """读取一个品种池，并校验成员与现有品种卡严格一致。"""

    config_path = get_instrument_universe_path(universe_id, directory)
    if not config_path.is_file():
        raise InstrumentUniverseConfigError(f"期货品种池配置不存在：{config_path}")
    payload = load_yaml(config_path)
    if not isinstance(payload, dict) or set(payload) != _ROOT_FIELDS:
        raise InstrumentUniverseConfigError("期货品种池配置字段不完整或包含未知字段")
    if payload["version"] != 1:
        raise InstrumentUniverseConfigError("期货品种池配置 version 当前必须为 1")
    configured_id = _required_text(payload["universe_id"], "universe_id")
    if configured_id != _required_text(universe_id, "universe_id"):
        raise InstrumentUniverseConfigError(
            f"品种池文件与声明 ID 不一致：请求 {universe_id}，配置声明 {configured_id}"
        )
    members_raw = payload["members"]
    if not isinstance(members_raw, list) or not members_raw:
        raise InstrumentUniverseConfigError("期货品种池 members 必须是非空列表")
    members = tuple(_parse_member(item, index=index) for index, item in enumerate(members_raw))
    products = [member.product for member in members]
    symbols = [member.continuous_symbol for member in members]
    if len(products) != len(set(products)):
        raise InstrumentUniverseConfigError("期货品种池 product 不能重复")
    if len(symbols) != len(set(symbols)):
        raise InstrumentUniverseConfigError("期货品种池 continuous_symbol 不能重复")
    universe = InstrumentUniverse(
        universe_id=configured_id,
        name=_required_text(payload["name"], "name"),
        market=_required_text(payload["market"], "market").upper(),
        asset_type=_required_text(payload["asset_type"], "asset_type").upper(),
        status=_choice(payload["status"], _STATUSES, "status"),
        members=members,
    )
    _validate_against_product_cards(universe)
    return universe


def instrument_universe_sha256(universe: InstrumentUniverse) -> str:
    """计算品种池配置指纹。"""

    encoded = json.dumps(
        asdict(universe),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_actual_product_membership(
    universe: InstrumentUniverse,
    observed: Mapping[str, str],
) -> dict[str, object]:
    """拒绝不在画像品种池中的实际合约品种或错误交易所。

    数据集可以是正在积累历史的子集，因此此处不要求全量成员都出现；完整覆盖由研究
    准入政策评估。这样工程验收的小样本不会伪装成已满足候选准入。
    """

    expected_exchange = {member.product: member.exchange for member in universe.members}
    normalized_observed = {
        _required_text(product, "observed.product").upper(): _required_text(
            exchange, "observed.exchange"
        ).upper()
        for product, exchange in observed.items()
    }
    unexpected = sorted(set(normalized_observed).difference(expected_exchange))
    if unexpected:
        raise InstrumentUniverseConfigError(
            f"数据品种不属于画像品种池 {universe.universe_id}：{', '.join(unexpected)}"
        )
    wrong_exchange = sorted(
        product
        for product, exchange in normalized_observed.items()
        if expected_exchange[product] != exchange
    )
    if wrong_exchange:
        raise InstrumentUniverseConfigError(
            "数据品种交易所与画像品种池不一致：" + ", ".join(wrong_exchange)
        )
    observed_products = set(normalized_observed)
    return {
        "universe_id": universe.universe_id,
        "configured_products": sorted(expected_exchange),
        "observed_products": sorted(observed_products),
        "product_coverage_ratio": universe.product_coverage(observed_products),
    }


def _parse_member(payload: Any, *, index: int) -> InstrumentUniverseMember:
    context = f"members[{index}]"
    if not isinstance(payload, dict) or set(payload) != _MEMBER_FIELDS:
        raise InstrumentUniverseConfigError(f"{context} 字段不完整或包含未知字段")
    product = _required_text(payload["product"], f"{context}.product").upper()
    exchange = _required_text(payload["exchange"], f"{context}.exchange").upper()
    symbol = _required_text(payload["continuous_symbol"], f"{context}.continuous_symbol").upper()
    if not symbol.endswith("_CONT"):
        raise InstrumentUniverseConfigError(f"{context}.continuous_symbol 必须以 _CONT 结尾")
    return InstrumentUniverseMember(
        product=product,
        exchange=exchange,
        continuous_symbol=symbol,
        admission_tier=_choice(payload["admission_tier"], _ADMISSION_TIERS, f"{context}.admission_tier"),
    )


def _validate_against_product_cards(universe: InstrumentUniverse) -> None:
    cards = {card.product: card for card in load_product_cards()}
    for member in universe.members:
        card = cards.get(member.product)
        if card is None:
            raise InstrumentUniverseConfigError(
                f"品种池 {universe.universe_id} 的 {member.product} 缺少品种卡"
            )
        if card.exchange != member.exchange:
            raise InstrumentUniverseConfigError(
                f"品种池 {universe.universe_id} 的 {member.product} 交易所与品种卡不一致"
            )


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InstrumentUniverseConfigError(f"{field} 必须是非空字符串")
    return value.strip()


def _choice(value: object, allowed: frozenset[str], field: str) -> str:
    normalized = _required_text(value, field).lower()
    if normalized not in allowed:
        raise InstrumentUniverseConfigError(
            f"{field} 取值无效；仅支持：{', '.join(sorted(allowed))}"
        )
    return normalized

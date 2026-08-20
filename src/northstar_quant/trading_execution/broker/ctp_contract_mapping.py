"""CTP 实际月份合约的券商身份映射。

连续合约仅用于研究，不能直接发送到 CTP，也不能由本模块换月成为实际订单。
``continuous_symbol`` 仅保留为人工审计关联；实际执行只能以已解析的具体月份
``data_symbol`` / ``instrument_id`` 查询映射。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, NoReturn

from northstar_quant.platform.config.settings import get_settings
from northstar_quant.platform.config.yaml_loader import load_yaml

_ROOT_FIELDS = frozenset({"version", "broker", "contracts"})
_CONTRACT_FIELDS = frozenset(
    {
        "continuous_symbol",
        "data_symbol",
        "instrument_id",
        "exchange_id",
        "product_id",
        "volume_multiple",
        "price_tick",
        "trading_enabled",
    }
)
_EXCHANGES = frozenset({"SHFE", "DCE", "CZCE", "CFFEX", "INE", "GFEX"})
_INSTRUMENT_ID_RE = re.compile(r"^[A-Za-z]+\d{3,4}$")


class CtpContractMappingError(ValueError):
    """CTP 合约映射配置或查询失败。"""


@dataclass(frozen=True, slots=True)
class CtpContractMapping:
    """一条连续合约到 CTP 具体合约的、可审计的映射。"""

    continuous_symbol: str
    data_symbol: str
    instrument_id: str
    exchange_id: str
    product_id: str
    volume_multiple: int
    price_tick: float
    trading_enabled: bool

    def require_trading_enabled(self) -> "CtpContractMapping":
        """确认该映射已由人工核验并允许交易。"""

        if not self.trading_enabled:
            raise CtpContractMappingError(
                "CTP_CONTRACT_DISABLED: "
                f"{self.data_symbol} 的 CTP 合约映射尚未人工启用。"
            )
        return self


@dataclass(frozen=True, slots=True)
class CtpContractRegistry:
    """不可变 CTP 合约映射表。"""

    version: int
    broker: str
    contracts: tuple[CtpContractMapping, ...]

    def resolve_continuous(self, continuous_symbol: str) -> NoReturn:
        """拒绝连续研究序列进入 CTP 身份解析。

        Contract Master 的未来授权发布链需要先在 Application 组合边界产出具体实际合约，
        再调用 :meth:`resolve_data_symbol`。保留本方法只为了让误用得到明确、安全的错误，
        不返回任何可交易映射。
        """

        normalized = _normalize_data_symbol(continuous_symbol, "continuous_symbol")
        raise CtpContractMappingError(
            "CTP_CONTINUOUS_CONTRACT_FORBIDDEN: "
            f"{normalized} 是连续研究序列，不能解析为 CTP 下单身份。"
        )

    def resolve_data_symbol(self, data_symbol: str) -> CtpContractMapping:
        """按实际数据 symbol 获取已启用的 CTP 合约。"""

        normalized = _normalize_data_symbol(data_symbol, "data_symbol")
        for contract in self.contracts:
            if contract.data_symbol == normalized:
                return contract.require_trading_enabled()
        raise CtpContractMappingError(
            f"CTP_CONTRACT_NOT_CONFIGURED: 未配置 {normalized} 的 CTP 合约。"
        )

    def resolve_ctp_identity(
        self,
        instrument_id: str,
        exchange_id: str,
    ) -> CtpContractMapping:
        """按 CTP 回报中的合约与交易所反向解析数据 symbol。"""

        normalized_instrument = _normalize_instrument_id(instrument_id, "instrument_id")
        normalized_exchange = _normalize_exchange_id(exchange_id, "exchange_id")
        for contract in self.contracts:
            if (
                contract.instrument_id == normalized_instrument
                and contract.exchange_id == normalized_exchange
            ):
                return contract.require_trading_enabled()
        raise CtpContractMappingError(
            "CTP_CONTRACT_NOT_CONFIGURED: 未配置 CTP 合约身份 "
            f"{normalized_exchange}/{normalized_instrument}。"
        )


def get_ctp_contract_mapping_path(config_path: str | Path | None = None) -> Path:
    """返回 CTP 合约映射文件路径。"""

    if config_path is None:
        return get_settings().ctp_contract_mapping_path
    path = Path(config_path)
    if not path.is_absolute():
        path = get_settings().project_root / path
    return path.resolve()


def load_ctp_contract_registry(
    config_path: str | Path | None = None,
    *,
    expected_broker: str | None = None,
) -> CtpContractRegistry:
    """读取并严格校验 CTP 合约映射，不缓存以支持人工换月。"""

    path = get_ctp_contract_mapping_path(config_path)
    if not path.is_file():
        raise CtpContractMappingError(f"CTP_CONTRACT_MAPPING_MISSING: 文件不存在：{path}")

    payload = load_yaml(path)
    if not isinstance(payload, dict):
        raise CtpContractMappingError("CTP 合约映射根节点必须是对象。")
    _ensure_exact_fields(payload, _ROOT_FIELDS, context="CTP 合约映射")

    version = payload["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise CtpContractMappingError("CTP 合约映射 version 当前必须为整数 1。")
    broker = _required_string(payload["broker"], "broker").lower()
    if broker not in {"ctp", "ctp_sim"}:
        raise CtpContractMappingError("CTP 合约映射 broker 必须为 ctp 或 ctp_sim。")
    normalized_expected_broker = str(expected_broker or "").strip().lower()
    if normalized_expected_broker and broker != normalized_expected_broker:
        raise CtpContractMappingError(
            "CTP_CONTRACT_BROKER_MISMATCH: 合约映射 broker "
            f"{broker} 与当前适配器 {normalized_expected_broker} 不一致。"
        )

    raw_contracts = payload["contracts"]
    if not isinstance(raw_contracts, list):
        raise CtpContractMappingError("CTP 合约映射 contracts 必须是列表。")
    contracts = tuple(_parse_contract(item, index) for index, item in enumerate(raw_contracts))
    _validate_unique(contracts)
    return CtpContractRegistry(version=version, broker=broker, contracts=contracts)


def _parse_contract(item: Any, index: int) -> CtpContractMapping:
    context = f"contracts[{index}]"
    if not isinstance(item, dict):
        raise CtpContractMappingError(f"{context} 必须是对象。")
    _ensure_exact_fields(item, _CONTRACT_FIELDS, context=context)

    instrument_id = _normalize_instrument_id(item["instrument_id"], f"{context}.instrument_id")
    data_symbol = _normalize_data_symbol(item["data_symbol"], f"{context}.data_symbol")
    if data_symbol.endswith("_CONT"):
        raise CtpContractMappingError(
            f"{context}.data_symbol 不能是连续研究合约，必须是具体月份合约。"
        )
    if data_symbol.lower() != instrument_id:
        raise CtpContractMappingError(
            f"{context}.data_symbol 必须与 instrument_id 忽略大小写后完全一致。"
        )
    product_id = _required_string(item["product_id"], f"{context}.product_id").lower()
    if not data_symbol.lower().startswith(product_id) or not instrument_id.startswith(product_id):
        raise CtpContractMappingError(
            f"{context}.product_id 必须同时是 data_symbol 和 instrument_id 的前缀。"
        )
    volume_multiple = item["volume_multiple"]
    if isinstance(volume_multiple, bool) or not isinstance(volume_multiple, int) or volume_multiple <= 0:
        raise CtpContractMappingError(f"{context}.volume_multiple 必须是正整数。")
    price_tick = item["price_tick"]
    if isinstance(price_tick, bool) or not isinstance(price_tick, (int, float)) or float(price_tick) <= 0:
        raise CtpContractMappingError(f"{context}.price_tick 必须是正数。")
    enabled = item["trading_enabled"]
    if not isinstance(enabled, bool):
        raise CtpContractMappingError(f"{context}.trading_enabled 必须是布尔值。")

    continuous_symbol = _normalize_data_symbol(item["continuous_symbol"], f"{context}.continuous_symbol")
    if not continuous_symbol.endswith("_CONT"):
        raise CtpContractMappingError(f"{context}.continuous_symbol 必须以 _CONT 结尾。")
    return CtpContractMapping(
        continuous_symbol=continuous_symbol,
        data_symbol=data_symbol,
        instrument_id=instrument_id,
        exchange_id=_normalize_exchange_id(item["exchange_id"], f"{context}.exchange_id"),
        product_id=product_id,
        volume_multiple=volume_multiple,
        price_tick=float(price_tick),
        trading_enabled=enabled,
    )


def _validate_unique(contracts: tuple[CtpContractMapping, ...]) -> None:
    continuous_symbols: set[str] = set()
    data_symbols: set[str] = set()
    identities: set[tuple[str, str]] = set()
    for contract in contracts:
        if contract.continuous_symbol in continuous_symbols:
            raise CtpContractMappingError(f"连续合约映射重复：{contract.continuous_symbol}")
        if contract.data_symbol in data_symbols:
            raise CtpContractMappingError(f"数据合约映射重复：{contract.data_symbol}")
        identity = (contract.exchange_id, contract.instrument_id)
        if identity in identities:
            raise CtpContractMappingError(
                f"CTP 合约身份重复：{contract.exchange_id}/{contract.instrument_id}"
            )
        continuous_symbols.add(contract.continuous_symbol)
        data_symbols.add(contract.data_symbol)
        identities.add(identity)


def _ensure_exact_fields(payload: dict[str, Any], expected: frozenset[str], *, context: str) -> None:
    missing = sorted(expected - set(payload))
    unknown = sorted(set(payload) - expected)
    if missing:
        raise CtpContractMappingError(f"{context} 缺少字段：{', '.join(missing)}")
    if unknown:
        raise CtpContractMappingError(f"{context} 包含未知字段：{', '.join(unknown)}")


def _required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CtpContractMappingError(f"{field_name} 必须是非空字符串。")
    return value.strip()


def _normalize_data_symbol(value: Any, field_name: str) -> str:
    return _required_string(value, field_name).upper()


def _normalize_instrument_id(value: Any, field_name: str) -> str:
    instrument_id = _required_string(value, field_name).lower()
    if not _INSTRUMENT_ID_RE.fullmatch(instrument_id):
        raise CtpContractMappingError(f"{field_name} 必须是 CTP 具体合约代码。")
    return instrument_id


def _normalize_exchange_id(value: Any, field_name: str) -> str:
    exchange_id = _required_string(value, field_name).upper()
    if exchange_id not in _EXCHANGES:
        raise CtpContractMappingError(
            f"{field_name} 不支持 {exchange_id!r}，可选值：{', '.join(sorted(_EXCHANGES))}"
        )
    return exchange_id

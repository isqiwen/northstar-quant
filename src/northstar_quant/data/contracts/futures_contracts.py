"""国内期货研究合约主数据。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from northstar_quant.foundation.config.settings import get_settings
from northstar_quant.foundation.config.yaml_loader import load_yaml

SUPPORTED_FUTURES_EXCHANGES = frozenset({"SHFE", "DCE", "CZCE", "GFEX", "INE"})


class FuturesContractConfigError(ValueError):
    """期货合约主数据不完整或不安全。"""


@dataclass(frozen=True, slots=True)
class FuturesContractSpec:
    """一条研究或执行合约规格。

    ``continuous`` 合约是研究序列，不是券商可交易对象；调用
    :meth:`require_tradable` 时必然拒绝它，避免把连续合约错误送往实盘。
    """

    symbol: str
    product: str
    exchange: str
    contract_kind: str
    multiplier: float
    tick_size: float
    tradable: bool

    def require_tradable(self) -> "FuturesContractSpec":
        """确认该规格可作为实际下单合约。"""

        if self.contract_kind == "continuous" or not self.tradable:
            raise FuturesContractConfigError(
                f"期货合约 {self.symbol} 是连续研究合约或未授权交易，不能下单。"
            )
        return self


@dataclass(frozen=True, slots=True)
class FuturesContractCatalog:
    """不可变的期货合约规格集合。"""

    version: int
    contracts: tuple[FuturesContractSpec, ...]

    def resolve(self, symbol: str) -> FuturesContractSpec:
        """按数据 symbol 查找合约规格。"""

        normalized = _required_string(symbol, field_name="symbol")
        for contract in self.contracts:
            if contract.symbol == normalized:
                return contract
        raise FuturesContractConfigError(f"未配置期货合约规格：{normalized}")


def get_futures_contract_path(path: str | Path) -> Path:
    """解析相对项目根目录的期货合约配置路径。"""

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = get_settings().project_root / candidate
    return candidate.resolve()


def load_futures_contract_catalog(path: str | Path) -> FuturesContractCatalog:
    """读取并严格校验期货合约主数据。"""

    config_path = get_futures_contract_path(path)
    if not config_path.is_file():
        raise FuturesContractConfigError(f"期货合约配置不存在：{config_path}")
    payload = load_yaml(config_path)
    if not isinstance(payload, dict) or set(payload) != {"version", "contracts"}:
        raise FuturesContractConfigError("期货合约配置只能包含 version 和 contracts")
    if payload["version"] != 1:
        raise FuturesContractConfigError("期货合约配置 version 当前必须为 1")
    contracts_raw = payload["contracts"]
    if not isinstance(contracts_raw, list) or not contracts_raw:
        raise FuturesContractConfigError("期货合约配置 contracts 必须是非空列表")

    contracts = tuple(_parse_contract(item, index=index) for index, item in enumerate(contracts_raw))
    symbols = [item.symbol for item in contracts]
    if len(symbols) != len(set(symbols)):
        raise FuturesContractConfigError("期货合约配置 symbol 不能重复")
    return FuturesContractCatalog(version=1, contracts=contracts)


def _parse_contract(payload: Any, *, index: int) -> FuturesContractSpec:
    context = f"contracts[{index}]"
    if not isinstance(payload, dict):
        raise FuturesContractConfigError(f"{context} 必须是对象")
    required = {"symbol", "product", "exchange", "contract_kind", "multiplier", "tick_size", "tradable"}
    if set(payload) != required:
        raise FuturesContractConfigError(f"{context} 字段必须为：{', '.join(sorted(required))}")
    contract_kind = _required_string(payload["contract_kind"], field_name=f"{context}.contract_kind").lower()
    if contract_kind not in {"continuous", "tradable"}:
        raise FuturesContractConfigError(f"{context}.contract_kind 仅支持 continuous / tradable")
    tradable = payload["tradable"]
    if not isinstance(tradable, bool):
        raise FuturesContractConfigError(f"{context}.tradable 必须是布尔值")
    if contract_kind == "continuous" and tradable:
        raise FuturesContractConfigError(f"{context} 连续合约不得标记为 tradable")
    exchange = _required_string(payload["exchange"], field_name=f"{context}.exchange")
    if exchange not in SUPPORTED_FUTURES_EXCHANGES:
        allowed = ", ".join(sorted(SUPPORTED_FUTURES_EXCHANGES))
        raise FuturesContractConfigError(
            f"{context}.exchange 不受支持：{exchange}；当前仅支持 {allowed}"
        )
    return FuturesContractSpec(
        symbol=_required_string(payload["symbol"], field_name=f"{context}.symbol"),
        product=_required_string(payload["product"], field_name=f"{context}.product"),
        exchange=exchange,
        contract_kind=contract_kind,
        multiplier=_positive_number(payload["multiplier"], field_name=f"{context}.multiplier"),
        tick_size=_positive_number(payload["tick_size"], field_name=f"{context}.tick_size"),
        tradable=tradable,
    )


def _required_string(value: object, *, field_name: str) -> str:
    normalized = str(value).strip().upper()
    if not normalized:
        raise FuturesContractConfigError(f"{field_name} 不能为空")
    return normalized


def _positive_number(value: object, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise FuturesContractConfigError(f"{field_name} 必须是正数")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise FuturesContractConfigError(f"{field_name} 必须是正数") from exc
    if numeric <= 0:
        raise FuturesContractConfigError(f"{field_name} 必须是正数")
    return numeric

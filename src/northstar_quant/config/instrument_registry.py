"""受配置管理的券商 instrument 主数据。

真实券商订单不能依赖数据源 symbol 动态猜测合约。本模块把数据 symbol 与
IBKR ``conId`` 的映射作为显式配置加载，并在加载时完成严格校验。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from northstar_quant.config.settings import get_settings
from northstar_quant.config.yaml_loader import load_yaml

_ROOT_FIELDS = frozenset({"version", "broker", "instruments"})
_INSTRUMENT_FIELDS = frozenset(
    {
        "data_symbol",
        "broker_symbol",
        "con_id",
        "sec_type",
        "exchange",
        "primary_exchange",
        "currency",
        "enabled",
    }
)


class InstrumentRegistryError(ValueError):
    """instrument registry 配置或查询失败。"""


@dataclass(frozen=True, slots=True)
class InstrumentDefinition:
    """一条经过校验的 IBKR instrument 映射。"""

    data_symbol: str
    broker_symbol: str
    con_id: int
    sec_type: str
    exchange: str
    primary_exchange: str
    currency: str
    enabled: bool

    @property
    def broker_identity(self) -> tuple[str, str, str, str, str]:
        """返回用于唯一性检查的券商合约身份。"""

        return (
            self.broker_symbol,
            self.sec_type,
            self.exchange,
            self.primary_exchange,
            self.currency,
        )


@dataclass(frozen=True, slots=True)
class InstrumentRegistry:
    """不可变的 instrument registry。"""

    broker: str
    version: int
    instruments: tuple[InstrumentDefinition, ...]

    def resolve(self, data_symbol: str) -> InstrumentDefinition:
        """按数据 symbol 返回启用的映射；缺失或禁用时 fail closed。"""

        normalized_symbol = _normalize_symbol(data_symbol, field_name="data_symbol")
        for instrument in self.instruments:
            if instrument.data_symbol != normalized_symbol:
                continue
            if not instrument.enabled:
                raise InstrumentRegistryError(
                    f"INSTRUMENT_DISABLED: {normalized_symbol} 的 IBKR 映射已禁用。"
                )
            return instrument
        raise InstrumentRegistryError(
            f"INSTRUMENT_NOT_CONFIGURED: 未配置 {normalized_symbol} 的 IBKR 合约映射。"
        )

    def resolve_many(
        self,
        data_symbols: list[str] | tuple[str, ...],
    ) -> tuple[InstrumentDefinition, ...]:
        """按输入顺序解析多条启用映射。"""

        return tuple(self.resolve(symbol) for symbol in data_symbols)

    def resolve_con_id(self, con_id: int) -> InstrumentDefinition:
        """按 ``conId`` 返回启用映射。"""

        if isinstance(con_id, bool) or not isinstance(con_id, int) or con_id <= 0:
            raise InstrumentRegistryError("conId 必须是大于 0 的整数。")
        for instrument in self.instruments:
            if instrument.con_id != con_id:
                continue
            if not instrument.enabled:
                raise InstrumentRegistryError(
                    f"INSTRUMENT_DISABLED: conId={con_id} 的 IBKR 映射已禁用。"
                )
            return instrument
        raise InstrumentRegistryError(
            f"INSTRUMENT_NOT_CONFIGURED: 未配置 conId={con_id} 的 IBKR 合约映射。"
        )


def get_instrument_registry_path(config_path: str | Path | None = None) -> Path:
    """返回 instrument registry 路径。"""

    if config_path is None:
        return get_settings().ibkr_instrument_registry_path
    path = Path(config_path)
    if not path.is_absolute():
        path = get_settings().project_root / path
    return path.resolve()


def load_instrument_registry(
    config_path: str | Path | None = None,
) -> InstrumentRegistry:
    """读取并严格校验 IBKR instrument registry。

    该函数不缓存结果。真实下单每次都重新读取配置，避免进程内旧映射在文件被
    禁用或替换后继续生效。
    """

    path = get_instrument_registry_path(config_path)
    if not path.is_file():
        raise InstrumentRegistryError(
            f"INSTRUMENT_REGISTRY_MISSING: IBKR instrument registry 不存在：{path}"
        )

    payload = load_yaml(path)
    if not isinstance(payload, dict):
        raise InstrumentRegistryError("instrument registry 根节点必须是对象。")
    _ensure_exact_fields(payload, _ROOT_FIELDS, context="instrument registry")

    version = payload["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise InstrumentRegistryError("instrument registry version 当前必须为整数 1。")

    broker = _required_string(payload["broker"], field_name="broker").lower()
    if broker != "ibkr":
        raise InstrumentRegistryError("instrument registry broker 当前必须为 ibkr。")

    raw_instruments = payload["instruments"]
    if not isinstance(raw_instruments, list):
        raise InstrumentRegistryError("instrument registry instruments 必须是列表。")

    instruments = tuple(
        _parse_instrument(item, index=index)
        for index, item in enumerate(raw_instruments)
    )
    _validate_uniqueness(instruments)
    return InstrumentRegistry(
        broker=broker,
        version=version,
        instruments=instruments,
    )


def _parse_instrument(item: Any, *, index: int) -> InstrumentDefinition:
    context = f"instruments[{index}]"
    if not isinstance(item, dict):
        raise InstrumentRegistryError(f"{context} 必须是对象。")
    _ensure_exact_fields(item, _INSTRUMENT_FIELDS, context=context)

    con_id = item["con_id"]
    if isinstance(con_id, bool) or not isinstance(con_id, int) or con_id <= 0:
        raise InstrumentRegistryError(f"{context}.con_id 必须是大于 0 的整数。")

    enabled = item["enabled"]
    if not isinstance(enabled, bool):
        raise InstrumentRegistryError(f"{context}.enabled 必须是明确的布尔值。")

    return InstrumentDefinition(
        data_symbol=_normalize_symbol(
            item["data_symbol"],
            field_name=f"{context}.data_symbol",
        ),
        broker_symbol=_normalize_symbol(
            item["broker_symbol"],
            field_name=f"{context}.broker_symbol",
        ),
        con_id=con_id,
        sec_type=_normalize_symbol(
            item["sec_type"],
            field_name=f"{context}.sec_type",
        ),
        exchange=_normalize_symbol(
            item["exchange"],
            field_name=f"{context}.exchange",
        ),
        primary_exchange=_normalize_symbol(
            item["primary_exchange"],
            field_name=f"{context}.primary_exchange",
        ),
        currency=_normalize_symbol(
            item["currency"],
            field_name=f"{context}.currency",
        ),
        enabled=enabled,
    )


def _validate_uniqueness(
    instruments: tuple[InstrumentDefinition, ...],
) -> None:
    data_symbols: set[str] = set()
    con_ids: set[int] = set()
    broker_identities: set[tuple[str, str, str, str, str]] = set()

    for instrument in instruments:
        if instrument.data_symbol in data_symbols:
            raise InstrumentRegistryError(
                f"instrument registry data_symbol 重复：{instrument.data_symbol}"
            )
        if instrument.con_id in con_ids:
            raise InstrumentRegistryError(
                f"instrument registry con_id 重复：{instrument.con_id}"
            )
        if instrument.broker_identity in broker_identities:
            raise InstrumentRegistryError(
                "instrument registry 券商合约身份重复："
                f"{instrument.broker_symbol}/{instrument.sec_type}/"
                f"{instrument.exchange}/{instrument.primary_exchange}/"
                f"{instrument.currency}"
            )
        data_symbols.add(instrument.data_symbol)
        con_ids.add(instrument.con_id)
        broker_identities.add(instrument.broker_identity)


def _ensure_exact_fields(
    payload: dict[str, Any],
    required_fields: frozenset[str],
    *,
    context: str,
) -> None:
    actual_fields = set(payload)
    missing_fields = sorted(required_fields - actual_fields)
    unknown_fields = sorted(actual_fields - required_fields)
    if missing_fields:
        raise InstrumentRegistryError(
            f"{context} 缺少字段：{', '.join(missing_fields)}"
        )
    if unknown_fields:
        raise InstrumentRegistryError(
            f"{context} 包含未知字段：{', '.join(unknown_fields)}"
        )


def _required_string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InstrumentRegistryError(f"{field_name} 必须是非空字符串。")
    return value.strip()


def _normalize_symbol(value: Any, *, field_name: str) -> str:
    return _required_string(value, field_name=field_name).upper()

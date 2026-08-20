"""合约主数据 YAML 的严格加载器。

静态 identity 可以随仓库发布；保证金、手续费、涨跌停和会话等动态规则只能以带来源
摘要和可用时间的 ``rule_snapshots`` 进入主数据。仓库默认配置故意不内置可执行规则，
避免研究配置或过期静态数值误被当成真实下单依据。
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from northstar_quant.data_platform.contracts.contract_master import (
    Commodity,
    Contract,
    ContractMaster,
    ContractMasterError,
    ContinuousResearchSeries,
    Exchange,
    Instrument,
)
from northstar_quant.platform.config.settings import get_settings
from northstar_quant.platform.config.yaml_loader import load_yaml


DEFAULT_CONTRACT_MASTER_PATH = "configs/instruments/contract_master.yaml"
_ROOT_FIELDS = frozenset(
    {
        "version",
        "master_id",
        "commodities",
        "exchanges",
        "instruments",
        "continuous_series",
        "contracts",
        "rule_snapshots",
    }
)


def get_contract_master_path(path: str | Path | None = None) -> Path:
    """返回主数据文件路径；相对路径相对项目根目录解释。"""

    candidate = Path(path or DEFAULT_CONTRACT_MASTER_PATH)
    if not candidate.is_absolute():
        candidate = get_settings().project_root / candidate
    return candidate.resolve()


def load_contract_master(path: str | Path | None = None) -> ContractMaster:
    """读取并严格校验一份不可变 Contract Master 配置。"""

    config_path = get_contract_master_path(path)
    if not config_path.is_file():
        raise ContractMasterError(f"合约主数据配置不存在：{config_path}")
    payload = load_yaml(config_path)
    if not isinstance(payload, dict):
        raise ContractMasterError("合约主数据根节点必须是对象")
    _exact_fields(payload, _ROOT_FIELDS, "contract master")
    if type(payload["version"]) is not int or payload["version"] != 1:
        raise ContractMasterError("contract master version 当前必须是整数 1")
    rule_snapshots = _list(payload["rule_snapshots"], "rule_snapshots")
    if rule_snapshots:
        raise ContractMasterError(
            "静态 contract master 不得声明 rule_snapshots；"
            "可执行规则只能由后续已授权的不可变制品发布链生成"
        )
    return ContractMaster(
        master_id=_text(payload["master_id"], "master_id"),
        version=f"v{payload['version']}",
        commodities=tuple(
            Commodity(
                commodity_id=_text(item.get("commodity_id"), "commodities[].commodity_id"),
                name=_text(item.get("name"), "commodities[].name"),
            )
            for item in _objects(payload["commodities"], {"commodity_id", "name"}, "commodities")
        ),
        exchanges=tuple(
            Exchange(
                exchange_id=_text(item.get("exchange_id"), "exchanges[].exchange_id"),
                name=_text(item.get("name"), "exchanges[].name"),
                market=_text(item.get("market"), "exchanges[].market"),
                timezone_name=_text(item.get("timezone_name"), "exchanges[].timezone_name"),
            )
            for item in _objects(
                payload["exchanges"],
                {"exchange_id", "name", "market", "timezone_name"},
                "exchanges",
            )
        ),
        instruments=tuple(
            Instrument(
                instrument_id=_text(item.get("instrument_id"), "instruments[].instrument_id"),
                commodity_id=_text(item.get("commodity_id"), "instruments[].commodity_id"),
                exchange_id=_text(item.get("exchange_id"), "instruments[].exchange_id"),
                product_code=_text(item.get("product_code"), "instruments[].product_code"),
            )
            for item in _objects(
                payload["instruments"],
                {"instrument_id", "commodity_id", "exchange_id", "product_code"},
                "instruments",
            )
        ),
        continuous_series=tuple(
            ContinuousResearchSeries(
                series_id=_text(item.get("series_id"), "continuous_series[].series_id"),
                instrument_id=_text(item.get("instrument_id"), "continuous_series[].instrument_id"),
                symbol=_text(item.get("symbol"), "continuous_series[].symbol"),
            )
            for item in _objects(
                payload["continuous_series"],
                {"series_id", "instrument_id", "symbol"},
                "continuous_series",
            )
        ),
        contracts=tuple(
            Contract(
                contract_id=_text(item.get("contract_id"), "contracts[].contract_id"),
                instrument_id=_text(item.get("instrument_id"), "contracts[].instrument_id"),
                symbol=_text(item.get("symbol"), "contracts[].symbol"),
                listed_on=_date(item.get("listed_on"), "contracts[].listed_on"),
                expires_on=_date(item.get("expires_on"), "contracts[].expires_on"),
            )
            for item in _objects(
                payload["contracts"],
                {"contract_id", "instrument_id", "symbol", "listed_on", "expires_on"},
                "contracts",
            )
        ),
        rule_snapshots=(),
    )


def _objects(value: object, fields: set[str], context: str) -> tuple[dict[str, Any], ...]:
    return tuple(
        _validated_object(item, fields, f"{context}[{index}]")
        for index, item in enumerate(_list(value, context))
    )


def _validated_object(value: object, fields: set[str], context: str) -> dict[str, Any]:
    item = _object(value, context)
    _exact_fields(item, fields, context)
    return item


def _object(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractMasterError(f"{context} 必须是对象")
    return value


def _list(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise ContractMasterError(f"{context} 必须是列表")
    return value


def _exact_fields(payload: dict[str, Any], expected: set[str] | frozenset[str], context: str) -> None:
    missing = sorted(expected - set(payload))
    unknown = sorted(set(payload) - expected)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append("缺少字段：" + ", ".join(missing))
        if unknown:
            details.append("未知字段：" + ", ".join(unknown))
        raise ContractMasterError(f"{context} " + "；".join(details))


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractMasterError(f"{context} 必须是非空字符串")
    return value.strip()


def _date(value: object, context: str) -> date:
    if isinstance(value, datetime):
        raise ContractMasterError(f"{context} 必须是 ISO 日期")
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ContractMasterError(f"{context} 必须是 ISO 日期")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ContractMasterError(f"{context} 必须是 ISO 日期") from exc

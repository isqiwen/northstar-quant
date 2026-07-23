"""从正式 instrument 映射构造并校验 IBKR 合约。"""

from __future__ import annotations

from typing import Any

from northstar_quant.config.instrument_registry import InstrumentDefinition

Contract: Any = None
try:
    from ib_async import Contract as _Contract
except Exception:  # pragma: no cover
    pass
else:
    Contract = _Contract


def build_ibkr_contract(instrument: InstrumentDefinition):
    """只使用已核验映射构造 IBKR Contract。"""

    if Contract is None:
        raise RuntimeError("未安装 ib_async，无法构造 IBKR 合约。")
    return Contract(
        conId=instrument.con_id,
        symbol=instrument.broker_symbol,
        secType=instrument.sec_type,
        exchange=instrument.exchange,
        primaryExchange=instrument.primary_exchange,
        currency=instrument.currency,
    )


def qualify_ibkr_contract(
    ib: Any,
    instrument: InstrumentDefinition,
):
    """向 IBKR qualification，并要求返回的 ``conId`` 与配置精确一致。"""

    requested_contract = build_ibkr_contract(instrument)
    qualified_contracts = ib.qualifyContracts(requested_contract)
    if len(qualified_contracts) != 1:
        raise ValueError(
            "IBKR 合约 qualification 必须唯一，"
            f"data_symbol={instrument.data_symbol}，"
            f"configured_con_id={instrument.con_id}，"
            f"matches={len(qualified_contracts)}。"
        )

    qualified_contract = qualified_contracts[0]
    qualified_con_id = int(getattr(qualified_contract, "conId", 0) or 0)
    if qualified_con_id != instrument.con_id:
        raise ValueError(
            "IBKR_CON_ID_MISMATCH: qualification 返回的 conId 与正式映射不一致，"
            f"data_symbol={instrument.data_symbol}，"
            f"configured={instrument.con_id}，returned={qualified_con_id}。"
        )
    return qualified_contract

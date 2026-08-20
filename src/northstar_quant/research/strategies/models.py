"""策略层的结构化输出模型。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class TradePlan:
    """单笔候选交易计划。

    该模型表达策略研究得出的方向、入场、初始止损、目标和失效条件，
    不包含账户资金、单笔风险额度或下单数量。这些账户相关字段必须由
    ``risk`` 与 ``execution`` 模块在通过风控检查后补充，不能由策略绕过。

    ``side`` 当前支持 ``BUY`` / ``SELL``；是否允许做空仍由交易画像和
    执行风控决定。价格约束在创建时即校验，避免不合理的计划进入后续流程。
    """

    symbol: str
    signal_value: float
    side: str
    planned_entry_price: float
    initial_stop_price: float
    entry_condition: str
    decision_time: date | datetime
    target_price: float | None = None
    target_r: float | None = None
    cancel_condition: str | None = None
    reason: str | None = None
    trend: str | None = None
    support_price: float | None = None
    resistance_price: float | None = None

    def __post_init__(self) -> None:
        side = self.side.upper().strip()
        object.__setattr__(self, "side", side)
        if not self.symbol.strip():
            raise ValueError("TradePlan 的 symbol 不能为空")
        if side not in {"BUY", "SELL"}:
            raise ValueError("TradePlan 的 side 仅支持 BUY / SELL")
        if self.planned_entry_price <= 0 or self.initial_stop_price <= 0:
            raise ValueError("TradePlan 的计划入场价和初始止损价必须大于 0")
        if side == "BUY" and self.initial_stop_price >= self.planned_entry_price:
            raise ValueError("BUY TradePlan 的初始止损价必须低于计划入场价")
        if side == "SELL" and self.initial_stop_price <= self.planned_entry_price:
            raise ValueError("SELL TradePlan 的初始止损价必须高于计划入场价")
        if self.target_price is not None:
            if self.target_price <= 0:
                raise ValueError("TradePlan 的目标价必须大于 0")
            if side == "BUY" and self.target_price <= self.planned_entry_price:
                raise ValueError("BUY TradePlan 的目标价必须高于计划入场价")
            if side == "SELL" and self.target_price >= self.planned_entry_price:
                raise ValueError("SELL TradePlan 的目标价必须低于计划入场价")
        if self.target_r is not None and self.target_r <= 0:
            raise ValueError("TradePlan 的 target_r 必须大于 0")
        if self.target_price is not None and self.target_r is not None:
            implied_target_r = (
                abs(self.target_price - self.planned_entry_price) / self.risk_per_unit
            )
            if abs(implied_target_r - self.target_r) > 1e-8:
                raise ValueError("TradePlan 的目标价与 target_r 不一致")
        if not self.entry_condition.strip():
            raise ValueError("TradePlan 的入场条件不能为空")

    @property
    def risk_per_unit(self) -> float:
        """每单位初始风险，等于计划入场价与初始止损价之差的绝对值。"""

        return abs(self.planned_entry_price - self.initial_stop_price)

    @property
    def resolved_target_price(self) -> float | None:
        """返回目标价；仅给定目标 R 时按初始风险推导。"""

        if self.target_price is not None:
            return self.target_price
        if self.target_r is None:
            return None
        direction = 1.0 if self.side == "BUY" else -1.0
        return self.planned_entry_price + direction * self.risk_per_unit * self.target_r

    @property
    def resolved_target_r(self) -> float | None:
        """返回目标盈亏 R；仅给定目标价时按初始风险推导。"""

        if self.target_r is not None:
            return self.target_r
        target_price = self.resolved_target_price
        if target_price is None:
            return None
        return abs(target_price - self.planned_entry_price) / self.risk_per_unit

    def to_row(self, *, time_column: str = "date") -> dict[str, object]:
        """转换为策略管线使用的一行标准化字段。"""

        target_price = self.resolved_target_price
        target_r = self.resolved_target_r
        reward_per_unit = (
            abs(target_price - self.planned_entry_price)
            if target_price is not None
            else None
        )
        return {
            time_column: self.decision_time,
            "symbol": self.symbol,
            "signal_value": self.signal_value,
            "side": self.side,
            "planned_entry_price": self.planned_entry_price,
            "initial_stop_price": self.initial_stop_price,
            "target_price": target_price,
            "target_r": target_r,
            "entry_condition": self.entry_condition,
            "cancel_condition": self.cancel_condition,
            "reason": self.reason or "trade_plan",
            "trend": self.trend,
            "support_price": self.support_price,
            "resistance_price": self.resistance_price,
            "risk_per_unit": self.risk_per_unit,
            "reward_per_unit": reward_per_unit,
            "risk_reward_ratio": target_r,
        }

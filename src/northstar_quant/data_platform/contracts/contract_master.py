"""期货合约主数据的不可变领域模型。

本模块只描述中立的市场事实：商品、交易所、品种、实际月份合约、连续研究序列和
可审计的规则快照。它不认识 CTP、账户或订单；把实际合约绑定到某个券商身份只能在
Application / Trading & Execution 组合边界完成。

连续研究序列与实际交割合约使用不同类型。解析器绝不会把 ``*_CONT`` 猜测或转换为
可执行合约；缺规则、规则未来才可用、状态不明或交割受限时一律返回不可执行结论。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from enum import Enum
import math
import re
from typing import TypedDict, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from northstar_quant.data_platform.artifacts.fingerprints import (
    FingerprintError,
    canonical_json_sha256,
    require_sha256,
)


class ContractMasterError(ValueError):
    """合约主数据缺失、不一致或不具备执行资格。"""


class ListingState(str, Enum):
    """实际合约在规则快照中确认的挂牌状态。"""

    LISTED = "listed"
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    UNLISTED = "unlisted"
    UNKNOWN = "unknown"


class RuleQualityStatus(str, Enum):
    """规则快照的质量结论；只有 PASS 可作为执行证据。"""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    UNKNOWN = "unknown"


class DeliveryRestriction(str, Enum):
    """交割期约束。

    P1-WP03 尚未实现开平仓语义和日历判定，因此除了 ``NONE`` 外的所有值均会让
    ``resolve_for_execution`` 失败关闭；后续交易日历和执行规则只能在不放宽此默认值的
    前提下细化 CLOSE_ONLY 等情形。
    """

    NONE = "none"
    CLOSE_ONLY = "close_only"
    NO_NEW_POSITION = "no_new_position"
    NO_TRADING = "no_trading"
    UNKNOWN = "unknown"


class ContractResolutionStatus(str, Enum):
    """一次显式时点解析的结论。"""

    RESOLVED = "resolved"
    UNKNOWN = "unknown"
    AMBIGUOUS = "ambiguous"
    CONTINUOUS_RESEARCH_ONLY = "continuous_research_only"
    RULES_UNKNOWN = "rules_unknown"
    RULES_NOT_YET_AVAILABLE = "rules_not_yet_available"
    NOT_LISTED = "not_listed"
    EXPIRED = "expired"
    DELIVERY_RESTRICTED = "delivery_restricted"
    RULES_NOT_EXECUTION_ELIGIBLE = "rules_not_execution_eligible"


class _NormalizedRuleSnapshotFields(TypedDict):
    """已经过运行时验证、可安全参与规则快照哈希的字段。"""

    snapshot_id: str
    contract_id: str
    observed_at: datetime
    available_at: datetime
    effective_from: datetime
    effective_until: datetime | None
    listing_state: ListingState
    expires_on: date
    multiplier: float
    tick_size: float
    initial_margin_rate: float
    fees: "ContractFeeSchedule"
    lower_price_limit: float
    upper_price_limit: float
    sessions: tuple["ContractTradingSession", ...]
    delivery_restriction: DeliveryRestriction
    source_artifact_hash: str
    source_authority: str
    quality_status: RuleQualityStatus
    execution_eligible: bool


_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_SYMBOL_RE = re.compile(r"^[A-Z]+[0-9]{3,4}$")
_CONTINUOUS_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]*_CONT$")


@dataclass(frozen=True, slots=True)
class Commodity:
    """经济品类的规范身份，不等同于交易所品种代码。"""

    commodity_id: str
    name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "commodity_id", _identifier(self.commodity_id, "commodity_id"))
        object.__setattr__(self, "name", _text(self.name, "commodity.name"))


@dataclass(frozen=True, slots=True)
class Exchange:
    """交易所的规范身份和时区事实。"""

    exchange_id: str
    name: str
    market: str
    timezone_name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "exchange_id", _identifier(self.exchange_id, "exchange_id"))
        object.__setattr__(self, "name", _text(self.name, "exchange.name"))
        object.__setattr__(self, "market", _identifier(self.market, "exchange.market"))
        timezone_name = _text(self.timezone_name, "exchange.timezone_name")
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ContractMasterError("exchange.timezone_name 必须是有效 IANA 时区") from exc
        object.__setattr__(self, "timezone_name", timezone_name)


@dataclass(frozen=True, slots=True)
class Instrument:
    """商品在一间交易所挂牌的稳定品种，不含交割年月。"""

    instrument_id: str
    commodity_id: str
    exchange_id: str
    product_code: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument_id", _identifier(self.instrument_id, "instrument_id"))
        object.__setattr__(self, "commodity_id", _identifier(self.commodity_id, "commodity_id"))
        object.__setattr__(self, "exchange_id", _identifier(self.exchange_id, "exchange_id"))
        product_code = _identifier(self.product_code, "instrument.product_code")
        if not product_code.isalpha():
            raise ContractMasterError("instrument.product_code 必须只包含字母")
        object.__setattr__(self, "product_code", product_code)


@dataclass(frozen=True, slots=True)
class ContinuousResearchSeries:
    """只服务研究和回测的连续序列，永远不是可下单合约。"""

    series_id: str
    instrument_id: str
    symbol: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "series_id", _identifier(self.series_id, "series_id"))
        object.__setattr__(self, "instrument_id", _identifier(self.instrument_id, "instrument_id"))
        symbol = _symbol(self.symbol, "continuous_series.symbol")
        if not _CONTINUOUS_SYMBOL_RE.fullmatch(symbol):
            raise ContractMasterError("continuous_series.symbol 必须以 _CONT 结尾")
        object.__setattr__(self, "symbol", symbol)


@dataclass(frozen=True, slots=True)
class Contract:
    """一个实际月份合约。

    此类型刻意不接受 ``*_CONT``。连续序列只能使用 :class:`ContinuousResearchSeries`，
    因而不能通过继承、类型转换或解析器自动换月进入执行路径。
    """

    contract_id: str
    instrument_id: str
    symbol: str
    listed_on: date
    expires_on: date

    def __post_init__(self) -> None:
        object.__setattr__(self, "contract_id", _identifier(self.contract_id, "contract_id"))
        object.__setattr__(self, "instrument_id", _identifier(self.instrument_id, "instrument_id"))
        symbol = _symbol(self.symbol, "contract.symbol")
        if symbol.endswith("_CONT") or not _SYMBOL_RE.fullmatch(symbol):
            raise ContractMasterError("contract.symbol 必须是实际月份合约代码，不能是连续序列")
        object.__setattr__(self, "symbol", symbol)
        if not isinstance(self.listed_on, date) or isinstance(self.listed_on, datetime):
            raise ContractMasterError("contract.listed_on 必须是 date")
        if not isinstance(self.expires_on, date) or isinstance(self.expires_on, datetime):
            raise ContractMasterError("contract.expires_on 必须是 date")
        if self.expires_on < self.listed_on:
            raise ContractMasterError("contract.expires_on 不能早于 contract.listed_on")


@dataclass(frozen=True, slots=True)
class ContractFeeSchedule:
    """规则快照中的已知费率；金额和比例均显式保存，零值也是已知值。"""

    open_per_lot: float
    open_rate: float
    close_per_lot: float
    close_rate: float
    close_today_per_lot: float
    close_today_rate: float

    def __post_init__(self) -> None:
        for field_name in (
            "open_per_lot",
            "open_rate",
            "close_per_lot",
            "close_rate",
            "close_today_per_lot",
            "close_today_rate",
        ):
            object.__setattr__(
                self,
                field_name,
                _nonnegative_number(getattr(self, field_name), f"fees.{field_name}"),
            )


@dataclass(frozen=True, slots=True)
class ContractTradingSession:
    """规则快照声明的一段交易会话；跨日语义由 P1-WP04 日历解释。"""

    session_id: str
    opens_at: time
    closes_at: time

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _identifier(self.session_id, "session.session_id"))
        if not isinstance(self.opens_at, time) or not isinstance(self.closes_at, time):
            raise ContractMasterError("session.opens_at 与 session.closes_at 必须是 time")
        if self.opens_at == self.closes_at:
            raise ContractMasterError("session.opens_at 与 session.closes_at 不能相同")


@dataclass(frozen=True, slots=True)
class ContractRuleSnapshot:
    """在明确时间窗口中可用的一份实际合约规则证据。

    规则数值并不从产品卡或研究行情推断。调用方必须提供来源制品摘要、质量和执行资格；
    当前公开研究数据只能形成研究证据，不能因此变成真实交易规则。
    """

    snapshot_id: str
    contract_id: str
    observed_at: datetime
    available_at: datetime
    effective_from: datetime
    effective_until: datetime | None
    listing_state: ListingState
    expires_on: date
    multiplier: float
    tick_size: float
    initial_margin_rate: float
    fees: ContractFeeSchedule
    lower_price_limit: float
    upper_price_limit: float
    sessions: tuple[ContractTradingSession, ...]
    delivery_restriction: DeliveryRestriction
    source_artifact_hash: str
    source_authority: str
    quality_status: RuleQualityStatus
    execution_eligible: bool
    snapshot_hash: str

    def __post_init__(self) -> None:
        normalized = _normalize_rule_snapshot_input(
            _NormalizedRuleSnapshotInput(
                snapshot_id=self.snapshot_id,
                contract_id=self.contract_id,
                observed_at=self.observed_at,
                available_at=self.available_at,
                effective_from=self.effective_from,
                effective_until=self.effective_until,
                listing_state=self.listing_state,
                expires_on=self.expires_on,
                multiplier=self.multiplier,
                tick_size=self.tick_size,
                initial_margin_rate=self.initial_margin_rate,
                fees=self.fees,
                lower_price_limit=self.lower_price_limit,
                upper_price_limit=self.upper_price_limit,
                sessions=self.sessions,
                delivery_restriction=self.delivery_restriction,
                source_artifact_hash=self.source_artifact_hash,
                source_authority=self.source_authority,
                quality_status=self.quality_status,
                execution_eligible=self.execution_eligible,
            )
        )
        for field_name, value in normalized.items():
            object.__setattr__(self, field_name, value)
        expected_hash = _contract_rule_snapshot_hash(**normalized)
        if self.snapshot_hash != expected_hash:
            raise ContractMasterError("rules.snapshot_hash 与规则内容或证据不一致")
        object.__setattr__(self, "snapshot_hash", _sha256(self.snapshot_hash, "rules.snapshot_hash"))

    @classmethod
    def create(
        cls,
        *,
        snapshot_id: str,
        contract_id: str,
        observed_at: datetime,
        available_at: datetime,
        effective_from: datetime,
        effective_until: datetime | None,
        listing_state: ListingState,
        expires_on: date,
        multiplier: float,
        tick_size: float,
        initial_margin_rate: float,
        fees: ContractFeeSchedule,
        lower_price_limit: float,
        upper_price_limit: float,
        sessions: tuple[ContractTradingSession, ...],
        delivery_restriction: DeliveryRestriction,
        source_artifact_hash: str,
        source_authority: str,
        quality_status: RuleQualityStatus,
        execution_eligible: bool,
    ) -> "ContractRuleSnapshot":
        """构造并绑定内容哈希，避免调用方手写快照身份。"""

        normalized = _normalize_rule_snapshot_input(
            _NormalizedRuleSnapshotInput(
                snapshot_id=snapshot_id,
                contract_id=contract_id,
                observed_at=observed_at,
                available_at=available_at,
                effective_from=effective_from,
                effective_until=effective_until,
                listing_state=listing_state,
                expires_on=expires_on,
                multiplier=multiplier,
                tick_size=tick_size,
                initial_margin_rate=initial_margin_rate,
                fees=fees,
                lower_price_limit=lower_price_limit,
                upper_price_limit=upper_price_limit,
                sessions=sessions,
                delivery_restriction=delivery_restriction,
                source_artifact_hash=source_artifact_hash,
                source_authority=source_authority,
                quality_status=quality_status,
                execution_eligible=execution_eligible,
            )
        )
        return cls(
            **normalized,
            snapshot_hash=_contract_rule_snapshot_hash(**normalized),
        )


@dataclass(frozen=True, slots=True)
class ContractResolution:
    """合约主数据在决策时点给出的不可变结论。

    结论只携带 ``contract_id`` 和 ``rule_snapshot_hash``，不直接暴露可执行对象。调用方
    必须回到同一份 :class:`ContractMaster` 调用 ``require_execution_contract``，使主数据
    成员关系、决策时点、PIT、质量和交割门禁在进入下游绑定前再次验证。这个 API 不接受
    连续序列到实际合约的自动映射，避免研究换月逻辑越界成为下单决策。
    """

    master_fingerprint: str
    requested_ref: str
    decision_at: datetime
    status: ContractResolutionStatus
    reason_code: str
    contract_id: str | None
    rule_snapshot_hash: str | None
    resolution_hash: str

    def __post_init__(self) -> None:
        master_fingerprint = _sha256(self.master_fingerprint, "resolution.master_fingerprint")
        requested_ref = _text(self.requested_ref, "resolution.requested_ref").upper()
        decision_at = _utc_datetime(self.decision_at, "resolution.decision_at")
        if not isinstance(self.status, ContractResolutionStatus):
            raise ContractMasterError("resolution.status 必须是 ContractResolutionStatus")
        reason_code = _identifier(self.reason_code, "resolution.reason_code")
        if self.status is ContractResolutionStatus.RESOLVED:
            if self.contract_id is None or self.rule_snapshot_hash is None:
                raise ContractMasterError("RESOLVED 合约解析必须携带 contract_id 和 rule_snapshot_hash")
            contract_id = _identifier(self.contract_id, "resolution.contract_id")
            rule_snapshot_hash = _sha256(
                self.rule_snapshot_hash, "resolution.rule_snapshot_hash"
            )
        elif self.contract_id is not None or self.rule_snapshot_hash is not None:
            raise ContractMasterError("不可执行合约解析不得携带 contract_id 或 rule_snapshot_hash")
        else:
            contract_id = None
            rule_snapshot_hash = None
        expected_hash = _contract_resolution_hash(
            master_fingerprint=master_fingerprint,
            requested_ref=requested_ref,
            decision_at=decision_at,
            status=self.status,
            reason_code=reason_code,
            contract_id=contract_id,
            rule_snapshot_hash=rule_snapshot_hash,
        )
        if self.resolution_hash != expected_hash:
            raise ContractMasterError("resolution_hash 与解析结论不一致")
        object.__setattr__(self, "master_fingerprint", master_fingerprint)
        object.__setattr__(self, "requested_ref", requested_ref)
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "reason_code", reason_code)
        object.__setattr__(self, "contract_id", contract_id)
        object.__setattr__(self, "rule_snapshot_hash", rule_snapshot_hash)
        object.__setattr__(self, "resolution_hash", _sha256(self.resolution_hash, "resolution_hash"))

    @property
    def is_resolved(self) -> bool:
        """是否得到待 Master 重验的实际合约身份。"""

        return self.status is ContractResolutionStatus.RESOLVED


@dataclass(frozen=True, slots=True)
class ContractMaster:
    """商品、交易所、品种、实际合约和规则快照的不可变集合。"""

    master_id: str
    version: str
    commodities: tuple[Commodity, ...]
    exchanges: tuple[Exchange, ...]
    instruments: tuple[Instrument, ...]
    continuous_series: tuple[ContinuousResearchSeries, ...]
    contracts: tuple[Contract, ...]
    rule_snapshots: tuple[ContractRuleSnapshot, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "master_id", _identifier(self.master_id, "master_id"))
        object.__setattr__(self, "version", _text(self.version, "master.version"))
        commodities = _typed_tuple(self.commodities, Commodity, "commodities")
        exchanges = _typed_tuple(self.exchanges, Exchange, "exchanges")
        instruments = _typed_tuple(self.instruments, Instrument, "instruments")
        series = _typed_tuple(self.continuous_series, ContinuousResearchSeries, "continuous_series")
        contracts = _typed_tuple(self.contracts, Contract, "contracts")
        snapshots = _typed_tuple(self.rule_snapshots, ContractRuleSnapshot, "rule_snapshots")
        if not commodities or not exchanges or not instruments:
            raise ContractMasterError("commodities、exchanges 和 instruments 均不能为空")
        _ensure_unique((item.commodity_id for item in commodities), "commodity_id")
        _ensure_unique((item.exchange_id for item in exchanges), "exchange_id")
        _ensure_unique((item.instrument_id for item in instruments), "instrument_id")
        _ensure_unique(
            (f"{item.exchange_id}:{item.product_code}" for item in instruments),
            "instrument exchange/product",
        )
        commodity_ids = {item.commodity_id for item in commodities}
        exchange_ids = {item.exchange_id for item in exchanges}
        instrument_ids = {item.instrument_id for item in instruments}
        for instrument in instruments:
            if instrument.commodity_id not in commodity_ids:
                raise ContractMasterError(
                    f"instrument {instrument.instrument_id} 引用了未知 commodity_id"
                )
            if instrument.exchange_id not in exchange_ids:
                raise ContractMasterError(
                    f"instrument {instrument.instrument_id} 引用了未知 exchange_id"
                )
        _ensure_unique((item.series_id for item in series), "continuous_series.series_id")
        _ensure_unique((item.symbol for item in series), "continuous_series.symbol")
        for item in series:
            if item.instrument_id not in instrument_ids:
                raise ContractMasterError(
                    f"continuous series {item.series_id} 引用了未知 instrument_id"
                )
        _ensure_unique((item.contract_id for item in contracts), "contract_id")
        _ensure_unique((item.symbol for item in contracts), "contract.symbol")
        for contract in contracts:
            if contract.instrument_id not in instrument_ids:
                raise ContractMasterError(
                    f"contract {contract.contract_id} 引用了未知 instrument_id"
                )
            instrument = next(
                item for item in instruments if item.instrument_id == contract.instrument_id
            )
            if not contract.symbol.startswith(instrument.product_code):
                raise ContractMasterError(
                    f"contract {contract.contract_id} 的 symbol 必须以品种代码 "
                    f"{instrument.product_code} 开头"
                )
        _ensure_unique((item.snapshot_id for item in snapshots), "rules.snapshot_id")
        contract_by_id = {item.contract_id: item for item in contracts}
        for snapshot in snapshots:
            contract = contract_by_id.get(snapshot.contract_id)
            if contract is None:
                raise ContractMasterError(
                    f"规则快照 {snapshot.snapshot_id} 引用了未知 contract_id"
                )
            if snapshot.expires_on != contract.expires_on:
                raise ContractMasterError(
                    f"规则快照 {snapshot.snapshot_id} 的 expires_on 必须与 Contract 一致"
                )
        object.__setattr__(self, "commodities", tuple(sorted(commodities, key=lambda item: item.commodity_id)))
        object.__setattr__(self, "exchanges", tuple(sorted(exchanges, key=lambda item: item.exchange_id)))
        object.__setattr__(self, "instruments", tuple(sorted(instruments, key=lambda item: item.instrument_id)))
        object.__setattr__(self, "continuous_series", tuple(sorted(series, key=lambda item: item.series_id)))
        object.__setattr__(self, "contracts", tuple(sorted(contracts, key=lambda item: item.contract_id)))
        object.__setattr__(self, "rule_snapshots", tuple(sorted(snapshots, key=lambda item: item.snapshot_id)))

    @property
    def fingerprint(self) -> str:
        """返回本份主数据及已发布规则快照集合的稳定身份。"""

        return canonical_json_sha256(
            {
                "commodities": [
                    {"commodity_id": item.commodity_id, "name": item.name}
                    for item in self.commodities
                ],
                "continuous_series": [
                    {
                        "instrument_id": item.instrument_id,
                        "series_id": item.series_id,
                        "symbol": item.symbol,
                    }
                    for item in self.continuous_series
                ],
                "contracts": [
                    {
                        "contract_id": item.contract_id,
                        "expires_on": item.expires_on.isoformat(),
                        "instrument_id": item.instrument_id,
                        "listed_on": item.listed_on.isoformat(),
                        "symbol": item.symbol,
                    }
                    for item in self.contracts
                ],
                "exchanges": [
                    {
                        "exchange_id": item.exchange_id,
                        "market": item.market,
                        "name": item.name,
                        "timezone_name": item.timezone_name,
                    }
                    for item in self.exchanges
                ],
                "instruments": [
                    {
                        "commodity_id": item.commodity_id,
                        "exchange_id": item.exchange_id,
                        "instrument_id": item.instrument_id,
                        "product_code": item.product_code,
                    }
                    for item in self.instruments
                ],
                "master_id": self.master_id,
                "rule_snapshot_hashes": [item.snapshot_hash for item in self.rule_snapshots],
                "version": self.version,
            }
        )

    def resolve_for_execution(self, contract_ref: str, *, decision_at: datetime) -> ContractResolution:
        """以明确决策时点解析实际合约，绝不自动从连续序列推导月份合约。"""

        requested_ref = _text(contract_ref, "contract_ref").upper()
        decision_at = _utc_datetime(decision_at, "decision_at")
        continuous = [
            item
            for item in self.continuous_series
            if requested_ref in {item.series_id.upper(), item.symbol}
        ]
        if continuous or requested_ref.endswith("_CONT"):
            return self._unresolved(
                requested_ref,
                decision_at,
                ContractResolutionStatus.CONTINUOUS_RESEARCH_ONLY,
                "CONTINUOUS_CONTRACT_CANNOT_BECOME_BROKER_ORDER",
            )

        candidates = [
            item
            for item in self.contracts
            if requested_ref in {item.contract_id.upper(), item.symbol}
        ]
        if not candidates:
            return self._unresolved(
                requested_ref,
                decision_at,
                ContractResolutionStatus.UNKNOWN,
                "CONTRACT_MAPPING_UNKNOWN",
            )
        if len(candidates) != 1:
            return self._unresolved(
                requested_ref,
                decision_at,
                ContractResolutionStatus.AMBIGUOUS,
                "CONTRACT_MAPPING_AMBIGUOUS",
            )
        contract = candidates[0]
        decision_date = self._contract_local_date(contract, decision_at)
        if decision_date < contract.listed_on:
            return self._unresolved(
                requested_ref,
                decision_at,
                ContractResolutionStatus.NOT_LISTED,
                "CONTRACT_NOT_LISTED_AT_DECISION_TIME",
            )
        if decision_date > contract.expires_on:
            return self._unresolved(
                requested_ref,
                decision_at,
                ContractResolutionStatus.EXPIRED,
                "CONTRACT_EXPIRED_AT_DECISION_TIME",
            )

        effective = [
            snapshot
            for snapshot in self.rule_snapshots
            if snapshot.contract_id == contract.contract_id
            and snapshot.effective_from <= decision_at
            and (snapshot.effective_until is None or decision_at < snapshot.effective_until)
        ]
        available = [snapshot for snapshot in effective if snapshot.available_at <= decision_at]
        if not available:
            status = (
                ContractResolutionStatus.RULES_NOT_YET_AVAILABLE
                if effective
                else ContractResolutionStatus.RULES_UNKNOWN
            )
            reason = (
                "CONTRACT_RULES_AVAILABLE_AFTER_DECISION_TIME"
                if effective
                else "CONTRACT_RULES_UNKNOWN"
            )
            return self._unresolved(requested_ref, decision_at, status, reason)
        if len(available) != 1:
            return self._unresolved(
                requested_ref,
                decision_at,
                ContractResolutionStatus.AMBIGUOUS,
                "CONTRACT_RULE_SNAPSHOTS_OVERLAP",
            )
        snapshot = available[0]
        if snapshot.listing_state is ListingState.EXPIRED or decision_date > snapshot.expires_on:
            return self._unresolved(
                requested_ref,
                decision_at,
                ContractResolutionStatus.EXPIRED,
                "CONTRACT_RULE_SNAPSHOT_EXPIRED",
            )
        if snapshot.listing_state is not ListingState.LISTED:
            return self._unresolved(
                requested_ref,
                decision_at,
                ContractResolutionStatus.NOT_LISTED,
                f"CONTRACT_LISTING_STATE_{snapshot.listing_state.value.upper()}",
            )
        if snapshot.delivery_restriction is not DeliveryRestriction.NONE:
            return self._unresolved(
                requested_ref,
                decision_at,
                ContractResolutionStatus.DELIVERY_RESTRICTED,
                f"CONTRACT_DELIVERY_RESTRICTION_{snapshot.delivery_restriction.value.upper()}",
            )
        if (
            snapshot.quality_status is not RuleQualityStatus.PASS
            or not snapshot.execution_eligible
        ):
            return self._unresolved(
                requested_ref,
                decision_at,
                ContractResolutionStatus.RULES_NOT_EXECUTION_ELIGIBLE,
                "CONTRACT_RULES_NOT_EXECUTION_ELIGIBLE",
            )
        return self._resolved(requested_ref, decision_at, contract, snapshot)

    def _contract_local_date(self, contract: Contract, decision_at: datetime) -> date:
        """按合约交易所本地日期判断挂牌与到期，不能拿 UTC 日期替代。"""

        instrument = next(
            item for item in self.instruments if item.instrument_id == contract.instrument_id
        )
        exchange = next(item for item in self.exchanges if item.exchange_id == instrument.exchange_id)
        return decision_at.astimezone(ZoneInfo(exchange.timezone_name)).date()

    def require_execution_contract(
        self,
        resolution: ContractResolution,
    ) -> tuple[Contract, ContractRuleSnapshot]:
        """重新验证解析结论仍属于本 Master 后，才返回执行所需事实。

        这一步会重新运行完整 resolver，而不是只相信 ``status=RESOLVED``。因此公开
        ``ContractResolution`` 被手工构造、规则质量变化或 Master 换版时，都不能把未授权
        的实际合约和规则对象交给下游券商绑定层。
        """

        if not isinstance(resolution, ContractResolution):
            raise ContractMasterError("resolution 必须是 ContractResolution")
        if resolution.master_fingerprint != self.fingerprint:
            raise ContractMasterError("CONTRACT_RESOLUTION_MASTER_MISMATCH")
        expected = self.resolve_for_execution(
            resolution.requested_ref,
            decision_at=resolution.decision_at,
        )
        if (
            not expected.is_resolved
            or expected.resolution_hash != resolution.resolution_hash
            or expected.contract_id != resolution.contract_id
            or expected.rule_snapshot_hash != resolution.rule_snapshot_hash
        ):
            raise ContractMasterError(
                "CONTRACT_RESOLUTION_REVALIDATION_FAILED: "
                f"{expected.status.value.upper()}: {expected.reason_code}"
            )
        assert expected.contract_id is not None
        assert expected.rule_snapshot_hash is not None
        contract = next(
            item for item in self.contracts if item.contract_id == expected.contract_id
        )
        snapshot = next(
            item
            for item in self.rule_snapshots
            if item.snapshot_hash == expected.rule_snapshot_hash
        )
        return contract, snapshot

    def _resolved(
        self,
        requested_ref: str,
        decision_at: datetime,
        contract: Contract,
        snapshot: ContractRuleSnapshot,
    ) -> ContractResolution:
        return ContractResolution(
            master_fingerprint=self.fingerprint,
            requested_ref=requested_ref,
            decision_at=decision_at,
            status=ContractResolutionStatus.RESOLVED,
            reason_code="CONTRACT_RESOLVED",
            contract_id=contract.contract_id,
            rule_snapshot_hash=snapshot.snapshot_hash,
            resolution_hash=_contract_resolution_hash(
                master_fingerprint=self.fingerprint,
                requested_ref=requested_ref,
                decision_at=decision_at,
                status=ContractResolutionStatus.RESOLVED,
                reason_code="CONTRACT_RESOLVED",
                contract_id=contract.contract_id,
                rule_snapshot_hash=snapshot.snapshot_hash,
            ),
        )

    def _unresolved(
        self,
        requested_ref: str,
        decision_at: datetime,
        status: ContractResolutionStatus,
        reason_code: str,
    ) -> ContractResolution:
        return ContractResolution(
            master_fingerprint=self.fingerprint,
            requested_ref=requested_ref,
            decision_at=decision_at,
            status=status,
            reason_code=reason_code,
            contract_id=None,
            rule_snapshot_hash=None,
            resolution_hash=_contract_resolution_hash(
                master_fingerprint=self.fingerprint,
                requested_ref=requested_ref,
                decision_at=decision_at,
                status=status,
                reason_code=reason_code,
                contract_id=None,
                rule_snapshot_hash=None,
            ),
        )


@dataclass(frozen=True, slots=True)
class _NormalizedRuleSnapshotInput:
    """供构造器与工厂共享的未规范化规则快照字段。"""

    snapshot_id: str
    contract_id: str
    observed_at: datetime
    available_at: datetime
    effective_from: datetime
    effective_until: datetime | None
    listing_state: ListingState
    expires_on: date
    multiplier: float
    tick_size: float
    initial_margin_rate: float
    fees: ContractFeeSchedule
    lower_price_limit: float
    upper_price_limit: float
    sessions: tuple[ContractTradingSession, ...]
    delivery_restriction: DeliveryRestriction
    source_artifact_hash: str
    source_authority: str
    quality_status: RuleQualityStatus
    execution_eligible: bool


def _normalize_rule_snapshot_input(
    value: _NormalizedRuleSnapshotInput,
) -> _NormalizedRuleSnapshotFields:
    """验证并规范化工厂和直接构造共享的规则字段。"""

    snapshot_id = _identifier(value.snapshot_id, "snapshot_id")
    contract_id = _identifier(value.contract_id, "contract_id")
    observed_at = _utc_datetime(value.observed_at, "rules.observed_at")
    available_at = _utc_datetime(value.available_at, "rules.available_at")
    effective_from = _utc_datetime(value.effective_from, "rules.effective_from")
    effective_until = (
        _utc_datetime(value.effective_until, "rules.effective_until")
        if value.effective_until is not None
        else None
    )
    if available_at < observed_at:
        raise ContractMasterError("rules.available_at 不能早于 rules.observed_at")
    if effective_until is not None and effective_until <= effective_from:
        raise ContractMasterError("rules.effective_until 必须晚于 rules.effective_from")
    if not isinstance(value.listing_state, ListingState):
        raise ContractMasterError("rules.listing_state 必须是 ListingState")
    if not isinstance(value.expires_on, date) or isinstance(value.expires_on, datetime):
        raise ContractMasterError("rules.expires_on 必须是 date")
    multiplier = _positive_number(value.multiplier, "rules.multiplier")
    tick_size = _positive_number(value.tick_size, "rules.tick_size")
    initial_margin_rate = _positive_number(value.initial_margin_rate, "rules.initial_margin_rate")
    if initial_margin_rate > 1:
        raise ContractMasterError("rules.initial_margin_rate 不能大于 1")
    if not isinstance(value.fees, ContractFeeSchedule):
        raise ContractMasterError("rules.fees 必须是 ContractFeeSchedule")
    lower_price_limit = _positive_number(value.lower_price_limit, "rules.lower_price_limit")
    upper_price_limit = _positive_number(value.upper_price_limit, "rules.upper_price_limit")
    if upper_price_limit <= lower_price_limit:
        raise ContractMasterError("rules.upper_price_limit 必须大于 rules.lower_price_limit")
    sessions = tuple(value.sessions)
    if not sessions or not all(isinstance(item, ContractTradingSession) for item in sessions):
        raise ContractMasterError("rules.sessions 必须是非空 ContractTradingSession 序列")
    if len({item.session_id for item in sessions}) != len(sessions):
        raise ContractMasterError("rules.sessions 不能包含重复 session_id")
    if not isinstance(value.delivery_restriction, DeliveryRestriction):
        raise ContractMasterError("rules.delivery_restriction 必须是 DeliveryRestriction")
    if not isinstance(value.quality_status, RuleQualityStatus):
        raise ContractMasterError("rules.quality_status 必须是 RuleQualityStatus")
    if type(value.execution_eligible) is not bool:
        raise ContractMasterError("rules.execution_eligible 必须是 bool")
    return {
        "snapshot_id": snapshot_id,
        "contract_id": contract_id,
        "observed_at": observed_at,
        "available_at": available_at,
        "effective_from": effective_from,
        "effective_until": effective_until,
        "listing_state": value.listing_state,
        "expires_on": value.expires_on,
        "multiplier": multiplier,
        "tick_size": tick_size,
        "initial_margin_rate": initial_margin_rate,
        "fees": value.fees,
        "lower_price_limit": lower_price_limit,
        "upper_price_limit": upper_price_limit,
        "sessions": tuple(sorted(sessions, key=lambda item: item.session_id)),
        "delivery_restriction": value.delivery_restriction,
        "source_artifact_hash": _sha256(value.source_artifact_hash, "rules.source_artifact_hash"),
        "source_authority": _text(value.source_authority, "rules.source_authority"),
        "quality_status": value.quality_status,
        "execution_eligible": value.execution_eligible,
    }


def _contract_rule_snapshot_hash(
    *,
    snapshot_id: str,
    contract_id: str,
    observed_at: datetime,
    available_at: datetime,
    effective_from: datetime,
    effective_until: datetime | None,
    listing_state: ListingState,
    expires_on: date,
    multiplier: float,
    tick_size: float,
    initial_margin_rate: float,
    fees: ContractFeeSchedule,
    lower_price_limit: float,
    upper_price_limit: float,
    sessions: tuple[ContractTradingSession, ...],
    delivery_restriction: DeliveryRestriction,
    source_artifact_hash: str,
    source_authority: str,
    quality_status: RuleQualityStatus,
    execution_eligible: bool,
) -> str:
    """返回完整规则证据的稳定 SHA-256 身份。"""

    return canonical_json_sha256(
        {
            "available_at": available_at.isoformat(),
            "contract_id": contract_id,
            "delivery_restriction": delivery_restriction.value,
            "effective_from": effective_from.isoformat(),
            "effective_until": effective_until.isoformat() if effective_until else None,
            "execution_eligible": execution_eligible,
            "expires_on": expires_on.isoformat(),
            "fees": {
                "close_per_lot": fees.close_per_lot,
                "close_rate": fees.close_rate,
                "close_today_per_lot": fees.close_today_per_lot,
                "close_today_rate": fees.close_today_rate,
                "open_per_lot": fees.open_per_lot,
                "open_rate": fees.open_rate,
            },
            "initial_margin_rate": initial_margin_rate,
            "listing_state": listing_state.value,
            "lower_price_limit": lower_price_limit,
            "multiplier": multiplier,
            "observed_at": observed_at.isoformat(),
            "quality_status": quality_status.value,
            "sessions": [
                {
                    "closes_at": item.closes_at.isoformat(),
                    "opens_at": item.opens_at.isoformat(),
                    "session_id": item.session_id,
                }
                for item in sessions
            ],
            "snapshot_id": snapshot_id,
            "source_artifact_hash": source_artifact_hash,
            "source_authority": source_authority,
            "tick_size": tick_size,
            "upper_price_limit": upper_price_limit,
        }
    )


def _contract_resolution_hash(
    *,
    master_fingerprint: str,
    requested_ref: str,
    decision_at: datetime,
    status: ContractResolutionStatus,
    reason_code: str,
    contract_id: str | None,
    rule_snapshot_hash: str | None,
) -> str:
    return canonical_json_sha256(
        {
            "contract_id": contract_id,
            "decision_at": decision_at.isoformat(),
            "master_fingerprint": master_fingerprint,
            "reason_code": reason_code,
            "requested_ref": requested_ref,
            "rule_snapshot_hash": rule_snapshot_hash,
            "status": status.value,
        }
    )


def _typed_tuple(values: object, expected_type: type, field_name: str) -> tuple:
    try:
        normalized: tuple[object, ...] = tuple(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ContractMasterError(f"{field_name} 必须是序列") from exc
    if not all(isinstance(value, expected_type) for value in normalized):
        raise ContractMasterError(f"{field_name} 必须全部是 {expected_type.__name__}")
    return normalized


def _ensure_unique(values: object, field_name: str) -> None:
    try:
        normalized: tuple[object, ...] = tuple(values)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ContractMasterError(f"{field_name} 必须是可迭代序列") from exc
    if len(normalized) != len(set(normalized)):
        raise ContractMasterError(f"{field_name} 不能重复")


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractMasterError(f"{field_name} 必须是非空字符串")
    return value.strip()


def _identifier(value: object, field_name: str) -> str:
    normalized = _text(value, field_name).upper()
    if not _IDENTIFIER_RE.fullmatch(normalized):
        raise ContractMasterError(f"{field_name} 包含不允许的字符")
    return normalized


def _symbol(value: object, field_name: str) -> str:
    normalized = _text(value, field_name).upper()
    if not re.fullmatch(r"[A-Z0-9_]+", normalized):
        raise ContractMasterError(f"{field_name} 必须是大写字母、数字或下划线")
    return normalized


def _utc_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ContractMasterError(f"{field_name} 必须是带时区的 datetime")
    return value.astimezone(timezone.utc)


def _positive_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractMasterError(f"{field_name} 必须是正有限数")
    try:
        numeric = float(cast(int | float, value))
    except (TypeError, ValueError) as exc:
        raise ContractMasterError(f"{field_name} 必须是正有限数") from exc
    if not math.isfinite(numeric) or numeric <= 0:
        raise ContractMasterError(f"{field_name} 必须是正有限数")
    return numeric


def _nonnegative_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractMasterError(f"{field_name} 必须是非负有限数")
    try:
        numeric = float(cast(int | float, value))
    except (TypeError, ValueError) as exc:
        raise ContractMasterError(f"{field_name} 必须是非负有限数") from exc
    if not math.isfinite(numeric) or numeric < 0:
        raise ContractMasterError(f"{field_name} 必须是非负有限数")
    return numeric


def _sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ContractMasterError(f"{field_name} 必须是 SHA-256")
    try:
        return require_sha256(cast(str, value), field_name=field_name)
    except FingerprintError as exc:
        raise ContractMasterError(str(exc)) from exc

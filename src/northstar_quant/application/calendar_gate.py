"""Application 层的期货交易日历组合门禁。

Data Platform 只保存可回放的日历事实，Trading & Execution 只认识订单与券商身份。
本模块在两者之间组合已验证的不可变日历、CTP 实际合约映射和最终订单载荷；它不是
交易授权，也不会用工作日、连续合约代码或本机时钟猜测会话。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from northstar_quant.data_platform.artifacts.immutable_store import (
    ArtifactStore,
    ArtifactStoreError,
)
from northstar_quant.data_platform.artifacts.fingerprints import (
    FingerprintError,
    content_sha256,
    require_sha256,
)
from northstar_quant.data_platform.calendars import (
    CalendarDecision,
    CalendarError,
    CalendarService,
    calendar_content_hash,
    load_trading_calendar_payload,
)
from northstar_quant.data_platform.contracts import ArtifactKind, QualityStatus
from northstar_quant.data_platform.contracts.contract_master import (
    Contract,
    ContractMaster,
    ContractMasterError,
    ContractRuleSnapshot,
)
from northstar_quant.data_platform.contracts.contract_master_loader import load_contract_master
from northstar_quant.platform.config.data_sources import (
    DataSourceConfigError,
    data_source_config_sha256,
    get_data_source,
)
from northstar_quant.platform.config.settings import get_settings
from northstar_quant.trading_execution.broker.ctp_contract_mapping import (
    CtpContractMapping,
    CtpContractMappingError,
    load_ctp_contract_registry,
)
from northstar_quant.trading_execution.execution.models import OrderRequest

if TYPE_CHECKING:
    from northstar_quant.platform.config.trading_profile import TradingProfile


class CalendarGateError(PermissionError):
    """提交前无法证明实际合约处于已知交易会话。"""


def load_calendar_service_for_profile(profile: TradingProfile) -> CalendarService:
    """加载一份用于真实提交的、内容和来源均可验证的日历快照。

    画像只保存最终 ``ArtifactSnapshot`` 的 hash，绝不把项目内 YAML 路径作为信任根。此处
    从不可变制品库读取并复核实际 bytes，要求其是受控 schema/transform 的 normalized
    日历 payload，且精确绑定已授权的 raw 来源 snapshot、语义内容 hash 和 PIT 时间。
    ``test_only`` fixture 永远不能经过该入口。
    """

    futures = getattr(profile, "futures", None)
    configured_hashes = getattr(futures, "calendar_artifact_snapshot_hashes", {}) or {}
    if not isinstance(configured_hashes, dict) or not configured_hashes:
        raise CalendarGateError(
            "TRADING_CALENDAR_ARTIFACT_REQUIRED: 期货执行画像缺少 "
            "futures.calendar_artifact_snapshot_hashes，禁止提交订单。"
        )
    try:
        settings = get_settings()
        store_root = settings.storage_dir / "artifacts"
        if not store_root.is_dir():
            raise CalendarGateError(
                "TRADING_CALENDAR_ARTIFACT_UNAVAILABLE: 不可变制品库不存在，禁止提交订单。"
            )
        store = ArtifactStore(store_root)
        snapshots = tuple(
            _load_one_runtime_calendar_artifact(
                store=store,
                expected_exchange_id=str(exchange_id).strip().upper(),
                configured_hash=str(configured_hash).strip(),
            )
            for exchange_id, configured_hash in sorted(configured_hashes.items())
        )
        return CalendarService(snapshots)
    except CalendarGateError:
        raise
    except (
        ArtifactStoreError,
        CalendarError,
        DataSourceConfigError,
        FingerprintError,
        OSError,
        ValueError,
    ) as exc:
        raise CalendarGateError(
            "TRADING_CALENDAR_UNAVAILABLE: 不可变日历制品、来源或授权验证失败，禁止提交订单。"
        ) from exc


def _load_one_runtime_calendar_artifact(
    *,
    store: ArtifactStore,
    expected_exchange_id: str,
    configured_hash: str,
):
    """读取并验证一个交易所的单快照不可变日历制品。"""

    if not expected_exchange_id:
        raise CalendarGateError("TRADING_CALENDAR_EXCHANGE_KEY_INVALID: 日历制品映射缺少交易所键。")
    artifact_snapshot_hash = require_sha256(
        configured_hash,
        field_name=f"futures.calendar_artifact_snapshot_hashes.{expected_exchange_id}",
    )
    stored = store.load_artifact(artifact_snapshot_hash)
    payload = store.read_payload(artifact_snapshot_hash)
    if content_sha256(payload, field_name="calendar artifact payload") != stored.snapshot.content_hash:
        raise CalendarGateError(
            "TRADING_CALENDAR_ARTIFACT_INTEGRITY: 日历制品 payload 哈希不一致。"
        )
    snapshots = load_trading_calendar_payload(payload, require_runtime=True)
    _validate_runtime_calendar_artifact(
        store=store,
        stored=stored,
        payload=payload,
        snapshots=snapshots,
    )
    snapshot = snapshots[0]
    if snapshot.exchange_id != expected_exchange_id:
        raise CalendarGateError(
            "TRADING_CALENDAR_EXCHANGE_BINDING_INVALID: 日历制品与画像交易所键不一致。"
        )
    return snapshot


def assert_order_calendar_open(
    *,
    profile: TradingProfile,
    broker_name: str,
    order: OrderRequest,
    calendar_service: CalendarService,
    contract_rule_snapshot: ContractRuleSnapshot,
    at: datetime | None = None,
) -> CalendarDecision:
    """确认最终实际订单处于品种日历与实际合约规则的明确交集会话。

    必须由 :class:`~northstar_quant.trading_execution.execution.router.OrderRouter`
    调用 ``broker.prepare_order`` 之后执行。这样 ``order.instrument_id`` 和
    ``order.exchange_id`` 已是实际 CTP 身份；本函数只借由受控映射取得稳定品种身份，
    不从 ``symbol`` 截取月份或猜测产品。产品日历会话不足以授权实际月份合约：调用方必须
    提供刚由 Contract Master 重新解析出的 ``contract_rule_snapshot``，当前时点必须同时落在
    两者的同名会话内。
    """

    if not isinstance(calendar_service, CalendarService):
        raise CalendarGateError("TRADING_CALENDAR_SERVICE_REQUIRED: 缺少日历服务。")
    if not isinstance(contract_rule_snapshot, ContractRuleSnapshot):
        raise CalendarGateError(
            "TRADING_CONTRACT_RULE_REQUIRED: 最终订单缺少已验证的实际合约规则快照。"
        )
    market_at = _aware_utc(at, "at") if at is not None else datetime.now(UTC)
    exchange_id, stable_instrument_id = _stable_calendar_instrument_identity(
        profile=profile,
        broker_name=broker_name,
        order=order,
    )
    decision = calendar_service.resolve_market_session(
        exchange_id,
        stable_instrument_id,
        market_at,
        market_at,
    )
    if not decision.is_open:
        raise CalendarGateError(
            "TRADING_CALENDAR_BLOCKED: 无法证明实际合约处于可交易会话；"
            f"status={decision.status.value}；reason={decision.reason_code}；"
            f"snapshot_hash={decision.snapshot_hash or 'none'}。"
        )
    _assert_contract_rule_session_open(
        decision=decision,
        contract_rule_snapshot=contract_rule_snapshot,
        market_at=market_at,
    )
    return decision


def assert_execution_contract_admissible(
    *,
    profile: TradingProfile,
    broker_name: str,
    order: OrderRequest,
    at: datetime | None = None,
    contract_master: ContractMaster | None = None,
) -> tuple[Contract, ContractRuleSnapshot]:
    """在实际订单提交前重验 Contract Master 的挂牌、到期和交割门禁。

    日历只能回答“稳定品种此刻是否有会话”，不能代替实际月份合约的挂牌、到期、规则质量和
    交割限制判断。此函数必须使用 ``prepare_order`` 后的 CTP 实际身份，重新解析并调用
    ``require_execution_contract``；连续代码、未知规则、未来可用规则、到期或任何
    ``DeliveryRestriction`` 都会失败关闭。
    """

    market_at = _aware_utc(at, "at") if at is not None else datetime.now(UTC)
    mapping = _resolved_order_ctp_mapping(
        profile=profile,
        broker_name=broker_name,
        order=order,
    )
    try:
        master = contract_master or load_contract_master()
        resolution = master.resolve_for_execution(
            mapping.data_symbol,
            decision_at=market_at,
        )
        contract, rules = master.require_execution_contract(resolution)
    except (ContractMasterError, OSError, ValueError) as exc:
        raise CalendarGateError(
            "TRADING_CONTRACT_MASTER_BLOCKED: 无法证明实际合约在当前时点可执行。"
        ) from exc

    expected_instrument_id = f"{mapping.exchange_id}.{mapping.product_id.upper()}"
    if (
        contract.symbol != mapping.data_symbol
        or contract.instrument_id != expected_instrument_id
        or rules.contract_id != contract.contract_id
    ):
        raise CalendarGateError(
            "TRADING_CONTRACT_MASTER_IDENTITY_MISMATCH: Contract Master 与 CTP 实际合约身份不一致。"
        )
    return contract, rules


def _assert_contract_rule_session_open(
    *,
    decision: CalendarDecision,
    contract_rule_snapshot: ContractRuleSnapshot,
    market_at: datetime,
) -> None:
    """将产品绝对会话与实际合约规则的本地钟表区间取交集。

    夜盘跨日归属只由 Calendar Session 的绝对起止时间决定；规则快照只进一步收窄同一
    ``session_id`` 可交易的本地时段。这样实际月份合约的临时/缩短时段不能被较宽的品种
    日历放行。
    """

    calendar_session = decision.session
    if calendar_session is None:
        raise CalendarGateError(
            "TRADING_CONTRACT_SESSION_UNRESOLVED: 日历 OPEN 决策缺少可审计会话。"
        )
    matching_rules = tuple(
        item
        for item in contract_rule_snapshot.sessions
        if item.session_id.upper() == calendar_session.session_id.upper()
    )
    if not matching_rules:
        raise CalendarGateError(
            "TRADING_CONTRACT_SESSION_MISMATCH: 实际合约规则未声明当前产品日历会话。"
        )

    exchange_clock = market_at.astimezone(calendar_session.opens_at.tzinfo).replace(
        tzinfo=None
    ).time()
    if not any(
        _clock_time_in_rule_session(
            clock_time=exchange_clock,
            opens_at=item.opens_at,
            closes_at=item.closes_at,
        )
        for item in matching_rules
    ):
        raise CalendarGateError(
            "TRADING_CONTRACT_SESSION_BLOCKED: 实际合约规则不允许当前产品会话内的该时点。"
        )


def _clock_time_in_rule_session(*, clock_time, opens_at, closes_at) -> bool:
    """按交易所本地钟表判断时刻是否在半开规则区间内，兼容跨午夜盘。"""

    normalized_clock = clock_time.replace(tzinfo=None)
    normalized_open = opens_at.replace(tzinfo=None)
    normalized_close = closes_at.replace(tzinfo=None)
    if normalized_open < normalized_close:
        return normalized_open <= normalized_clock < normalized_close
    return normalized_clock >= normalized_open or normalized_clock < normalized_close


def assert_profile_calendar_trading_day(
    *,
    profile: TradingProfile,
    broker_name: str,
    at: datetime | None = None,
) -> tuple[CalendarDecision, ...]:
    """确认调度画像中的每个已启用实际品种都有已知交易日。

    这是调度器的保守日级过滤，不替代逐订单、逐会话的
    :func:`assert_order_calendar_open`。任一已启用品种没有明确会话就跳过会产生新风险的
    任务；对账和盘中风控不应使用此过滤。
    """

    calendar_service, stable_instruments, market_at, local_day = _profile_calendar_context(
        profile=profile,
        broker_name=broker_name,
        at=at,
    )
    decisions = tuple(
        calendar_service.is_trading_day(
            exchange_id,
            stable_instrument_id,
            local_day,
            market_at,
        )
        for exchange_id, stable_instrument_id in stable_instruments
    )
    blocked = next((item for item in decisions if not item.is_open), None)
    if blocked is not None:
        raise CalendarGateError(
            "TRADING_CALENDAR_SCHEDULER_BLOCKED: 调度日历未明确覆盖全部已启用品种；"
            f"status={blocked.status.value}；reason={blocked.reason_code}；"
            f"snapshot_hash={blocked.snapshot_hash or 'none'}。"
        )
    return decisions


def assert_profile_calendar_market_session(
    *,
    profile: TradingProfile,
    broker_name: str,
    at: datetime | None = None,
) -> tuple[CalendarDecision, ...]:
    """确认调度当前绝对时点处于所有已启用品种的明确交易会话。

    该入口供候选执行任务使用，不能以画像本地自然日替代会话判断：例如周五夜盘可以明确
    归属下周一交易日。它仍只是节流门；最终订单在 ``prepare_order()`` 后还会以实际月份
    合约再次执行 Contract Master 与日历提交门禁。
    """

    calendar_service, stable_instruments, market_at, _local_day = _profile_calendar_context(
        profile=profile,
        broker_name=broker_name,
        at=at,
    )
    decisions = tuple(
        calendar_service.resolve_market_session(
            exchange_id,
            stable_instrument_id,
            market_at,
            market_at,
        )
        for exchange_id, stable_instrument_id in stable_instruments
    )
    blocked = next((item for item in decisions if not item.is_open), None)
    if blocked is not None:
        raise CalendarGateError(
            "TRADING_CALENDAR_EXECUTION_SESSION_BLOCKED: 调度时点未明确处于全部已启用品种会话；"
            f"status={blocked.status.value}；reason={blocked.reason_code}；"
            f"snapshot_hash={blocked.snapshot_hash or 'none'}。"
        )
    return decisions


def is_profile_last_trading_day(
    *,
    profile: TradingProfile,
    broker_name: str,
    period: str,
    at: datetime | None = None,
) -> bool:
    """判断是否为日历快照可证明的当月或当年最后交易日。

    日历覆盖不足、来源不可验证或品种会话未知均抛出 :class:`CalendarGateError`，由调度器
    跳过报告而不是通过工作日规则补算。
    """

    if period not in {"month", "year"}:
        raise CalendarGateError("TRADING_CALENDAR_PERIOD_INVALID: period 只能是 month 或 year。")
    calendar_service, stable_instruments, market_at, local_day = _profile_calendar_context(
        profile=profile,
        broker_name=broker_name,
        at=at,
    )
    current_decisions = tuple(
        calendar_service.is_trading_day(
            exchange_id,
            stable_instrument_id,
            local_day,
            market_at,
        )
        for exchange_id, stable_instrument_id in stable_instruments
    )
    current_blocked = next((item for item in current_decisions if not item.is_open), None)
    if current_blocked is not None:
        raise CalendarGateError(
            "TRADING_CALENDAR_SCHEDULER_BLOCKED: 当前日期不是全部已启用品种的明确交易日；"
            f"status={current_blocked.status.value}；reason={current_blocked.reason_code}。"
        )

    next_decisions = tuple(
        calendar_service.next_trading_day(
            exchange_id,
            stable_instrument_id,
            local_day,
            market_at,
        )
        for exchange_id, stable_instrument_id in stable_instruments
    )
    unknown_next = next((item for item in next_decisions if not item.is_open), None)
    if unknown_next is not None:
        raise CalendarGateError(
            "TRADING_CALENDAR_PERIOD_UNKNOWN: 无法在当前快照覆盖内确认下一交易日；"
            f"status={unknown_next.status.value}；reason={unknown_next.reason_code}。"
        )
    if period == "month":
        return all(item.trading_day is not None and item.trading_day.month != local_day.month for item in next_decisions)
    return all(item.trading_day is not None and item.trading_day.year != local_day.year for item in next_decisions)


def _stable_calendar_instrument_identity(
    *,
    profile: TradingProfile,
    broker_name: str,
    order: OrderRequest,
) -> tuple[str, str]:
    """以已启用的 CTP 实际合约映射取得稳定品种身份。

    CTP mapping 在这里仅承担 ``RB2610 -> SHFE.RB`` 的显式身份桥接；它不构成日历
    来源、交易时段或真实交易授权。任何不一致都必须停止提交。
    """

    mapping = _resolved_order_ctp_mapping(
        profile=profile,
        broker_name=broker_name,
        order=order,
    )
    return mapping.exchange_id, f"{mapping.exchange_id}.{mapping.product_id.upper()}"


def _resolved_order_ctp_mapping(
    *,
    profile: TradingProfile,
    broker_name: str,
    order: OrderRequest,
) -> CtpContractMapping:
    """返回与最终订单逐字段匹配的、已启用 CTP 实际月份映射。"""

    instrument_id = str(order.instrument_id or "").strip()
    exchange_id = str(order.exchange_id or "").strip()
    if not instrument_id or not exchange_id:
        raise CalendarGateError(
            "TRADING_CALENDAR_CONTRACT_IDENTITY_REQUIRED: 最终订单缺少实际 "
            "instrument_id 或 exchange_id。"
        )
    try:
        mapping = _load_profile_ctp_registry(
            profile=profile,
            broker_name=broker_name,
        ).resolve_ctp_identity(instrument_id, exchange_id)
    except (CtpContractMappingError, OSError, ValueError) as exc:
        raise CalendarGateError(
            "TRADING_CALENDAR_CONTRACT_IDENTITY_UNRESOLVED: 无法验证实际合约到稳定品种的映射。"
        ) from exc

    normalized_symbol = str(order.symbol or "").strip().upper()
    if normalized_symbol != mapping.data_symbol:
        raise CalendarGateError(
            "TRADING_CALENDAR_CONTRACT_IDENTITY_MISMATCH: 最终订单 symbol 与已验证的 "
            "CTP 实际合约映射不一致。"
        )
    return mapping


def _profile_calendar_context(
    *,
    profile: TradingProfile,
    broker_name: str,
    at: datetime | None,
) -> tuple[CalendarService, tuple[tuple[str, str], ...], datetime, date]:
    """构造调度日级判断所需的服务、稳定品种和画像本地日期。"""

    market_at = _aware_utc(at, "at") if at is not None else datetime.now(UTC)
    timezone_name = str(getattr(profile, "timezone", "") or "").strip()
    try:
        local_day = market_at.astimezone(ZoneInfo(timezone_name)).date()
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CalendarGateError(
            "TRADING_CALENDAR_TIMEZONE_INVALID: 画像缺少有效 IANA 时区。"
        ) from exc

    registry = _load_profile_ctp_registry(profile=profile, broker_name=broker_name)
    stable_instruments: set[tuple[str, str]] = set()
    try:
        for mapping in registry.contracts:
            enabled_mapping = mapping.require_trading_enabled()
            stable_instruments.add(
                (
                    enabled_mapping.exchange_id,
                    f"{enabled_mapping.exchange_id}.{enabled_mapping.product_id.upper()}",
                )
            )
    except CtpContractMappingError as exc:
        raise CalendarGateError(
            "TRADING_CALENDAR_CONTRACT_IDENTITY_UNRESOLVED: 调度画像包含未启用的实际合约映射。"
        ) from exc
    if not stable_instruments:
        raise CalendarGateError(
            "TRADING_CALENDAR_CONTRACT_IDENTITY_REQUIRED: 调度画像没有已启用的实际合约映射。"
        )
    return (
        load_calendar_service_for_profile(profile),
        tuple(sorted(stable_instruments)),
        market_at,
        local_day,
    )


def _load_profile_ctp_registry(*, profile: TradingProfile, broker_name: str):
    futures = getattr(profile, "futures", None)
    mapping_path = str(getattr(futures, "ctp_contract_mapping_path", "") or "").strip()
    if not mapping_path:
        raise CalendarGateError(
            "TRADING_CALENDAR_CONTRACT_IDENTITY_REQUIRED: 画像缺少 CTP 实际合约映射。"
        )
    try:
        return load_ctp_contract_registry(
            mapping_path,
            expected_broker=str(broker_name).strip().lower(),
        )
    except (CtpContractMappingError, OSError, ValueError) as exc:
        raise CalendarGateError(
            "TRADING_CALENDAR_CONTRACT_IDENTITY_UNRESOLVED: 无法读取已验证的 CTP 实际合约映射。"
        ) from exc


def _validate_runtime_calendar_artifact(
    *,
    store: ArtifactStore,
    stored,
    payload: bytes,
    snapshots,
) -> None:
    """将日历语义与不可变 payload、血缘和授权范围逐项绑定。

    这不是“source hash 是否存在”的检查。最终 normalized calendar artifact 的 record 中必须
    冻结 payload/content/source 三个身份；同时该 record 的唯一 raw 上游必须恰好是日历
    YAML 所声明的 source artifact。任一步失败均不能退回到可变配置文件。
    """

    snapshot = stored.snapshot
    if (
        snapshot.kind is not ArtifactKind.NORMALIZED
        or snapshot.artifact_id != "trading-calendar.runtime.v1"
        or snapshot.schema_version != "trading-calendar.v1"
        or snapshot.transform_version != "trading-calendar-normalize.v1"
        or snapshot.quality_status is not QualityStatus.PASS
    ):
        raise CalendarGateError(
            "TRADING_CALENDAR_ARTIFACT_TYPE_INVALID: 日历制品不是受控的 PASS normalized calendar。"
        )
    if len(snapshots) != 1:
        raise CalendarGateError(
            "TRADING_CALENDAR_ARTIFACT_CARDINALITY_INVALID: 一个运行时日历制品只能声明一个快照。"
        )

    calendar_snapshot = snapshots[0]
    if calendar_snapshot.quality_status.value != QualityStatus.PASS.value:
        raise CalendarGateError(
            "TRADING_CALENDAR_QUALITY_INVALID: 运行时日历快照质量不是 PASS。"
        )
    if calendar_snapshot.available_at != snapshot.available_at:
        raise CalendarGateError(
            "TRADING_CALENDAR_PIT_MISMATCH: 日历 payload 与不可变制品的 available_at 不一致。"
        )

    source_hash = calendar_snapshot.source_artifact_hash
    if stored.parent_snapshot_hashes != (source_hash,):
        raise CalendarGateError(
            "TRADING_CALENDAR_LINEAGE_MISMATCH: 日历制品必须只有一个与 payload 一致的 raw 来源。"
        )
    source_stored = store.load_artifact(source_hash)
    if (
        source_stored.snapshot.kind is not ArtifactKind.RAW
        or source_stored.snapshot.quality_status is not QualityStatus.PASS
        or source_stored.snapshot.available_at > snapshot.available_at
        or source_stored.source != stored.source
    ):
        raise CalendarGateError(
            "TRADING_CALENDAR_SOURCE_SNAPSHOT_INVALID: 日历 raw 来源未通过质量、PIT 或身份校验。"
        )

    attributes = dict(snapshot.provenance.attributes)
    if (
        attributes.get("calendar_payload_sha256") != content_sha256(
            payload,
            field_name="calendar artifact payload",
        )
        or attributes.get("calendar_content_hash") != calendar_content_hash(snapshots)
        or attributes.get("calendar_source_snapshot_hash") != source_hash
    ):
        raise CalendarGateError(
            "TRADING_CALENDAR_RECORD_BINDING_INVALID: 制品 record 未绑定日历内容和来源快照。"
        )
    _assert_calendar_source_authorized(
        source_stored=source_stored,
        calendar_snapshot=calendar_snapshot,
    )


def _assert_calendar_source_authorized(*, source_stored, calendar_snapshot) -> None:
    """按运行时当前授权和冻结的来源快照同时核对日历覆盖范围。"""

    configured_source = get_data_source(source_stored.source.source_id)
    license_config = configured_source.license
    declared_products = {
        session.instrument_id.split(".", maxsplit=1)[1]
        for session in calendar_snapshot.sessions
    }
    if not declared_products:
        raise CalendarGateError(
            "TRADING_CALENDAR_SOURCE_SCOPE_INVALID: 日历未声明任何稳定品种会话。"
        )
    if not (
        source_stored.source.config_sha256 == data_source_config_sha256(configured_source)
        and source_stored.source.license.allows_live_trading
        and configured_source.status == "active"
        and configured_source.supported.authoritative_calendar
        and "CN" in configured_source.supported.markets
        and "FUTURES" in configured_source.supported.asset_types
        and license_config.allows_live_trading
        and license_config.is_active
        and calendar_snapshot.exchange_id in license_config.authorized_exchanges
        and declared_products.issubset(set(license_config.authorized_products))
        and "trading_calendar" in license_config.authorized_datasets
        and "live" in license_config.authorized_environments
    ):
        raise CalendarGateError(
            "TRADING_CALENDAR_SOURCE_UNAUTHORIZED: 当前来源授权不覆盖该日历的交易所、品种、数据集或 live 环境。"
        )


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CalendarGateError(f"TRADING_CALENDAR_TIME_REQUIRED: {field_name} 必须包含时区。")
    return value.astimezone(UTC)

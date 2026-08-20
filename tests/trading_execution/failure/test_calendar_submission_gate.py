"""P1-WP04：日历门禁必须在最终订单进入券商前失败关闭。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from northstar_quant.application import calendar_gate, live_service
from northstar_quant.application.calendar_gate import (
    CalendarGateError,
    assert_execution_contract_admissible,
    assert_order_calendar_open,
    assert_profile_calendar_market_session,
    assert_profile_calendar_trading_day,
    load_calendar_service_for_profile,
)
from northstar_quant.data_platform.artifacts.fingerprints import content_sha256
from northstar_quant.data_platform.calendars import (
    CalendarError,
    CalendarQualityStatus,
    CalendarSession,
    CalendarService,
    TradingCalendarSnapshot,
    calendar_content_hash,
    load_trading_calendar,
    load_trading_calendar_payload,
)
from northstar_quant.data_platform.contracts import (
    ArtifactKind,
    Commodity,
    Contract,
    ContractFeeSchedule,
    ContractMaster,
    ContractRuleSnapshot,
    ContractTradingSession,
    DeliveryRestriction,
    Exchange,
    Instrument,
    ListingState,
    QualityStatus,
    RuleQualityStatus,
)
from northstar_quant.platform.common.enums import AssetType
from northstar_quant.trading_execution.broker.broker_base import BrokerAdapter
from northstar_quant.trading_execution.execution.models import OrderRequest, OrderResult
from northstar_quant.trading_execution.execution.router import OrderRouter
from northstar_quant.portfolio_risk.limits.models import RiskLimits
from tests.helpers.paths import PROJECT_ROOT


SHANGHAI = ZoneInfo("Asia/Shanghai")
FIXTURE_PATH = (
    PROJECT_ROOT / "tests" / "golden" / "trading_calendar" / "cn_futures_synthetic_v1.yaml"
)


def _local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=SHANGHAI)


def _calendar_service() -> CalendarService:
    return CalendarService(load_trading_calendar(FIXTURE_PATH, allow_test_fixtures=True))


def _profile(tmp_path: Path) -> SimpleNamespace:
    mapping_path = tmp_path / "ctp_sim.yaml"
    mapping_path.write_text(
        """
version: 1
broker: ctp_sim
contracts:
  - continuous_symbol: RB_CONT
    data_symbol: RB2610
    instrument_id: rb2610
    exchange_id: SHFE
    product_id: rb
    volume_multiple: 10
    price_tick: 1
    trading_enabled: true
""".strip(),
        encoding="utf-8",
    )
    return SimpleNamespace(
        timezone="Asia/Shanghai",
        futures=SimpleNamespace(ctp_contract_mapping_path=str(mapping_path)),
    )


def _runtime_calendar_payload() -> bytes:
    """一份只用于制品绑定测试的最小 runtime payload。"""

    return b"""
version: 1
fixture_scope: runtime
snapshots:
  - calendar_id: SHFE_RB_RUNTIME_V1
    exchange_id: SHFE
    timezone_name: Asia/Shanghai
    observed_at: \"2026-01-01T08:00:00+08:00\"
    available_at: \"2026-01-01T09:00:00+08:00\"
    coverage_start: 2026-01-05
    coverage_end: 2026-01-05
    source_artifact_hash: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
    quality_status: pass
    trading_days: [2026-01-05]
    closed_dates: []
    sessions:
      - exchange_id: SHFE
        instrument_id: SHFE.RB
        trading_day: 2026-01-05
        session_id: DAY
        opens_at: \"2026-01-05T09:00:00+08:00\"
        closes_at: \"2026-01-05T15:00:00+08:00\"
""".strip()


def _runtime_snapshot_for(
    exchange_id: str,
    product_code: str,
    *,
    source_artifact_hash: str,
) -> TradingCalendarSnapshot:
    """构造多交易所聚合测试用的单交易所 runtime 日历事实。"""

    trading_day = date(2026, 1, 5)
    stable_instrument_id = f"{exchange_id}.{product_code}"
    return TradingCalendarSnapshot.create(
        calendar_id=f"{exchange_id}_{product_code}_RUNTIME_V1",
        exchange_id=exchange_id,
        timezone_name="Asia/Shanghai",
        observed_at=_local(2026, 1, 1, 8),
        available_at=_local(2026, 1, 1, 9),
        coverage_start=trading_day,
        coverage_end=trading_day,
        source_artifact_hash=source_artifact_hash,
        quality_status=CalendarQualityStatus.PASS,
        trading_days=(trading_day,),
        closed_dates=(),
        sessions=(
            CalendarSession(
                exchange_id=exchange_id,
                instrument_id=stable_instrument_id,
                trading_day=trading_day,
                session_id="DAY",
                opens_at=_local(2026, 1, 5, 9),
                closes_at=_local(2026, 1, 5, 15),
            ),
        ),
    )


def _install_verified_calendar_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    payload: bytes | None = None,
    final_content_hash: str | None = None,
    source_license=None,
) -> SimpleNamespace:
    """以纯内存制品库替身安装完整 runtime 信任链。"""

    payload = payload or _runtime_calendar_payload()
    snapshots = load_trading_calendar_payload(payload, require_runtime=True)
    source_hash = snapshots[0].source_artifact_hash
    final_hash = "b" * 64
    config_hash = "c" * 64
    source = SimpleNamespace(
        source_id="licensed-calendar-source",
        config_sha256=config_hash,
        license=SimpleNamespace(allows_live_trading=True),
    )
    final_available_at = snapshots[0].available_at
    attributes = (
        ("calendar_payload_sha256", content_sha256(_runtime_calendar_payload())),
        ("calendar_content_hash", calendar_content_hash(
            load_trading_calendar_payload(_runtime_calendar_payload(), require_runtime=True)
        )),
        ("calendar_source_snapshot_hash", source_hash),
    )
    raw = SimpleNamespace(
        snapshot=SimpleNamespace(
            kind=ArtifactKind.RAW,
            quality_status=QualityStatus.PASS,
            available_at=datetime(2026, 1, 1, 0, tzinfo=UTC),
        ),
        source=source,
        parent_snapshot_hashes=(),
    )
    final = SimpleNamespace(
        snapshot=SimpleNamespace(
            kind=ArtifactKind.NORMALIZED,
            artifact_id="trading-calendar.runtime.v1",
            schema_version="trading-calendar.v1",
            transform_version="trading-calendar-normalize.v1",
            quality_status=QualityStatus.PASS,
            available_at=final_available_at,
            content_hash=final_content_hash or content_sha256(payload),
            provenance=SimpleNamespace(attributes=attributes),
        ),
        source=source,
        parent_snapshot_hashes=(source_hash,),
    )

    class _FakeArtifactStore:
        def __init__(self, _root: Path) -> None:
            pass

        def load_artifact(self, snapshot_hash: str):
            if snapshot_hash == final_hash:
                return final
            if snapshot_hash == source_hash:
                return raw
            raise AssertionError(f"unexpected artifact hash: {snapshot_hash}")

        def read_payload(self, snapshot_hash: str) -> bytes:
            assert snapshot_hash == final_hash
            return payload

    license_config = source_license or SimpleNamespace(
        allows_live_trading=True,
        is_active=True,
        authorized_exchanges=("SHFE",),
        authorized_products=("RB",),
        authorized_datasets=("trading_calendar",),
        authorized_environments=("live",),
    )
    configured_source = SimpleNamespace(
        status="active",
        supported=SimpleNamespace(
            authoritative_calendar=True,
            markets=("CN",),
            asset_types=("FUTURES",),
        ),
        license=license_config,
    )
    storage_dir = tmp_path / "storage"
    (storage_dir / "artifacts").mkdir(parents=True)
    monkeypatch.setattr(calendar_gate, "ArtifactStore", _FakeArtifactStore)
    monkeypatch.setattr(
        calendar_gate,
        "get_settings",
        lambda: SimpleNamespace(storage_dir=storage_dir),
    )
    monkeypatch.setattr(calendar_gate, "get_data_source", lambda _source_id: configured_source)
    monkeypatch.setattr(calendar_gate, "data_source_config_sha256", lambda _source: config_hash)
    return SimpleNamespace(
        futures=SimpleNamespace(calendar_artifact_snapshot_hashes={"SHFE": final_hash}),
        source_hash=source_hash,
    )


class _PreparedContractBroker(BrokerAdapter):
    """模拟 prepare_order 后才拥有真实 CTP 合约身份的适配器。"""

    def __init__(self) -> None:
        self.submitted: list[OrderRequest] = []

    def get_name(self) -> str:
        return "ctp_sim"

    def prepare_order(self, order: OrderRequest) -> OrderRequest:
        return replace(
            order,
            symbol="RB2610",
            instrument_id="rb2610",
            exchange_id="SHFE",
        )

    def submit_order(self, order: OrderRequest) -> OrderResult:
        self.submitted.append(order)
        return OrderResult(
            accepted=True,
            broker_order_id="ctp-sim-1",
            status="Submitted",
        )


def _order() -> OrderRequest:
    return OrderRequest(
        strategy_id="calendar-test",
        symbol="RB2610",
        side="BUY",
        qty=1.0,
        reference_price=3500.0,
    )


def _execution_master(
    *,
    expires_on: date = date(2026, 12, 31),
    delivery_restriction: DeliveryRestriction = DeliveryRestriction.NONE,
    include_rules: bool = True,
    rules_available_at: datetime = datetime(2026, 1, 1, 1, tzinfo=UTC),
    sessions: tuple[ContractTradingSession, ...] | None = None,
) -> ContractMaster:
    contract = Contract(
        contract_id="SHFE.RB2610",
        instrument_id="SHFE.RB",
        symbol="RB2610",
        listed_on=date(2025, 1, 1),
        expires_on=expires_on,
    )
    rule = ContractRuleSnapshot.create(
        snapshot_id="SHFE.RB2610.RULES.V1",
        contract_id=contract.contract_id,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        available_at=rules_available_at,
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
        effective_until=None,
        listing_state=ListingState.LISTED,
        expires_on=expires_on,
        multiplier=10.0,
        tick_size=1.0,
        initial_margin_rate=0.1,
        fees=ContractFeeSchedule(
            open_per_lot=0.0,
            open_rate=0.0,
            close_per_lot=0.0,
            close_rate=0.0,
            close_today_per_lot=0.0,
            close_today_rate=0.0,
        ),
        lower_price_limit=1.0,
        upper_price_limit=10_000.0,
        sessions=sessions
        or (
            ContractTradingSession(
                session_id="NIGHT",
                opens_at=time(21),
                closes_at=time(2, 30),
            ),
            ContractTradingSession(
                session_id="DAY",
                opens_at=time(9),
                closes_at=time(15),
            ),
        ),
        delivery_restriction=delivery_restriction,
        source_artifact_hash="d" * 64,
        source_authority="authorized-calendar-contract-fixture",
        quality_status=RuleQualityStatus.PASS,
        execution_eligible=True,
    )
    return ContractMaster(
        master_id="CN_FUTURES_TEST",
        version="v1",
        commodities=(Commodity(commodity_id="REBAR", name="螺纹钢"),),
        exchanges=(
            Exchange(
                exchange_id="SHFE",
                name="上海期货交易所",
                market="CN",
                timezone_name="Asia/Shanghai",
            ),
        ),
        instruments=(
            Instrument(
                instrument_id="SHFE.RB",
                commodity_id="REBAR",
                exchange_id="SHFE",
                product_code="RB",
            ),
        ),
        continuous_series=(),
        contracts=(contract,),
        rule_snapshots=(rule,) if include_rules else (),
    )


def test_closed_holiday_calendar_blocks_after_prepare_and_before_broker_submit(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    broker = _PreparedContractBroker()
    rules = _execution_master().rule_snapshots[0]
    observed: list[OrderRequest] = []

    def guard(order: OrderRequest) -> None:
        observed.append(order)
        assert_order_calendar_open(
            profile=profile,
            broker_name="ctp_sim",
            order=order,
            calendar_service=_calendar_service(),
            contract_rule_snapshot=rules,
            at=_local(2026, 2, 20, 10),
        )

    router = OrderRouter(
        broker,
        RiskLimits(max_order_notional=None, long_only=False),
        submission_guard=guard,
    )

    with pytest.raises(CalendarGateError, match="TRADING_CALENDAR_BLOCKED"):
        router.route(_order())

    assert len(observed) == 1
    assert observed[0].instrument_id == "rb2610"
    assert observed[0].exchange_id == "SHFE"
    assert broker.submitted == []


def test_explicit_night_session_allows_final_actual_contract_identity(tmp_path: Path) -> None:
    profile = _profile(tmp_path)
    broker = _PreparedContractBroker()
    rules = _execution_master().rule_snapshots[0]

    def guard(order: OrderRequest) -> None:
        decision = assert_order_calendar_open(
            profile=profile,
            broker_name="ctp_sim",
            order=order,
            calendar_service=_calendar_service(),
            contract_rule_snapshot=rules,
            at=_local(2026, 1, 4, 21, 15),
        )
        assert decision.session is not None
        assert decision.session.session_id == "NIGHT"
        assert decision.trading_day.isoformat() == "2026-01-05"

    router = OrderRouter(
        broker,
        RiskLimits(max_order_notional=None, long_only=False),
        submission_guard=guard,
    )

    result = router.route(_order())

    assert result.accepted is True
    assert len(broker.submitted) == 1
    assert broker.submitted[0].instrument_id == "rb2610"


def test_actual_contract_rule_session_can_narrow_an_open_product_calendar_session(
    tmp_path: Path,
) -> None:
    """实际月份合约临时缩短交易时段时，较宽的品种日历不能单独放行。"""

    profile = _profile(tmp_path)
    rules = _execution_master(
        sessions=(
            ContractTradingSession(
                session_id="DAY",
                opens_at=time(9),
                closes_at=time(10),
            ),
        )
    ).rule_snapshots[0]

    with pytest.raises(CalendarGateError, match="TRADING_CONTRACT_SESSION_BLOCKED"):
        assert_order_calendar_open(
            profile=profile,
            broker_name="ctp_sim",
            order=replace(_order(), instrument_id="rb2610", exchange_id="SHFE"),
            calendar_service=_calendar_service(),
            contract_rule_snapshot=rules,
            at=_local(2026, 1, 5, 14),
        )


def test_contract_master_gate_accepts_only_current_actual_contract_with_pass_rules(
    tmp_path: Path,
) -> None:
    profile = _profile(tmp_path)
    contract, rules = assert_execution_contract_admissible(
        profile=profile,
        broker_name="ctp_sim",
        order=replace(_order(), instrument_id="rb2610", exchange_id="SHFE"),
        at=_local(2026, 1, 5, 10),
        contract_master=_execution_master(),
    )

    assert contract.symbol == "RB2610"
    assert rules.delivery_restriction is DeliveryRestriction.NONE


@pytest.mark.parametrize(
    "master",
    [
        _execution_master(expires_on=date(2026, 1, 4)),
        _execution_master(delivery_restriction=DeliveryRestriction.CLOSE_ONLY),
        _execution_master(include_rules=False),
        _execution_master(rules_available_at=datetime(2026, 1, 6, tzinfo=UTC)),
    ],
    ids=("expired", "delivery", "rules_unknown", "rules_not_yet_available"),
)
def test_contract_master_gate_blocks_expiry_delivery_and_unknown_rules(
    tmp_path: Path,
    master: ContractMaster,
) -> None:
    profile = _profile(tmp_path)

    with pytest.raises(CalendarGateError, match="TRADING_CONTRACT_MASTER_BLOCKED"):
        assert_execution_contract_admissible(
            profile=profile,
            broker_name="ctp_sim",
            order=replace(_order(), instrument_id="rb2610", exchange_id="SHFE"),
            at=_local(2026, 1, 5, 10),
            contract_master=master,
        )


def test_missing_final_actual_contract_identity_never_guesses_product(tmp_path: Path) -> None:
    profile = _profile(tmp_path)

    with pytest.raises(CalendarGateError, match="CONTRACT_IDENTITY_REQUIRED"):
        assert_order_calendar_open(
            profile=profile,
            broker_name="ctp_sim",
            order=_order(),
            calendar_service=_calendar_service(),
            contract_rule_snapshot=_execution_master().rule_snapshots[0],
            at=_local(2026, 1, 4, 21, 15),
        )


def test_missing_runtime_calendar_artifact_hashes_block_before_any_load() -> None:
    profile = SimpleNamespace(futures=SimpleNamespace(calendar_artifact_snapshot_hashes={}))

    with pytest.raises(CalendarGateError, match="TRADING_CALENDAR_ARTIFACT_REQUIRED"):
        load_calendar_service_for_profile(profile)


def test_runtime_calendar_is_loaded_from_verified_immutable_payload_not_project_yaml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    profile = _install_verified_calendar_artifact(monkeypatch, tmp_path)

    service = load_calendar_service_for_profile(profile)

    decision = service.resolve_market_session(
        "SHFE",
        "SHFE.RB",
        _local(2026, 1, 5, 10),
        _local(2026, 1, 5, 10),
    )
    assert decision.is_open


def test_runtime_calendar_artifact_map_aggregates_each_exchange_independently(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """一个画像可持有多个交易所的不可变日历，而不信任单个混合 YAML。"""

    snapshots_by_hash = {
        "b" * 64: _runtime_snapshot_for(
            "SHFE",
            "RB",
            source_artifact_hash="d" * 64,
        ),
        "c" * 64: _runtime_snapshot_for(
            "DCE",
            "I",
            source_artifact_hash="e" * 64,
        ),
    }
    loaded: list[tuple[str, str]] = []
    storage_dir = tmp_path / "storage"
    (storage_dir / "artifacts").mkdir(parents=True)

    class _UnusedArtifactStore:
        def __init__(self, _root: Path) -> None:
            pass

    def _load_one(*, store, expected_exchange_id: str, configured_hash: str):
        del store
        loaded.append((expected_exchange_id, configured_hash))
        return snapshots_by_hash[configured_hash]

    monkeypatch.setattr(calendar_gate, "ArtifactStore", _UnusedArtifactStore)
    monkeypatch.setattr(
        calendar_gate,
        "get_settings",
        lambda: SimpleNamespace(storage_dir=storage_dir),
    )
    monkeypatch.setattr(
        calendar_gate,
        "_load_one_runtime_calendar_artifact",
        _load_one,
    )
    profile = SimpleNamespace(
        futures=SimpleNamespace(
            calendar_artifact_snapshot_hashes={"DCE": "c" * 64, "SHFE": "b" * 64},
        ),
    )

    service = load_calendar_service_for_profile(profile)

    assert loaded == [("DCE", "c" * 64), ("SHFE", "b" * 64)]
    assert {
        snapshot.exchange_id
        for snapshot in service.snapshots_as_of(_local(2026, 1, 5, 10))
    } == {"DCE", "SHFE"}
    assert service.resolve_market_session(
        "DCE",
        "DCE.I",
        _local(2026, 1, 5, 10),
        _local(2026, 1, 5, 10),
    ).is_open


@pytest.mark.parametrize(
    "mutated_payload",
    [
        lambda payload: payload.replace(b"15:00:00+08:00", b"14:00:00+08:00", 1),
        lambda payload: payload.replace(b"09:00:00+08:00", b"10:00:00+08:00", 1),
        lambda payload: payload.replace(b"quality_status: pass", b"quality_status: warn", 1),
        lambda payload: payload.replace(b"exchange_id: SHFE", b"exchange_id: DCE", 1),
    ],
    ids=("session", "available_at", "quality", "exchange"),
)
def test_mutated_runtime_calendar_payload_never_reuses_authorized_artifact_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutated_payload,
) -> None:
    original = _runtime_calendar_payload()
    mutated = mutated_payload(original)
    # 模拟攻击者试图随 payload 修改 record 的 blob hash；内容/来源 provenance 仍是原始
    # immutable record，必须不能被重用。若修改先破坏 YAML 领域约束，也同样不能进入运行时。
    with pytest.raises((CalendarError, CalendarGateError)):
        profile = _install_verified_calendar_artifact(
            monkeypatch,
            tmp_path,
            payload=mutated,
            final_content_hash=content_sha256(mutated),
        )
        load_calendar_service_for_profile(profile)


def test_runtime_calendar_blocks_when_authorized_source_scope_omits_declared_product(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    profile = _install_verified_calendar_artifact(
        monkeypatch,
        tmp_path,
        source_license=SimpleNamespace(
            allows_live_trading=True,
            is_active=True,
            authorized_exchanges=("SHFE",),
            authorized_products=(),
            authorized_datasets=("trading_calendar",),
            authorized_environments=("live",),
        ),
    )

    with pytest.raises(CalendarGateError, match="TRADING_CALENDAR_SOURCE_UNAUTHORIZED"):
        load_calendar_service_for_profile(profile)


def test_runtime_calendar_blocks_when_source_license_is_not_effective_yet(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    profile = _install_verified_calendar_artifact(
        monkeypatch,
        tmp_path,
        source_license=SimpleNamespace(
            allows_live_trading=True,
            is_active=False,
            authorized_exchanges=("SHFE",),
            authorized_products=("RB",),
            authorized_datasets=("trading_calendar",),
            authorized_environments=("live",),
        ),
    )

    with pytest.raises(CalendarGateError, match="TRADING_CALENDAR_SOURCE_UNAUTHORIZED"):
        load_calendar_service_for_profile(profile)


def test_scheduler_day_gate_requires_each_enabled_product_to_have_a_declared_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    profile = _profile(tmp_path)
    service = _calendar_service()
    monkeypatch.setattr(
        calendar_gate,
        "load_calendar_service_for_profile",
        lambda _profile: service,
    )

    decisions = assert_profile_calendar_trading_day(
        profile=profile,
        broker_name="ctp_sim",
        at=_local(2026, 1, 5, 10),
    )

    assert len(decisions) == 1
    assert decisions[0].is_open


def test_execution_session_guard_allows_friday_night_when_calendar_attributes_it_to_monday(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _profile(tmp_path)
    service = _calendar_service()
    monkeypatch.setattr(
        calendar_gate,
        "load_calendar_service_for_profile",
        lambda _profile: service,
    )

    decisions = assert_profile_calendar_market_session(
        profile=profile,
        broker_name="ctp_sim",
        at=_local(2026, 1, 2, 21, 15),
    )

    assert len(decisions) == 1
    assert decisions[0].is_open
    assert decisions[0].trading_day is not None
    assert decisions[0].trading_day.isoformat() == "2026-01-05"


def test_final_submission_guard_reloads_profile_without_process_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """画像的日历制品 pin 被撤销后，长进程的下一笔订单不能沿用旧缓存。"""

    order = OrderRequest(
        strategy_id="calendar-test",
        profile_id="runtime-profile",
        account="ctp-sim-account",
        symbol="RB2610",
        side="BUY",
        qty=1.0,
    )
    uncached_profiles: list[str] = []
    resolved_rules = object()
    calendar_calls: list[dict[str, object]] = []

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
            return None

    monkeypatch.setattr(
        live_service,
        "load_settings",
        lambda: SimpleNamespace(
            broker="ctp_sim",
            kill_switch_enabled=False,
            live_trading_enabled=False,
            runtime_risk_gate_max_age_seconds=90,
        ),
    )
    monkeypatch.setattr(
        live_service,
        "load_trading_profile",
        lambda _profile_id: pytest.fail("最终门禁不得使用缓存画像 loader"),
    )
    monkeypatch.setattr(
        live_service,
        "load_trading_profile_uncached",
        lambda profile_id: (
            uncached_profiles.append(profile_id)
            or SimpleNamespace(profile_id=profile_id, futures=SimpleNamespace())
        ),
    )
    monkeypatch.setattr(live_service, "validate_profile_data_governance", lambda _profile: None)
    monkeypatch.setattr(
        live_service,
        "ensure_broker_profile",
        lambda profile, **_kwargs: profile,
    )
    monkeypatch.setattr(live_service, "SessionLocal", _Session)
    monkeypatch.setattr(
        live_service,
        "latest_runtime_risk_record",
        lambda *_args, **_kwargs: SimpleNamespace(
            can_submit=True,
            checked_at=datetime.now(UTC),
        ),
    )
    monkeypatch.setattr(
        live_service,
        "assert_execution_contract_admissible",
        lambda **_kwargs: (object(), resolved_rules),
    )
    monkeypatch.setattr(live_service, "load_calendar_service_for_profile", lambda _profile: object())
    monkeypatch.setattr(
        live_service,
        "assert_order_calendar_open",
        lambda **kwargs: calendar_calls.append(kwargs),
    )

    live_service._assert_live_submission_allowed("ctp_sim", order)

    assert uncached_profiles == ["runtime-profile"]
    assert calendar_calls[0]["contract_rule_snapshot"] is resolved_rules


def test_final_submission_guard_rechecks_uncached_profile_execution_eligibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """操作员撤销画像的实际合约执行资格后，不能继续走到合约或日历门禁。"""

    order = OrderRequest(
        strategy_id="calendar-test",
        profile_id="revoked-profile",
        account="ctp-sim-account",
        symbol="RB2610",
        side="BUY",
        qty=1.0,
    )
    revoked_profile = SimpleNamespace(
        profile_id="revoked-profile",
        asset_type=AssetType.FUTURES,
        lifecycle=SimpleNamespace(role="simulated"),
        futures=SimpleNamespace(
            symbols_are_continuous=True,
            execution_allowed=False,
        ),
        data=SimpleNamespace(source_id=""),
    )
    downstream_calls: list[str] = []
    monkeypatch.setattr(
        live_service,
        "load_settings",
        lambda: SimpleNamespace(
            broker="ctp_sim",
            kill_switch_enabled=False,
            live_trading_enabled=False,
            runtime_risk_gate_max_age_seconds=90,
        ),
    )
    monkeypatch.setattr(
        live_service,
        "load_trading_profile_uncached",
        lambda _profile_id: revoked_profile,
    )
    monkeypatch.setattr(
        live_service,
        "assert_execution_contract_admissible",
        lambda **_kwargs: downstream_calls.append("contract"),
    )
    monkeypatch.setattr(
        live_service,
        "load_calendar_service_for_profile",
        lambda _profile: downstream_calls.append("calendar-service"),
    )
    monkeypatch.setattr(
        live_service,
        "assert_order_calendar_open",
        lambda **_kwargs: downstream_calls.append("calendar-order"),
    )

    with pytest.raises(PermissionError, match="PROFILE_GOVERNANCE_BLOCKED"):
        live_service._assert_live_submission_allowed("ctp_sim", order)

    assert downstream_calls == []

"""P1-WP03 合约主数据与执行资格解析。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
import hashlib

import pytest

from northstar_quant.data.contracts.contract_master import (
    Commodity,
    Contract,
    ContractFeeSchedule,
    ContractMaster,
    ContractMasterError,
    ContractResolution,
    ContractResolutionStatus,
    ContractRuleSnapshot,
    ContractTradingSession,
    ContinuousResearchSeries,
    DeliveryRestriction,
    Exchange,
    Instrument,
    ListingState,
    RuleQualityStatus,
)
from northstar_quant.data.artifacts.fingerprints import canonical_json_sha256
from northstar_quant.data.contracts.contract_master_loader import load_contract_master
from tests.helpers.paths import PROJECT_ROOT


UTC_TIME = datetime(2026, 7, 1, 8, tzinfo=UTC)
CONTRACT_EXPIRY = date(2026, 10, 30)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _contract() -> Contract:
    return Contract(
        contract_id="shfe.rb.2610",
        instrument_id="shfe.rb",
        symbol="rb2610",
        listed_on=date(2025, 10, 1),
        expires_on=CONTRACT_EXPIRY,
    )


def _snapshot(
    *,
    snapshot_id: str = "shfe.rb.2610.20260701",
    observed_at: datetime = UTC_TIME,
    available_at: datetime = UTC_TIME + timedelta(minutes=5),
    effective_from: datetime = UTC_TIME,
    effective_until: datetime | None = None,
    listing_state: ListingState = ListingState.LISTED,
    delivery_restriction: DeliveryRestriction = DeliveryRestriction.NONE,
    quality_status: RuleQualityStatus = RuleQualityStatus.PASS,
    execution_eligible: bool = True,
    expires_on: date = CONTRACT_EXPIRY,
) -> ContractRuleSnapshot:
    return ContractRuleSnapshot.create(
        snapshot_id=snapshot_id,
        contract_id="shfe.rb.2610",
        observed_at=observed_at,
        available_at=available_at,
        effective_from=effective_from,
        effective_until=effective_until,
        listing_state=listing_state,
        expires_on=expires_on,
        multiplier=10,
        tick_size=1,
        initial_margin_rate=0.1,
        fees=ContractFeeSchedule(
            open_per_lot=1.0,
            open_rate=0.0,
            close_per_lot=1.0,
            close_rate=0.0,
            close_today_per_lot=1.0,
            close_today_rate=0.0,
        ),
        lower_price_limit=2800,
        upper_price_limit=3600,
        sessions=(
            ContractTradingSession("night", time(21), time(2, 30)),
            ContractTradingSession("day", time(9), time(15)),
        ),
        delivery_restriction=delivery_restriction,
        source_artifact_hash=_hash(snapshot_id),
        source_authority="exchange_daily_notice",
        quality_status=quality_status,
        execution_eligible=execution_eligible,
    )


def _master(*, snapshots: tuple[ContractRuleSnapshot, ...] = ()) -> ContractMaster:
    return ContractMaster(
        master_id="cn-futures",
        version="v1",
        commodities=(Commodity("rebar", "螺纹钢"),),
        exchanges=(Exchange("shfe", "上海期货交易所", "cn", "Asia/Shanghai"),),
        instruments=(Instrument("shfe.rb", "rebar", "shfe", "rb"),),
        continuous_series=(ContinuousResearchSeries("shfe.rb.cont", "shfe.rb", "rb_cont"),),
        contracts=(_contract(),),
        rule_snapshots=snapshots,
    )


def test_contract_master_keeps_continuous_research_series_out_of_execution() -> None:
    master = _master(snapshots=(_snapshot(),))

    resolution = master.resolve_for_execution("RB_CONT", decision_at=UTC_TIME + timedelta(hours=1))

    assert resolution.status is ContractResolutionStatus.CONTINUOUS_RESEARCH_ONLY
    assert resolution.contract_id is None
    assert resolution.rule_snapshot_hash is None
    with pytest.raises(ContractMasterError, match="CONTINUOUS_RESEARCH_ONLY"):
        master.require_execution_contract(resolution)


def test_repository_static_contract_master_has_no_implicit_execution_contracts() -> None:
    master = load_contract_master(PROJECT_ROOT / "configs" / "instruments" / "contract_master.yaml")

    assert master.master_id == "CN_FUTURES"
    assert len(master.commodities) == 8
    assert len(master.continuous_series) == 8
    assert master.contracts == ()
    assert master.resolve_for_execution(
        "RB_CONT", decision_at=UTC_TIME
    ).status is ContractResolutionStatus.CONTINUOUS_RESEARCH_ONLY
    assert master.resolve_for_execution(
        "RB2610", decision_at=UTC_TIME
    ).status is ContractResolutionStatus.UNKNOWN


def test_static_contract_master_rejects_rule_snapshots_and_unknown_fields(tmp_path) -> None:
    path = tmp_path / "master.yaml"
    path.write_text(
        """version: 1
master_id: fixture
commodities:
  - {commodity_id: REBAR, name: 螺纹钢}
exchanges:
  - {exchange_id: SHFE, name: 上海期货交易所, market: CN, timezone_name: Asia/Shanghai}
instruments:
  - {instrument_id: SHFE.RB, commodity_id: REBAR, exchange_id: SHFE, product_code: RB}
continuous_series:
  - {series_id: SHFE.RB.CONT, instrument_id: SHFE.RB, symbol: RB_CONT}
contracts:
  - {contract_id: SHFE.RB.2610, instrument_id: SHFE.RB, symbol: RB2610, listed_on: 2025-10-01, expires_on: 2026-10-30}
rule_snapshots:
  - untrusted_static_rule: true
""",
        encoding="utf-8",
    )

    with pytest.raises(ContractMasterError, match="不得声明 rule_snapshots"):
        load_contract_master(path)

    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "rule_snapshots:\n  - untrusted_static_rule: true",
            "rule_snapshots: []",
        ),
        encoding="utf-8",
    )
    master = load_contract_master(path)

    assert master.resolve_for_execution(
        "RB2610", decision_at=UTC_TIME + timedelta(hours=1)
    ).status is ContractResolutionStatus.RULES_UNKNOWN

    path.write_text(path.read_text(encoding="utf-8").replace("master_id: fixture", "master_id: fixture\nunknown: true"), encoding="utf-8")
    with pytest.raises(ContractMasterError, match="未知字段"):
        load_contract_master(path)


def test_unknown_contract_mapping_fails_closed_with_auditable_status() -> None:
    master = _master(snapshots=(_snapshot(),))

    resolution = master.resolve_for_execution("RB2699", decision_at=UTC_TIME + timedelta(hours=1))

    assert resolution.status is ContractResolutionStatus.UNKNOWN
    assert resolution.reason_code == "CONTRACT_MAPPING_UNKNOWN"
    with pytest.raises(ContractMasterError, match="CONTRACT_MAPPING_UNKNOWN"):
        master.require_execution_contract(resolution)


def test_resolved_contract_binds_pit_rule_snapshot_and_has_stable_identity() -> None:
    snapshot = _snapshot()
    master = _master(snapshots=(snapshot,))
    decision_at = UTC_TIME + timedelta(hours=1)

    resolution = master.resolve_for_execution("rb2610", decision_at=decision_at)
    contract, resolved_rules = master.require_execution_contract(resolution)

    assert resolution.status is ContractResolutionStatus.RESOLVED
    assert contract.contract_id == "SHFE.RB.2610"
    assert resolved_rules.snapshot_hash == snapshot.snapshot_hash
    assert resolution.resolution_hash == master.resolve_for_execution(
        "RB2610", decision_at=decision_at
    ).resolution_hash
    assert resolution.resolution_hash != master.resolve_for_execution(
        "RB2610", decision_at=decision_at + timedelta(minutes=1)
    ).resolution_hash


def test_master_revalidates_a_forged_resolved_status_before_exposing_rules() -> None:
    """公开值对象不是执行授权：主数据必须重跑所有门禁。"""

    snapshot = _snapshot(execution_eligible=False)
    master = _master(snapshots=(snapshot,))
    decision_at = UTC_TIME + timedelta(hours=1)
    forged = ContractResolution(
        master_fingerprint=master.fingerprint,
        requested_ref="RB2610",
        decision_at=decision_at,
        status=ContractResolutionStatus.RESOLVED,
        reason_code="CONTRACT_RESOLVED",
        contract_id="SHFE.RB.2610",
        rule_snapshot_hash=snapshot.snapshot_hash,
        resolution_hash=canonical_json_sha256(
            {
                "contract_id": "SHFE.RB.2610",
                "decision_at": decision_at.isoformat(),
                "master_fingerprint": master.fingerprint,
                "reason_code": "CONTRACT_RESOLVED",
                "requested_ref": "RB2610",
                "rule_snapshot_hash": snapshot.snapshot_hash,
                "status": "resolved",
            }
        ),
    )

    with pytest.raises(ContractMasterError, match="CONTRACT_RESOLUTION_REVALIDATION_FAILED"):
        master.require_execution_contract(forged)


def test_resolution_from_a_different_master_cannot_be_reused() -> None:
    source_master = _master(snapshots=(_snapshot(),))
    target_master = _master(snapshots=(_snapshot(snapshot_id="later-rule"),))
    resolution = source_master.resolve_for_execution(
        "RB2610", decision_at=UTC_TIME + timedelta(hours=1)
    )

    with pytest.raises(ContractMasterError, match="CONTRACT_RESOLUTION_MASTER_MISMATCH"):
        target_master.require_execution_contract(resolution)


def test_expiry_uses_exchange_local_date_not_utc_date() -> None:
    master = _master(snapshots=(_snapshot(),))
    after_local_midnight = datetime(2026, 10, 30, 17, tzinfo=UTC)

    resolution = master.resolve_for_execution("RB2610", decision_at=after_local_midnight)

    assert resolution.status is ContractResolutionStatus.EXPIRED
    assert resolution.reason_code == "CONTRACT_EXPIRED_AT_DECISION_TIME"


def test_rule_not_available_at_decision_time_never_leaks_backwards() -> None:
    master = _master(
        snapshots=(
            _snapshot(
                available_at=UTC_TIME + timedelta(hours=2),
                effective_from=UTC_TIME,
            ),
        )
    )

    resolution = master.resolve_for_execution("RB2610", decision_at=UTC_TIME + timedelta(hours=1))

    assert resolution.status is ContractResolutionStatus.RULES_NOT_YET_AVAILABLE
    assert resolution.reason_code == "CONTRACT_RULES_AVAILABLE_AFTER_DECISION_TIME"


def test_overlapping_rule_snapshots_fail_closed_instead_of_picking_latest() -> None:
    master = _master(
        snapshots=(
            _snapshot(snapshot_id="rule-one"),
            _snapshot(snapshot_id="rule-two", effective_from=UTC_TIME + timedelta(minutes=1)),
        )
    )

    resolution = master.resolve_for_execution("RB2610", decision_at=UTC_TIME + timedelta(hours=1))

    assert resolution.status is ContractResolutionStatus.AMBIGUOUS
    assert resolution.reason_code == "CONTRACT_RULE_SNAPSHOTS_OVERLAP"


@pytest.mark.parametrize(
    ("snapshot_kwargs", "expected_status"),
    [
        ({"listing_state": ListingState.SUSPENDED}, ContractResolutionStatus.NOT_LISTED),
        ({"listing_state": ListingState.EXPIRED}, ContractResolutionStatus.EXPIRED),
        (
            {"delivery_restriction": DeliveryRestriction.CLOSE_ONLY},
            ContractResolutionStatus.DELIVERY_RESTRICTED,
        ),
        (
            {"quality_status": RuleQualityStatus.WARN},
            ContractResolutionStatus.RULES_NOT_EXECUTION_ELIGIBLE,
        ),
        (
            {"execution_eligible": False},
            ContractResolutionStatus.RULES_NOT_EXECUTION_ELIGIBLE,
        ),
    ],
)
def test_non_executable_listing_delivery_and_quality_states_fail_closed(
    snapshot_kwargs: dict[str, object],
    expected_status: ContractResolutionStatus,
) -> None:
    master = _master(snapshots=(_snapshot(**snapshot_kwargs),))  # type: ignore[arg-type]

    resolution = master.resolve_for_execution("RB2610", decision_at=UTC_TIME + timedelta(hours=1))

    assert resolution.status is expected_status
    assert resolution.contract_id is None


def test_contract_master_rejects_continuous_as_actual_contract_and_tampered_snapshot() -> None:
    with pytest.raises(ContractMasterError, match="实际月份合约"):
        Contract(
            contract_id="shfe.rb.cont",
            instrument_id="shfe.rb",
            symbol="RB_CONT",
            listed_on=date(2025, 1, 1),
            expires_on=date(2026, 1, 1),
        )

    snapshot = _snapshot()
    with pytest.raises(ContractMasterError, match="snapshot_hash"):
        replace(snapshot, multiplier=20)
    with pytest.raises(ContractMasterError, match="expires_on 必须与 Contract 一致"):
        _master(snapshots=(_snapshot(expires_on=date(2026, 9, 30)),))

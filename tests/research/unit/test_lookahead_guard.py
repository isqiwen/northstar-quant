"""P2-WP05：逐决策前视偏差防线。"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
import hashlib
from pathlib import Path

import polars as pl
import pytest

import northstar_quant.research.validation.lookahead as lookahead_module
from northstar_quant.data_platform.artifacts.immutable_store import ArtifactStore
from northstar_quant.data_platform.contracts.contract_master import (
    Contract,
    ContractFeeSchedule,
    ContractRuleSnapshot,
    ContractTradingSession,
    DeliveryRestriction,
    ListingState,
    RuleQualityStatus,
)
from northstar_quant.data_platform.market.pit import (
    MarketDataKind,
    MarketDataPITSpec,
    MarketDataSnapshot,
)
from northstar_quant.data_platform.sources.protocol import PublicationPurpose, PublicationScope
from northstar_quant.research.features.models import FeatureBackfill, FeatureValue
from northstar_quant.research.validation.lookahead import (
    ContractKnowledgeEvidence,
    DecisionMarketDataEvidence,
    DecisionReplayCheckpoint,
    DecisionReplayEvidence,
    DecisionReplayPlan,
    EventAvailabilityEvidence,
    FeatureAvailabilityEvidence,
    FeeMarginRuleEvidence,
    LookaheadCertificate,
    LookaheadGuard,
    LookaheadGuardError,
    LookaheadReport,
    LookaheadViolationKind,
    TargetDecisionEvidence,
)
from tests.helpers.pit_publication import publish_authorized_pit_dataset


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


DECISION = datetime(2026, 1, 5, 8, tzinfo=UTC)


def _spec() -> MarketDataPITSpec:
    return MarketDataPITSpec(
        kind=MarketDataKind.BAR,
        key_columns=("date", "symbol"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=("close",),
        schema_version="lookahead_fixture_v1",
    )


def _snapshot(*, as_of: datetime = DECISION) -> MarketDataSnapshot:
    spec = _spec()
    available_at = as_of - timedelta(minutes=1)
    frame = pl.DataFrame(
        {
            "date": [date(2026, 1, 5)],
            "symbol": ["RB_CONT"],
            "close": [100.0],
            "available_at": [available_at],
        }
    ).with_columns(pl.col("available_at").cast(pl.Datetime("us", "UTC")))
    return MarketDataSnapshot.from_selected_frame(
        dataset_id="lookahead-dataset",
        dataset_version_hash=_hash("dataset"),
        source_artifact_snapshot_hash=_hash("source-snapshot"),
        source_id="fixture-source",
        source_config_sha256=_hash("source-config"),
        publication_authorization_hash=_hash("authorization"),
        publication_scope=PublicationScope(
            dataset_id="lookahead-dataset",
            market="CN",
            asset_type="FUTURES",
            frequency="1d",
            purpose=PublicationPurpose.HISTORICAL_BACKTEST,
            environment="test",
            exchanges=("SHFE",),
            products=("RB",),
            actual_contract_data=False,
        ),
        spec=spec,
        source_artifact_available_at=as_of,
        as_of=as_of,
        frame=frame,
    )


def _checkpoint() -> DecisionReplayCheckpoint:
    return DecisionReplayCheckpoint(
        decision_at=DECISION,
        decision_event_time=date(2026, 1, 5),
        dataset_version_hash=_hash("dataset"),
        pit_spec=_spec(),
    )


def _contract() -> Contract:
    return Contract(
        contract_id="SHFE.RB.2605",
        instrument_id="SHFE.RB",
        symbol="RB2605",
        listed_on=date(2025, 10, 1),
        expires_on=date(2026, 5, 15),
    )


def _rule(
    *,
    available_at: datetime = DECISION - timedelta(minutes=1),
    listing_state: ListingState = ListingState.LISTED,
    expires_on: date = date(2026, 5, 15),
    delivery_restriction: DeliveryRestriction = DeliveryRestriction.NONE,
) -> ContractRuleSnapshot:
    return ContractRuleSnapshot.create(
        snapshot_id="SHFE.RB.2605.20260105",
        contract_id="SHFE.RB.2605",
        observed_at=available_at - timedelta(minutes=1),
        available_at=available_at,
        effective_from=available_at - timedelta(minutes=1),
        effective_until=None,
        listing_state=listing_state,
        expires_on=expires_on,
        multiplier=10.0,
        tick_size=1.0,
        initial_margin_rate=0.1,
        fees=ContractFeeSchedule(
            open_per_lot=1.0,
            open_rate=0.0,
            close_per_lot=1.0,
            close_rate=0.0,
            close_today_per_lot=1.0,
            close_today_rate=0.0,
        ),
        lower_price_limit=2800.0,
        upper_price_limit=3600.0,
        sessions=(ContractTradingSession("day", time(9), time(15)),),
        delivery_restriction=delivery_restriction,
        source_artifact_hash=_hash("rule-source"),
        source_authority="fixture-rule-authority",
        quality_status=RuleQualityStatus.PASS,
        execution_eligible=True,
    )


def _evidence(
    *,
    snapshot: MarketDataSnapshot | None = None,
    checkpoint: DecisionReplayCheckpoint | None = None,
    target_decision_at: datetime = DECISION,
    target_available_at: datetime = DECISION,
    target_execution_at: datetime | None = DECISION + timedelta(days=1),
    features: tuple[FeatureAvailabilityEvidence, ...] = (),
    events: tuple[EventAvailabilityEvidence, ...] = (),
    contracts: tuple[ContractKnowledgeEvidence, ...] = (),
    rules: tuple[FeeMarginRuleEvidence, ...] = (),
    require_execution_rules: bool = False,
) -> DecisionReplayEvidence:
    market_snapshot = snapshot or _snapshot()
    checkpoint = checkpoint or _checkpoint()
    return DecisionReplayEvidence(
        market_data=DecisionMarketDataEvidence(
            checkpoint=checkpoint,
            market_snapshot=market_snapshot,
        ),
        target=TargetDecisionEvidence(
            decision_at=target_decision_at,
            available_at=target_available_at,
            source_snapshot_hash=market_snapshot.snapshot_id,
            target_hash=_hash("target"),
            execution_at=target_execution_at,
        ),
        features=features,
        events=events,
        contracts=contracts,
        fee_margin_rules=rules,
        require_execution_rules=require_execution_rules,
    )


def _kind_set(evidence: DecisionReplayEvidence) -> set[LookaheadViolationKind]:
    return {item.kind for item in LookaheadGuard().evaluate(evidence).violations}


def _published_evidence(tmp_path: Path) -> tuple[ArtifactStore, DecisionReplayEvidence]:
    frame = pl.DataFrame(
        {
            "date": [date(2026, 1, 5)],
            "symbol": ["RB_CONT"],
            "close": [100.0],
            "available_at": [DECISION - timedelta(minutes=1)],
        }
    ).with_columns(pl.col("available_at").cast(pl.Datetime("us", "UTC")))
    store, dataset = publish_authorized_pit_dataset(
        tmp_path,
        frame,
        dataset_id="lookahead-dataset",
        source_id="lookahead-fixture-source",
        adapter_id="lookahead-fixture-adapter",
        schema_version="lookahead_fixture_v1",
        artifact_id="lookahead-fixture",
        key_columns=("date", "symbol"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=("close",),
        normalized_available_at=DECISION,
    )
    checkpoint = DecisionReplayCheckpoint(
        decision_at=DECISION,
        decision_event_time=date(2026, 1, 5),
        dataset_version_hash=dataset.version_hash,
        pit_spec=_spec(),
    )
    snapshot = DecisionReplayPlan.create((checkpoint,)).replay_market_data(store)[0].market_snapshot
    return store, _evidence(snapshot=snapshot, checkpoint=checkpoint)


class _OverriddenReplayPlan(DecisionReplayPlan):
    """模拟调用方试图用子类替换固定 replay 语义。"""

    def replay_market_data(self, artifact_store: ArtifactStore):  # type: ignore[override]
        raise AssertionError("不应调用调用方覆写的 replay")


class _OverriddenArtifactStore(ArtifactStore):
    """模拟调用方试图替换不可变制品重放。"""

    def replay_dataset_version(self, version_hash: str):  # type: ignore[override]
        raise AssertionError("不应调用调用方覆写的 ArtifactStore")


def test_guard_replays_immutable_market_data_before_issuing_evidence_receipt(
    tmp_path: Path,
) -> None:
    store, evidence = _published_evidence(tmp_path)
    plan = DecisionReplayPlan.create((evidence.market_data.checkpoint,))

    guard = LookaheadGuard()
    certificate = guard.certify(plan, (evidence,), artifact_store=store)

    assert certificate.decision_time_safe is False
    assert certificate.candidate_admission_eligible is False
    assert certificate.selection_mode == "PER_DECISION_POINT_IN_TIME_REPLAY"
    assert certificate.as_manifest_mapping()["replay_plan"]["schedule_hash"] == plan.schedule_hash
    assert guard.verify_certificate(certificate, artifact_store=store).certificate_hash == certificate.certificate_hash


def test_guard_rejects_subclassed_plan_and_artifact_store(tmp_path: Path) -> None:
    store, evidence = _published_evidence(tmp_path)
    checkpoint = evidence.market_data.checkpoint
    plan = _OverriddenReplayPlan(checkpoints=(checkpoint,))

    with pytest.raises(LookaheadGuardError, match="精确的 DecisionReplayPlan"):
        LookaheadGuard().certify(plan, (evidence,), artifact_store=store)
    with pytest.raises(LookaheadGuardError, match="精确的 ArtifactStore"):
        LookaheadGuard().certify(
            DecisionReplayPlan.create((checkpoint,)),
            (evidence,),
            artifact_store=_OverriddenArtifactStore(tmp_path / "subclass-store"),
        )


def test_certificate_cannot_be_directly_constructed_without_guard_issuance() -> None:
    evidence = _evidence()
    plan = DecisionReplayPlan.create((evidence.market_data.checkpoint,))

    with pytest.raises(LookaheadGuardError, match="只能由 LookaheadGuard.certify"):
        LookaheadCertificate(plan=plan, reports=())


def test_verify_certificate_rejects_private_sentinel_forgery(tmp_path: Path) -> None:
    store, safe_evidence = _published_evidence(tmp_path)
    unsafe_evidence = _evidence(
        snapshot=safe_evidence.market_data.market_snapshot,
        checkpoint=safe_evidence.market_data.checkpoint,
        target_available_at=DECISION + timedelta(minutes=1),
    )
    plan = DecisionReplayPlan.create((unsafe_evidence.market_data.checkpoint,))
    forged = LookaheadCertificate(
        plan=plan,
        reports=(LookaheadReport(evidence=unsafe_evidence, violations=()),),
        _issuer=lookahead_module._CERTIFICATE_ISSUER,
    )

    with pytest.raises(LookaheadGuardError, match="future_target:TARGET_AVAILABLE_AFTER_DECISION"):
        LookaheadGuard().verify_certificate(forged, artifact_store=store)


def test_guard_rejects_hand_built_market_snapshot_that_does_not_match_replay(
    tmp_path: Path,
) -> None:
    store, safe_evidence = _published_evidence(tmp_path)
    snapshot = safe_evidence.market_data.market_snapshot
    forged_snapshot = MarketDataSnapshot.from_selected_frame(
        dataset_id=snapshot.dataset_id,
        dataset_version_hash=snapshot.dataset_version_hash,
        source_artifact_snapshot_hash=snapshot.source_artifact_snapshot_hash,
        source_id=snapshot.source_id,
        source_config_sha256=_hash("forged-source-config"),
        publication_authorization_hash=snapshot.publication_authorization_hash,
        publication_scope=snapshot.publication_scope,
        spec=snapshot.spec,
        source_artifact_available_at=snapshot.source_artifact_available_at,
        as_of=snapshot.as_of,
        frame=snapshot.selected_frame(),
    )
    evidence = _evidence(
        snapshot=forged_snapshot,
        checkpoint=safe_evidence.market_data.checkpoint,
    )
    plan = DecisionReplayPlan.create((evidence.market_data.checkpoint,))

    with pytest.raises(LookaheadGuardError, match="MARKET_SNAPSHOT_REPLAY_MISMATCH"):
        LookaheadGuard().certify(plan, (evidence,), artifact_store=store)


def test_replay_plan_rejects_unsorted_or_duplicate_decision_times() -> None:
    first = _checkpoint()
    later = DecisionReplayCheckpoint(
        decision_at=DECISION + timedelta(days=1),
        decision_event_time=date(2026, 1, 6),
        dataset_version_hash=_hash("dataset"),
        pit_spec=_spec(),
    )
    with pytest.raises(LookaheadGuardError, match="升序"):
        DecisionReplayPlan.create((later, first))
    with pytest.raises(LookaheadGuardError, match="重复"):
        DecisionReplayPlan.create((first, first))


def test_guard_reports_revised_historical_data_when_final_snapshot_is_used_early() -> None:
    future_snapshot = _snapshot(as_of=DECISION + timedelta(hours=1))

    kinds = _kind_set(_evidence(snapshot=future_snapshot))

    assert LookaheadViolationKind.REVISED_HISTORICAL_DATA in kinds


def test_guard_reports_future_feature() -> None:
    snapshot = _snapshot()
    value = FeatureValue(
        feature_version_hash=_hash("feature-version"),
        lineage_hash=_hash("feature-lineage"),
        key_json='{"symbol":"RB_CONT"}',
        event_time=date(2026, 1, 5),
        available_at=DECISION + timedelta(minutes=1),
        value=1.0,
    )
    feature = FeatureAvailabilityEvidence(
        feature_version_hash=value.feature_version_hash,
        lineage_hash=value.lineage_hash,
        input_snapshot_hash=snapshot.snapshot_id,
        decision_at=DECISION,
        available_at=DECISION + timedelta(minutes=1),
        values=(value,),
    )

    assert LookaheadViolationKind.FUTURE_FEATURE in _kind_set(
        _evidence(snapshot=snapshot, features=(feature,))
    )


def test_guard_reports_future_event_and_target_but_allows_delayed_execution() -> None:
    event = EventAvailabilityEvidence(
        event_id="macro-release",
        event_at=DECISION - timedelta(minutes=1),
        available_at=DECISION + timedelta(minutes=1),
        source_artifact_snapshot_hash=_hash("event-source"),
    )
    evidence = _evidence(
        events=(event,),
        target_available_at=DECISION + timedelta(minutes=1),
    )

    kinds = _kind_set(evidence)

    assert LookaheadViolationKind.FUTURE_EVENT in kinds
    assert LookaheadViolationKind.FUTURE_TARGET in kinds


def test_target_evidence_rejects_backdated_availability_or_execution() -> None:
    with pytest.raises(LookaheadGuardError, match="available_at 不能早于"):
        TargetDecisionEvidence(
            decision_at=DECISION,
            available_at=DECISION - timedelta(seconds=1),
            source_snapshot_hash=_hash("source"),
            target_hash=_hash("target"),
        )
    with pytest.raises(LookaheadGuardError, match="execution_at 不能早于"):
        TargetDecisionEvidence(
            decision_at=DECISION,
            available_at=DECISION,
            source_snapshot_hash=_hash("source"),
            target_hash=_hash("target"),
            execution_at=DECISION - timedelta(seconds=1),
        )


def test_guard_reports_future_contract_knowledge_and_fee_margin_rule() -> None:
    contract = _contract()
    contract_evidence = ContractKnowledgeEvidence(
        contract=contract,
        master_fingerprint=_hash("master"),
        available_at=DECISION + timedelta(minutes=1),
        source_artifact_snapshot_hash=_hash("contract-source"),
    )
    rule_evidence = FeeMarginRuleEvidence(
        master_fingerprint=_hash("master"),
        rule_snapshot=_rule(available_at=DECISION + timedelta(minutes=1)),
    )

    kinds = _kind_set(
        _evidence(
            contracts=(contract_evidence,),
            rules=(rule_evidence,),
            require_execution_rules=True,
        )
    )

    assert LookaheadViolationKind.FUTURE_CONTRACT_KNOWLEDGE in kinds
    assert LookaheadViolationKind.FUTURE_FEE_MARGIN_RULE in kinds


def test_guard_reports_contract_rule_master_fingerprint_mismatch() -> None:
    contract_evidence = ContractKnowledgeEvidence(
        contract=_contract(),
        master_fingerprint=_hash("contract-master-a"),
        available_at=DECISION,
        source_artifact_snapshot_hash=_hash("contract-source"),
    )
    rule_evidence = FeeMarginRuleEvidence(
        master_fingerprint=_hash("contract-master-b"),
        rule_snapshot=_rule(),
    )
    report = LookaheadGuard().evaluate(
        _evidence(
            contracts=(contract_evidence,),
            rules=(rule_evidence,),
            require_execution_rules=True,
        )
    )

    assert any(
        item.reason_code == "CONTRACT_RULE_MASTER_FINGERPRINT_MISMATCH"
        for item in report.violations
    )


@pytest.mark.parametrize(
    ("rule", "reason_code"),
    (
        (_rule(listing_state=ListingState.EXPIRED), "FEE_MARGIN_RULE_LISTING_NOT_LISTED"),
        (_rule(expires_on=date(2026, 1, 4)), "FEE_MARGIN_RULE_EXPIRED_AT_DECISION"),
        (
            _rule(delivery_restriction=DeliveryRestriction.NO_NEW_POSITION),
            "FEE_MARGIN_RULE_DELIVERY_RESTRICTED",
        ),
        (_rule(expires_on=date(2026, 5, 14)), "FEE_MARGIN_RULE_CONTRACT_EXPIRY_MISMATCH"),
    ),
)
def test_execution_rule_evidence_rejects_non_executable_contract_semantics(
    rule: ContractRuleSnapshot,
    reason_code: str,
) -> None:
    contract_evidence = ContractKnowledgeEvidence(
        contract=_contract(),
        master_fingerprint=_hash("master"),
        available_at=DECISION,
        source_artifact_snapshot_hash=_hash("contract-source"),
    )
    rule_evidence = FeeMarginRuleEvidence(
        master_fingerprint=_hash("master"),
        rule_snapshot=rule,
    )
    report = LookaheadGuard().evaluate(
        _evidence(
            contracts=(contract_evidence,),
            rules=(rule_evidence,),
            require_execution_rules=True,
        )
    )

    assert any(item.reason_code == reason_code for item in report.violations)


def test_guard_rejects_any_violation_when_issuing_evidence_receipt(tmp_path: Path) -> None:
    store, safe_evidence = _published_evidence(tmp_path)
    evidence = _evidence(
        snapshot=safe_evidence.market_data.market_snapshot,
        checkpoint=safe_evidence.market_data.checkpoint,
        target_available_at=DECISION + timedelta(minutes=1),
    )
    plan = DecisionReplayPlan.create((evidence.market_data.checkpoint,))

    with pytest.raises(LookaheadGuardError, match="future_target:TARGET_AVAILABLE_AFTER_DECISION"):
        LookaheadGuard().certify(plan, (evidence,), artifact_store=store)


def test_existing_static_feature_backfill_cannot_be_used_as_strict_evidence() -> None:
    value = FeatureValue(
        feature_version_hash=_hash("feature-version"),
        lineage_hash=_hash("feature-lineage"),
        key_json='{"symbol":"RB_CONT"}',
        event_time=date(2026, 1, 5),
        available_at=DECISION,
        value=1.0,
    )
    backfill = FeatureBackfill(
        lineage_hash=value.lineage_hash,
        feature_version_hash=value.feature_version_hash,
        implementation_hash=_hash("implementation"),
        available_at=DECISION,
        selection_mode="STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY",
        decision_time_safe=False,
        values=(value,),
    )

    with pytest.raises(LookaheadGuardError, match="STATIC_FEATURE_BACKFILL"):
        LookaheadGuard().assert_static_feature_rejected(backfill=backfill, decision_at=DECISION)


def test_static_feature_value_cannot_be_wrapped_into_replay_receipt(tmp_path: Path) -> None:
    store, safe_evidence = _published_evidence(tmp_path)
    snapshot = safe_evidence.market_data.market_snapshot
    value = FeatureValue(
        feature_version_hash=_hash("feature-version"),
        lineage_hash=_hash("feature-lineage"),
        key_json='{"symbol":"RB_CONT"}',
        event_time=date(2026, 1, 5),
        available_at=DECISION,
        value=1.0,
    )
    feature = FeatureAvailabilityEvidence(
        feature_version_hash=value.feature_version_hash,
        lineage_hash=value.lineage_hash,
        input_snapshot_hash=snapshot.snapshot_id,
        decision_at=DECISION,
        available_at=DECISION,
        values=(value,),
    )
    evidence = _evidence(
        snapshot=snapshot,
        checkpoint=safe_evidence.market_data.checkpoint,
        features=(feature,),
    )
    plan = DecisionReplayPlan.create((evidence.market_data.checkpoint,))

    with pytest.raises(
        LookaheadGuardError,
        match="future_feature:STRICT_FEATURE_REPLAY_PRODUCER_UNAVAILABLE",
    ):
        LookaheadGuard().certify(plan, (evidence,), artifact_store=store)

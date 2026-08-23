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
from northstar_quant.data_platform.contracts.artifact_rulebook import (
    RULEBOOK_DATASET_ID,
    RULEBOOK_DATASET_TRANSFORM_VERSION,
    RULEBOOK_SCHEMA_VERSION,
    RULEBOOK_TRANSFORM_VERSION,
    _REQUIRED_COLUMNS as RULEBOOK_COLUMNS,
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
    LookaheadInputKind,
    LookaheadInputUsage,
    LookaheadInputUsageDeclaration,
    LookaheadReport,
    LookaheadViolationKind,
    TargetDecisionEvidence,
    replay_artifact_event_evidence,
    replay_artifact_rulebook_evidence,
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


def _rulebook_frame() -> pl.DataFrame:
    row: dict[str, object] = {
        "master_id": "CN_FUTURES",
        "master_version": "rulebook-v1",
        "commodity_id": "REBAR",
        "commodity_name": "螺纹钢",
        "exchange_id": "SHFE",
        "exchange_name": "上海期货交易所",
        "market": "CN",
        "timezone_name": "Asia/Shanghai",
        "instrument_id": "SHFE.RB",
        "product_code": "RB",
        "contract_id": "SHFE.RB.2605",
        "contract_symbol": "RB2605",
        "contract_available_at": DECISION - timedelta(minutes=3),
        "listed_on": date(2025, 10, 1),
        "contract_expires_on": date(2026, 5, 15),
        "rule_snapshot_id": "SHFE.RB.2605.20260105",
        "observed_at": DECISION - timedelta(minutes=3),
        "available_at": DECISION - timedelta(minutes=2),
        "effective_from": DECISION - timedelta(minutes=2),
        "effective_until": None,
        "listing_state": "listed",
        "multiplier": 10.0,
        "tick_size": 1.0,
        "initial_margin_rate": 0.1,
        "open_per_lot": 1.0,
        "open_rate": 0.0,
        "close_per_lot": 1.0,
        "close_rate": 0.0,
        "close_today_per_lot": 1.0,
        "close_today_rate": 0.0,
        "lower_price_limit": 2800.0,
        "upper_price_limit": 3600.0,
        "sessions_json": '[{"closes_at":"15:00:00","opens_at":"09:00:00","session_id":"day"}]',
        "delivery_restriction": "none",
        "source_authority": "fixture_rulebook_notice",
    }
    schema: dict[str, object] = {column: pl.String for column in RULEBOOK_COLUMNS}
    for column in (
        "contract_available_at",
        "observed_at",
        "available_at",
        "effective_from",
        "effective_until",
    ):
        schema[column] = pl.Datetime(time_zone="UTC")
    for column in ("listed_on", "contract_expires_on"):
        schema[column] = pl.Date
    for column in (
        "multiplier",
        "tick_size",
        "initial_margin_rate",
        "open_per_lot",
        "open_rate",
        "close_per_lot",
        "close_rate",
        "close_today_per_lot",
        "close_today_rate",
        "lower_price_limit",
        "upper_price_limit",
    ):
        schema[column] = pl.Float64
    return pl.DataFrame([row], schema=schema, strict=True).select(RULEBOOK_COLUMNS)


def _published_rulebook(tmp_path: Path) -> tuple[ArtifactStore, str]:
    store, dataset = publish_authorized_pit_dataset(
        tmp_path,
        _rulebook_frame(),
        dataset_id=RULEBOOK_DATASET_ID,
        source_id="lookahead-rulebook-source",
        adapter_id="lookahead-rulebook-adapter",
        schema_version=RULEBOOK_SCHEMA_VERSION,
        artifact_id="lookahead-rulebook-v1",
        key_columns=("contract_id", "rule_snapshot_id"),
        event_time_column="effective_from",
        available_at_column="available_at",
        value_columns=tuple(
            column
            for column in RULEBOOK_COLUMNS
            if column not in {"contract_id", "rule_snapshot_id", "available_at"}
        ),
        normalized_available_at=DECISION,
        frequency="snapshot",
        scope_exchanges=("SHFE",),
        scope_products=("RB",),
        actual_contract_data=True,
        requires_authoritative_dynamic_rules=True,
        transform_version=RULEBOOK_TRANSFORM_VERSION,
        dataset_transform_version=RULEBOOK_DATASET_TRANSFORM_VERSION,
    )
    return store, dataset.version_hash


def _evidence(
    *,
    snapshot: MarketDataSnapshot | None = None,
    checkpoint: DecisionReplayCheckpoint | None = None,
    target_decision_at: datetime = DECISION,
    target_available_at: datetime = DECISION,
    target_execution_at: datetime | None = DECISION + timedelta(days=1),
    features: tuple[FeatureAvailabilityEvidence, ...] = (),
    events: tuple[EventAvailabilityEvidence, ...] = (),
    artifact_events: lookahead_module.ArtifactEventReplayEvidence | None = None,
    contracts: tuple[ContractKnowledgeEvidence, ...] = (),
    rules: tuple[FeeMarginRuleEvidence, ...] = (),
    artifact_rulebook: lookahead_module.ArtifactRuleBookEvidence | None = None,
    input_usage: tuple[LookaheadInputUsageDeclaration, ...] | None = None,
    require_execution_rules: bool = False,
) -> DecisionReplayEvidence:
    market_snapshot = snapshot or _snapshot()
    checkpoint = checkpoint or _checkpoint()
    if input_usage is None:
        input_usage = (
            LookaheadInputUsageDeclaration(
                input_kind=LookaheadInputKind.FEATURE,
                usage=LookaheadInputUsage.PROVIDED if features else LookaheadInputUsage.NOT_USED,
                producer_identity_hash=_hash("fixture-producer"),
            ),
            LookaheadInputUsageDeclaration(
                input_kind=LookaheadInputKind.EVENT,
                usage=LookaheadInputUsage.PROVIDED if events else LookaheadInputUsage.NOT_USED,
                producer_identity_hash=_hash("fixture-producer"),
            ),
            LookaheadInputUsageDeclaration(
                input_kind=LookaheadInputKind.CONTRACT,
                usage=LookaheadInputUsage.PROVIDED if contracts else LookaheadInputUsage.NOT_USED,
                producer_identity_hash=_hash("fixture-producer"),
            ),
            LookaheadInputUsageDeclaration(
                input_kind=LookaheadInputKind.FEE_MARGIN_RULE,
                usage=LookaheadInputUsage.PROVIDED if rules else LookaheadInputUsage.NOT_USED,
                producer_identity_hash=_hash("fixture-producer"),
            ),
        )
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
        artifact_events=artifact_events,
        contracts=contracts,
        fee_margin_rules=rules,
        artifact_rulebook=artifact_rulebook,
        input_usage=input_usage,
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
            "available_at": [DECISION],
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


def test_guard_refuses_to_sign_a_receipt_with_implicit_empty_input_categories(
    tmp_path: Path,
) -> None:
    store, safe_evidence = _published_evidence(tmp_path)
    evidence = _evidence(
        snapshot=safe_evidence.market_data.market_snapshot,
        checkpoint=safe_evidence.market_data.checkpoint,
        input_usage=(),
    )
    plan = DecisionReplayPlan.create((evidence.market_data.checkpoint,))

    with pytest.raises(
        LookaheadGuardError,
        match="INPUT_USAGE_DECLARATIONS_INCOMPLETE",
    ):
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


def test_guard_replays_immutable_event_fact_dataset_when_signing(tmp_path: Path) -> None:
    store, safe_evidence = _published_evidence(tmp_path)
    source_hash = safe_evidence.market_data.market_snapshot.source_artifact_snapshot_hash
    event_frame = pl.DataFrame(
        {
            "event_id": ["fixture-event"],
            "event_at": [DECISION - timedelta(minutes=2)],
            "available_at": [DECISION],
            "source_evidence_hash": [source_hash],
        }
    ).with_columns(
        pl.col("event_at").cast(pl.Datetime("us", "UTC")),
        pl.col("available_at").cast(pl.Datetime("us", "UTC")),
    )
    event_store, event_dataset = publish_authorized_pit_dataset(
        tmp_path,
        event_frame,
        dataset_id="research_event_fact",
        source_id="event-fixture-source",
        adapter_id="event-fixture-adapter",
        schema_version="research_event_fact_v1",
        artifact_id="event-fixture-v1",
        key_columns=("event_id", "event_at"),
        event_time_column="event_at",
        available_at_column="available_at",
        value_columns=("source_evidence_hash",),
        normalized_available_at=DECISION,
    )
    strict_events = replay_artifact_event_evidence(
        artifact_store=event_store,
        dataset_version_hash=event_dataset.version_hash,
        decision_at=DECISION,
    )
    evidence = _evidence(
        snapshot=safe_evidence.market_data.market_snapshot,
        checkpoint=safe_evidence.market_data.checkpoint,
        events=strict_events.events,
        artifact_events=strict_events,
    )

    certificate = LookaheadGuard().certify(
        DecisionReplayPlan.create((evidence.market_data.checkpoint,)),
        (evidence,),
        artifact_store=event_store,
    )

    assert certificate.certificate_hash
    assert strict_events.events[0].event_id == "fixture-event"


def test_guard_refuses_hand_built_event_evidence_when_signing(tmp_path: Path) -> None:
    store, safe_evidence = _published_evidence(tmp_path)
    evidence = _evidence(
        snapshot=safe_evidence.market_data.market_snapshot,
        checkpoint=safe_evidence.market_data.checkpoint,
        events=(
            EventAvailabilityEvidence(
                event_id="manual-event",
                event_at=DECISION - timedelta(minutes=1),
                available_at=DECISION,
                source_artifact_snapshot_hash=_hash("manual-event-source"),
            ),
        ),
    )

    with pytest.raises(LookaheadGuardError, match="ARTIFACT_EVENT_EVIDENCE_REQUIRED_FOR_EVENTS"):
        LookaheadGuard().certify(
            DecisionReplayPlan.create((evidence.market_data.checkpoint,)),
            (evidence,),
            artifact_store=store,
        )


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


def test_guard_uses_only_replayed_immutable_rulebook_contract_and_rule_evidence(
    tmp_path: Path,
) -> None:
    store, dataset_version_hash = _published_rulebook(tmp_path)
    strict = replay_artifact_rulebook_evidence(
        artifact_store=store,
        dataset_version_hash=dataset_version_hash,
        decision_at=DECISION,
        contract_refs=("SHFE.RB.2605",),
    )
    market_dataset_store, market_dataset = publish_authorized_pit_dataset(
        tmp_path,
        pl.DataFrame(
            {
                "date": [date(2026, 1, 5)],
                "symbol": ["RB_CONT"],
                "close": [100.0],
                "available_at": [DECISION - timedelta(minutes=1)],
            }
        ).with_columns(pl.col("available_at").cast(pl.Datetime("us", "UTC"))),
        dataset_id="lookahead-market-dataset",
        source_id="lookahead-market-source",
        adapter_id="lookahead-market-adapter",
        schema_version="lookahead_fixture_v1",
        artifact_id="lookahead-market-v1",
        key_columns=("date", "symbol"),
        event_time_column="date",
        available_at_column="available_at",
        value_columns=("close",),
        normalized_available_at=DECISION,
    )
    checkpoint = DecisionReplayCheckpoint(
        decision_at=DECISION,
        decision_event_time=date(2026, 1, 5),
        dataset_version_hash=market_dataset.version_hash,
        pit_spec=_spec(),
    )
    market = DecisionReplayPlan.create((checkpoint,)).replay_market_data(market_dataset_store)[0]
    evidence = _evidence(
        snapshot=market.market_snapshot,
        checkpoint=checkpoint,
        contracts=strict.contracts,
        rules=strict.fee_margin_rules,
        artifact_rulebook=strict,
    )

    certificate = LookaheadGuard().certify(
        DecisionReplayPlan.create((checkpoint,)),
        (evidence,),
        artifact_store=market_dataset_store,
    )

    assert certificate.certificate_hash
    assert strict.replay.dataset_version_hash == dataset_version_hash
    assert strict.contracts[0].available_at == strict.fee_margin_rules[0].rule_snapshot.available_at
    assert strict.fee_margin_rules[0].rule_snapshot.execution_eligible is False


def test_guard_refuses_hand_built_contract_rule_evidence_when_signing(
    tmp_path: Path,
) -> None:
    store, safe_evidence = _published_evidence(tmp_path)
    evidence = _evidence(
        snapshot=safe_evidence.market_data.market_snapshot,
        checkpoint=safe_evidence.market_data.checkpoint,
        contracts=(
            ContractKnowledgeEvidence(
                contract=_contract(),
                master_fingerprint=_hash("manual-master"),
                available_at=DECISION,
                source_artifact_snapshot_hash=_hash("manual-contract-source"),
            ),
        ),
        rules=(
            FeeMarginRuleEvidence(
                master_fingerprint=_hash("manual-master"),
                rule_snapshot=_rule(),
            ),
        ),
    )

    with pytest.raises(
        LookaheadGuardError,
        match="ARTIFACT_RULEBOOK_EVIDENCE_REQUIRED_FOR_CONTRACT_RULES",
    ):
        LookaheadGuard().certify(
            DecisionReplayPlan.create((evidence.market_data.checkpoint,)),
            (evidence,),
            artifact_store=store,
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

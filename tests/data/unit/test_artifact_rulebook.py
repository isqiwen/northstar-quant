"""不可变制品化 Contract RuleBook 的历史重放测试。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from northstar_quant.data.contracts.artifact_rulebook import (
    ArtifactRuleBookError,
    ContractRuleBookPITSelector,
    RULEBOOK_DATASET_ID,
    RULEBOOK_DATASET_TRANSFORM_VERSION,
    RULEBOOK_SCHEMA_VERSION,
    RULEBOOK_TRANSFORM_VERSION,
    _REQUIRED_COLUMNS,
)
from tests.helpers.pit_publication import publish_authorized_pit_dataset


DECISION_AT = datetime(2026, 7, 1, 10, tzinfo=UTC)


def _row(**changes: object) -> dict[str, object]:
    """构造一行可被严格 codec 重建的 SHFE RB 实际合约规则。"""

    result: dict[str, object] = {
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
        "contract_id": "SHFE.RB.2610",
        "contract_symbol": "RB2610",
        "contract_available_at": DECISION_AT - timedelta(minutes=3),
        "listed_on": date(2025, 10, 1),
        "contract_expires_on": date(2026, 10, 30),
        "rule_snapshot_id": "SHFE.RB.2610.20260701",
        "observed_at": DECISION_AT - timedelta(minutes=3),
        "available_at": DECISION_AT - timedelta(minutes=2),
        "effective_from": DECISION_AT - timedelta(minutes=2),
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
        "sessions_json": (
            '[{"closes_at":"15:00:00","opens_at":"09:00:00","session_id":"day"}]'
        ),
        "delivery_restriction": "none",
        "source_authority": "fixture_rulebook_notice",
    }
    result.update(changes)
    return result


def _frame(*rows: dict[str, object]) -> pl.DataFrame:
    """保留 nullable timestamp 的 schema，避免把 open-ended 窗口降级为 Null dtype。"""

    schema: dict[str, Any] = {column: pl.String for column in _REQUIRED_COLUMNS}
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
    return pl.DataFrame(list(rows), schema=schema, strict=True).select(_REQUIRED_COLUMNS)


def _publish(
    root: Path,
    frame: pl.DataFrame,
    *,
    artifact_id: str = "rulebook-fixture-v1",
    normalized_available_at: datetime = DECISION_AT,
    scope_exchanges: tuple[str, ...] = ("SHFE",),
    scope_products: tuple[str, ...] = ("RB",),
    actual_contract_data: bool = True,
    requires_authoritative_dynamic_rules: bool = True,
) -> tuple[object, object]:
    return publish_authorized_pit_dataset(
        root,
        frame,
        dataset_id=RULEBOOK_DATASET_ID,
        source_id="fixture-contract-rulebook",
        adapter_id="fixture-contract-rulebook-adapter",
        schema_version=RULEBOOK_SCHEMA_VERSION,
        artifact_id=artifact_id,
        key_columns=("contract_id", "rule_snapshot_id"),
        event_time_column="effective_from",
        available_at_column="available_at",
        value_columns=tuple(
            column
            for column in frame.columns
            if column not in {"contract_id", "rule_snapshot_id", "available_at"}
        ),
        normalized_available_at=normalized_available_at,
        frequency="snapshot",
        scope_exchanges=scope_exchanges,
        scope_products=scope_products,
        actual_contract_data=actual_contract_data,
        requires_authoritative_dynamic_rules=requires_authoritative_dynamic_rules,
        transform_version=RULEBOOK_TRANSFORM_VERSION,
        dataset_transform_version=RULEBOOK_DATASET_TRANSFORM_VERSION,
    )


def test_replays_authorized_pass_rulebook_as_historical_non_execution_evidence(tmp_path: Path) -> None:
    store, dataset = _publish(tmp_path, _frame(_row()))

    replay = ContractRuleBookPITSelector(store).select(
        dataset_version_hash=dataset.version_hash,
        decision_at=DECISION_AT,
        contract_refs=("SHFE.RB.2610",),
    )

    assert replay.dataset_version_hash == dataset.version_hash
    assert replay.master.contracts[0].contract_id == "SHFE.RB.2610"
    assert replay.master.rule_snapshots[0].source_artifact_hash == replay.raw_snapshot_hash
    assert replay.master.rule_snapshots[0].execution_eligible is False
    assert replay.as_mapping()["execution_eligible"] is False
    assert replay.replay_hash == ContractRuleBookPITSelector(store).select(
        dataset_version_hash=dataset.version_hash,
        decision_at=DECISION_AT,
        contract_refs=("SHFE.RB.2610",),
    ).replay_hash


@pytest.mark.parametrize(
    ("actual_contract_data", "requires_authoritative_dynamic_rules"),
    ((False, True), (True, False)),
)
def test_rejects_dataset_without_actual_dynamic_rule_authorization(
    tmp_path: Path,
    actual_contract_data: bool,
    requires_authoritative_dynamic_rules: bool,
) -> None:
    store, dataset = _publish(
        tmp_path,
        _frame(_row()),
        actual_contract_data=actual_contract_data,
        requires_authoritative_dynamic_rules=requires_authoritative_dynamic_rules,
    )

    with pytest.raises(ArtifactRuleBookError, match="authorization scope"):
        ContractRuleBookPITSelector(store).select(
            dataset_version_hash=dataset.version_hash,
            decision_at=DECISION_AT,
            contract_refs=("SHFE.RB.2610",),
        )


def test_rejects_rulebook_row_outside_frozen_exchange_product_scope(tmp_path: Path) -> None:
    row = _row(
        commodity_id="COPPER",
        commodity_name="铜",
        exchange_id="DCE",
        exchange_name="大连商品交易所",
        instrument_id="DCE.CU",
        product_code="CU",
        contract_id="DCE.CU.2610",
        contract_symbol="CU2610",
        rule_snapshot_id="DCE.CU.2610.20260701",
    )
    store, dataset = _publish(tmp_path, _frame(row))

    with pytest.raises(ArtifactRuleBookError, match="冻结授权范围"):
        ContractRuleBookPITSelector(store).select(
            dataset_version_hash=dataset.version_hash,
            decision_at=DECISION_AT,
            contract_refs=("DCE.CU.2610",),
        )


def test_rejects_continuous_symbol_and_noncanonical_actual_contract_identity(tmp_path: Path) -> None:
    store, dataset = _publish(tmp_path, _frame(_row()))

    with pytest.raises(ArtifactRuleBookError, match="连续研究序列"):
        ContractRuleBookPITSelector(store).select(
            dataset_version_hash=dataset.version_hash,
            decision_at=DECISION_AT,
            contract_refs=("RB_CONT",),
        )

    invalid_store, invalid_dataset = _publish(
        tmp_path / "invalid",
        _frame(_row(contract_id="RB2610")),
    )
    with pytest.raises(ArtifactRuleBookError, match="contract_id/symbol"):
        ContractRuleBookPITSelector(invalid_store).select(
            dataset_version_hash=invalid_dataset.version_hash,
            decision_at=DECISION_AT,
            contract_refs=("RB2610",),
        )


def test_rejects_future_row_and_rule_overlap_instead_of_selecting_latest(tmp_path: Path) -> None:
    future_row = _row(available_at=DECISION_AT + timedelta(minutes=1))
    future_store, future_dataset = _publish(tmp_path / "future", _frame(future_row))
    with pytest.raises(ArtifactRuleBookError, match="行级 available_at"):
        ContractRuleBookPITSelector(future_store).select(
            dataset_version_hash=future_dataset.version_hash,
            decision_at=DECISION_AT + timedelta(minutes=2),
            contract_refs=("SHFE.RB.2610",),
        )

    overlap_store, overlap_dataset = _publish(
        tmp_path / "overlap",
        _frame(
            _row(),
            _row(rule_snapshot_id="SHFE.RB.2610.duplicate"),
        ),
    )
    with pytest.raises(ArtifactRuleBookError, match="重叠或冲突规则"):
        ContractRuleBookPITSelector(overlap_store).select(
            dataset_version_hash=overlap_dataset.version_hash,
            decision_at=DECISION_AT,
            contract_refs=("SHFE.RB.2610",),
        )


def test_expiry_uses_exchange_local_date_and_canonical_sessions_are_required(tmp_path: Path) -> None:
    store, dataset = _publish(tmp_path, _frame(_row()))

    with pytest.raises(ArtifactRuleBookError, match="已经到期"):
        ContractRuleBookPITSelector(store).select(
            dataset_version_hash=dataset.version_hash,
            decision_at=datetime(2026, 10, 30, 17, tzinfo=UTC),
            contract_refs=("SHFE.RB.2610",),
        )

    invalid_store, invalid_dataset = _publish(
        tmp_path / "sessions",
        _frame(
            _row(
                sessions_json=(
                    '[ {"session_id":"day","opens_at":"09:00:00","closes_at":"15:00:00"} ]'
                )
            )
        ),
    )
    with pytest.raises(ArtifactRuleBookError, match="canonical JSON"):
        ContractRuleBookPITSelector(invalid_store).select(
            dataset_version_hash=invalid_dataset.version_hash,
            decision_at=DECISION_AT,
            contract_refs=("SHFE.RB.2610",),
        )

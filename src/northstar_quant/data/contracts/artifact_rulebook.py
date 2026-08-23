"""不可变制品化的期货 Contract RuleBook 历史重放。

静态 ``contract_master.yaml`` 只保存稳定分类，不能作为历史合约、保证金、手续费或交易
规则的事实来源。本模块只读取已经由 P1 ``DataSourcePublisher`` 写入的 immutable
``DatasetVersion``，并在调用方给出的 ``decision_at`` 选择一份唯一、可见且有效的规则。

它是研究历史证据边界，不是 live Contract Master publisher：所有重建出的
``ContractRuleSnapshot.execution_eligible`` 都固定为 ``False``，不得以此放开任何 CTP、
ctp_sim 或非 paper 路径。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
import json
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import polars as pl

from northstar_quant.data.artifacts.fingerprints import (
    FingerprintError,
    canonical_json_sha256,
    content_sha256,
    require_sha256,
)
from northstar_quant.data.artifacts.immutable_store import (
    ArtifactStore,
    ArtifactStoreError,
    StoredArtifact,
)
from northstar_quant.data.contracts.contract_master import (
    Commodity,
    Contract,
    ContractFeeSchedule,
    ContractMaster,
    ContractMasterError,
    ContractRuleSnapshot,
    ContractTradingSession,
    DeliveryRestriction,
    Exchange,
    Instrument,
    ListingState,
    RuleQualityStatus,
)
from northstar_quant.data.contracts.data_domain import ArtifactKind, QualityStatus
from northstar_quant.data.market.pit import (
    MarketDataPITError,
    _decode_canonical_frame,
)
from northstar_quant.data.quality import DataQualityError, canonical_frame_payload
from northstar_quant.data.sources.protocol import PublicationPurpose


RULEBOOK_DATASET_ID = "cn_futures_contract_rule_book"
RULEBOOK_SCHEMA_VERSION = "cn_futures_contract_rule_book_v1"
RULEBOOK_TRANSFORM_VERSION = "normalize.contract-rulebook.v1"
RULEBOOK_DATASET_TRANSFORM_VERSION = "dataset.contract-rulebook.v1"

_REQUIRED_COLUMNS = (
    "master_id",
    "master_version",
    "commodity_id",
    "commodity_name",
    "exchange_id",
    "exchange_name",
    "market",
    "timezone_name",
    "instrument_id",
    "product_code",
    "contract_id",
    "contract_symbol",
    "contract_available_at",
    "listed_on",
    "contract_expires_on",
    "rule_snapshot_id",
    "observed_at",
    "available_at",
    "effective_from",
    "effective_until",
    "listing_state",
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
    "sessions_json",
    "delivery_restriction",
    "source_authority",
)


class ArtifactRuleBookError(ValueError):
    """规则账本来源、授权、schema、PIT 或领域语义不满足时失败关闭。"""


@dataclass(frozen=True, slots=True)
class ArtifactBackedContractRuleReplay:
    """一份在明确时点重放的、非执行资格的 Contract Master 事实。"""

    dataset_version_hash: str
    normalized_snapshot_hash: str
    raw_snapshot_hash: str
    quality_assessment_hash: str
    publication_authorization_hash: str
    decision_at: datetime
    master: ContractMaster
    selected_contract_ids: tuple[str, ...]
    selected_rule_snapshot_hashes: tuple[str, ...]
    replay_hash: str = field(init=False)

    def __post_init__(self) -> None:
        dataset_version_hash = _hash(self.dataset_version_hash, "dataset_version_hash")
        normalized_snapshot_hash = _hash(
            self.normalized_snapshot_hash,
            "normalized_snapshot_hash",
        )
        raw_snapshot_hash = _hash(self.raw_snapshot_hash, "raw_snapshot_hash")
        quality_assessment_hash = _hash(
            self.quality_assessment_hash,
            "quality_assessment_hash",
        )
        authorization_hash = _hash(
            self.publication_authorization_hash,
            "publication_authorization_hash",
        )
        decision_at = _utc_datetime(self.decision_at, "decision_at")
        if not isinstance(self.master, ContractMaster):
            raise ArtifactRuleBookError("master 必须是 ContractMaster")
        contract_ids = _contract_ids(self.selected_contract_ids, "selected_contract_ids")
        rule_hashes = _hashes(self.selected_rule_snapshot_hashes, "selected_rule_snapshot_hashes")
        if len(contract_ids) != len(rule_hashes):
            raise ArtifactRuleBookError("selected contract 与 rule snapshot 数量必须一致")
        rules_by_contract = {item.contract_id: item for item in self.master.rule_snapshots}
        if set(contract_ids) != set(rules_by_contract):
            raise ArtifactRuleBookError("master rule snapshots 必须与 selected contracts 精确一致")
        if tuple(sorted(item.snapshot_hash for item in rules_by_contract.values())) != rule_hashes:
            raise ArtifactRuleBookError("selected_rule_snapshot_hashes 与 master 规则不一致")
        if any(item.execution_eligible for item in rules_by_contract.values()):
            raise ArtifactRuleBookError("artifact-backed historical rules 永远不能声明 execution_eligible")
        replay_hash = canonical_json_sha256(
            {
                "dataset_version_hash": dataset_version_hash,
                "decision_at": decision_at.isoformat(),
                "format": "northstar.artifact-contract-rulebook-replay.v1",
                "master_fingerprint": self.master.fingerprint,
                "normalized_snapshot_hash": normalized_snapshot_hash,
                "publication_authorization_hash": authorization_hash,
                "quality_assessment_hash": quality_assessment_hash,
                "raw_snapshot_hash": raw_snapshot_hash,
                "selected_contract_ids": list(contract_ids),
                "selected_rule_snapshot_hashes": list(rule_hashes),
            }
        )
        object.__setattr__(self, "dataset_version_hash", dataset_version_hash)
        object.__setattr__(self, "normalized_snapshot_hash", normalized_snapshot_hash)
        object.__setattr__(self, "raw_snapshot_hash", raw_snapshot_hash)
        object.__setattr__(self, "quality_assessment_hash", quality_assessment_hash)
        object.__setattr__(self, "publication_authorization_hash", authorization_hash)
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "selected_contract_ids", contract_ids)
        object.__setattr__(self, "selected_rule_snapshot_hashes", rule_hashes)
        object.__setattr__(self, "replay_hash", replay_hash)

    def as_mapping(self) -> dict[str, object]:
        """返回 hash-only 历史证据；不把 raw payload、路径或当前配置写入结果。"""

        return {
            "dataset_version_hash": self.dataset_version_hash,
            "decision_at": self.decision_at.isoformat(),
            "format": "northstar.artifact-contract-rulebook-replay.v1",
            "master_fingerprint": self.master.fingerprint,
            "normalized_snapshot_hash": self.normalized_snapshot_hash,
            "publication_authorization_hash": self.publication_authorization_hash,
            "quality_assessment_hash": self.quality_assessment_hash,
            "raw_snapshot_hash": self.raw_snapshot_hash,
            "replay_hash": self.replay_hash,
            "selected_contract_ids": list(self.selected_contract_ids),
            "selected_rule_snapshot_hashes": list(self.selected_rule_snapshot_hashes),
            "execution_eligible": False,
        }


class ContractRuleBookPITSelector:
    """按显式 DatasetVersion 与 ``decision_at`` 重放 RuleBook，不读取当前配置或 YAML。"""

    def __init__(self, store: ArtifactStore) -> None:
        if type(store) is not ArtifactStore:
            raise ArtifactRuleBookError("store 必须是精确的 ArtifactStore，不能使用子类")
        self._store = store

    def select(
        self,
        *,
        dataset_version_hash: str,
        decision_at: datetime,
        contract_refs: Sequence[str],
    ) -> ArtifactBackedContractRuleReplay:
        """返回每个请求实际合约在该时点唯一可见且有效的非执行规则事实。"""

        version_hash = _hash(dataset_version_hash, "dataset_version_hash")
        decision = _utc_datetime(decision_at, "decision_at")
        requested_ids = _contract_ids(contract_refs, "contract_refs")
        try:
            dataset_replay = self._store.replay_dataset_version(version_hash)
        except ArtifactStoreError as exc:
            raise ArtifactRuleBookError("RuleBook DatasetVersion 无法从 immutable store 重放") from exc
        dataset = dataset_replay.dataset_version
        if dataset.dataset_id != RULEBOOK_DATASET_ID:
            raise ArtifactRuleBookError("RuleBook 必须使用固定 dataset_id")
        if dataset.schema_version != RULEBOOK_SCHEMA_VERSION:
            raise ArtifactRuleBookError("RuleBook DatasetVersion schema_version 不受支持")
        if dataset.transform_version != RULEBOOK_DATASET_TRANSFORM_VERSION:
            raise ArtifactRuleBookError("RuleBook DatasetVersion transform_version 不受支持")
        if len(dataset_replay.artifacts) != 1:
            raise ArtifactRuleBookError("RuleBook DatasetVersion 必须恰好包含一个 normalized 制品")
        artifact_replay = dataset_replay.artifacts[0]
        normalized = artifact_replay.stored
        self._validate_normalized_artifact(normalized, artifact_replay.payload, decision)
        raw = self._load_raw_parent(normalized)
        authorization_hash = normalized.publication_authorization_hash
        assessment_hash = normalized.quality_assessment_hash
        if authorization_hash is None or assessment_hash is None:  # defensive; validation above covers it.
            raise ArtifactRuleBookError("RuleBook normalized 制品缺少授权或质量绑定")
        authorized_exchanges, authorized_products = self._validate_authorization(
            authorization_hash,
            normalized,
            requested_ids,
        )
        frame = _decode_frame(artifact_replay.payload)
        parsed = _parse_rows(
            frame,
            decision_at=decision,
            requested_ids=requested_ids,
            raw_snapshot_hash=raw.snapshot.snapshot_hash,
            normalized_available_at=normalized.snapshot.available_at,
            authorized_exchanges=authorized_exchanges,
            authorized_products=authorized_products,
        )
        master = _build_master(parsed)
        selected_rules = tuple(
            next(
                item
                for item in master.rule_snapshots
                if item.contract_id == contract_id
            )
            for contract_id in requested_ids
        )
        return ArtifactBackedContractRuleReplay(
            dataset_version_hash=dataset.version_hash,
            normalized_snapshot_hash=normalized.snapshot.snapshot_hash,
            raw_snapshot_hash=raw.snapshot.snapshot_hash,
            quality_assessment_hash=assessment_hash,
            publication_authorization_hash=authorization_hash,
            decision_at=decision,
            master=master,
            selected_contract_ids=requested_ids,
            selected_rule_snapshot_hashes=tuple(
                sorted(item.snapshot_hash for item in selected_rules)
            ),
        )

    def _validate_normalized_artifact(
        self,
        stored: StoredArtifact,
        payload: bytes,
        decision_at: datetime,
    ) -> None:
        snapshot = stored.snapshot
        if snapshot.kind is not ArtifactKind.NORMALIZED:
            raise ArtifactRuleBookError("RuleBook DatasetVersion 只能引用 normalized 制品")
        if snapshot.schema_version != RULEBOOK_SCHEMA_VERSION:
            raise ArtifactRuleBookError("RuleBook normalized schema_version 不受支持")
        if snapshot.transform_version != RULEBOOK_TRANSFORM_VERSION:
            raise ArtifactRuleBookError("RuleBook normalized transform_version 不受支持")
        if snapshot.quality_status is not QualityStatus.PASS:
            raise ArtifactRuleBookError("RuleBook normalized 制品质量必须为 PASS")
        if snapshot.available_at > decision_at:
            raise ArtifactRuleBookError("RuleBook normalized 制品在 decision_at 后才可用")
        if content_sha256(payload, field_name="RuleBook normalized payload") != snapshot.content_hash:
            raise ArtifactRuleBookError("RuleBook normalized payload 哈希与 snapshot 不一致")
        if stored.quality_assessment_hash is None:
            raise ArtifactRuleBookError("RuleBook normalized 制品缺少 immutable quality assessment")
        if stored.publication_authorization_hash is None:
            raise ArtifactRuleBookError("RuleBook normalized 制品缺少 immutable authorization receipt")
        try:
            assessment = self._store.load_quality_assessment(snapshot.snapshot_hash).assessment
        except ArtifactStoreError as exc:
            raise ArtifactRuleBookError("RuleBook quality assessment 无法重放") from exc
        if assessment.assessment_hash != stored.quality_assessment_hash:
            raise ArtifactRuleBookError("RuleBook quality assessment binding 不一致")
        if assessment.aggregate_status is not QualityStatus.PASS:
            raise ArtifactRuleBookError("RuleBook quality assessment 必须为 PASS")
        if len(stored.parent_snapshot_hashes) != 1:
            raise ArtifactRuleBookError("RuleBook normalized 制品必须精确绑定一个 raw parent")

    def _load_raw_parent(self, normalized: StoredArtifact) -> StoredArtifact:
        raw_hash = normalized.parent_snapshot_hashes[0]
        try:
            raw = self._store.load_artifact(raw_hash)
        except ArtifactStoreError as exc:
            raise ArtifactRuleBookError("RuleBook raw parent 无法重放") from exc
        if raw.snapshot.kind is not ArtifactKind.RAW:
            raise ArtifactRuleBookError("RuleBook normalized parent 必须是 raw 制品")
        if raw.snapshot.source_id != normalized.snapshot.source_id:
            raise ArtifactRuleBookError("RuleBook raw/normalized source_id 不一致")
        if raw.snapshot.quality_status is not QualityStatus.PASS:
            raise ArtifactRuleBookError("RuleBook raw 制品质量必须为 PASS")
        if raw.publication_authorization_hash != normalized.publication_authorization_hash:
            raise ArtifactRuleBookError("RuleBook raw/normalized authorization receipt 不一致")
        if raw.snapshot.available_at > normalized.snapshot.available_at:
            raise ArtifactRuleBookError("RuleBook raw parent 不能晚于 normalized 制品可用")
        return raw

    def _validate_authorization(
        self,
        authorization_hash: str,
        normalized: StoredArtifact,
        requested_ids: tuple[str, ...],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        try:
            receipt = self._store.load_publication_authorization(authorization_hash)
        except ArtifactStoreError as exc:
            raise ArtifactRuleBookError("RuleBook authorization receipt 无法重放") from exc
        if receipt.authorization_hash != authorization_hash:
            raise ArtifactRuleBookError("RuleBook authorization receipt 哈希不一致")
        scope = _mapping(receipt.authorization.get("scope"), "authorization.scope")
        expected_scope_keys = {
            "actual_contract_data",
            "asset_type",
            "dataset_id",
            "environment",
            "exchanges",
            "frequency",
            "market",
            "products",
            "purpose",
            "requires_authoritative_calendar",
            "requires_authoritative_dynamic_rules",
        }
        if set(scope) != expected_scope_keys:
            raise ArtifactRuleBookError("RuleBook authorization scope 字段不受支持")
        if (
            scope["dataset_id"] != RULEBOOK_DATASET_ID
            or scope["market"] != "CN"
            or scope["asset_type"] != "FUTURES"
            or scope["frequency"] != "snapshot"
            or scope["purpose"] != PublicationPurpose.HISTORICAL_BACKTEST.value
            or scope["actual_contract_data"] is not True
            or scope["requires_authoritative_dynamic_rules"] is not True
        ):
            raise ArtifactRuleBookError("RuleBook authorization scope 不满足历史实际合约动态规则要求")
        exchanges = _identifier_set(scope["exchanges"], "authorization.scope.exchanges")
        products = _identifier_set(scope["products"], "authorization.scope.products")
        if not exchanges or not products:
            raise ArtifactRuleBookError("RuleBook authorization scope 必须显式覆盖 exchanges/products")
        source = _mapping(receipt.authorization.get("source"), "authorization.source")
        license_payload = _mapping(source.get("license"), "authorization.source.license")
        if source.get("source_id") != normalized.snapshot.source_id:
            raise ArtifactRuleBookError("RuleBook authorization source_id 与 normalized 制品不一致")
        if source.get("status") != "active" or license_payload.get("status") != "active":
            raise ArtifactRuleBookError("RuleBook 冻结授权来源必须为 active")
        purposes = _identifier_set(
            license_payload.get("permitted_purposes"),
            "authorization.source.license.permitted_purposes",
            upper=False,
        )
        if PublicationPurpose.HISTORICAL_BACKTEST.value not in purposes:
            raise ArtifactRuleBookError("RuleBook 冻结授权不包含 historical_backtest")
        # Scope 的实际 contract 归属在行级 parse 中再次核对；这里先验证请求集合不是空值。
        if not requested_ids:
            raise ArtifactRuleBookError("RuleBook contract_refs 不能为空")
        return exchanges, products


@dataclass(frozen=True, slots=True)
class _ParsedRow:
    master_id: str
    master_version: str
    commodity: Commodity
    exchange: Exchange
    instrument: Instrument
    contract: Contract
    contract_available_at: datetime
    rule: ContractRuleSnapshot


def _parse_rows(
    frame: pl.DataFrame,
    *,
    decision_at: datetime,
    requested_ids: tuple[str, ...],
    raw_snapshot_hash: str,
    normalized_available_at: datetime,
    authorized_exchanges: tuple[str, ...],
    authorized_products: tuple[str, ...],
) -> tuple[_ParsedRow, ...]:
    if tuple(frame.columns) != _REQUIRED_COLUMNS:
        raise ArtifactRuleBookError("RuleBook canonical frame 字段必须与固定 schema 精确一致")
    if frame.is_empty():
        raise ArtifactRuleBookError("RuleBook canonical frame 不能为空")
    requested = set(requested_ids)
    visible_candidates: dict[str, list[_ParsedRow]] = {contract_id: [] for contract_id in requested}
    contract_facts: dict[str, tuple[Commodity, Exchange, Instrument, Contract, datetime]] = {}
    master_identity: tuple[str, str] | None = None
    for index, row in enumerate(frame.iter_rows(named=True)):
        parsed = _parse_row(
            row,
            index=index,
            raw_snapshot_hash=raw_snapshot_hash,
            normalized_available_at=normalized_available_at,
        )
        if parsed.exchange.exchange_id not in authorized_exchanges:
            raise ArtifactRuleBookError("RuleBook row.exchange_id 不在冻结授权范围内")
        if parsed.instrument.product_code not in authorized_products:
            raise ArtifactRuleBookError("RuleBook row.product_code 不在冻结授权范围内")
        identity = (parsed.master_id, parsed.master_version)
        if master_identity is None:
            master_identity = identity
        elif master_identity != identity:
            raise ArtifactRuleBookError("RuleBook rows 必须属于同一个 master_id/version")
        previous = contract_facts.get(parsed.contract.contract_id)
        current = (
            parsed.commodity,
            parsed.exchange,
            parsed.instrument,
            parsed.contract,
            parsed.contract_available_at,
        )
        if previous is None:
            contract_facts[parsed.contract.contract_id] = current
        elif previous != current:
            raise ArtifactRuleBookError("同一 contract_id 的 RuleBook taxonomy 或 PIT 事实不一致")
        if parsed.contract.contract_id not in requested:
            continue
        _assert_contract_active_at(parsed, decision_at)
        rule = parsed.rule
        if rule.available_at > decision_at:
            continue
        if rule.effective_from > decision_at:
            continue
        if rule.effective_until is not None and decision_at >= rule.effective_until:
            continue
        visible_candidates[parsed.contract.contract_id].append(parsed)
    if master_identity is None:  # pragma: no cover - frame emptiness is checked above.
        raise ArtifactRuleBookError("RuleBook 缺少 master 身份")
    selected: list[_ParsedRow] = []
    for contract_id in requested_ids:
        candidates = visible_candidates[contract_id]
        if not candidates:
            raise ArtifactRuleBookError(f"RuleBook 在 decision_at 没有可见有效规则：{contract_id}")
        if len(candidates) != 1:
            raise ArtifactRuleBookError(f"RuleBook 在 decision_at 出现重叠或冲突规则：{contract_id}")
        selected.append(candidates[0])
    return tuple(selected)


def _parse_row(
    row: Mapping[str, object],
    *,
    index: int,
    raw_snapshot_hash: str,
    normalized_available_at: datetime,
) -> _ParsedRow:
    prefix = f"RuleBook row[{index}]"
    try:
        master_id = _text(row["master_id"], f"{prefix}.master_id")
        master_version = _text(row["master_version"], f"{prefix}.master_version")
        commodity = Commodity(
            commodity_id=_text(row["commodity_id"], f"{prefix}.commodity_id"),
            name=_text(row["commodity_name"], f"{prefix}.commodity_name"),
        )
        exchange = Exchange(
            exchange_id=_text(row["exchange_id"], f"{prefix}.exchange_id").upper(),
            name=_text(row["exchange_name"], f"{prefix}.exchange_name"),
            market=_text(row["market"], f"{prefix}.market").upper(),
            timezone_name=_timezone_name(
                row["timezone_name"],
                f"{prefix}.timezone_name",
            ),
        )
        if exchange.market != "CN":
            raise ArtifactRuleBookError(f"{prefix}.market 必须为 CN")
        instrument = Instrument(
            instrument_id=_text(row["instrument_id"], f"{prefix}.instrument_id"),
            commodity_id=commodity.commodity_id,
            exchange_id=exchange.exchange_id,
            product_code=_text(row["product_code"], f"{prefix}.product_code").upper(),
        )
        contract = Contract(
            contract_id=_text(row["contract_id"], f"{prefix}.contract_id"),
            instrument_id=instrument.instrument_id,
            symbol=_text(row["contract_symbol"], f"{prefix}.contract_symbol").upper(),
            listed_on=_date(row["listed_on"], f"{prefix}.listed_on"),
            expires_on=_date(row["contract_expires_on"], f"{prefix}.contract_expires_on"),
        )
        _assert_canonical_contract_identity(contract, exchange, instrument, prefix)
        contract_available_at = _utc_datetime(
            row["contract_available_at"],
            f"{prefix}.contract_available_at",
        )
        observed_at = _utc_datetime(row["observed_at"], f"{prefix}.observed_at")
        available_at = _utc_datetime(row["available_at"], f"{prefix}.available_at")
        effective_from = _utc_datetime(row["effective_from"], f"{prefix}.effective_from")
        effective_until = _optional_utc_datetime(
            row["effective_until"],
            f"{prefix}.effective_until",
        )
        if contract_available_at > normalized_available_at or available_at > normalized_available_at:
            raise ArtifactRuleBookError(f"{prefix} 行级 available_at 不能晚于 normalized 制品可读时间")
        if observed_at > available_at:
            raise ArtifactRuleBookError(f"{prefix}.observed_at 不能晚于 available_at")
        sessions = _sessions(row["sessions_json"], f"{prefix}.sessions_json")
        rule = ContractRuleSnapshot.create(
            snapshot_id=_text(row["rule_snapshot_id"], f"{prefix}.rule_snapshot_id"),
            contract_id=contract.contract_id,
            observed_at=observed_at,
            available_at=available_at,
            effective_from=effective_from,
            effective_until=effective_until,
            listing_state=_enum(ListingState, row["listing_state"], f"{prefix}.listing_state"),
            expires_on=contract.expires_on,
            multiplier=_positive_float(row["multiplier"], f"{prefix}.multiplier"),
            tick_size=_positive_float(row["tick_size"], f"{prefix}.tick_size"),
            initial_margin_rate=_nonnegative_float(
                row["initial_margin_rate"],
                f"{prefix}.initial_margin_rate",
            ),
            fees=ContractFeeSchedule(
                open_per_lot=_nonnegative_float(row["open_per_lot"], f"{prefix}.open_per_lot"),
                open_rate=_nonnegative_float(row["open_rate"], f"{prefix}.open_rate"),
                close_per_lot=_nonnegative_float(row["close_per_lot"], f"{prefix}.close_per_lot"),
                close_rate=_nonnegative_float(row["close_rate"], f"{prefix}.close_rate"),
                close_today_per_lot=_nonnegative_float(
                    row["close_today_per_lot"],
                    f"{prefix}.close_today_per_lot",
                ),
                close_today_rate=_nonnegative_float(
                    row["close_today_rate"],
                    f"{prefix}.close_today_rate",
                ),
            ),
            lower_price_limit=_positive_float(
                row["lower_price_limit"],
                f"{prefix}.lower_price_limit",
            ),
            upper_price_limit=_positive_float(
                row["upper_price_limit"],
                f"{prefix}.upper_price_limit",
            ),
            sessions=sessions,
            delivery_restriction=_enum(
                DeliveryRestriction,
                row["delivery_restriction"],
                f"{prefix}.delivery_restriction",
            ),
            source_artifact_hash=raw_snapshot_hash,
            source_authority=_text(row["source_authority"], f"{prefix}.source_authority"),
            quality_status=RuleQualityStatus.PASS,
            execution_eligible=False,
        )
    except (ContractMasterError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ArtifactRuleBookError):
            raise
        raise ArtifactRuleBookError(f"{prefix} 无法重建严格 Contract Rule 事实") from exc
    if rule.lower_price_limit >= rule.upper_price_limit:
        raise ArtifactRuleBookError(f"{prefix} lower_price_limit 必须小于 upper_price_limit")
    return _ParsedRow(
        master_id=master_id,
        master_version=master_version,
        commodity=commodity,
        exchange=exchange,
        instrument=instrument,
        contract=contract,
        contract_available_at=contract_available_at,
        rule=rule,
    )


def _assert_contract_active_at(parsed: _ParsedRow, decision_at: datetime) -> None:
    if parsed.contract_available_at > decision_at:
        raise ArtifactRuleBookError("RuleBook contract 在 decision_at 后才可见")
    try:
        local_day = decision_at.astimezone(ZoneInfo(parsed.exchange.timezone_name)).date()
    except ZoneInfoNotFoundError as exc:  # ContractMaster Exchange 已验证；保留防御。
        raise ArtifactRuleBookError("RuleBook exchange timezone 无法解析") from exc
    if local_day < parsed.contract.listed_on:
        raise ArtifactRuleBookError("RuleBook contract 在交易所本地日期尚未挂牌")
    if local_day > parsed.contract.expires_on:
        raise ArtifactRuleBookError("RuleBook contract 在交易所本地日期已经到期")
    if parsed.rule.listing_state is not ListingState.LISTED:
        raise ArtifactRuleBookError("RuleBook rule listing_state 必须为 listed")


def _build_master(rows: tuple[_ParsedRow, ...]) -> ContractMaster:
    if not rows:
        raise ArtifactRuleBookError("RuleBook 没有可选择的 contract/rule rows")
    master_id = rows[0].master_id
    master_version = rows[0].master_version
    try:
        return ContractMaster(
            master_id=master_id,
            version=master_version,
            commodities=tuple(_unique(rows, "commodity", lambda item: item.commodity.commodity_id)),
            exchanges=tuple(_unique(rows, "exchange", lambda item: item.exchange.exchange_id)),
            instruments=tuple(_unique(rows, "instrument", lambda item: item.instrument.instrument_id)),
            continuous_series=(),
            contracts=tuple(_unique(rows, "contract", lambda item: item.contract.contract_id)),
            rule_snapshots=tuple(item.rule for item in rows),
        )
    except ContractMasterError as exc:
        raise ArtifactRuleBookError("RuleBook 无法重建一致的 ContractMaster") from exc


def _unique(
    rows: tuple[_ParsedRow, ...],
    field_name: str,
    key: Any,
) -> list[Any]:
    items: dict[str, Any] = {}
    for row in rows:
        value = getattr(row, field_name)
        item_key = key(row)
        previous = items.get(item_key)
        if previous is not None and previous != value:
            raise ArtifactRuleBookError(f"RuleBook 同一 {field_name} 身份出现冲突")
        items[item_key] = value
    return [items[item_key] for item_key in sorted(items)]


def _decode_frame(payload: bytes) -> pl.DataFrame:
    try:
        frame = _decode_canonical_frame(payload)
    except MarketDataPITError as exc:
        raise ArtifactRuleBookError("RuleBook payload 必须是严格 canonical frame") from exc
    try:
        round_trip = canonical_frame_payload(frame)
    except DataQualityError as exc:
        raise ArtifactRuleBookError("RuleBook canonical frame 无法规范化") from exc
    if round_trip != payload:
        raise ArtifactRuleBookError("RuleBook canonical frame round-trip 不一致")
    return frame


def _sessions(value: object, field_name: str) -> tuple[ContractTradingSession, ...]:
    if not isinstance(value, str) or not value:
        raise ArtifactRuleBookError(f"{field_name} 必须是非空 canonical JSON")

    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, nested in pairs:
            if key in result:
                raise ArtifactRuleBookError(f"{field_name} 不能包含重复 JSON 键")
            result[key] = nested
        return result

    try:
        decoded = json.loads(value, object_pairs_hook=no_duplicates)
    except json.JSONDecodeError as exc:
        raise ArtifactRuleBookError(f"{field_name} 必须是有效 JSON") from exc
    if not isinstance(decoded, list) or not decoded:
        raise ArtifactRuleBookError(f"{field_name} 必须是非空 session 列表")
    canonical = json.dumps(decoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if canonical != value:
        raise ArtifactRuleBookError(f"{field_name} 必须是 canonical JSON")
    sessions: list[ContractTradingSession] = []
    for item in decoded:
        if not isinstance(item, dict) or set(item) != {"closes_at", "opens_at", "session_id"}:
            raise ArtifactRuleBookError(f"{field_name} session 字段不受支持")
        try:
            sessions.append(
                ContractTradingSession(
                    session_id=_text(item["session_id"], f"{field_name}.session_id"),
                    opens_at=time.fromisoformat(_text(item["opens_at"], f"{field_name}.opens_at")),
                    closes_at=time.fromisoformat(_text(item["closes_at"], f"{field_name}.closes_at")),
                )
            )
        except (ContractMasterError, ValueError) as exc:
            raise ArtifactRuleBookError(f"{field_name} session 无法重建") from exc
    if len({item.session_id for item in sessions}) != len(sessions):
        raise ArtifactRuleBookError(f"{field_name} session_id 不能重复")
    return tuple(sorted(sessions, key=lambda item: item.session_id))


def _hash(value: object, field_name: str) -> str:
    try:
        return require_sha256(value, field_name=field_name)  # type: ignore[arg-type]
    except FingerprintError as exc:
        raise ArtifactRuleBookError(str(exc)) from exc


def _hashes(values: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(values, tuple) or not values:
        raise ArtifactRuleBookError(f"{field_name} 必须是非空 hash 元组")
    result = tuple(sorted(_hash(item, field_name) for item in values))
    if len(set(result)) != len(result):
        raise ArtifactRuleBookError(f"{field_name} 不能包含重复 hash")
    return result


def _contract_ids(values: object, field_name: str) -> tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, Sequence) or not values:
        raise ArtifactRuleBookError(f"{field_name} 必须是非空实际 contract_id 序列")
    result = tuple(sorted(_text(item, field_name) for item in values))
    if len(set(result)) != len(result):
        raise ArtifactRuleBookError(f"{field_name} 不能包含重复 contract_id")
    if any(item.upper().endswith("_CONT") for item in result):
        raise ArtifactRuleBookError(f"{field_name} 不能包含连续研究序列")
    return result


def _assert_canonical_contract_identity(
    contract: Contract,
    exchange: Exchange,
    instrument: Instrument,
    prefix: str,
) -> None:
    """RuleBook 只接受 ``EXCHANGE.PRODUCT.YYMM`` 形式的实际合约身份。

    静态领域模型允许更宽的稳定 identifier；这里是 artifact-backed 历史规则的边界，
    必须把行级 exchange、品种、symbol 和 contract_id 精确锁在一起，避免同名产品跨所
    拼接或把供应商 symbol 误当 canonical contract_id。
    """

    parts = contract.contract_id.upper().split(".")
    if (
        len(parts) != 3
        or parts[0] != exchange.exchange_id
        or parts[1] != instrument.product_code
        or not parts[2].isdigit()
        or len(parts[2]) not in {3, 4}
        or contract.symbol != f"{instrument.product_code}{parts[2]}"
    ):
        raise ArtifactRuleBookError(
            f"{prefix}.contract_id/symbol 必须绑定为 "
            "EXCHANGE.PRODUCT.YYMM 与对应实际月份代码"
        )


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "/" in value or "\\" in value:
        raise ArtifactRuleBookError(f"{field_name} 必须是非空、无路径文本")
    return value.strip()


def _timezone_name(value: object, field_name: str) -> str:
    """接受 IANA 时区名；它有意包含 ``/``，但不是文件路径。"""

    if not isinstance(value, str) or not value.strip():
        raise ArtifactRuleBookError(f"{field_name} 必须是非空 IANA 时区名")
    result = value.strip()
    if "\\" in result or result.startswith("/") or ".." in result.split("/"):
        raise ArtifactRuleBookError(f"{field_name} 必须是安全的 IANA 时区名")
    try:
        ZoneInfo(result)
    except ZoneInfoNotFoundError as exc:
        raise ArtifactRuleBookError(f"{field_name} 必须是有效 IANA 时区名") from exc
    return result


def _utc_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ArtifactRuleBookError(f"{field_name} 必须是带时区 datetime")
    return value.astimezone(UTC)


def _optional_utc_datetime(value: object, field_name: str) -> datetime | None:
    if value is None:
        return None
    return _utc_datetime(value, field_name)


def _date(value: object, field_name: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ArtifactRuleBookError(f"{field_name} 必须是 date")
    return value


def _positive_float(value: object, field_name: str) -> float:
    result = _number(value, field_name)
    if result <= 0:
        raise ArtifactRuleBookError(f"{field_name} 必须大于 0")
    return result


def _nonnegative_float(value: object, field_name: str) -> float:
    result = _number(value, field_name)
    if result < 0:
        raise ArtifactRuleBookError(f"{field_name} 必须大于等于 0")
    return result


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArtifactRuleBookError(f"{field_name} 必须是有限数值")
    result = float(value)
    if result != result or result in {float("inf"), float("-inf")}:
        raise ArtifactRuleBookError(f"{field_name} 必须是有限数值")
    return result


def _enum(enum_type: type[Any], value: object, field_name: str) -> Any:
    if not isinstance(value, str):
        raise ArtifactRuleBookError(f"{field_name} 必须是枚举文本")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ArtifactRuleBookError(f"{field_name} 不受支持") from exc


def _mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ArtifactRuleBookError(f"{field_name} 必须是文本键 Mapping")
    return value


def _identifier_set(
    value: object,
    field_name: str,
    *,
    upper: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ArtifactRuleBookError(f"{field_name} 必须是非空文本列表")
    result = tuple(sorted(((_text(item, field_name).upper() if upper else _text(item, field_name)) for item in value)))
    if len(set(result)) != len(result):
        raise ArtifactRuleBookError(f"{field_name} 不能包含重复值")
    return result


__all__ = [
    "ArtifactBackedContractRuleReplay",
    "ArtifactRuleBookError",
    "ContractRuleBookPITSelector",
    "RULEBOOK_DATASET_ID",
    "RULEBOOK_DATASET_TRANSFORM_VERSION",
    "RULEBOOK_SCHEMA_VERSION",
    "RULEBOOK_TRANSFORM_VERSION",
]

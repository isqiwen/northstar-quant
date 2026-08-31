"""PIT checkpoint → strict feature materialization → factor exposure conversion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from typing import Mapping

import polars as pl

from northstar_quant.data.market.pit import MarketDataKind
from northstar_quant.data.sources.protocol import PublicationPurpose
from northstar_quant.research.factors.models import (
    FactorCheckpointData,
    FactorExposure,
    FactorMarketSlice,
    FactorMaterializationReference,
    FactorPipelineConfig,
    FactorResearchError,
)
from northstar_quant.research.features.models import (
    DecisionReplayFeatureMaterialization,
    FeatureVersion,
)
from northstar_quant.research.features.registry import FeatureRegistry
from northstar_quant.research.validation.lookahead import DecisionMarketDataEvidence


@dataclass(frozen=True, slots=True)
class FactorCheckpointComputation:
    """仅在 application 编排期间保留 strict materialization 的 checkpoint 结果。"""

    data: FactorCheckpointData
    materializations: tuple[tuple[str, DecisionReplayFeatureMaterialization], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.data, FactorCheckpointData):
            raise FactorResearchError("computation.data 必须是 FactorCheckpointData")
        materializations = tuple(self.materializations)
        if not materializations or not all(
            isinstance(factor_id, str) and isinstance(value, DecisionReplayFeatureMaterialization)
            for factor_id, value in materializations
        ):
            raise FactorResearchError("computation.materializations 类型无效")
        if tuple(sorted(materializations, key=lambda item: item[0])) != materializations:
            raise FactorResearchError("computation.materializations 必须按 factor_id 排序")
        if len({factor_id for factor_id, _ in materializations}) != len(materializations):
            raise FactorResearchError("computation.materializations 不能有重复 factor_id")
        expected = {item.factor_id: item.materialization_hash for item in self.data.materializations}
        actual = {factor_id: value.materialization_hash for factor_id, value in materializations}
        if expected != actual:
            raise FactorResearchError("computation.materializations 必须精确匹配 checkpoint data")
        object.__setattr__(self, "materializations", materializations)

    def materialization_for(self, factor_id: str) -> DecisionReplayFeatureMaterialization:
        for registered_factor_id, materialization in self.materializations:
            if registered_factor_id == factor_id:
                return materialization
        raise KeyError(f"未知 factor materialization: {factor_id}")


class FactorEngine:
    """只把在一个 strict checkpoint 可见的 FeatureValue 转为 FactorExposure。"""

    def __init__(
        self,
        *,
        config: FactorPipelineConfig,
        registry: FeatureRegistry,
        feature_versions: Mapping[str, FeatureVersion],
    ) -> None:
        if not isinstance(config, FactorPipelineConfig):
            raise FactorResearchError("config 必须是 FactorPipelineConfig")
        if not isinstance(registry, FeatureRegistry):
            raise FactorResearchError("registry 必须是 FeatureRegistry")
        if not isinstance(feature_versions, Mapping):
            raise FactorResearchError("feature_versions 必须是映射")
        definitions = {item.factor_id: item for item in config.factors}
        versions = dict(feature_versions)
        if set(versions) != set(definitions):
            raise FactorResearchError("feature_versions 必须精确覆盖 config.factors")
        if not all(isinstance(item, FeatureVersion) for item in versions.values()):
            raise FactorResearchError("feature_versions 值必须是 FeatureVersion")
        if any(versions[factor_id].feature_id != definition.feature_id for factor_id, definition in definitions.items()):
            raise FactorResearchError("feature_versions 的 feature_id 与 FactorDefinition 不一致")
        if any(
            version.version != config.feature_version or version.code_revision != config.code_revision
            for version in versions.values()
        ):
            raise FactorResearchError("feature_versions 必须与 FactorPipelineConfig 的版本/代码身份一致")
        self._config = config
        self._registry = registry
        self._definitions = definitions
        self._feature_versions = versions

    def compute(self, market_evidence: DecisionMarketDataEvidence) -> FactorCheckpointComputation:
        """对一个 checkpoint 双执行受控 feature 并提取当前横截面暴露。"""

        if not isinstance(market_evidence, DecisionMarketDataEvidence):
            raise FactorResearchError("market_evidence 必须是 DecisionMarketDataEvidence")
        checkpoint = market_evidence.checkpoint
        snapshot = market_evidence.market_snapshot
        decision_session = checkpoint.decision_event_time
        if not isinstance(decision_session, date) or hasattr(decision_session, "hour"):
            raise FactorResearchError("P11 因子流水线不支持 datetime 型 checkpoint")
        spec = snapshot.spec
        if (
            spec.kind is not MarketDataKind.BAR
            or spec.schema_version != "cn_futures_feature_bar_v1"
            or spec.event_time_column != "date"
            or spec.available_at_column != "available_at"
            or set(spec.key_columns) != {"symbol", "date"}
        ):
            raise FactorResearchError("P11 因子流水线只接受 cn_futures_feature_bar_v1 PIT 日线")
        if "close" not in spec.value_columns or "volume" not in spec.value_columns:
            raise FactorResearchError("P11 因子流水线输入必须包含 close 与 volume")
        if snapshot.publication_scope.purpose not in {
            PublicationPurpose.HISTORICAL_BACKTEST,
            PublicationPurpose.INTERNAL_RESEARCH,
        }:
            raise FactorResearchError("因子研究输入必须授权 historical_backtest 或 internal_research")
        if checkpoint.decision_at != snapshot.as_of:
            raise FactorResearchError("market_evidence snapshot.as_of 必须精确匹配 checkpoint.decision_at")

        market_slices = self._market_slices(market_evidence, decision_session)
        materializations: list[tuple[str, DecisionReplayFeatureMaterialization]] = []
        exposure_values: list[FactorExposure] = []
        references: list[FactorMaterializationReference] = []
        for factor_id, definition in sorted(self._definitions.items()):
            feature_version = self._feature_versions[factor_id]
            materialization = self._registry.materialize_per_decision_replay(
                feature_version_hash=feature_version.version_hash,
                market_snapshot=snapshot,
                replay_checkpoint_hash=checkpoint.checkpoint_hash,
                parameters=definition.parameters,
            )
            if materialization.lineage.decision_at != checkpoint.decision_at:
                raise FactorResearchError("strict Feature materialization decision_at 与 checkpoint 不一致")
            if materialization.input_snapshot_hash != snapshot.snapshot_id:
                raise FactorResearchError("strict Feature materialization snapshot 与 checkpoint 不一致")
            references.append(
                FactorMaterializationReference(
                    factor_id=factor_id,
                    factor_definition_hash=definition.definition_hash,
                    feature_version_hash=feature_version.version_hash,
                    materialization_hash=materialization.materialization_hash,
                )
            )
            for value in materialization.values:
                if value.event_time != decision_session or value.value is None:
                    continue
                key = value.key
                raw_symbol = key.get("symbol")
                if not isinstance(raw_symbol, str):
                    raise FactorResearchError("strict FeatureValue 必须包含 symbol key")
                exposure_values.append(
                    FactorExposure(
                        checkpoint_hash=checkpoint.checkpoint_hash,
                        decision_at=checkpoint.decision_at,
                        decision_session=decision_session,
                        snapshot_id=snapshot.snapshot_id,
                    factor_id=factor_id,
                    factor_definition_hash=definition.definition_hash,
                    config_hash=self._config.config_hash,
                    materialization_hash=materialization.materialization_hash,
                        symbol=raw_symbol,
                        value=value.value,
                    )
                )
            materializations.append((factor_id, materialization))

        data = FactorCheckpointData(
            checkpoint_hash=checkpoint.checkpoint_hash,
            decision_at=checkpoint.decision_at,
            decision_session=decision_session,
            market_evidence_hash=market_evidence.evidence_hash,
            snapshot_id=snapshot.snapshot_id,
            dataset_version_hash=snapshot.dataset_version_hash,
            config_hash=self._config.config_hash,
            materializations=tuple(references),
            exposures=tuple(sorted(exposure_values, key=lambda item: (item.factor_id, item.symbol))),
            market_slices=market_slices,
        )
        return FactorCheckpointComputation(data=data, materializations=tuple(materializations))

    @staticmethod
    def _market_slices(
        market_evidence: DecisionMarketDataEvidence,
        decision_session: date,
    ) -> tuple[FactorMarketSlice, ...]:
        snapshot = market_evidence.market_snapshot
        frame = snapshot.selected_frame().filter(pl.col("date") == decision_session)
        if frame.is_empty():
            raise FactorResearchError("checkpoint 当期没有可见日线，拒绝生成因子提案")
        rows: list[FactorMarketSlice] = []
        for row in frame.select(("symbol", "close", "available_at")).iter_rows(named=True):
            symbol = row["symbol"]
            close = row["close"]
            available_at = row["available_at"]
            if not isinstance(symbol, str) or isinstance(close, bool) or not isinstance(close, (int, float)):
                raise FactorResearchError("checkpoint 日线 symbol/close 类型无效")
            if not math.isfinite(float(close)) or float(close) <= 0:
                raise FactorResearchError("checkpoint 日线 close 必须为正有限数")
            if not hasattr(available_at, "tzinfo") or available_at.tzinfo is None:
                raise FactorResearchError("checkpoint 日线 available_at 必须带时区")
            if available_at > market_evidence.checkpoint.decision_at:
                raise FactorResearchError("checkpoint 日线包含 decision_at 后才可见的数据")
            rows.append(
                FactorMarketSlice(
                    checkpoint_hash=market_evidence.checkpoint.checkpoint_hash,
                    decision_session=decision_session,
                    snapshot_id=snapshot.snapshot_id,
                    symbol=symbol,
                    close=float(close),
                )
            )
        if len({item.symbol for item in rows}) != len(rows):
            raise FactorResearchError("checkpoint 日线不得包含重复 symbol")
        return tuple(sorted(rows, key=lambda item: item.symbol))


__all__ = ["FactorCheckpointComputation", "FactorEngine"]

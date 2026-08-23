"""内存中的 Feature Registry。

Registry 只登记不可变 FeatureSpec/FeatureVersion，不执行任意函数，也不把“最新”
版本隐式注入回测。调用方必须显式持有 ``version_hash``，从而使实验和回放记录可审计。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from northstar_quant.research.features.models import (
    DecisionReplayFeatureMaterialization,
    _DECISION_REPLAY_FEATURE_ISSUER,
    FeatureBackfill,
    _FeatureBackfillRunner,
    FeatureDependency,
    FeatureDatasetEvidence,
    FeatureLineage,
    FeatureRegistryError,
    FeatureSpec,
    FeatureValue,
    FeatureVersion,
)

if TYPE_CHECKING:
    from northstar_quant.data_platform.artifacts.immutable_store import ArtifactStore
    from northstar_quant.data_platform.market.pit import MarketDataSnapshot


@runtime_checkable
class FeatureComputer(Protocol):
    """已登记版本的受控计算实现。

    Feature Registry 只接受一次按 ``FeatureVersion`` 绑定的 computer；回填时它获得的是
    Registry 刚刚从 immutable DatasetVersion 重算出的 snapshot 与 canonical parameters，
    而不是调用方注入的任意无参 lambda。实现哈希代表经过代码审查/构建流程确认的实现身份，
    不是在 Python 进程内执行不受信任代码的沙箱。
    """

    feature_version_hash: str
    implementation_hash: str

    def compute(
        self,
        *,
        market_snapshot: MarketDataSnapshot,
        parameters: Mapping[str, object],
        lineage: FeatureLineage,
    ) -> Iterable[FeatureValue]: ...


class FeatureRegistry:
    """用于进程内注册和查询 FeatureSpec/FeatureVersion 的受控目录。"""

    def __init__(self, *, artifact_store: ArtifactStore | None = None) -> None:
        self._specs_by_id: dict[str, FeatureSpec] = {}
        self._versions_by_hash: dict[str, FeatureVersion] = {}
        self._versions_by_semantic_key: dict[tuple[str, str], FeatureVersion] = {}
        self._lineages_by_hash: dict[str, FeatureLineage] = {}
        self._backfills_by_lineage_hash: dict[str, FeatureBackfill] = {}
        self._computers_by_version_hash: dict[str, FeatureComputer] = {}
        self._decision_replay_materializations: dict[
            str,
            DecisionReplayFeatureMaterialization,
        ] = {}
        self._artifact_store = artifact_store

    def register_spec(self, spec: FeatureSpec) -> FeatureSpec:
        """登记特征定义；相同身份重试幂等，不允许同 ID 静默覆盖。"""

        if not isinstance(spec, FeatureSpec):
            raise FeatureRegistryError("spec 必须是 FeatureSpec")
        existing = self._specs_by_id.get(spec.feature_id)
        if existing is None:
            self._specs_by_id[spec.feature_id] = spec
            return spec
        if existing.spec_hash != spec.spec_hash:
            raise FeatureRegistryError(f"FeatureSpec {spec.feature_id} 已存在且身份不同，拒绝覆盖")
        return existing

    def register_version(self, version: FeatureVersion) -> FeatureVersion:
        """登记实现版本，并验证其绑定的 FeatureSpec 与语义版本唯一。"""

        if not isinstance(version, FeatureVersion):
            raise FeatureRegistryError("version 必须是 FeatureVersion")
        spec = self._specs_by_id.get(version.feature_id)
        if spec is None:
            raise FeatureRegistryError(f"FeatureVersion {version.feature_id} 尚未登记 FeatureSpec")
        if spec.spec_hash != version.spec_hash:
            raise FeatureRegistryError("FeatureVersion.spec_hash 与已登记 FeatureSpec 不一致")
        existing_by_hash = self._versions_by_hash.get(version.version_hash)
        if existing_by_hash is not None:
            return existing_by_hash
        semantic_key = (version.feature_id, version.version)
        existing_by_semantic_key = self._versions_by_semantic_key.get(semantic_key)
        if existing_by_semantic_key is not None:
            raise FeatureRegistryError(
                f"FeatureVersion {version.feature_id}@{version.version} 已存在且身份不同，拒绝覆盖"
            )
        self._versions_by_hash[version.version_hash] = version
        self._versions_by_semantic_key[semantic_key] = version
        return version

    def get_spec(self, feature_id: str) -> FeatureSpec:
        """按稳定特征 ID 查询定义；不存在时失败关闭。"""

        try:
            return self._specs_by_id[feature_id]
        except KeyError as exc:
            raise FeatureRegistryError(f"未登记的 FeatureSpec: {feature_id}") from exc

    def get_version(self, version_hash: str) -> FeatureVersion:
        """按不可变 version hash 查询实现；不提供隐式 latest。"""

        try:
            return self._versions_by_hash[version_hash]
        except KeyError as exc:
            raise FeatureRegistryError(f"未登记的 FeatureVersion: {version_hash}") from exc

    def get_lineage(self, lineage_hash: str) -> FeatureLineage:
        """按不可变 hash 读取由本 Registry 创建的特征血缘。

        这是 Experiment 等下游研究账本取得输入证据的唯一公开读取入口。它不会接受
        调用方手工拼接的 ``FeatureLineage``，也不会按“最新”版本回退。
        """

        try:
            return self._lineages_by_hash[lineage_hash]
        except KeyError as exc:
            raise FeatureRegistryError(f"未登记的 FeatureLineage: {lineage_hash}") from exc

    def get_backfill(self, lineage_hash: str) -> FeatureBackfill:
        """按 lineage hash 读取受控、确定性物化的回填结果。"""

        try:
            return self._backfills_by_lineage_hash[lineage_hash]
        except KeyError as exc:
            raise FeatureRegistryError(
                f"FeatureLineage 尚未登记确定性 FeatureBackfill: {lineage_hash}"
            ) from exc

    def list_specs(self) -> tuple[FeatureSpec, ...]:
        """按稳定 ID 返回所有已登记定义。"""

        return tuple(self._specs_by_id[key] for key in sorted(self._specs_by_id))

    def list_versions(self, *, feature_id: str | None = None) -> tuple[FeatureVersion, ...]:
        """按稳定身份返回版本；调用方仍必须显式选择可用于研究的版本。"""

        versions: Iterable[FeatureVersion] = self._versions_by_hash.values()
        if feature_id is not None:
            versions = (item for item in versions if item.feature_id == feature_id)
        return tuple(
            sorted(versions, key=lambda item: (item.feature_id, item.version, item.version_hash))
        )

    def register_computer(self, computer: FeatureComputer) -> FeatureComputer:
        """登记一个与既有 FeatureVersion 精确绑定的受控计算实现。

        这是实现代码进入 Registry 的唯一入口。相同对象重试幂等；同一 version 试图替换为
        另一个对象会失败关闭，即使调用方声称 implementation hash 相同也不静默接受。
        """

        if not isinstance(computer, FeatureComputer) or not callable(computer.compute):
            raise FeatureRegistryError("computer 必须实现 FeatureComputer.compute")
        version = self.get_version(computer.feature_version_hash)
        if computer.implementation_hash != version.implementation_hash:
            raise FeatureRegistryError(
                "FeatureComputer.implementation_hash 与已登记 FeatureVersion 不一致"
            )
        existing = self._computers_by_version_hash.get(version.version_hash)
        if existing is not None:
            if existing is not computer:
                raise FeatureRegistryError(
                    "同一 FeatureVersion 已登记另一个 FeatureComputer，拒绝替换实现"
                )
            return existing
        self._computers_by_version_hash[version.version_hash] = computer
        return computer

    def create_market_data_lineage(
        self,
        *,
        feature_version_hash: str,
        market_snapshot: MarketDataSnapshot,
        parameters: Mapping[str, object],
    ) -> FeatureLineage:
        """以 P1 immutable PIT selector 重算输入后创建并登记特征血缘。

        不接收裸 DataFrame、DatasetVersion 名称或 mutable ``latest`` 指针。当前 P1
        snapshot 是静态 as-of 视图，因此输出仍会保留
        ``STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY`` / ``decision_time_safe=false``；
        Feature Registry 绝不会把它升级为逐决策无前视证据。
        """

        from northstar_quant.data_platform.market.pit import MarketDataSnapshot

        version = self.get_version(feature_version_hash)
        feature_spec = self.get_spec(version.feature_id)
        if not isinstance(market_snapshot, MarketDataSnapshot):
            raise FeatureRegistryError("market_snapshot 必须是 MarketDataSnapshot")
        proposed_evidence = FeatureDatasetEvidence.from_market_data_snapshot(market_snapshot)
        verified_snapshot = self._reselect_dataset_evidence(proposed_evidence)
        if verified_snapshot.snapshot_id != market_snapshot.snapshot_id:
            raise FeatureRegistryError("market_snapshot 未能通过 immutable DatasetVersion 重算验证")
        self._assert_feature_input_compatible(feature_spec, verified_snapshot)
        lineage = FeatureLineage.create(
            feature_version=version,
            dependencies=(
                FeatureDependency.from_market_data_snapshot(
                    role="market_data",
                    snapshot=verified_snapshot,
                ),
            ),
            parameters=parameters,
            decision_at=verified_snapshot.as_of,
            available_at=verified_snapshot.as_of,
        )
        existing = self._lineages_by_hash.get(lineage.lineage_hash)
        if existing is not None:
            return existing
        self._lineages_by_hash[lineage.lineage_hash] = lineage
        return lineage

    @staticmethod
    def _assert_feature_input_compatible(
        feature_spec: FeatureSpec,
        market_snapshot: MarketDataSnapshot,
    ) -> None:
        """禁止把声明的特征语义绑定到列、键或时间语义不匹配的行情输入。"""

        market_spec = market_snapshot.spec
        available_columns = {
            *market_spec.key_columns,
            market_spec.available_at_column,
            *market_spec.value_columns,
        }
        missing_columns = sorted(set(feature_spec.input_columns).difference(available_columns))
        if missing_columns:
            raise FeatureRegistryError(
                "FeatureSpec.input_columns 未被 verified MarketDataPITSpec 提供: "
                + ", ".join(missing_columns)
            )
        if feature_spec.input_schema_version != market_spec.schema_version:
            raise FeatureRegistryError(
                "FeatureSpec.input_schema_version 与 verified MarketDataPITSpec.schema_version 不一致"
            )
        if feature_spec.event_time_column != market_spec.event_time_column:
            raise FeatureRegistryError(
                "FeatureSpec.event_time_column 与 verified MarketDataPITSpec 不一致"
            )
        if feature_spec.available_at_column != market_spec.available_at_column:
            raise FeatureRegistryError(
                "FeatureSpec.available_at_column 与 verified MarketDataPITSpec 不一致"
            )
        source_entity_keys = set(market_spec.key_columns).difference(
            {market_spec.event_time_column}
        )
        if set(feature_spec.entity_key_columns) != source_entity_keys:
            raise FeatureRegistryError(
                "FeatureSpec.entity_key_columns 必须精确匹配输入 MarketDataPITSpec 的非时间主键"
            )

    def _reselect_dataset_evidence(self, evidence: FeatureDatasetEvidence) -> MarketDataSnapshot:
        """按 lineage 自身冻结的 DatasetVersion/spec/as-of 重放并逐项核验。"""

        from northstar_quant.data_platform.market.pit import (
            MarketDataPITError,
            MarketDataPITSelector,
            MarketDataSnapshot,
        )
        from northstar_quant.data_platform.artifacts.immutable_store import ArtifactStore

        if not isinstance(evidence, FeatureDatasetEvidence):
            raise FeatureRegistryError("dataset evidence 必须是 FeatureDatasetEvidence")
        if not isinstance(self._artifact_store, ArtifactStore):
            raise FeatureRegistryError("Feature Registry 缺少受控 immutable ArtifactStore")
        selector = MarketDataPITSelector(self._artifact_store)
        try:
            verified_snapshot = selector.select(
                dataset_version_hash=evidence.dataset_version_hash,
                spec=evidence.pit_spec,
                as_of=evidence.as_of,
            )
        except MarketDataPITError as exc:
            raise FeatureRegistryError(
                "FeatureLineage 的 DatasetVersion/PIT 证据无法安全重算"
            ) from exc
        if not isinstance(verified_snapshot, MarketDataSnapshot):  # 防御 monkeypatch/adapter 误用。
            raise FeatureRegistryError("MarketDataPITSelector 必须返回 MarketDataSnapshot")
        verified_evidence = FeatureDatasetEvidence.from_market_data_snapshot(verified_snapshot)
        if verified_evidence.evidence_hash != evidence.evidence_hash:
            raise FeatureRegistryError(
                "FeatureLineage 的完整 DatasetVersion/PIT 证据与重算结果不一致"
            )
        return verified_snapshot

    def materialize_deterministic_backfill(self, lineage: FeatureLineage) -> FeatureBackfill:
        """以已登记 FeatureComputer 对刚重放的 PIT 输入执行两次并冻结回填结果。"""

        if not isinstance(lineage, FeatureLineage):
            raise FeatureRegistryError("lineage 必须是 FeatureLineage")
        version = self.get_version(lineage.feature_version_hash)
        if lineage.implementation_hash != version.implementation_hash:
            raise FeatureRegistryError(
                "FeatureLineage.implementation_hash 与已登记 FeatureVersion 不一致"
            )
        if self._lineages_by_hash.get(lineage.lineage_hash) != lineage:
            raise FeatureRegistryError(
                "FeatureLineage 必须由 Registry 通过 immutable PIT selector 创建并登记"
            )
        verified_snapshots: list[MarketDataSnapshot] = []
        for dependency in lineage.dependencies:
            if dependency.dataset_evidence is None:
                raise FeatureRegistryError("P2-WP01 回填只能使用完整的 DatasetVersion/PIT 证据")
            verified_snapshots.append(self._reselect_dataset_evidence(dependency.dataset_evidence))
        if len(verified_snapshots) != 1:
            raise FeatureRegistryError("P2-WP01 只能物化单一 verified MarketDataSnapshot 输入")
        try:
            computer = self._computers_by_version_hash[version.version_hash]
        except KeyError as exc:
            raise FeatureRegistryError(
                "FeatureVersion 尚未登记受控 FeatureComputer，拒绝执行回填"
            ) from exc
        if (
            computer.feature_version_hash != version.version_hash
            or computer.implementation_hash != version.implementation_hash
        ):
            raise FeatureRegistryError(
                "已登记 FeatureComputer 的 version/implementation 身份发生变化，拒绝执行回填"
            )

        def compute_once() -> Iterable[FeatureValue]:
            return computer.compute(
                market_snapshot=verified_snapshots[0],
                parameters=lineage.parameters,
                lineage=lineage,
            )

        backfill = _FeatureBackfillRunner.run_deterministic(lineage, compute_once)
        if backfill.implementation_hash != version.implementation_hash:
            raise FeatureRegistryError(
                "FeatureBackfill.implementation_hash 与已登记 FeatureVersion 不一致"
            )
        spec = self.get_spec(version.feature_id)
        expected_key_columns = set(spec.entity_key_columns)
        for value in backfill.values:
            actual_key_columns = set(value.key)
            if actual_key_columns != expected_key_columns:
                raise FeatureRegistryError(
                    "FeatureValue.key 必须精确匹配已登记 FeatureSpec.entity_key_columns"
                )
        existing = self._backfills_by_lineage_hash.get(lineage.lineage_hash)
        if existing is not None:
            if existing.backfill_hash != backfill.backfill_hash:
                raise FeatureRegistryError(
                    "同一 FeatureLineage 已登记不同的 deterministic backfill，拒绝覆盖"
                )
            return existing
        self._backfills_by_lineage_hash[lineage.lineage_hash] = backfill
        return backfill

    def materialize_per_decision_replay(
        self,
        *,
        feature_version_hash: str,
        market_snapshot: MarketDataSnapshot,
        replay_checkpoint_hash: str,
        parameters: Mapping[str, object],
    ) -> DecisionReplayFeatureMaterialization:
        """从单个 checkpoint 的 immutable PIT snapshot 受控物化严格特征输出。"""

        from northstar_quant.data_platform.market.pit import MarketDataSnapshot

        if not isinstance(market_snapshot, MarketDataSnapshot):
            raise FeatureRegistryError("market_snapshot 必须是 MarketDataSnapshot")
        version = self.get_version(feature_version_hash)
        feature_spec = self.get_spec(version.feature_id)
        proposed_evidence = FeatureDatasetEvidence.from_market_data_snapshot(market_snapshot)
        verified_snapshot = self._reselect_dataset_evidence(proposed_evidence)
        if verified_snapshot.snapshot_id != market_snapshot.snapshot_id:
            raise FeatureRegistryError("market_snapshot 未能通过 immutable DatasetVersion 重算验证")
        self._assert_feature_input_compatible(feature_spec, verified_snapshot)
        lineage = FeatureLineage.create_per_decision_replay(
            feature_version=version,
            dependencies=(
                FeatureDependency.from_market_data_snapshot(
                    role="market_data",
                    snapshot=verified_snapshot,
                ),
            ),
            parameters=parameters,
            decision_at=verified_snapshot.as_of,
            available_at=verified_snapshot.as_of,
            replay_checkpoint_hash=replay_checkpoint_hash,
        )
        computer = self._computers_by_version_hash.get(version.version_hash)
        if computer is None:
            raise FeatureRegistryError("FeatureVersion 尚未登记受控 FeatureComputer，拒绝执行逐决策 replay")
        if (
            computer.feature_version_hash != version.version_hash
            or computer.implementation_hash != version.implementation_hash
        ):
            raise FeatureRegistryError("已登记 FeatureComputer 身份与 FeatureVersion 不一致")

        def compute_once() -> tuple[FeatureValue, ...]:
            values = tuple(
                computer.compute(
                    market_snapshot=verified_snapshot,
                    parameters=lineage.parameters,
                    lineage=lineage,
                )
            )
            expected_keys = set(feature_spec.entity_key_columns)
            for value in values:
                if set(value.key) != expected_keys:
                    raise FeatureRegistryError(
                        "FeatureValue.key 必须精确匹配已登记 FeatureSpec.entity_key_columns"
                    )
            return values

        first = compute_once()
        second = compute_once()
        first_ids = tuple(sorted(item.value_id for item in first))
        second_ids = tuple(sorted(item.value_id for item in second))
        if first_ids != second_ids:
            raise FeatureRegistryError("同一逐决策 FeatureLineage 的两次计算结果不同")
        materialization = DecisionReplayFeatureMaterialization(
            lineage=lineage,
            replay_checkpoint_hash=replay_checkpoint_hash,
            input_snapshot_hash=verified_snapshot.snapshot_id,
            values=first,
            _issuer=_DECISION_REPLAY_FEATURE_ISSUER,
        )
        existing = self._decision_replay_materializations.get(materialization.materialization_hash)
        if existing is not None:
            return existing
        self._decision_replay_materializations[materialization.materialization_hash] = materialization
        return materialization

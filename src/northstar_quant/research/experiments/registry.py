"""受控、进程内的 Experiment Registry。

该 Registry 只能从同一进程的 ``FeatureRegistry`` 读取已经受 immutable PIT 证据验证并完成
确定性物化的 FeatureLineage/FeatureBackfill。它不接受 DataFrame、裸数据集 hash、任意
selector、策略 factory 或数据库记录，因而不能把当前静态 P1/P2 输入升级成回测或准入资格。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from northstar_quant.research.experiments.models import (
    ExperimentDatasetInput,
    ExperimentError,
    ExperimentFeatureInput,
    ExperimentModelAssumption,
    ExperimentPeriod,
    ExperimentRun,
    ExperimentRunStatus,
    ExperimentSpec,
    StrategyVersionReference,
)
from northstar_quant.research.features.models import FeatureRegistryError
from northstar_quant.research.features.registry import FeatureRegistry


class ExperimentRegistry:
    """创建、登记和读取静态可复现实验账本。

    没有 ``latest``、自动回测或自动审批入口。实验输入在创建时从 Feature Registry 重新读取，
    以拒绝调用方手工传入的版本/血缘/回填摘要。
    """

    def __init__(self, *, feature_registry: FeatureRegistry) -> None:
        if not isinstance(feature_registry, FeatureRegistry):
            raise ExperimentError("ExperimentRegistry 必须持有受控 FeatureRegistry")
        self._feature_registry = feature_registry
        self._specs_by_hash: dict[str, ExperimentSpec] = {}
        self._specs_by_id: dict[str, ExperimentSpec] = {}
        self._runs_by_id: dict[str, ExperimentRun] = {}
        self._runs_by_spec_hash: dict[str, ExperimentRun] = {}

    def freeze_feature_input(self, *, lineage_hash: str) -> ExperimentFeatureInput:
        """从已登记的 FeatureLineage/Backfill 精确派生一项实验输入。"""

        try:
            lineage = self._feature_registry.get_lineage(lineage_hash)
            backfill = self._feature_registry.get_backfill(lineage_hash)
            version = self._feature_registry.get_version(lineage.feature_version_hash)
            spec = self._feature_registry.get_spec(version.feature_id)
        except FeatureRegistryError as exc:
            raise ExperimentError(
                "Experiment 输入必须引用已登记的 FeatureLineage/Backfill"
            ) from exc

        if lineage.lineage_hash != lineage_hash:
            raise ExperimentError("FeatureRegistry 返回的 FeatureLineage 与请求 hash 不一致")
        if lineage.feature_version_hash != version.version_hash:
            raise ExperimentError("FeatureLineage.feature_version_hash 与已登记版本不一致")
        if lineage.implementation_hash != version.implementation_hash:
            raise ExperimentError("FeatureLineage.implementation_hash 与已登记版本不一致")
        if version.feature_id != spec.feature_id or version.spec_hash != spec.spec_hash:
            raise ExperimentError("FeatureVersion 与已登记 FeatureSpec 身份不一致")
        if backfill.lineage_hash != lineage.lineage_hash:
            raise ExperimentError("FeatureBackfill.lineage_hash 与已登记 FeatureLineage 不一致")
        if (
            backfill.feature_version_hash != version.version_hash
            or backfill.implementation_hash != version.implementation_hash
        ):
            raise ExperimentError("FeatureBackfill 的 version/implementation 身份不一致")
        if lineage.selection_mode != "STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY":
            raise ExperimentError("Experiment 当前只接受静态 P1/P2 FeatureLineage")
        if (
            lineage.decision_time_safe is not False
            or backfill.decision_time_safe is not False
            or backfill.selection_mode != lineage.selection_mode
        ):
            raise ExperimentError("Experiment 不得接纳或伪造逐决策 PIT 安全特征")
        if not (lineage.decision_at == lineage.available_at == backfill.available_at):
            raise ExperimentError("FeatureLineage/Backfill 的静态 as_of/available_at 必须精确一致")

        dataset_inputs: list[ExperimentDatasetInput] = []
        for dependency in lineage.dependencies:
            evidence = dependency.dataset_evidence
            if evidence is None:
                raise ExperimentError(
                    "Experiment FeatureLineage 必须保留完整 DatasetVersion/PIT 证据"
                )
            if (
                dependency.available_at != lineage.decision_at
                or evidence.as_of != lineage.decision_at
            ):
                raise ExperimentError(
                    "FeatureLineage 的所有 DatasetVersion/PIT evidence.as_of 必须一致"
                )
            dataset_inputs.append(ExperimentDatasetInput(role=dependency.role, evidence=evidence))

        return ExperimentFeatureInput(
            feature_id=spec.feature_id,
            feature_version_hash=version.version_hash,
            feature_spec_hash=spec.spec_hash,
            implementation_hash=version.implementation_hash,
            code_revision=version.code_revision,
            lineage_hash=lineage.lineage_hash,
            backfill_hash=backfill.backfill_hash,
            as_of=lineage.decision_at,
            available_at=backfill.available_at,
            dataset_inputs=tuple(dataset_inputs),
            source_selection_mode=lineage.selection_mode,
            source_decision_time_safe=lineage.decision_time_safe,
        )

    def create_spec(
        self,
        *,
        experiment_id: str,
        strategy: StrategyVersionReference,
        feature_lineage_hashes: Sequence[str],
        parameters: Mapping[str, object],
        train_period: ExperimentPeriod,
        validation_period: ExperimentPeriod,
        oos_period: ExperimentPeriod,
        cost_model: ExperimentModelAssumption,
        slippage_model: ExperimentModelAssumption,
        random_seed: int,
        code_revision: str,
        input_as_of: datetime,
    ) -> ExperimentSpec:
        """从已登记特征证据创建 ExperimentSpec；不接收调用方拼接的输入对象。"""

        if not isinstance(feature_lineage_hashes, (list, tuple)):
            raise ExperimentError("feature_lineage_hashes 必须是非空 SHA-256 list 或 tuple")
        lineage_hashes = tuple(feature_lineage_hashes)
        if not lineage_hashes or len(lineage_hashes) != len(set(lineage_hashes)):
            raise ExperimentError("feature_lineage_hashes 必须非空且不能重复")
        if not all(isinstance(item, str) for item in lineage_hashes):
            raise ExperimentError("feature_lineage_hashes 只能包含 SHA-256 文本")
        feature_inputs = tuple(
            self.freeze_feature_input(lineage_hash=lineage_hash)
            for lineage_hash in sorted(lineage_hashes)
        )
        proposed = ExperimentSpec.create(
            experiment_id=experiment_id,
            strategy=strategy,
            feature_inputs=feature_inputs,
            parameters=parameters,
            train_period=train_period,
            validation_period=validation_period,
            oos_period=oos_period,
            cost_model=cost_model,
            slippage_model=slippage_model,
            random_seed=random_seed,
            code_revision=code_revision,
            input_as_of=input_as_of,
        )
        existing_by_id = self._specs_by_id.get(proposed.experiment_id)
        if existing_by_id is not None:
            if existing_by_id.spec_hash != proposed.spec_hash:
                raise ExperimentError(
                    f"ExperimentSpec {proposed.experiment_id} 已存在且身份不同，拒绝覆盖"
                )
            return existing_by_id
        existing_by_hash = self._specs_by_hash.get(proposed.spec_hash)
        if existing_by_hash is not None:
            # 同一完整声明不得以第二个名称制造两份看似独立的研究证据。
            if existing_by_hash.experiment_id != proposed.experiment_id:
                raise ExperimentError("相同 ExperimentSpec 不能以不同 experiment_id 重复登记")
            return existing_by_hash
        self._specs_by_id[proposed.experiment_id] = proposed
        self._specs_by_hash[proposed.spec_hash] = proposed
        return proposed

    def get_spec(self, spec_hash: str) -> ExperimentSpec:
        """按不可变 spec hash 读取；不提供 latest 或按策略名的隐式选择。"""

        try:
            return self._specs_by_hash[spec_hash]
        except KeyError as exc:
            raise ExperimentError(f"未登记的 ExperimentSpec: {spec_hash}") from exc

    def get_spec_by_id(self, experiment_id: str) -> ExperimentSpec:
        """按稳定实验 ID 读取已登记的唯一声明。"""

        try:
            return self._specs_by_id[experiment_id]
        except KeyError as exc:
            raise ExperimentError(f"未登记的 ExperimentSpec: {experiment_id}") from exc

    def record_run(
        self,
        *,
        run_id: str,
        spec_hash: str,
        status: ExperimentRunStatus,
        runner_id: str,
        run_configuration_hash: str,
        outcome_hash: str,
        evidence_hashes: Sequence[str],
    ) -> ExperimentRun:
        """记录一个声明性、静态实验结果，而不执行任何研究/交易动作。"""

        spec = self.get_spec(spec_hash)
        if (
            spec.selection_mode != "STATIC_REPRODUCIBILITY_ONLY"
            or spec.decision_time_safe is not False
            or spec.eligible_for_backtest is not False
            or spec.eligible_for_admission is not False
        ):
            raise ExperimentError("ExperimentSpec 不是受限的静态可复现声明，拒绝记录运行")
        if not isinstance(evidence_hashes, (list, tuple)):
            raise ExperimentError("evidence_hashes 必须是非空 SHA-256 list 或 tuple")
        proposed = ExperimentRun.from_spec(
            run_id=run_id,
            spec=spec,
            status=status,
            runner_id=runner_id,
            run_configuration_hash=run_configuration_hash,
            outcome_hash=outcome_hash,
            evidence_hashes=tuple(evidence_hashes),
        )
        existing_by_id = self._runs_by_id.get(proposed.run_id)
        if existing_by_id is not None:
            if existing_by_id.run_hash != proposed.run_hash:
                raise ExperimentError(f"ExperimentRun {proposed.run_id} 已存在且结果不同，拒绝覆盖")
            return existing_by_id
        existing_for_spec = self._runs_by_spec_hash.get(spec.spec_hash)
        if existing_for_spec is not None:
            if existing_for_spec.run_hash != proposed.run_hash:
                raise ExperimentError("同一静态 ExperimentSpec 产生不同结果，拒绝登记非确定性运行")
            raise ExperimentError("同一静态 ExperimentSpec 已由不同 run_id 登记相同结果")
        self._runs_by_id[proposed.run_id] = proposed
        self._runs_by_spec_hash[spec.spec_hash] = proposed
        return proposed

    def get_run(self, run_id: str) -> ExperimentRun:
        """按显式账本 ID 读取运行记录。"""

        try:
            return self._runs_by_id[run_id]
        except KeyError as exc:
            raise ExperimentError(f"未登记的 ExperimentRun: {run_id}") from exc

    def list_specs(self) -> tuple[ExperimentSpec, ...]:
        """稳定排序列出进程内声明；仅供审计展示。"""

        return tuple(self._specs_by_id[key] for key in sorted(self._specs_by_id))

    def list_runs(self) -> tuple[ExperimentRun, ...]:
        """稳定排序列出静态记录；不表示研究准入队列。"""

        return tuple(self._runs_by_id[key] for key in sorted(self._runs_by_id))

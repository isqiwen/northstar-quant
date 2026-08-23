"""隔离的逐决策策略 target replay 组合根。

这是 P2-WP05 的一小段受控编排：它仅对每个 checkpoint 重新选择 immutable 市场数据，
以当期历史生成 ``futures_trend`` target，并只保留该 checkpoint 明确指定的 event-time
切片。它**不**调用现有回测器、不生成 RunManifest、不调用 Research Admission，也不触及
券商、数据库、调度或 CLI。

因此返回的 trace 仍固定 ``decision_time_safe=false``、不可作为候选准入或交易证据。完整
strict 回测还需要受控 Feature/Event/Target producer、artifact-backed Contract Rule replay 和
对整个绑定的 LookaheadGuard 重算。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import inspect
import json
from pathlib import Path
import sys

import polars as pl

from northstar_quant import __version__
from northstar_quant.data_platform.artifacts.fingerprints import canonical_json_sha256
from northstar_quant.data_platform.artifacts.immutable_store import ArtifactStore
from northstar_quant.data_platform.artifacts.storage import profile_config_sha256
from northstar_quant.data_platform.contracts.instrument_universes import (
    load_instrument_universe,
)
from northstar_quant.data_platform.contracts.profile_governance import (
    validate_profile_data_governance,
)
from northstar_quant.data_platform.market.pit import MarketDataSnapshot
from northstar_quant.data_platform.quality.schema import (
    schema_version_for_profile,
    to_signal_market_data,
    validate_market_dataset,
)
from northstar_quant.data_platform.quality import schema as quality_schema_module
from northstar_quant.data_platform.sources.protocol import PublicationPurpose
from northstar_quant.platform.common.enums import DataFrequency, StrategyFamily
from northstar_quant.platform.config.trading_profile import TradingProfile, load_trading_profile
from northstar_quant.portfolio_risk.allocation import allocator as allocation_module
from northstar_quant.portfolio_risk.limits import models as risk_limits_module
from northstar_quant.portfolio_risk.portfolio.strategy_pipeline import (
    build_profile_risk_limits,
    enforce_profile_target_policy,
)
from northstar_quant.portfolio_risk.portfolio import multi_strategy as multi_strategy_module
from northstar_quant.portfolio_risk.portfolio import strategy_pipeline as strategy_pipeline_module
from northstar_quant.portfolio_risk.portfolio.multi_strategy import (
    build_target_weight_portfolio,
)
from northstar_quant.portfolio_risk.risk import global_risk as global_risk_module
from northstar_quant.portfolio_risk.risk import strategy_risk as strategy_risk_module
from northstar_quant.research.backtest.models import (
    BacktestAssumptions,
    BacktestCodeReference,
    BacktestContractError,
    BacktestDataInputKind,
    BacktestDataReference,
    BacktestDecisionReplayBinding,
    BacktestEngine,
    BacktestRequest,
    TargetFrameReference,
)
from northstar_quant.research.strategies import base as strategy_base_module
from northstar_quant.research.strategies import futures_trend as futures_trend_module
from northstar_quant.research.strategies.futures_trend import FuturesTrendStrategy
from northstar_quant.research.validation.decision_replay import (
    DecisionReplayStrategyIdentity,
    DecisionReplayTargetError,
    DecisionReplayTargetTrace,
    DecisionTarget,
    DecisionTargetSlice,
    DecisionTargetStatus,
)
from northstar_quant.research.validation.lookahead import (
    DecisionReplayPlan,
    LookaheadCertificate,
    LookaheadGuard,
    LookaheadGuardError,
)


_SUPPORTED_PROFILE_ID = "cn_futures_daily_trend_offline"
_SUPPORTED_STRATEGY_ID = "futures_trend"


class DecisionReplayCompositionError(ValueError):
    """受控逐决策 target replay 的输入或编排边界不满足。"""


@dataclass(frozen=True, slots=True)
class DecisionReplayReceipt:
    """A controlled target trace and its recomputable LookaheadGuard receipt.

    The receipt only proves that every target slice matches immutable market replay at its
    checkpoint. It is never admission, simulation, or trading evidence.
    """

    trace: DecisionReplayTargetTrace
    certificate: LookaheadCertificate
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.trace, DecisionReplayTargetTrace):
            raise DecisionReplayCompositionError("trace must be a DecisionReplayTargetTrace")
        if not isinstance(self.certificate, LookaheadCertificate):
            raise DecisionReplayCompositionError("certificate must be a LookaheadCertificate")
        if self.certificate.plan != self.trace.plan:
            raise DecisionReplayCompositionError("certificate must bind the trace replay plan")
        expected_target_hashes = tuple(
            item.target_frame_sha256 for item in self.trace.target_slices
        )
        certified_target_hashes = tuple(
            report.evidence.target.target_hash for report in self.certificate.reports
        )
        if certified_target_hashes != expected_target_hashes:
            raise DecisionReplayCompositionError(
                "certificate must bind every target trace slice"
            )
        if self.certificate.decision_time_safe or self.certificate.candidate_admission_eligible:
            raise DecisionReplayCompositionError("receipt cannot be promoted to admission evidence")
        receipt_hash = canonical_json_sha256(
            {
                "certificate_hash": self.certificate.certificate_hash,
                "format": "northstar.decision-replay-receipt.v1",
                "trace_hash": self.trace.trace_hash,
            }
        )
        object.__setattr__(self, "receipt_hash", receipt_hash)

    def as_mapping(self) -> dict[str, object]:
        return {
            "candidate_admission_eligible": False,
            "certificate": self.certificate.as_manifest_mapping(),
            "decision_time_safe": False,
            "format": "northstar.decision-replay-receipt.v1",
            "receipt_hash": self.receipt_hash,
            "trace": self.trace.as_mapping(),
        }


def _selected_strategy_ids(strategy_ids: Sequence[str] | None) -> tuple[str, ...]:
    if strategy_ids is None:
        return (_SUPPORTED_STRATEGY_ID,)
    if isinstance(strategy_ids, str):
        raise DecisionReplayCompositionError("strategy_ids 必须是字符串序列，不能是单个字符串")
    selected = tuple(str(item).strip() for item in strategy_ids)
    if selected != (_SUPPORTED_STRATEGY_ID,):
        raise DecisionReplayCompositionError(
            "当前逐决策 target replay 只允许唯一内建策略 futures_trend"
        )
    return selected


def _lookback_days(profile: TradingProfile, selected_strategy_ids: tuple[str, ...]) -> int:
    """将首个 strict 切片限制到唯一、明确的连续日线趋势画像。"""

    if profile.profile_id != _SUPPORTED_PROFILE_ID:
        raise DecisionReplayCompositionError(
            f"当前逐决策 target replay 只支持 {_SUPPORTED_PROFILE_ID}，"
            "实际合约和其他画像缺少受控规则 replay。"
        )
    if selected_strategy_ids != (_SUPPORTED_STRATEGY_ID,):
        raise DecisionReplayCompositionError("逐决策 target replay 的策略选择不受支持")
    if profile.lifecycle.role != "research" or profile.research_admission.enabled:
        raise DecisionReplayCompositionError("逐决策 target replay 只允许不可准入的 research 画像")
    if profile.data_frequency is not DataFrequency.D1 or profile.backtest.engine != "weight_return":
        raise DecisionReplayCompositionError("当前逐决策 target replay 只支持日线 weight_return 研究语义")
    if profile.strategy_family is not StrategyFamily.TREND_FOLLOWING:
        raise DecisionReplayCompositionError("逐决策 target replay 只支持 trend_following 策略族")
    if profile.futures is None or not profile.futures.symbols_are_continuous:
        raise DecisionReplayCompositionError("逐决策 target replay 只支持连续研究序列")
    if profile.futures.execution_allowed or profile.data.live_trading_eligible:
        raise DecisionReplayCompositionError("逐决策 target replay 不得用于可执行或 live-eligible 画像")
    if len(profile.strategies) != 1 or len(profile.enabled_strategies) != 1:
        raise DecisionReplayCompositionError("当前逐决策 target replay 只支持单一启用策略画像")
    strategy_config = profile.enabled_strategies[0]
    if strategy_config.strategy_id != _SUPPORTED_STRATEGY_ID:
        raise DecisionReplayCompositionError("画像唯一启用策略必须是 futures_trend")
    if strategy_config.strategy_family is not StrategyFamily.TREND_FOLLOWING:
        raise DecisionReplayCompositionError("futures_trend 的 strategy_family 必须是 trend_following")
    if strategy_config.capital_weight != 1.0:
        raise DecisionReplayCompositionError("当前逐决策 target replay 要求 futures_trend capital_weight=1")
    if set(strategy_config.params) != {"lookback_days"}:
        raise DecisionReplayCompositionError(
            "当前逐决策 target replay 只接受 futures_trend.lookback_days 参数"
        )
    lookback = strategy_config.params["lookback_days"]
    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 2:
        raise DecisionReplayCompositionError("futures_trend.lookback_days 必须是大于等于 2 的整数")
    return lookback


def _code_reference_sources() -> dict[str, str]:
    """读取形成本受控 target 的完整本地代码闭包。

    这些文本只被立即摘要，不写入 trace；缺少任何一项就拒绝生成可重放身份，避免将部分
    源码误称为完整实现版本。
    """

    modules = {
        "allocation": allocation_module,
        "composition_root": sys.modules[__name__],
        "futures_trend": futures_trend_module,
        "global_risk": global_risk_module,
        "market_signal_transform": quality_schema_module,
        "multi_strategy": multi_strategy_module,
        "risk_limits": risk_limits_module,
        "strategy_base": strategy_base_module,
        "strategy_pipeline": strategy_pipeline_module,
        "strategy_risk": strategy_risk_module,
    }
    try:
        return {name: inspect.getsource(module) for name, module in modules.items()}
    except (OSError, TypeError) as exc:
        raise DecisionReplayCompositionError("无法读取完整 target 代码闭包，拒绝构造 replay 身份") from exc


def _dependency_lock_hash() -> str:
    """绑定受控根运行时的锁定依赖版本，但绝不把本机路径写入证据。"""

    lock_path = Path(__file__).resolve().parents[3] / "uv.lock"
    try:
        contents = lock_path.read_bytes()
    except OSError as exc:
        raise DecisionReplayCompositionError("缺少 uv.lock，拒绝构造可重放 target 身份") from exc
    if not contents:
        raise DecisionReplayCompositionError("uv.lock 不能为空")
    return hashlib.sha256(contents).hexdigest()


def _strategy_identity(profile: TradingProfile, *, lookback_days: int) -> DecisionReplayStrategyIdentity:
    """从固定内建实现和画像有效参数构造可审计身份。

    这里不使用可替换的通用 Strategy Registry；受控根直接引用
    :class:`FuturesTrendStrategy`，并把信号变换、策略基类、组合、分配与风控的完整本地
    代码闭包以及 ``uv.lock`` 摘要一并纳入代码引用。
    """

    strategy_config = profile.enabled_strategies[0]
    source_closure = _code_reference_sources()
    dependency_lock_hash = _dependency_lock_hash()
    effective_parameters = {"lookback_days": lookback_days}
    effective_parameters_json = json.dumps(
        effective_parameters,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    profile_strategy_config_hash = canonical_json_sha256(
        {
            "capital_weight": strategy_config.capital_weight,
            "enabled": strategy_config.enabled,
            "params": effective_parameters,
            "strategy_family": StrategyFamily.TREND_FOLLOWING.value,
            "strategy_id": strategy_config.strategy_id,
        }
    )
    implementation_hash = hashlib.sha256(
        source_closure["futures_trend"].encode("utf-8")
    ).hexdigest()
    code_reference_hash = canonical_json_sha256(
        {
            "format": "northstar.decision-replay-code-reference.v1",
            "package_version": __version__,
            "dependency_lock_sha256": dependency_lock_hash,
            "sources": source_closure,
        }
    )
    return DecisionReplayStrategyIdentity(
        strategy_id=_SUPPORTED_STRATEGY_ID,
        output_type="target_weight",
        time_column="date",
        effective_parameters_json=effective_parameters_json,
        profile_strategy_config_hash=profile_strategy_config_hash,
        implementation_hash=implementation_hash,
        code_reference_hash=code_reference_hash,
    )


def _assert_snapshot_matches_profile(
    profile: TradingProfile,
    snapshot: MarketDataSnapshot,
    frame: pl.DataFrame,
) -> None:
    """在每个 checkpoint 重新核对冻结数据、授权范围和画像语义。"""

    if snapshot.dataset_id != profile.data.dataset_id:
        raise DecisionReplayCompositionError("checkpoint DatasetVersion 与画像 data.dataset_id 不一致")
    if not profile.data.source_id or snapshot.source_id != profile.data.source_id:
        raise DecisionReplayCompositionError("checkpoint 数据来源与画像 data.source_id 不一致")
    scope = snapshot.publication_scope
    if scope.purpose is not PublicationPurpose.HISTORICAL_BACKTEST:
        raise DecisionReplayCompositionError("checkpoint 数据授权不包含 historical_backtest 用途")
    if (
        scope.market != profile.market.value
        or scope.asset_type != profile.asset_type.value
        or scope.frequency != profile.data_frequency.value
    ):
        raise DecisionReplayCompositionError("checkpoint 数据授权维度与画像不一致")
    if scope.actual_contract_data:
        raise DecisionReplayCompositionError("连续研究 target replay 不得消费 actual_contract_data")
    if snapshot.spec.schema_version != schema_version_for_profile(profile):
        raise DecisionReplayCompositionError("checkpoint PIT schema 与画像要求不一致")
    if snapshot.spec.event_time_column != "date":
        raise DecisionReplayCompositionError("当前逐决策 target replay 只支持 date 事件时间")
    validate_market_dataset(profile, frame)

    universe = load_instrument_universe(profile.universe_id)
    members_by_symbol = {member.continuous_symbol: member for member in universe.members}
    required_symbols = {str(symbol).strip().upper() for symbol in profile.data.download.symbols}
    observed_symbols = {
        str(symbol).strip().upper() for symbol in frame.get_column("symbol").unique().to_list()
    }
    if not required_symbols or observed_symbols != required_symbols:
        raise DecisionReplayCompositionError("checkpoint 连续标的集合必须与画像完整一致")
    unknown_symbols = sorted(required_symbols.difference(members_by_symbol))
    if unknown_symbols:
        raise DecisionReplayCompositionError(
            "画像连续标的不在受控品种池中：" + ", ".join(unknown_symbols)
        )
    required_products = {members_by_symbol[symbol].product for symbol in required_symbols}
    required_exchanges = {members_by_symbol[symbol].exchange for symbol in required_symbols}
    missing_products = sorted(required_products.difference(scope.products))
    missing_exchanges = sorted(required_exchanges.difference(scope.exchanges))
    if missing_products or missing_exchanges:
        details: list[str] = []
        if missing_products:
            details.append("products 缺少：" + ", ".join(missing_products))
        if missing_exchanges:
            details.append("exchanges 缺少：" + ", ".join(missing_exchanges))
        raise DecisionReplayCompositionError("checkpoint 授权范围未覆盖画像品种池；" + "；".join(details))


def _assert_complete_current_universe(
    profile: TradingProfile,
    frame: pl.DataFrame,
    *,
    source_name: str,
) -> None:
    """要求一个当期输入或输出精确覆盖配置品种池，禁止静默缩小仓位集合。"""

    required_symbols = {str(symbol).strip().upper() for symbol in profile.data.download.symbols}
    current_symbols = {
        str(symbol).strip().upper() for symbol in frame.get_column("symbol").to_list()
    }
    missing_symbols = sorted(required_symbols.difference(current_symbols))
    unexpected_symbols = sorted(current_symbols.difference(required_symbols))
    if (
        not required_symbols
        or frame.height != len(required_symbols)
        or missing_symbols
        or unexpected_symbols
    ):
        details: list[str] = []
        if missing_symbols:
            details.append("缺少：" + ", ".join(missing_symbols))
        if unexpected_symbols:
            details.append("意外出现：" + ", ".join(unexpected_symbols))
        if frame.height != len(required_symbols):
            details.append(
                f"行数={frame.height}，期望={len(required_symbols)}"
            )
        raise DecisionReplayCompositionError(
            f"{source_name} 必须为画像每个连续标的恰好提供一条记录，"
            "不得静默缩小投资集合；"
            + "；".join(details)
        )


def _assert_checkpoint_event_time(
    *,
    plan: DecisionReplayPlan,
    checkpoint_index: int,
    profile: TradingProfile,
    frame: pl.DataFrame,
) -> None:
    """阻止把含有更晚 bar 的最终快照用于早期 checkpoint。"""

    checkpoint = plan.checkpoints[checkpoint_index]
    expected_event = checkpoint.decision_event_time
    if isinstance(expected_event, datetime):
        raise DecisionReplayCompositionError("当前日线 target replay 只接受 date decision_event_time")
    event_values = frame.get_column("date").to_list()
    if not event_values or any(isinstance(value, datetime) or not isinstance(value, date) for value in event_values):
        raise DecisionReplayCompositionError("checkpoint 市场数据的 date 列必须是非空 date 值")
    latest_event = max(event_values)
    if latest_event != expected_event:
        raise DecisionReplayCompositionError(
            "checkpoint 的 decision_event_time 必须精确等于已重放市场数据中的最新事件时点"
        )
    _assert_complete_current_universe(
        profile,
        frame.filter(pl.col("date") == expected_event),
        source_name="checkpoint 当前 decision_event_time 的市场数据",
    )


def _assert_snapshot_matches_checkpoint(
    snapshot: MarketDataSnapshot,
    checkpoint_index: int,
    plan: DecisionReplayPlan,
) -> None:
    """二次固定 selector 输出与 checkpoint 的精确绑定，拒绝任何手工快照。"""

    checkpoint = plan.checkpoints[checkpoint_index]
    if snapshot.dataset_version_hash != checkpoint.dataset_version_hash:
        raise DecisionReplayCompositionError(
            "已重放市场快照与 checkpoint.dataset_version_hash 不一致"
        )
    if snapshot.spec.spec_hash != checkpoint.pit_spec.spec_hash:
        raise DecisionReplayCompositionError("已重放市场快照与 checkpoint.pit_spec 不一致")
    if snapshot.as_of != checkpoint.decision_at:
        raise DecisionReplayCompositionError(
            "已重放市场快照的 as_of 必须精确等于 checkpoint.decision_at"
        )


def _target_slice(
    *,
    snapshot: MarketDataSnapshot,
    checkpoint_index: int,
    plan: DecisionReplayPlan,
    strategy: FuturesTrendStrategy,
    strategy_identity: DecisionReplayStrategyIdentity,
    profile: TradingProfile,
) -> DecisionTargetSlice:
    """只从本 checkpoint 的历史输入形成当期 target，绝不拼接全表输出。"""

    checkpoint = plan.checkpoints[checkpoint_index]
    _assert_snapshot_matches_checkpoint(snapshot, checkpoint_index, plan)
    market_frame = snapshot.selected_frame()
    _assert_snapshot_matches_profile(profile, snapshot, market_frame)
    _assert_checkpoint_event_time(
        plan=plan,
        checkpoint_index=checkpoint_index,
        profile=profile,
        frame=market_frame,
    )
    signal_frame = to_signal_market_data(profile, market_frame)
    strategy_output = strategy.generate_targets(signal_frame)
    current_output = strategy_output.filter(pl.col("date") == checkpoint.decision_event_time)
    if current_output.is_empty():
        if not strategy_output.is_empty():
            raise DecisionReplayCompositionError(
                "策略在已有历史 target 后未为当前 decision_event_time 生成 target，拒绝静默空仓"
            )
        target_status = DecisionTargetStatus.NO_TARGET_WARMUP
        targets: tuple[DecisionTarget, ...] = ()
    else:
        _assert_complete_current_universe(
            profile,
            current_output,
            source_name="策略当前 target（历史缺口不得静默缩小投资集合）",
        )
        target_status = DecisionTargetStatus.TARGETS
        current_portfolio = build_target_weight_portfolio(
            [current_output.drop("date")],
            [1.0],
            build_profile_risk_limits(profile),
        )
        current_portfolio = enforce_profile_target_policy(current_portfolio, profile).with_columns(
            pl.lit(checkpoint.decision_event_time).cast(pl.Date).alias("date")
        )
        required_columns = {"date", "symbol", "signal_value", "target_weight"}
        if set(current_portfolio.columns) != required_columns:
            raise DecisionReplayCompositionError("受控策略 target 输出字段不完整或包含未知字段")
        _assert_complete_current_universe(
            profile,
            current_portfolio,
            source_name="组合与风控后的当前 target",
        )
        targets = tuple(
            DecisionTarget(
                symbol=str(row["symbol"]),
                signal_value=row["signal_value"],
                target_weight=row["target_weight"],
            )
            for row in current_portfolio.sort("symbol").iter_rows(named=True)
        )
    return DecisionTargetSlice(
        checkpoint_hash=checkpoint.checkpoint_hash,
        decision_at=checkpoint.decision_at,
        decision_event_time=checkpoint.decision_event_time,
        market_snapshot_id=snapshot.snapshot_id,
        market_selected_frame_hash=snapshot.selected_frame_hash,
        market_revision_ids_hash=canonical_json_sha256(
            {"revision_ids": list(snapshot.revision_ids)}
        ),
        source_artifact_snapshot_hash=snapshot.source_artifact_snapshot_hash,
        strategy_identity_hash=strategy_identity.identity_hash,
        time_column="date",
        target_status=target_status,
        targets=targets,
    )


def build_profile_decision_replay_targets(
    *,
    profile_id: str,
    artifact_store: ArtifactStore,
    plan: DecisionReplayPlan,
    strategy_ids: Sequence[str] | None = None,
) -> DecisionReplayTargetTrace:
    """构造连续日线趋势的逐 checkpoint target trace。

    此 API 是故意狭窄的 composition root。它不接受裸 DataFrame、手工 MarketDataSnapshot、
    FeatureBackfill、事件、合约规则、策略工厂或 BacktestRequest；每个市场输入只能由
    ``DecisionReplayPlan.replay_market_data(plan, artifact_store)`` 重新选择。返回 trace 仍不可用于 backtest
    admission 或交易。
    """

    if not isinstance(profile_id, str) or not profile_id.strip():
        raise DecisionReplayCompositionError("profile_id 必须是非空字符串")
    if type(artifact_store) is not ArtifactStore:
        raise DecisionReplayCompositionError(
            "artifact_store 必须是精确的 ArtifactStore，不能使用子类"
        )
    if type(plan) is not DecisionReplayPlan:
        raise DecisionReplayCompositionError("plan 必须是精确的 DecisionReplayPlan，不能使用子类")
    selected_ids = _selected_strategy_ids(strategy_ids)
    profile = load_trading_profile(profile_id.strip())
    lookback_days = _lookback_days(profile, selected_ids)
    validate_profile_data_governance(profile)
    strategy_identity = _strategy_identity(profile, lookback_days=lookback_days)
    strategy = FuturesTrendStrategy(lookback_days=lookback_days)
    # 这是安全边界：既拒绝子类，又无绑定调用基类方法，不能由调用方覆写 replay 结果。
    market_evidence = DecisionReplayPlan.replay_market_data(plan, artifact_store)
    target_slices = tuple(
        _target_slice(
            snapshot=evidence.market_snapshot,
            checkpoint_index=index,
            plan=plan,
            strategy=strategy,
            strategy_identity=strategy_identity,
            profile=profile,
        )
        for index, evidence in enumerate(market_evidence)
    )
    target_frames = [item.targets_frame() for item in target_slices if item.targets]
    if not target_frames:
        raise DecisionReplayCompositionError("全部 checkpoint 均处于策略 warmup，拒绝生成空轨迹")
    aggregate_frame = pl.concat(target_frames, how="vertical").sort(["date", "symbol"])
    try:
        aggregate_target = TargetFrameReference.from_frame(aggregate_frame, time_column="date")
    except BacktestContractError as exc:
        raise DecisionReplayCompositionError("逐决策 target 聚合无法通过 target-weight 合同校验") from exc
    try:
        return DecisionReplayTargetTrace(
            plan=plan,
            profile_id=profile.profile_id,
            profile_config_sha256=profile_config_sha256(profile),
            profile_dimension_key=profile.dimension_key,
            selected_strategy_ids=selected_ids,
            strategy_identity=strategy_identity,
            target_slices=target_slices,
            aggregate_target=aggregate_target,
        )
    except DecisionReplayTargetError as exc:
        raise DecisionReplayCompositionError("逐决策 target trace 无法通过不可变合同校验") from exc


def build_profile_decision_replay_receipt(
    *,
    profile_id: str,
    artifact_store: ArtifactStore,
    plan: DecisionReplayPlan,
    strategy_ids: Sequence[str] | None = None,
) -> DecisionReplayReceipt:
    """Build a controlled target trace and bind it to a recomputable guard receipt.

    This remains a research-only API: it does not call a backtest engine, Research Admission,
    or any trading entry point.
    """

    trace = build_profile_decision_replay_targets(
        profile_id=profile_id,
        artifact_store=artifact_store,
        plan=plan,
        strategy_ids=strategy_ids,
    )
    try:
        market_data = DecisionReplayPlan.replay_market_data(plan, artifact_store)
        certificate = LookaheadGuard().certify(
            plan,
            trace.lookahead_evidence(market_data),
            artifact_store=artifact_store,
        )
    except (DecisionReplayTargetError, LookaheadGuardError) as exc:
        raise DecisionReplayCompositionError(
            "controlled target trace could not be bound to a LookaheadGuard receipt"
        ) from exc
    return DecisionReplayReceipt(trace=trace, certificate=certificate)


def build_profile_decision_replay_backtest_request(
    *,
    profile_id: str,
    artifact_store: ArtifactStore,
    plan: DecisionReplayPlan,
    strategy_ids: Sequence[str] | None = None,
) -> BacktestRequest:
    """Build, but never execute, a weight-return request bound to a verified replay receipt."""

    receipt = build_profile_decision_replay_receipt(
        profile_id=profile_id,
        artifact_store=artifact_store,
        plan=plan,
        strategy_ids=strategy_ids,
    )
    try:
        certificate = LookaheadGuard().verify_certificate(
            receipt.certificate,
            artifact_store=artifact_store,
        )
    except LookaheadGuardError as exc:
        raise DecisionReplayCompositionError(
            "decision replay receipt failed full LookaheadGuard recomputation"
        ) from exc
    profile = load_trading_profile(profile_id.strip())
    snapshots = tuple(
        report.evidence.market_data.market_snapshot for report in certificate.reports
    )
    if not snapshots:  # pragma: no cover - receipt construction requires checkpoints.
        raise DecisionReplayCompositionError("decision replay receipt has no market snapshots")
    first_snapshot = snapshots[0]
    binding = BacktestDecisionReplayBinding(
        receipt_hash=receipt.receipt_hash,
        certificate_hash=certificate.certificate_hash,
        trace_hash=receipt.trace.trace_hash,
        schedule_hash=receipt.trace.plan.schedule_hash,
        market_replay_hash=receipt.trace.market_replay_hash,
        strategy_identity_hash=receipt.trace.strategy_identity.identity_hash,
        target_frame_sha256=receipt.trace.aggregate_target.target_frame_sha256,
        profile_id=receipt.trace.profile_id,
        profile_config_sha256=receipt.trace.profile_config_sha256,
        profile_dimension_key=receipt.trace.profile_dimension_key,
        selected_strategy_ids=receipt.trace.selected_strategy_ids,
        pit_evidence_json=tuple(
            json.dumps(
                snapshot.as_manifest_mapping(),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            for snapshot in snapshots
        ),
    )
    config = profile.backtest
    try:
        return BacktestRequest(
            engine=BacktestEngine.WEIGHT_RETURN,
            profile_id=receipt.trace.profile_id,
            profile_config_sha256=receipt.trace.profile_config_sha256,
            profile_dimension_key=receipt.trace.profile_dimension_key,
            source_frequency=profile.data_frequency.value,
            signal_frequency=profile.strategy_data_frequency.value,
            execution_frequency="1d",
            settlement_frequency="1d_eod",
            result_frequency="1d_eod",
            selected_strategy_ids=receipt.trace.selected_strategy_ids,
            target=receipt.trace.aggregate_target,
            data=BacktestDataReference(
                input_kind=BacktestDataInputKind.DECISION_REPLAY_RECEIPT,
                dataset_id=first_snapshot.dataset_id,
                source_id=first_snapshot.source_id,
                adapter_id=None,
                content_sha256=receipt.trace.market_replay_hash,
                schema_version=first_snapshot.spec.schema_version,
                source_config_sha256=first_snapshot.source_config_sha256,
                selection_mode="PER_DECISION_POINT_IN_TIME_REPLAY",
                decision_time_safe=False,
                decision_replay=binding,
            ),
            assumptions=BacktestAssumptions(
                initial_cash=config.initial_cash,
                commission_bps=config.commission_bps,
                min_commission=config.min_commission,
                slippage_bps=config.slippage_bps,
                slippage_ticks=config.slippage_ticks,
                max_volume_participation=config.max_volume_participation,
                lot_size=config.lot_size,
                execution_delay_sessions=config.execution_delay_sessions,
                sellable_after_sessions=config.sellable_after_sessions,
                order_ttl_bars=config.order_ttl_bars,
                queue_ahead_ratio=config.queue_ahead_ratio,
            ),
            code=BacktestCodeReference(
                package_version=__version__,
                git_commit=None,
                git_dirty=None,
                worktree_sha256=None,
                strategy_identity_hash=receipt.trace.strategy_identity.identity_hash,
            ),
        )
    except BacktestContractError as exc:
        raise DecisionReplayCompositionError(
            "verified decision replay receipt could not build a BacktestRequest"
        ) from exc


__all__ = [
    "DecisionReplayCompositionError",
    "DecisionReplayReceipt",
    "build_profile_decision_replay_backtest_request",
    "build_profile_decision_replay_receipt",
    "build_profile_decision_replay_targets",
]

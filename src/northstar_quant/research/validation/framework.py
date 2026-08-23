"""P2-WP06 的确定性离线验证框架。

本模块评估已经形成的净收益序列，不执行策略、回测、订单或准入状态迁移。输入序列只在
内存中用于计算；公开报告只保存其不可变 hash 和派生指标，避免把原始市场数据混入研究
决策制品。任何缺失样本、重叠区间、未知 regime 或不完整压力场景都会失败关闭。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
import json
import math
import random
import re

from northstar_quant.data_platform.artifacts.fingerprints import canonical_json_sha256


_TEXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_TRADING_DAYS_PER_YEAR = 252


class ValidationError(ValueError):
    """验证计划、输入序列或统计结果不完整、不确定时抛出。"""


class ValidationStage(str, Enum):
    IN_SAMPLE = "in_sample"
    VALIDATION = "validation"
    OUT_OF_SAMPLE = "out_of_sample"


class StressKind(str, Enum):
    BASELINE = "baseline"
    TRANSACTION_COST = "transaction_cost"
    SLIPPAGE = "slippage"
    LATENCY = "latency"


class ResearchInputEvidenceKind(str, Enum):
    """The provenance class of research input evidence.

    ``DATASET_VERSIONED`` retains the P1/P2 DatasetVersion-backed research
    path. ``FIXTURE_ONLY_INTELLIGENCE_REPLAY`` is a distinct non-admissible
    acceptance-evidence lane and can never be confused with market data.
    """

    DATASET_VERSIONED = "dataset_versioned"
    FIXTURE_ONLY_INTELLIGENCE_REPLAY = "fixture_only_intelligence_replay"


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _TEXT_RE.fullmatch(value.strip()) is None:
        raise ValidationError(f"{field_name} 必须是规范、非空标识符")
    return value.strip()


def _sha256(value: object, field_name: str) -> str:
    text = _text(value, field_name)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValidationError(f"{field_name} 必须是小写 SHA-256")
    return text


def _finite(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field_name} 必须是有限数")
    result = float(value)
    if not math.isfinite(result):
        raise ValidationError(f"{field_name} 必须是有限数")
    return result


def _date(value: object, field_name: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise ValidationError(f"{field_name} 必须是 date")
    return value


def _canonical_parameters(value: Mapping[str, object]) -> str:
    if not isinstance(value, Mapping):
        raise ValidationError("parameters 必须是映射")
    normalized: dict[str, object] = {}
    for key, item in value.items():
        normalized_key = _text(key, "parameters.key")
        if isinstance(item, bool) or item is None or isinstance(item, (str, int, float)):
            if isinstance(item, float) and not math.isfinite(item):
                raise ValidationError("parameters 不能包含 NaN 或无穷")
            normalized[normalized_key] = item
        else:
            raise ValidationError("parameters 只允许标量，不得隐藏原始数据或路径")
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ValidationPeriod:
    start: date
    end: date

    def __post_init__(self) -> None:
        start = _date(self.start, "period.start")
        end = _date(self.end, "period.end")
        if end < start:
            raise ValidationError("period.end 不能早于 period.start")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def as_mapping(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


@dataclass(frozen=True, slots=True)
class ValidationSplit:
    in_sample: ValidationPeriod
    validation: ValidationPeriod
    out_of_sample: ValidationPeriod
    split_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not all(
            isinstance(item, ValidationPeriod)
            for item in (self.in_sample, self.validation, self.out_of_sample)
        ):
            raise ValidationError("split 必须包含三个 ValidationPeriod")
        if self.in_sample.end >= self.validation.start:
            raise ValidationError("in_sample 必须严格早于 validation")
        if self.validation.end >= self.out_of_sample.start:
            raise ValidationError("validation 必须严格早于 out_of_sample")
        object.__setattr__(
            self,
            "split_hash",
            canonical_json_sha256(
                {
                    "format": "northstar.validation-split.v1",
                    "in_sample": self.in_sample.as_mapping(),
                    "validation": self.validation.as_mapping(),
                    "out_of_sample": self.out_of_sample.as_mapping(),
                }
            ),
        )

    def period_for(self, stage: ValidationStage) -> ValidationPeriod:
        if not isinstance(stage, ValidationStage):
            raise ValidationError("stage 必须是 ValidationStage")
        return {
            ValidationStage.IN_SAMPLE: self.in_sample,
            ValidationStage.VALIDATION: self.validation,
            ValidationStage.OUT_OF_SAMPLE: self.out_of_sample,
        }[stage]


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    fold_id: str
    split: ValidationSplit
    fold_hash: str = field(init=False)

    def __post_init__(self) -> None:
        fold_id = _text(self.fold_id, "fold_id")
        if not isinstance(self.split, ValidationSplit):
            raise ValidationError("fold.split 必须是 ValidationSplit")
        object.__setattr__(self, "fold_id", fold_id)
        object.__setattr__(
            self,
            "fold_hash",
            canonical_json_sha256(
                {
                    "fold_id": fold_id,
                    "format": "northstar.walk-forward-fold.v1",
                    "split_hash": self.split.split_hash,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class RollingWindow:
    window_sessions: int
    stride_sessions: int

    def __post_init__(self) -> None:
        for name in ("window_sessions", "stride_sessions"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValidationError(f"rolling.{name} 必须是正整数")


@dataclass(frozen=True, slots=True)
class ReturnObservation:
    session: date
    net_return: float
    regime: str | None = None

    def __post_init__(self) -> None:
        session = _date(self.session, "observation.session")
        net_return = _finite(self.net_return, "observation.net_return")
        if net_return <= -1.0:
            raise ValidationError("observation.net_return 必须大于 -1")
        regime = _text(self.regime, "observation.regime") if self.regime is not None else None
        object.__setattr__(self, "session", session)
        object.__setattr__(self, "net_return", net_return)
        object.__setattr__(self, "regime", regime)


@dataclass(frozen=True, slots=True)
class ValidationReturnSeries:
    observations: tuple[ReturnObservation, ...]
    series_hash: str = field(init=False)

    @classmethod
    def create(cls, observations: Iterable[ReturnObservation]) -> "ValidationReturnSeries":
        return cls(observations=tuple(observations))

    def __post_init__(self) -> None:
        observations = tuple(self.observations)
        if not observations or not all(isinstance(item, ReturnObservation) for item in observations):
            raise ValidationError("observations 必须是非空 ReturnObservation 元组")
        if tuple(sorted(observations, key=lambda item: item.session)) != observations:
            raise ValidationError("observations 必须按 session 严格升序")
        if len({item.session for item in observations}) != len(observations):
            raise ValidationError("observations 不能包含重复 session")
        object.__setattr__(self, "observations", observations)
        object.__setattr__(
            self,
            "series_hash",
            canonical_json_sha256(
                {
                    "format": "northstar.validation-return-series.v1",
                    "observations": [
                        {
                            "session": item.session.isoformat(),
                            "net_return": item.net_return.hex(),
                            "regime": item.regime,
                        }
                        for item in observations
                    ],
                }
            ),
        )

    def for_period(self, period: ValidationPeriod) -> "ValidationReturnSeries":
        if not isinstance(period, ValidationPeriod):
            raise ValidationError("period 必须是 ValidationPeriod")
        selected = tuple(item for item in self.observations if period.contains(item.session))
        if not selected:
            raise ValidationError("验证区间没有任何收益样本")
        return ValidationReturnSeries(observations=selected)


@dataclass(frozen=True, slots=True)
class ResearchValidationEvidence:
    """验证报告的 hash-only 上游身份，不携带行情、特征值或回测曲线。"""

    dataset_version_hashes: tuple[str, ...]
    feature_version_hashes: tuple[str, ...]
    strategy_version_hash: str
    experiment_spec_hash: str
    experiment_run_hash: str
    backtest_result_hash: str
    input_kind: ResearchInputEvidenceKind
    fixture_replay_binding_hash: str | None
    code_revision: str
    evidence_hash: str = field(init=False)

    def __post_init__(self) -> None:
        datasets = tuple(sorted(_sha256(item, "dataset_version_hash") for item in self.dataset_version_hashes))
        features = tuple(sorted(_sha256(item, "feature_version_hash") for item in self.feature_version_hashes))
        if not isinstance(self.input_kind, ResearchInputEvidenceKind):
            raise ValidationError("input_kind must be a ResearchInputEvidenceKind")
        if not features or len(set(features)) != len(features):
            raise ValidationError("feature version hashes must be non-empty and unique")
        fixture_binding = (
            _sha256(self.fixture_replay_binding_hash, "fixture_replay_binding_hash")
            if self.fixture_replay_binding_hash is not None
            else None
        )
        if self.input_kind is ResearchInputEvidenceKind.DATASET_VERSIONED:
            if not datasets or len(set(datasets)) != len(datasets):
                raise ValidationError("dataset version hashes must be non-empty and unique")
            if fixture_binding is not None:
                raise ValidationError("dataset-versioned evidence cannot carry a fixture replay binding")
        else:
            if datasets:
                raise ValidationError("fixture-only replay evidence cannot carry DatasetVersion hashes")
            if fixture_binding is None:
                raise ValidationError("fixture-only replay evidence requires a binding hash")
        strategy = _sha256(self.strategy_version_hash, "strategy_version_hash")
        experiment_spec = _sha256(self.experiment_spec_hash, "experiment_spec_hash")
        experiment_run = _sha256(self.experiment_run_hash, "experiment_run_hash")
        result = _sha256(self.backtest_result_hash, "backtest_result_hash")
        code_revision = _text(self.code_revision, "code_revision")
        evidence_hash = canonical_json_sha256(
            {
                "backtest_result_hash": result,
                "code_revision": code_revision,
                "dataset_version_hashes": list(datasets),
                "experiment_run_hash": experiment_run,
                "experiment_spec_hash": experiment_spec,
                "feature_version_hashes": list(features),
                "fixture_replay_binding_hash": fixture_binding,
                "format": "northstar.research-validation-evidence.v1",
                "input_kind": self.input_kind.value,
                "strategy_version_hash": strategy,
            }
        )
        object.__setattr__(self, "dataset_version_hashes", datasets)
        object.__setattr__(self, "feature_version_hashes", features)
        object.__setattr__(self, "strategy_version_hash", strategy)
        object.__setattr__(self, "experiment_spec_hash", experiment_spec)
        object.__setattr__(self, "experiment_run_hash", experiment_run)
        object.__setattr__(self, "backtest_result_hash", result)
        object.__setattr__(self, "fixture_replay_binding_hash", fixture_binding)
        object.__setattr__(self, "code_revision", code_revision)
        object.__setattr__(self, "evidence_hash", evidence_hash)

    @property
    def eligible_for_candidate_admission(self) -> bool:
        """Validation evidence never grants candidate or trading authority."""

        return False

    def as_mapping(self) -> dict[str, object]:
        return {
            "dataset_version_hashes": list(self.dataset_version_hashes),
            "feature_version_hashes": list(self.feature_version_hashes),
            "strategy_version_hash": self.strategy_version_hash,
            "experiment_spec_hash": self.experiment_spec_hash,
            "experiment_run_hash": self.experiment_run_hash,
            "backtest_result_hash": self.backtest_result_hash,
            "input_kind": self.input_kind.value,
            "fixture_replay_binding_hash": self.fixture_replay_binding_hash,
            "code_revision": self.code_revision,
            "eligible_for_candidate_admission": False,
            "evidence_hash": self.evidence_hash,
        }


@dataclass(frozen=True, slots=True)
class ParameterNeighbor:
    """一个实际重跑的参数邻域结果；不允许以布尔“通过”替代收益证据。"""

    neighbor_id: str
    parameters_json: str
    series: ValidationReturnSeries
    neighbor_hash: str = field(init=False)

    @classmethod
    def create(
        cls,
        *,
        neighbor_id: str,
        parameters: Mapping[str, object],
        series: ValidationReturnSeries,
    ) -> "ParameterNeighbor":
        return cls(
            neighbor_id=neighbor_id,
            parameters_json=_canonical_parameters(parameters),
            series=series,
        )

    @property
    def parameters(self) -> Mapping[str, object]:
        return json.loads(self.parameters_json)

    def __post_init__(self) -> None:
        neighbor_id = _text(self.neighbor_id, "neighbor_id")
        if not isinstance(self.series, ValidationReturnSeries):
            raise ValidationError("neighbor.series 必须是 ValidationReturnSeries")
        try:
            parameters = json.loads(self.parameters_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValidationError("neighbor.parameters_json 必须是 canonical JSON") from exc
        if not isinstance(parameters, dict) or _canonical_parameters(parameters) != self.parameters_json:
            raise ValidationError("neighbor.parameters_json 必须是 canonical 标量映射")
        neighbor_hash = canonical_json_sha256(
            {
                "format": "northstar.validation-parameter-neighbor.v1",
                "neighbor_id": neighbor_id,
                "parameters": parameters,
                "series_hash": self.series.series_hash,
            }
        )
        object.__setattr__(self, "neighbor_id", neighbor_id)
        object.__setattr__(self, "neighbor_hash", neighbor_hash)


@dataclass(frozen=True, slots=True)
class ValidationMetrics:
    session_count: int
    total_return: float
    annualized_return: float
    sharpe: float | None
    max_drawdown: float
    positive_session_fraction: float

    def __post_init__(self) -> None:
        if isinstance(self.session_count, bool) or not isinstance(self.session_count, int) or self.session_count < 1:
            raise ValidationError("metrics.session_count 必须是正整数")
        for field_name in ("total_return", "annualized_return", "max_drawdown", "positive_session_fraction"):
            value = _finite(getattr(self, field_name), f"metrics.{field_name}")
            object.__setattr__(self, field_name, value)
        if self.total_return <= -1.0 or self.annualized_return <= -1.0:
            raise ValidationError("metrics return 必须大于 -1")
        if not 0 <= self.max_drawdown <= 1 or not 0 <= self.positive_session_fraction <= 1:
            raise ValidationError("metrics fraction 必须位于 [0, 1]")
        if self.sharpe is not None:
            object.__setattr__(self, "sharpe", _finite(self.sharpe, "metrics.sharpe"))

    def as_mapping(self) -> dict[str, object]:
        return {
            "session_count": self.session_count,
            "total_return": self.total_return,
            "annualized_return": self.annualized_return,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
            "positive_session_fraction": self.positive_session_fraction,
        }


@dataclass(frozen=True, slots=True)
class StressScenario:
    scenario_id: str
    kind: StressKind
    penalty_bps: float = 0.0
    delay_sessions: int = 0
    scenario_hash: str = field(init=False)

    def __post_init__(self) -> None:
        scenario_id = _text(self.scenario_id, "scenario_id")
        if not isinstance(self.kind, StressKind):
            raise ValidationError("scenario.kind 必须是 StressKind")
        penalty_bps = _finite(self.penalty_bps, "scenario.penalty_bps")
        if penalty_bps < 0:
            raise ValidationError("scenario.penalty_bps 不能为负")
        if isinstance(self.delay_sessions, bool) or not isinstance(self.delay_sessions, int) or self.delay_sessions < 0:
            raise ValidationError("scenario.delay_sessions 必须是非负整数")
        if self.kind is StressKind.BASELINE and (penalty_bps != 0 or self.delay_sessions != 0):
            raise ValidationError("baseline scenario 不得附加压力参数")
        if self.kind in {StressKind.TRANSACTION_COST, StressKind.SLIPPAGE} and self.delay_sessions:
            raise ValidationError("cost/slippage scenario 不得设置 delay_sessions")
        if self.kind is StressKind.LATENCY and penalty_bps:
            raise ValidationError("latency scenario 不得设置 penalty_bps")
        object.__setattr__(self, "scenario_id", scenario_id)
        object.__setattr__(self, "penalty_bps", penalty_bps)
        object.__setattr__(
            self,
            "scenario_hash",
            canonical_json_sha256(
                {
                    "delay_sessions": self.delay_sessions,
                    "format": "northstar.validation-stress-scenario.v1",
                    "kind": self.kind.value,
                    "penalty_bps": penalty_bps.hex(),
                    "scenario_id": scenario_id,
                }
            ),
        )


@dataclass(frozen=True, slots=True)
class DistributionSummary:
    method: str
    iteration_count: int
    seed: int
    total_return_p05: float
    total_return_p50: float
    total_return_p95: float

    def __post_init__(self) -> None:
        method = _text(self.method, "distribution.method")
        if method not in {"bootstrap", "monte_carlo"}:
            raise ValidationError("distribution.method 不受支持")
        if isinstance(self.iteration_count, bool) or not isinstance(self.iteration_count, int) or self.iteration_count < 10:
            raise ValidationError("distribution.iteration_count 必须至少为 10")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise ValidationError("distribution.seed 必须是非负整数")
        for field_name in ("total_return_p05", "total_return_p50", "total_return_p95"):
            object.__setattr__(self, field_name, _finite(getattr(self, field_name), f"distribution.{field_name}"))
        if not self.total_return_p05 <= self.total_return_p50 <= self.total_return_p95:
            raise ValidationError("distribution quantiles 必须单调")
        object.__setattr__(self, "method", method)


@dataclass(frozen=True, slots=True)
class ValidationReport:
    evidence: ResearchValidationEvidence
    split_hash: str
    input_series_hash: str
    stage_metrics: tuple[tuple[ValidationStage, ValidationMetrics], ...]
    walk_forward_oos_metrics: tuple[tuple[str, ValidationMetrics], ...]
    rolling_metrics: tuple[ValidationMetrics, ...]
    stress_metrics: tuple[tuple[str, ValidationMetrics], ...]
    parameter_neighbor_metrics: tuple[tuple[str, ValidationMetrics], ...]
    parameter_neighbor_hashes: tuple[tuple[str, str], ...]
    regime_metrics: tuple[tuple[str, ValidationMetrics], ...]
    bootstrap: DistributionSummary
    monte_carlo: DistributionSummary
    report_hash: str = field(init=False)

    @property
    def eligible_for_admission(self) -> bool:
        """验证本身不构成候选策略或任何交易资格。"""

        return False

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, ResearchValidationEvidence):
            raise ValidationError("report.evidence 必须是 ResearchValidationEvidence")
        split_hash = _sha256(self.split_hash, "report.split_hash")
        input_hash = _sha256(self.input_series_hash, "report.input_series_hash")
        stages = tuple(self.stage_metrics)
        if {stage for stage, _ in stages} != set(ValidationStage) or len(stages) != 3:
            raise ValidationError("stage_metrics 必须精确包含 IS/Validation/OOS")
        if not all(isinstance(stage, ValidationStage) and isinstance(metrics, ValidationMetrics) for stage, metrics in stages):
            raise ValidationError("stage_metrics 类型无效")
        for name, values in (
            ("walk_forward_oos_metrics", self.walk_forward_oos_metrics),
            ("stress_metrics", self.stress_metrics),
            ("parameter_neighbor_metrics", self.parameter_neighbor_metrics),
            ("regime_metrics", self.regime_metrics),
        ):
            if not isinstance(values, tuple) or not values or not all(
                isinstance(key, str) and isinstance(metrics, ValidationMetrics)
                for key, metrics in values
            ):
                raise ValidationError(f"{name} 必须是非空 (id, ValidationMetrics) 元组")
            if len({key for key, _ in values}) != len(values):
                raise ValidationError(f"{name} 不能包含重复 id")
        if not isinstance(self.rolling_metrics, tuple) or not self.rolling_metrics or not all(isinstance(item, ValidationMetrics) for item in self.rolling_metrics):
            raise ValidationError("rolling_metrics 必须是非空 ValidationMetrics 元组")
        neighbor_hashes = tuple(sorted(self.parameter_neighbor_hashes))
        if (
            not neighbor_hashes
            or {identifier for identifier, _ in neighbor_hashes}
            != {identifier for identifier, _ in self.parameter_neighbor_metrics}
            or any(_text(identifier, "parameter_neighbor_hash.id") != identifier or _sha256(value, "parameter_neighbor_hash") != value for identifier, value in neighbor_hashes)
        ):
            raise ValidationError("parameter_neighbor_hashes 必须精确绑定参数邻域指标")
        if not isinstance(self.bootstrap, DistributionSummary) or self.bootstrap.method != "bootstrap":
            raise ValidationError("bootstrap 必须是 bootstrap DistributionSummary")
        if not isinstance(self.monte_carlo, DistributionSummary) or self.monte_carlo.method != "monte_carlo":
            raise ValidationError("monte_carlo 必须是 monte_carlo DistributionSummary")
        canonical_stages = tuple(sorted(stages, key=lambda item: item[0].value))
        report_hash = canonical_json_sha256(
            {
                "bootstrap": _distribution_mapping(self.bootstrap),
                "evidence": self.evidence.as_mapping(),
                "format": "northstar.validation-report.v1",
                "input_series_hash": input_hash,
                "monte_carlo": _distribution_mapping(self.monte_carlo),
                "parameter_neighbor_metrics": _metric_pairs(self.parameter_neighbor_metrics),
                "regime_metrics": _metric_pairs(self.regime_metrics),
                "rolling_metrics": [item.as_mapping() for item in self.rolling_metrics],
                "split_hash": split_hash,
                "stage_metrics": [(stage.value, metrics.as_mapping()) for stage, metrics in canonical_stages],
                "stress_metrics": _metric_pairs(self.stress_metrics),
                "walk_forward_oos_metrics": _metric_pairs(self.walk_forward_oos_metrics),
            }
        )
        object.__setattr__(self, "split_hash", split_hash)
        object.__setattr__(self, "input_series_hash", input_hash)
        object.__setattr__(self, "stage_metrics", canonical_stages)
        object.__setattr__(self, "parameter_neighbor_hashes", neighbor_hashes)
        object.__setattr__(self, "report_hash", report_hash)

    def as_mapping(self) -> dict[str, object]:
        return {
            "format": "northstar.validation-report.v1",
            "eligible_for_admission": False,
            "evidence": self.evidence.as_mapping(),
            "input_series_hash": self.input_series_hash,
            "split_hash": self.split_hash,
            "stage_metrics": {stage.value: metrics.as_mapping() for stage, metrics in self.stage_metrics},
            "walk_forward_oos_metrics": dict(_metric_pairs(self.walk_forward_oos_metrics)),
            "rolling_metrics": [item.as_mapping() for item in self.rolling_metrics],
            "stress_metrics": dict(_metric_pairs(self.stress_metrics)),
            "parameter_neighbor_metrics": dict(_metric_pairs(self.parameter_neighbor_metrics)),
            "parameter_neighbor_hashes": dict(self.parameter_neighbor_hashes),
            "regime_metrics": dict(_metric_pairs(self.regime_metrics)),
            "bootstrap": _distribution_mapping(self.bootstrap),
            "monte_carlo": _distribution_mapping(self.monte_carlo),
            "report_hash": self.report_hash,
        }


def _distribution_mapping(value: DistributionSummary) -> dict[str, object]:
    return {
        "method": value.method,
        "iteration_count": value.iteration_count,
        "seed": value.seed,
        "total_return_p05": value.total_return_p05,
        "total_return_p50": value.total_return_p50,
        "total_return_p95": value.total_return_p95,
    }


def _metric_pairs(values: tuple[tuple[str, ValidationMetrics], ...]) -> list[tuple[str, dict[str, object]]]:
    return [(key, metrics.as_mapping()) for key, metrics in sorted(values)]


def _metrics(series: ValidationReturnSeries) -> ValidationMetrics:
    returns = [item.net_return for item in series.observations]
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, 1.0 - equity / peak)
    count = len(returns)
    total_return = equity - 1.0
    annualized_return = equity ** (_TRADING_DAYS_PER_YEAR / count) - 1.0
    mean = sum(returns) / count
    variance = sum((item - mean) ** 2 for item in returns) / (count - 1) if count > 1 else 0.0
    sharpe = mean / math.sqrt(variance) * math.sqrt(_TRADING_DAYS_PER_YEAR) if variance > 0 else None
    return ValidationMetrics(
        session_count=count,
        total_return=total_return,
        annualized_return=annualized_return,
        sharpe=sharpe,
        max_drawdown=max_drawdown,
        positive_session_fraction=sum(item > 0 for item in returns) / count,
    )


def _stress_series(series: ValidationReturnSeries, scenario: StressScenario) -> ValidationReturnSeries:
    returns = [item.net_return for item in series.observations]
    if scenario.kind in {StressKind.TRANSACTION_COST, StressKind.SLIPPAGE}:
        penalty = scenario.penalty_bps / 10_000
        transformed = [item - penalty for item in returns]
    elif scenario.kind is StressKind.LATENCY:
        transformed = [0.0] * scenario.delay_sessions + returns
        transformed = transformed[: len(returns)]
    else:
        transformed = returns
    return ValidationReturnSeries.create(
        ReturnObservation(session=item.session, net_return=transformed[index], regime=item.regime)
        for index, item in enumerate(series.observations)
    )


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValidationError("distribution values 不能为空")
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - index) + ordered[upper] * (index - lower)


def _distribution(
    series: ValidationReturnSeries,
    *,
    method: str,
    iteration_count: int,
    seed: int,
) -> DistributionSummary:
    if iteration_count < 10:
        raise ValidationError("iteration_count 必须至少为 10")
    if seed < 0:
        raise ValidationError("seed 必须是非负整数")
    returns = [item.net_return for item in series.observations]
    if len(returns) < 2:
        raise ValidationError("bootstrap/monte_carlo 至少需要两个样本")
    rng = random.Random(seed)
    totals: list[float] = []
    for _ in range(iteration_count):
        if method == "bootstrap":
            sample = [returns[rng.randrange(len(returns))] for _ in returns]
        elif method == "monte_carlo":
            sample = list(returns)
            rng.shuffle(sample)
        else:  # pragma: no cover - only internal constants call this helper.
            raise ValidationError("distribution method 不受支持")
        equity = math.prod(1.0 + value for value in sample)
        totals.append(equity - 1.0)
    return DistributionSummary(
        method=method,
        iteration_count=iteration_count,
        seed=seed,
        total_return_p05=_quantile(totals, 0.05),
        total_return_p50=_quantile(totals, 0.50),
        total_return_p95=_quantile(totals, 0.95),
    )


def evaluate_validation(
    *,
    evidence: ResearchValidationEvidence,
    series: ValidationReturnSeries,
    split: ValidationSplit,
    walk_forward_folds: Sequence[WalkForwardFold],
    rolling_window: RollingWindow,
    stress_scenarios: Sequence[StressScenario],
    parameter_neighbors: Sequence[ParameterNeighbor],
    bootstrap_iterations: int,
    monte_carlo_iterations: int,
    random_seed: int,
) -> ValidationReport:
    """执行完整、确定性的研究验证，不授予任何研究或交易资格。"""

    if not isinstance(evidence, ResearchValidationEvidence):
        raise ValidationError("evidence 必须是 ResearchValidationEvidence")
    if not isinstance(series, ValidationReturnSeries) or not isinstance(split, ValidationSplit):
        raise ValidationError("series 与 split 必须是严格验证对象")
    if not isinstance(rolling_window, RollingWindow):
        raise ValidationError("rolling_window 必须是 RollingWindow")
    folds = tuple(walk_forward_folds)
    if not folds or not all(isinstance(item, WalkForwardFold) for item in folds):
        raise ValidationError("walk_forward_folds 必须是非空 WalkForwardFold 序列")
    if len({item.fold_id for item in folds}) != len(folds):
        raise ValidationError("walk_forward_folds 不能包含重复 fold_id")
    scenarios = tuple(stress_scenarios)
    if not scenarios or not all(isinstance(item, StressScenario) for item in scenarios):
        raise ValidationError("stress_scenarios 必须是非空 StressScenario 序列")
    if len({item.scenario_id for item in scenarios}) != len(scenarios):
        raise ValidationError("stress_scenarios 不能包含重复 scenario_id")
    if sum(item.kind is StressKind.BASELINE for item in scenarios) != 1:
        raise ValidationError("stress_scenarios 必须精确包含一个 baseline")
    neighbors_input = tuple(parameter_neighbors)
    if not neighbors_input or not all(isinstance(item, ParameterNeighbor) for item in neighbors_input):
        raise ValidationError("parameter_neighbors 必须是非空 ParameterNeighbor 序列")
    if len({item.neighbor_id for item in neighbors_input}) != len(neighbors_input):
        raise ValidationError("parameter_neighbors 不能包含重复 neighbor_id")
    if isinstance(random_seed, bool) or not isinstance(random_seed, int) or random_seed < 0:
        raise ValidationError("random_seed 必须是非负整数")

    stage_metrics = tuple(
        (stage, _metrics(series.for_period(split.period_for(stage))))
        for stage in ValidationStage
    )
    walk_forward = tuple(
        (fold.fold_id, _metrics(series.for_period(fold.split.out_of_sample)))
        for fold in sorted(folds, key=lambda item: item.fold_id)
    )
    if any(folds[index].split.out_of_sample.end >= folds[index + 1].split.out_of_sample.start for index in range(len(folds) - 1)):
        raise ValidationError("walk-forward OOS folds 必须严格按时间排序且不重叠")

    observations = series.observations
    if len(observations) < rolling_window.window_sessions:
        raise ValidationError("收益样本不足以覆盖 rolling window")
    rolling = tuple(
        _metrics(ValidationReturnSeries(observations=observations[start : start + rolling_window.window_sessions]))
        for start in range(0, len(observations) - rolling_window.window_sessions + 1, rolling_window.stride_sessions)
    )
    stress = tuple(
        (scenario.scenario_id, _metrics(_stress_series(series.for_period(split.out_of_sample), scenario)))
        for scenario in sorted(scenarios, key=lambda item: item.scenario_id)
    )
    neighbors: list[tuple[str, ValidationMetrics]] = []
    for neighbor in sorted(neighbors_input, key=lambda item: item.neighbor_id):
        neighbors.append((neighbor.neighbor_id, _metrics(neighbor.series.for_period(split.out_of_sample))))
    regimes: dict[str, list[ReturnObservation]] = defaultdict(list)
    for observation in series.for_period(split.out_of_sample).observations:
        if observation.regime is None:
            raise ValidationError("OOS 观测必须显式声明 regime")
        regimes[observation.regime].append(observation)
    regime_metrics = tuple(
        (regime, _metrics(ValidationReturnSeries(observations=tuple(values))))
        for regime, values in sorted(regimes.items())
    )
    oos_series = series.for_period(split.out_of_sample)
    return ValidationReport(
        evidence=evidence,
        split_hash=split.split_hash,
        input_series_hash=series.series_hash,
        stage_metrics=stage_metrics,
        walk_forward_oos_metrics=walk_forward,
        rolling_metrics=rolling,
        stress_metrics=stress,
        parameter_neighbor_metrics=tuple(neighbors),
        parameter_neighbor_hashes=tuple(
            (item.neighbor_id, item.neighbor_hash)
            for item in sorted(neighbors_input, key=lambda item: item.neighbor_id)
        ),
        regime_metrics=regime_metrics,
        bootstrap=_distribution(
            oos_series,
            method="bootstrap",
            iteration_count=bootstrap_iterations,
            seed=random_seed,
        ),
        monte_carlo=_distribution(
            oos_series,
            method="monte_carlo",
            iteration_count=monte_carlo_iterations,
            seed=random_seed + 1,
        ),
    )


__all__ = [
    "DistributionSummary",
    "ParameterNeighbor",
    "ResearchInputEvidenceKind",
    "ResearchValidationEvidence",
    "ReturnObservation",
    "RollingWindow",
    "StressKind",
    "StressScenario",
    "ValidationError",
    "ValidationMetrics",
    "ValidationPeriod",
    "ValidationReport",
    "ValidationReturnSeries",
    "ValidationSplit",
    "ValidationStage",
    "WalkForwardFold",
    "evaluate_validation",
]

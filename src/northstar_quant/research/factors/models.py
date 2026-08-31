"""严格 PIT 横截面因子研究的领域合同。

这里的对象只描述因子定义、暴露、研究组合提案和到期后的分析结果。它们刻意不使用
``StrategyTarget``、``PortfolioTarget``、``ExecutionPlan`` 或 broker 对象：因子研究的
输出必须先经过独立的研究决策和人工审批，不能被误当作可交易指令。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
import json
import math
import re

from northstar_quant.data.artifacts.fingerprints import (
    FingerprintError,
    canonical_json_sha256,
    require_sha256,
)
from northstar_quant.research.validation.framework import ValidationPeriod, WalkForwardFold


_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_FEATURE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


class FactorResearchError(ValueError):
    """因子研究输入、时间语义或研究边界不满足时抛出。"""


class FactorRole(str, Enum):
    """alpha 因子和风险估计因子拥有不同语义，不能互换。"""

    ALPHA = "alpha"
    RISK_MODEL = "risk_model"


class ProposalStatus(str, Enum):
    """研究组合提案的可用状态，不表示任何下游执行授权。"""

    PROPOSAL = "proposal"
    NO_PROPOSAL_WARMUP = "no_proposal_warmup"


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise FactorResearchError(f"{field_name} 必须是小写 snake_case 标识符")
    return value


def _feature_id(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _FEATURE_ID_RE.fullmatch(value) is None:
        raise FactorResearchError(f"{field_name} 必须是至少两段的小写点分 feature ID")
    return value


def _version(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _VERSION_RE.fullmatch(value) is None:
        raise FactorResearchError(f"{field_name} 必须是稳定版本文本")
    return value


def _hash(value: object, field_name: str) -> str:
    try:
        return require_sha256(value, field_name=field_name)  # type: ignore[arg-type]
    except FingerprintError as exc:
        raise FactorResearchError(str(exc)) from exc


def _number(
    value: object,
    field_name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FactorResearchError(f"{field_name} 必须是有限数值")
    result = float(value)
    if not math.isfinite(result):
        raise FactorResearchError(f"{field_name} 必须是有限数值")
    if minimum is not None and result < minimum:
        raise FactorResearchError(f"{field_name} 不能小于 {minimum}")
    if maximum is not None and result > maximum:
        raise FactorResearchError(f"{field_name} 不能大于 {maximum}")
    return result


def _positive_int(value: object, field_name: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise FactorResearchError(f"{field_name} 必须是不小于 {minimum} 的整数")
    return value


def _utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise FactorResearchError(f"{field_name} 必须是带时区 datetime")
    return value.astimezone(UTC)


def _session(value: object, field_name: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise FactorResearchError(f"{field_name} 必须是 date，不能使用 datetime")
    return value


def _symbol(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _SYMBOL_RE.fullmatch(value.strip().upper()) is None:
        raise FactorResearchError(f"{field_name} 必须是规范大写连续研究标的")
    return value.strip().upper()


def _parameters_json(value: Mapping[str, object]) -> str:
    if not isinstance(value, Mapping):
        raise FactorResearchError("parameters 必须是映射")
    normalized: dict[str, object] = {}
    for key, item in value.items():
        normalized[_identifier(key, "parameters.key")] = item
    try:
        return json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise FactorResearchError("parameters 必须是有限、可 JSON 序列化的映射") from exc


def _load_parameters(value: str) -> Mapping[str, object]:
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:  # pragma: no cover - constructors protect this.
        raise FactorResearchError("parameters_json 必须是 canonical JSON") from exc
    if not isinstance(decoded, dict) or _parameters_json(decoded) != value:
        raise FactorResearchError("parameters_json 必须是 canonical JSON 映射")
    return decoded


@dataclass(frozen=True, slots=True)
class FactorDefinition:
    """一个因子与其受控 FeatureVersion 输入之间的稳定绑定。"""

    factor_id: str
    feature_id: str
    role: FactorRole
    direction: float
    risk_budget: float
    parameters_json: str
    definition_hash: str = field(init=False)

    @classmethod
    def create(
        cls,
        *,
        factor_id: str,
        feature_id: str,
        role: FactorRole,
        direction: float,
        risk_budget: float,
        parameters: Mapping[str, object],
    ) -> "FactorDefinition":
        return cls(
            factor_id=factor_id,
            feature_id=feature_id,
            role=role,
            direction=direction,
            risk_budget=risk_budget,
            parameters_json=_parameters_json(parameters),
        )

    @property
    def parameters(self) -> Mapping[str, object]:
        return dict(_load_parameters(self.parameters_json))

    def __post_init__(self) -> None:
        factor_id = _identifier(self.factor_id, "factor_id")
        feature_id = _feature_id(self.feature_id, "feature_id")
        if not isinstance(self.role, FactorRole):
            raise FactorResearchError("role 必须是 FactorRole")
        direction = _number(self.direction, "direction")
        risk_budget = _number(self.risk_budget, "risk_budget", minimum=0.0, maximum=1.0)
        if self.role is FactorRole.ALPHA:
            if direction not in {-1.0, 1.0}:
                raise FactorResearchError("alpha factor.direction 必须为 -1 或 1")
            if risk_budget <= 0:
                raise FactorResearchError("alpha factor.risk_budget 必须大于 0")
        elif direction != 1.0 or risk_budget != 0.0:
            raise FactorResearchError("risk_model factor 必须使用 direction=1 且 risk_budget=0")
        parameters = _load_parameters(self.parameters_json)
        definition_hash = canonical_json_sha256(
            {
                "direction": direction.hex(),
                "factor_id": factor_id,
                "feature_id": feature_id,
                "format": "northstar.factor-definition.v1",
                "parameters": parameters,
                "risk_budget": risk_budget.hex(),
                "role": self.role.value,
            }
        )
        object.__setattr__(self, "factor_id", factor_id)
        object.__setattr__(self, "feature_id", feature_id)
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "risk_budget", risk_budget)
        object.__setattr__(self, "definition_hash", definition_hash)


@dataclass(frozen=True, slots=True)
class FactorRobustnessCostScenario:
    """A frozen continuous-series cost sensitivity scenario.

    This remains a research cost approximation.  It does not introduce an
    order, fill, margin, or actual-contract execution model.
    """

    scenario_id: str
    commission_bps: float
    min_commission: float
    slippage_bps: float
    execution_delay_sessions: int
    scenario_hash: str = field(init=False)

    def __post_init__(self) -> None:
        scenario_id = _identifier(self.scenario_id, "robustness_cost.scenario_id")
        commission_bps = _number(
            self.commission_bps,
            "robustness_cost.commission_bps",
            minimum=0.0,
        )
        min_commission = _number(
            self.min_commission,
            "robustness_cost.min_commission",
            minimum=0.0,
        )
        slippage_bps = _number(
            self.slippage_bps,
            "robustness_cost.slippage_bps",
            minimum=0.0,
        )
        execution_delay_sessions = _positive_int(
            self.execution_delay_sessions,
            "robustness_cost.execution_delay_sessions",
        )
        scenario_hash = canonical_json_sha256(
            {
                "commission_bps": commission_bps.hex(),
                "execution_delay_sessions": execution_delay_sessions,
                "format": "northstar.factor-robustness-cost-scenario.v1",
                "min_commission": min_commission.hex(),
                "scenario_id": scenario_id,
                "slippage_bps": slippage_bps.hex(),
            }
        )
        object.__setattr__(self, "scenario_id", scenario_id)
        object.__setattr__(self, "commission_bps", commission_bps)
        object.__setattr__(self, "min_commission", min_commission)
        object.__setattr__(self, "slippage_bps", slippage_bps)
        object.__setattr__(self, "execution_delay_sessions", execution_delay_sessions)
        object.__setattr__(self, "scenario_hash", scenario_hash)


@dataclass(frozen=True, slots=True)
class FactorRobustnessSubperiod:
    """A named, precommitted subperiod and explicit symbol exclusion set."""

    scenario_id: str
    period: ValidationPeriod
    excluded_symbols: tuple[str, ...]
    scenario_hash: str = field(init=False)

    def __post_init__(self) -> None:
        scenario_id = _identifier(self.scenario_id, "robustness_subperiod.scenario_id")
        if type(self.period) is not ValidationPeriod:
            raise FactorResearchError("robustness_subperiod.period 必须是精确的 ValidationPeriod")
        symbols = tuple(
            _symbol(item, "robustness_subperiod.excluded_symbols")
            for item in self.excluded_symbols
        )
        if tuple(sorted(symbols)) != symbols or len(set(symbols)) != len(symbols):
            raise FactorResearchError(
                "robustness_subperiod.excluded_symbols 必须排序且无重复"
            )
        scenario_hash = canonical_json_sha256(
            {
                "excluded_symbols": list(symbols),
                "format": "northstar.factor-robustness-subperiod.v1",
                "period": self.period.as_mapping(),
                "scenario_id": scenario_id,
            }
        )
        object.__setattr__(self, "scenario_id", scenario_id)
        object.__setattr__(self, "excluded_symbols", symbols)
        object.__setattr__(self, "scenario_hash", scenario_hash)


@dataclass(frozen=True, slots=True)
class FactorRobustnessParameterVariant:
    """A finite one-factor parameter neighbour, not an AI-generated mutation."""

    variant_id: str
    factor_id: str
    parameters_json: str
    variant_hash: str = field(init=False)

    @classmethod
    def create(
        cls,
        *,
        variant_id: str,
        factor_id: str,
        parameters: Mapping[str, object],
    ) -> "FactorRobustnessParameterVariant":
        return cls(
            variant_id=variant_id,
            factor_id=factor_id,
            parameters_json=_parameters_json(parameters),
        )

    @property
    def parameters(self) -> Mapping[str, object]:
        return dict(_load_parameters(self.parameters_json))

    def __post_init__(self) -> None:
        variant_id = _identifier(self.variant_id, "robustness_parameter.variant_id")
        factor_id = _identifier(self.factor_id, "robustness_parameter.factor_id")
        parameters = _load_parameters(self.parameters_json)
        if not parameters:
            raise FactorResearchError("robustness_parameter.parameters 不能为空")
        variant_hash = canonical_json_sha256(
            {
                "factor_id": factor_id,
                "format": "northstar.factor-robustness-parameter-variant.v1",
                "parameters": parameters,
                "variant_id": variant_id,
            }
        )
        object.__setattr__(self, "variant_id", variant_id)
        object.__setattr__(self, "factor_id", factor_id)
        object.__setattr__(self, "variant_hash", variant_hash)


@dataclass(frozen=True, slots=True)
class FactorStabilityThresholds:
    """Precommitted thresholds used only to label research robustness evidence."""

    minimum_analysis_periods: int
    minimum_mean_rank_ic: float
    minimum_positive_ic_fraction: float
    minimum_quantile_spread: float
    maximum_ic_standard_deviation: float
    maximum_mean_turnover: float
    minimum_scenario_pass_fraction: float
    minimum_cost_scenario_total_return: float
    minimum_cost_scenario_max_drawdown: float
    thresholds_hash: str = field(init=False)

    def __post_init__(self) -> None:
        minimum_analysis_periods = _positive_int(
            self.minimum_analysis_periods,
            "stability.minimum_analysis_periods",
        )
        minimum_mean_rank_ic = _number(
            self.minimum_mean_rank_ic,
            "stability.minimum_mean_rank_ic",
            minimum=-1.0,
            maximum=1.0,
        )
        minimum_positive_ic_fraction = _number(
            self.minimum_positive_ic_fraction,
            "stability.minimum_positive_ic_fraction",
            minimum=0.0,
            maximum=1.0,
        )
        minimum_quantile_spread = _number(
            self.minimum_quantile_spread,
            "stability.minimum_quantile_spread",
        )
        maximum_ic_standard_deviation = _number(
            self.maximum_ic_standard_deviation,
            "stability.maximum_ic_standard_deviation",
            minimum=0.0,
        )
        maximum_mean_turnover = _number(
            self.maximum_mean_turnover,
            "stability.maximum_mean_turnover",
            minimum=0.0,
        )
        minimum_scenario_pass_fraction = _number(
            self.minimum_scenario_pass_fraction,
            "stability.minimum_scenario_pass_fraction",
            minimum=0.0,
            maximum=1.0,
        )
        minimum_cost_scenario_total_return = _number(
            self.minimum_cost_scenario_total_return,
            "stability.minimum_cost_scenario_total_return",
            minimum=-1.0,
        )
        minimum_cost_scenario_max_drawdown = _number(
            self.minimum_cost_scenario_max_drawdown,
            "stability.minimum_cost_scenario_max_drawdown",
            minimum=-1.0,
            maximum=0.0,
        )
        thresholds_hash = canonical_json_sha256(
            {
                "format": "northstar.factor-stability-thresholds.v1",
                "maximum_ic_standard_deviation": maximum_ic_standard_deviation.hex(),
                "maximum_mean_turnover": maximum_mean_turnover.hex(),
                "minimum_analysis_periods": minimum_analysis_periods,
                "minimum_cost_scenario_max_drawdown": minimum_cost_scenario_max_drawdown.hex(),
                "minimum_cost_scenario_total_return": minimum_cost_scenario_total_return.hex(),
                "minimum_mean_rank_ic": minimum_mean_rank_ic.hex(),
                "minimum_positive_ic_fraction": minimum_positive_ic_fraction.hex(),
                "minimum_quantile_spread": minimum_quantile_spread.hex(),
                "minimum_scenario_pass_fraction": minimum_scenario_pass_fraction.hex(),
            }
        )
        object.__setattr__(self, "minimum_analysis_periods", minimum_analysis_periods)
        object.__setattr__(self, "minimum_mean_rank_ic", minimum_mean_rank_ic)
        object.__setattr__(self, "minimum_positive_ic_fraction", minimum_positive_ic_fraction)
        object.__setattr__(self, "minimum_quantile_spread", minimum_quantile_spread)
        object.__setattr__(self, "maximum_ic_standard_deviation", maximum_ic_standard_deviation)
        object.__setattr__(self, "maximum_mean_turnover", maximum_mean_turnover)
        object.__setattr__(self, "minimum_scenario_pass_fraction", minimum_scenario_pass_fraction)
        object.__setattr__(self, "minimum_cost_scenario_total_return", minimum_cost_scenario_total_return)
        object.__setattr__(self, "minimum_cost_scenario_max_drawdown", minimum_cost_scenario_max_drawdown)
        object.__setattr__(self, "thresholds_hash", thresholds_hash)


@dataclass(frozen=True, slots=True)
class FactorRobustnessPlan:
    """The complete pre-run robustness study declaration for a factor pipeline."""

    plan_id: str
    version: str
    subperiods: tuple[FactorRobustnessSubperiod, ...]
    parameter_variants: tuple[FactorRobustnessParameterVariant, ...]
    cost_scenarios: tuple[FactorRobustnessCostScenario, ...]
    stability_thresholds: FactorStabilityThresholds
    plan_hash: str = field(init=False)

    def __post_init__(self) -> None:
        plan_id = _identifier(self.plan_id, "robustness_plan.plan_id")
        version = _version(self.version, "robustness_plan.version")
        subperiods = tuple(self.subperiods)
        if len(subperiods) < 2 or not all(
            type(item) is FactorRobustnessSubperiod for item in subperiods
        ):
            raise FactorResearchError(
                "robustness_plan.subperiods 必须至少包含两个 FactorRobustnessSubperiod"
            )
        if tuple(sorted(subperiods, key=lambda item: item.scenario_id)) != subperiods:
            raise FactorResearchError("robustness_plan.subperiods 必须按 scenario_id 排序")
        if len({item.scenario_id for item in subperiods}) != len(subperiods):
            raise FactorResearchError("robustness_plan.subperiods 不能包含重复 scenario_id")
        for previous, current in zip(subperiods, subperiods[1:]):
            if previous.period.end >= current.period.start:
                raise FactorResearchError("robustness_plan 子样本区间必须严格不重叠")
        if not any(item.excluded_symbols for item in subperiods):
            raise FactorResearchError("robustness_plan 必须包含至少一个显式品种剔除情景")
        variants = tuple(self.parameter_variants)
        if not variants or not all(type(item) is FactorRobustnessParameterVariant for item in variants):
            raise FactorResearchError(
                "robustness_plan.parameter_variants 必须包含 FactorRobustnessParameterVariant"
            )
        if tuple(sorted(variants, key=lambda item: item.variant_id)) != variants:
            raise FactorResearchError("robustness_plan.parameter_variants 必须按 variant_id 排序")
        if len({item.variant_id for item in variants}) != len(variants):
            raise FactorResearchError("robustness_plan.parameter_variants 不能包含重复 variant_id")
        if len({(item.factor_id, item.parameters_json) for item in variants}) != len(variants):
            raise FactorResearchError("robustness_plan.parameter_variants 不能包含重复参数邻域")
        costs = tuple(self.cost_scenarios)
        if len(costs) < 2 or not all(type(item) is FactorRobustnessCostScenario for item in costs):
            raise FactorResearchError(
                "robustness_plan.cost_scenarios 必须至少包含两个 FactorRobustnessCostScenario"
            )
        if tuple(sorted(costs, key=lambda item: item.scenario_id)) != costs:
            raise FactorResearchError("robustness_plan.cost_scenarios 必须按 scenario_id 排序")
        if len({item.scenario_id for item in costs}) != len(costs):
            raise FactorResearchError("robustness_plan.cost_scenarios 不能包含重复 scenario_id")
        if {item.scenario_id for item in costs} != {"adverse", "baseline"}:
            raise FactorResearchError(
                "robustness_plan.cost_scenarios 必须精确包含 adverse 与 baseline"
            )
        if type(self.stability_thresholds) is not FactorStabilityThresholds:
            raise FactorResearchError(
                "robustness_plan.stability_thresholds 必须是精确的 FactorStabilityThresholds"
            )
        plan_hash = canonical_json_sha256(
            {
                "cost_scenario_hashes": [item.scenario_hash for item in costs],
                "format": "northstar.factor-robustness-plan.v1",
                "parameter_variant_hashes": [item.variant_hash for item in variants],
                "plan_id": plan_id,
                "stability_thresholds_hash": self.stability_thresholds.thresholds_hash,
                "subperiod_hashes": [item.scenario_hash for item in subperiods],
                "version": version,
            }
        )
        object.__setattr__(self, "plan_id", plan_id)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "subperiods", subperiods)
        object.__setattr__(self, "parameter_variants", variants)
        object.__setattr__(self, "cost_scenarios", costs)
        object.__setattr__(self, "plan_hash", plan_hash)

    @property
    def baseline_cost_scenario(self) -> FactorRobustnessCostScenario:
        return next(item for item in self.cost_scenarios if item.scenario_id == "baseline")


def _validate_robustness_plan_for_config(
    plan: FactorRobustnessPlan,
    *,
    factors: tuple[FactorDefinition, ...],
    commission_bps: float,
    min_commission: float,
    slippage_bps: float,
    execution_delay_sessions: int,
) -> None:
    """Bind a generic frozen plan to one exact alpha configuration.

    Parameter neighbours are intentionally checked here rather than in the
    plan constructor, because only a concrete pipeline can establish which
    factors are alpha factors and which parameter names are immutable.
    """

    baseline = plan.baseline_cost_scenario
    if (
        baseline.commission_bps != commission_bps
        or baseline.min_commission != min_commission
        or baseline.slippage_bps != slippage_bps
        or baseline.execution_delay_sessions != execution_delay_sessions
    ):
        raise FactorResearchError(
            "robustness_plan baseline cost scenario 必须与 pipeline 成本配置精确一致"
        )
    alpha_by_id = {item.factor_id: item for item in factors if item.role is FactorRole.ALPHA}
    variant_factor_ids = {item.factor_id for item in plan.parameter_variants}
    if variant_factor_ids != set(alpha_by_id):
        raise FactorResearchError(
            "robustness_plan.parameter_variants 必须为每个 alpha factor 声明参数邻域"
        )
    for variant in plan.parameter_variants:
        base = alpha_by_id.get(variant.factor_id)
        if base is None:  # Defensive: the exact-set check above should catch this first.
            raise FactorResearchError("robustness parameter variant 引用了未知 alpha factor")
        if set(variant.parameters) != set(base.parameters):
            raise FactorResearchError(
                "robustness parameter variant 只能变更原 alpha factor 的既有参数"
            )
    for factor_id in alpha_by_id:
        parameter_points = {
            item.parameters_json
            for item in plan.parameter_variants
            if item.factor_id == factor_id
        }
        if len(parameter_points) < 2:
            raise FactorResearchError(
                "robustness_plan 必须为每个 alpha factor 包含至少两个不同的参数邻域点"
            )


@dataclass(frozen=True, slots=True)
class FactorPipelineConfig:
    """一个显式、冻结且只用于离线研究的连续日线因子配置。"""

    pipeline_id: str
    version: str
    feature_version: str
    code_revision: str
    factors: tuple[FactorDefinition, ...]
    volatility_factor_id: str
    min_cross_section: int
    quantile_count: int
    target_volatility: float
    max_abs_weight: float
    max_gross_exposure: float
    holding_period_sessions: int
    initial_cash: float
    commission_bps: float
    min_commission: float
    slippage_bps: float
    execution_delay_sessions: int
    walk_forward_folds: tuple[WalkForwardFold, ...]
    robustness_plan: FactorRobustnessPlan
    config_hash: str = field(init=False)

    def __post_init__(self) -> None:
        pipeline_id = _identifier(self.pipeline_id, "pipeline_id")
        version = _version(self.version, "version")
        feature_version = _version(self.feature_version, "feature_version")
        if not isinstance(self.code_revision, str) or not self.code_revision.strip() or "\n" in self.code_revision:
            raise FactorResearchError("code_revision 必须是非空单行文本")
        factors = tuple(self.factors)
        if len(factors) < 2 or not all(isinstance(item, FactorDefinition) for item in factors):
            raise FactorResearchError("factors 必须至少包含一个 alpha 和一个 risk_model factor")
        if tuple(sorted(factors, key=lambda item: item.factor_id)) != factors:
            raise FactorResearchError("factors 必须按 factor_id 升序排列")
        if len({item.factor_id for item in factors}) != len(factors):
            raise FactorResearchError("factors 不能包含重复 factor_id")
        alpha_factors = tuple(item for item in factors if item.role is FactorRole.ALPHA)
        risk_factors = tuple(item for item in factors if item.role is FactorRole.RISK_MODEL)
        if not alpha_factors:
            raise FactorResearchError("factors 必须至少包含一个 alpha factor")
        if len(risk_factors) != 1:
            raise FactorResearchError("factors 必须精确包含一个 risk_model factor")
        volatility_factor_id = _identifier(self.volatility_factor_id, "volatility_factor_id")
        if risk_factors[0].factor_id != volatility_factor_id:
            raise FactorResearchError("volatility_factor_id 必须指向唯一 risk_model factor")
        if not math.isclose(sum(item.risk_budget for item in alpha_factors), 1.0, abs_tol=1e-12):
            raise FactorResearchError("alpha factor risk_budget 之和必须精确为 1")
        min_cross_section = _positive_int(self.min_cross_section, "min_cross_section", minimum=2)
        quantile_count = _positive_int(self.quantile_count, "quantile_count", minimum=2)
        if quantile_count > min_cross_section:
            raise FactorResearchError("quantile_count 不能大于 min_cross_section")
        if min_cross_section < len(alpha_factors) + 1:
            raise FactorResearchError("min_cross_section 必须足以形成 alpha 横截面排名")
        target_volatility = _number(self.target_volatility, "target_volatility", minimum=1e-12)
        max_abs_weight = _number(self.max_abs_weight, "max_abs_weight", minimum=1e-12, maximum=1.0)
        max_gross_exposure = _number(
            self.max_gross_exposure,
            "max_gross_exposure",
            minimum=max_abs_weight,
            maximum=1.0,
        )
        holding_period_sessions = _positive_int(
            self.holding_period_sessions,
            "holding_period_sessions",
        )
        initial_cash = _number(self.initial_cash, "initial_cash", minimum=1e-12)
        commission_bps = _number(self.commission_bps, "commission_bps", minimum=0.0)
        min_commission = _number(self.min_commission, "min_commission", minimum=0.0)
        slippage_bps = _number(self.slippage_bps, "slippage_bps", minimum=0.0)
        execution_delay_sessions = _positive_int(
            self.execution_delay_sessions,
            "execution_delay_sessions",
        )
        folds = tuple(self.walk_forward_folds)
        if len(folds) < 2 or not all(isinstance(item, WalkForwardFold) for item in folds):
            raise FactorResearchError("walk_forward_folds 必须至少包含两个 WalkForwardFold")
        if tuple(sorted(folds, key=lambda item: item.fold_id)) != folds:
            raise FactorResearchError("walk_forward_folds 必须按 fold_id 升序排列")
        if len({item.fold_id for item in folds}) != len(folds):
            raise FactorResearchError("walk_forward_folds 不能包含重复 fold_id")
        oos_periods = tuple(item.split.out_of_sample for item in folds)
        for previous, current in zip(oos_periods, oos_periods[1:]):
            if previous.end >= current.start:
                raise FactorResearchError("walk_forward_folds 的 OOS 区间必须严格不重叠")
        if type(self.robustness_plan) is not FactorRobustnessPlan:
            raise FactorResearchError("robustness_plan 必须是精确的 FactorRobustnessPlan")
        _validate_robustness_plan_for_config(
            self.robustness_plan,
            factors=factors,
            commission_bps=commission_bps,
            min_commission=min_commission,
            slippage_bps=slippage_bps,
            execution_delay_sessions=execution_delay_sessions,
        )
        config_hash = canonical_json_sha256(
            {
                "code_revision": self.code_revision.strip(),
                "commission_bps": commission_bps.hex(),
                "execution_delay_sessions": execution_delay_sessions,
                "factors": [item.definition_hash for item in factors],
                "feature_version": feature_version,
                "format": "northstar.factor-pipeline-config.v1",
                "holding_period_sessions": holding_period_sessions,
                "initial_cash": initial_cash.hex(),
                "max_abs_weight": max_abs_weight.hex(),
                "max_gross_exposure": max_gross_exposure.hex(),
                "min_commission": min_commission.hex(),
                "min_cross_section": min_cross_section,
                "pipeline_id": pipeline_id,
                "quantile_count": quantile_count,
                "robustness_plan_hash": self.robustness_plan.plan_hash,
                "slippage_bps": slippage_bps.hex(),
                "target_volatility": target_volatility.hex(),
                "version": version,
                "volatility_factor_id": volatility_factor_id,
                "walk_forward_fold_hashes": [item.fold_hash for item in folds],
            }
        )
        object.__setattr__(self, "pipeline_id", pipeline_id)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "feature_version", feature_version)
        object.__setattr__(self, "code_revision", self.code_revision.strip())
        object.__setattr__(self, "factors", factors)
        object.__setattr__(self, "volatility_factor_id", volatility_factor_id)
        object.__setattr__(self, "min_cross_section", min_cross_section)
        object.__setattr__(self, "quantile_count", quantile_count)
        object.__setattr__(self, "target_volatility", target_volatility)
        object.__setattr__(self, "max_abs_weight", max_abs_weight)
        object.__setattr__(self, "max_gross_exposure", max_gross_exposure)
        object.__setattr__(self, "holding_period_sessions", holding_period_sessions)
        object.__setattr__(self, "initial_cash", initial_cash)
        object.__setattr__(self, "commission_bps", commission_bps)
        object.__setattr__(self, "min_commission", min_commission)
        object.__setattr__(self, "slippage_bps", slippage_bps)
        object.__setattr__(self, "execution_delay_sessions", execution_delay_sessions)
        object.__setattr__(self, "walk_forward_folds", folds)
        object.__setattr__(self, "config_hash", config_hash)

    @property
    def alpha_factors(self) -> tuple[FactorDefinition, ...]:
        return tuple(item for item in self.factors if item.role is FactorRole.ALPHA)

    @property
    def volatility_factor(self) -> FactorDefinition:
        return next(item for item in self.factors if item.factor_id == self.volatility_factor_id)

    def with_parameter_variant(
        self,
        variant: FactorRobustnessParameterVariant,
    ) -> "FactorPipelineConfig":
        """Return one bounded neighbour without changing the frozen study plan.

        The method intentionally exposes no generic field mutation: robustness
        may vary only parameters for the declared alpha factor.  Feature,
        direction, role and risk budget remain identical.
        """

        if type(variant) is not FactorRobustnessParameterVariant:
            raise FactorResearchError(
                "parameter variant 必须是精确的 FactorRobustnessParameterVariant"
            )
        if variant not in self.robustness_plan.parameter_variants:
            raise FactorResearchError("parameter variant 不属于该 frozen robustness plan")
        base = next((item for item in self.alpha_factors if item.factor_id == variant.factor_id), None)
        if base is None:
            raise FactorResearchError("parameter variant 只能引用 alpha factor")
        replacement = FactorDefinition.create(
            factor_id=base.factor_id,
            feature_id=base.feature_id,
            role=base.role,
            direction=base.direction,
            risk_budget=base.risk_budget,
            parameters=variant.parameters,
        )
        factors = tuple(
            replacement if item.factor_id == base.factor_id else item for item in self.factors
        )
        return FactorPipelineConfig(
            pipeline_id=self.pipeline_id,
            version=self.version,
            feature_version=self.feature_version,
            code_revision=self.code_revision,
            factors=factors,
            volatility_factor_id=self.volatility_factor_id,
            min_cross_section=self.min_cross_section,
            quantile_count=self.quantile_count,
            target_volatility=self.target_volatility,
            max_abs_weight=self.max_abs_weight,
            max_gross_exposure=self.max_gross_exposure,
            holding_period_sessions=self.holding_period_sessions,
            initial_cash=self.initial_cash,
            commission_bps=self.commission_bps,
            min_commission=self.min_commission,
            slippage_bps=self.slippage_bps,
            execution_delay_sessions=self.execution_delay_sessions,
            walk_forward_folds=self.walk_forward_folds,
            robustness_plan=self.robustness_plan,
        )


@dataclass(frozen=True, slots=True)
class FactorMaterializationReference:
    """一个 checkpoint 中严格 Feature materialization 的 hash-only 绑定。"""

    factor_id: str
    factor_definition_hash: str
    feature_version_hash: str
    materialization_hash: str
    reference_hash: str = field(init=False)

    def __post_init__(self) -> None:
        factor_id = _identifier(self.factor_id, "materialization.factor_id")
        factor_definition_hash = _hash(
            self.factor_definition_hash,
            "materialization.factor_definition_hash",
        )
        feature_version_hash = _hash(self.feature_version_hash, "materialization.feature_version_hash")
        materialization_hash = _hash(self.materialization_hash, "materialization.materialization_hash")
        reference_hash = canonical_json_sha256(
            {
                "factor_id": factor_id,
                "factor_definition_hash": factor_definition_hash,
                "feature_version_hash": feature_version_hash,
                "format": "northstar.factor-materialization-reference.v1",
                "materialization_hash": materialization_hash,
            }
        )
        object.__setattr__(self, "factor_id", factor_id)
        object.__setattr__(self, "factor_definition_hash", factor_definition_hash)
        object.__setattr__(self, "feature_version_hash", feature_version_hash)
        object.__setattr__(self, "materialization_hash", materialization_hash)
        object.__setattr__(self, "reference_hash", reference_hash)


@dataclass(frozen=True, slots=True)
class FactorExposure:
    """一个已在决策时点可见的单标的因子暴露。"""

    checkpoint_hash: str
    decision_at: datetime
    decision_session: date
    snapshot_id: str
    factor_id: str
    factor_definition_hash: str
    config_hash: str
    materialization_hash: str
    symbol: str
    value: float
    exposure_hash: str = field(init=False)

    def __post_init__(self) -> None:
        checkpoint_hash = _hash(self.checkpoint_hash, "exposure.checkpoint_hash")
        decision_at = _utc(self.decision_at, "exposure.decision_at")
        decision_session = _session(self.decision_session, "exposure.decision_session")
        if decision_session > decision_at.date():
            raise FactorResearchError("exposure.decision_session 不能晚于 decision_at")
        snapshot_id = _hash(self.snapshot_id, "exposure.snapshot_id")
        factor_id = _identifier(self.factor_id, "exposure.factor_id")
        factor_definition_hash = _hash(
            self.factor_definition_hash,
            "exposure.factor_definition_hash",
        )
        config_hash = _hash(self.config_hash, "exposure.config_hash")
        materialization_hash = _hash(self.materialization_hash, "exposure.materialization_hash")
        if not isinstance(self.symbol, str) or _SYMBOL_RE.fullmatch(self.symbol.strip().upper()) is None:
            raise FactorResearchError("exposure.symbol 必须是规范大写研究标的")
        symbol = self.symbol.strip().upper()
        numeric_value = _number(self.value, "exposure.value")
        exposure_hash = canonical_json_sha256(
            {
                "checkpoint_hash": checkpoint_hash,
                "decision_at": decision_at.isoformat(),
                "decision_session": decision_session.isoformat(),
                "factor_id": factor_id,
                "factor_definition_hash": factor_definition_hash,
                "config_hash": config_hash,
                "format": "northstar.factor-exposure.v1",
                "materialization_hash": materialization_hash,
                "snapshot_id": snapshot_id,
                "symbol": symbol,
                "value": numeric_value.hex(),
            }
        )
        object.__setattr__(self, "checkpoint_hash", checkpoint_hash)
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "decision_session", decision_session)
        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "factor_id", factor_id)
        object.__setattr__(self, "factor_definition_hash", factor_definition_hash)
        object.__setattr__(self, "config_hash", config_hash)
        object.__setattr__(self, "materialization_hash", materialization_hash)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "value", numeric_value)
        object.__setattr__(self, "exposure_hash", exposure_hash)


@dataclass(frozen=True, slots=True)
class FactorMarketSlice:
    """同一 PIT checkpoint 当期收盘价的冻结研究输入，不是实时行情。"""

    checkpoint_hash: str
    decision_session: date
    snapshot_id: str
    symbol: str
    close: float
    slice_hash: str = field(init=False)

    def __post_init__(self) -> None:
        checkpoint_hash = _hash(self.checkpoint_hash, "market_slice.checkpoint_hash")
        session = _session(self.decision_session, "market_slice.decision_session")
        snapshot_id = _hash(self.snapshot_id, "market_slice.snapshot_id")
        if not isinstance(self.symbol, str) or _SYMBOL_RE.fullmatch(self.symbol.strip().upper()) is None:
            raise FactorResearchError("market_slice.symbol 必须是规范大写研究标的")
        symbol = self.symbol.strip().upper()
        close = _number(self.close, "market_slice.close", minimum=1e-12)
        slice_hash = canonical_json_sha256(
            {
                "checkpoint_hash": checkpoint_hash,
                "close": close.hex(),
                "decision_session": session.isoformat(),
                "format": "northstar.factor-market-slice.v1",
                "snapshot_id": snapshot_id,
                "symbol": symbol,
            }
        )
        object.__setattr__(self, "checkpoint_hash", checkpoint_hash)
        object.__setattr__(self, "decision_session", session)
        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "close", close)
        object.__setattr__(self, "slice_hash", slice_hash)


@dataclass(frozen=True, slots=True)
class FactorCheckpointData:
    """一个决策 checkpoint 的可审计因子输入/暴露集合。"""

    checkpoint_hash: str
    decision_at: datetime
    decision_session: date
    market_evidence_hash: str
    snapshot_id: str
    dataset_version_hash: str
    config_hash: str
    materializations: tuple[FactorMaterializationReference, ...]
    exposures: tuple[FactorExposure, ...]
    market_slices: tuple[FactorMarketSlice, ...]
    checkpoint_data_hash: str = field(init=False)

    def __post_init__(self) -> None:
        checkpoint_hash = _hash(self.checkpoint_hash, "checkpoint_data.checkpoint_hash")
        decision_at = _utc(self.decision_at, "checkpoint_data.decision_at")
        decision_session = _session(self.decision_session, "checkpoint_data.decision_session")
        if decision_session > decision_at.date():
            raise FactorResearchError("checkpoint_data.decision_session 不能晚于 decision_at")
        market_evidence_hash = _hash(self.market_evidence_hash, "checkpoint_data.market_evidence_hash")
        snapshot_id = _hash(self.snapshot_id, "checkpoint_data.snapshot_id")
        dataset_version_hash = _hash(
            self.dataset_version_hash,
            "checkpoint_data.dataset_version_hash",
        )
        config_hash = _hash(self.config_hash, "checkpoint_data.config_hash")
        materializations = tuple(self.materializations)
        exposures = tuple(self.exposures)
        market_slices = tuple(self.market_slices)
        if not materializations or not all(isinstance(item, FactorMaterializationReference) for item in materializations):
            raise FactorResearchError("checkpoint_data.materializations 必须非空")
        if tuple(sorted(materializations, key=lambda item: item.factor_id)) != materializations:
            raise FactorResearchError("checkpoint_data.materializations 必须按 factor_id 排序")
        if len({item.factor_id for item in materializations}) != len(materializations):
            raise FactorResearchError("checkpoint_data.materializations 不能重复 factor_id")
        materialization_by_factor = {item.factor_id: item for item in materializations}
        if not all(isinstance(item, FactorExposure) for item in exposures):
            raise FactorResearchError("checkpoint_data.exposures 类型无效")
        if any(
            item.checkpoint_hash != checkpoint_hash
            or item.decision_at != decision_at
            or item.decision_session != decision_session
            or item.snapshot_id != snapshot_id
            or item.config_hash != config_hash
            for item in exposures
        ):
            raise FactorResearchError("checkpoint_data.exposures 必须精确绑定 checkpoint/snapshot")
        if any(
            (reference := materialization_by_factor.get(item.factor_id)) is None
            or item.factor_definition_hash != reference.factor_definition_hash
            or item.materialization_hash != reference.materialization_hash
            for item in exposures
        ):
            raise FactorResearchError(
                "checkpoint_data.exposures 必须精确绑定 factor definition/materialization"
            )
        if not all(isinstance(item, FactorMarketSlice) for item in market_slices):
            raise FactorResearchError("checkpoint_data.market_slices 类型无效")
        if any(
            item.checkpoint_hash != checkpoint_hash
            or item.decision_session != decision_session
            or item.snapshot_id != snapshot_id
            for item in market_slices
        ):
            raise FactorResearchError("checkpoint_data.market_slices 必须精确绑定 checkpoint/snapshot")
        if len({item.symbol for item in market_slices}) != len(market_slices):
            raise FactorResearchError("checkpoint_data.market_slices 不能包含重复 symbol")
        checkpoint_data_hash = canonical_json_sha256(
            {
                "checkpoint_hash": checkpoint_hash,
                "config_hash": config_hash,
                "dataset_version_hash": dataset_version_hash,
                "decision_at": decision_at.isoformat(),
                "decision_session": decision_session.isoformat(),
                "exposure_hashes": sorted(item.exposure_hash for item in exposures),
                "format": "northstar.factor-checkpoint-data.v1",
                "market_evidence_hash": market_evidence_hash,
                "market_slice_hashes": sorted(item.slice_hash for item in market_slices),
                "materialization_reference_hashes": [item.reference_hash for item in materializations],
                "snapshot_id": snapshot_id,
            }
        )
        object.__setattr__(self, "checkpoint_hash", checkpoint_hash)
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "decision_session", decision_session)
        object.__setattr__(self, "market_evidence_hash", market_evidence_hash)
        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "dataset_version_hash", dataset_version_hash)
        object.__setattr__(self, "config_hash", config_hash)
        object.__setattr__(self, "materializations", materializations)
        object.__setattr__(self, "exposures", tuple(sorted(exposures, key=lambda item: (item.factor_id, item.symbol))))
        object.__setattr__(self, "market_slices", tuple(sorted(market_slices, key=lambda item: item.symbol)))
        object.__setattr__(self, "checkpoint_data_hash", checkpoint_data_hash)


@dataclass(frozen=True, slots=True)
class FactorPortfolioWeight:
    """研究组合提案中的连续序列目标权重；它不是 PortfolioTarget。"""

    symbol: str
    composite_score: float
    target_weight: float

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or _SYMBOL_RE.fullmatch(self.symbol.strip().upper()) is None:
            raise FactorResearchError("proposal_weight.symbol 必须是规范大写研究标的")
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        object.__setattr__(self, "composite_score", _number(self.composite_score, "proposal_weight.composite_score"))
        object.__setattr__(self, "target_weight", _number(self.target_weight, "proposal_weight.target_weight"))


@dataclass(frozen=True, slots=True)
class FactorPortfolioProposal:
    """风险受限的 research-only 因子组合提案。"""

    checkpoint_hash: str
    decision_at: datetime
    decision_session: date
    snapshot_id: str
    checkpoint_data_hash: str
    config_hash: str
    status: ProposalStatus
    weights: tuple[FactorPortfolioWeight, ...]
    estimated_volatility: float | None
    volatility_scale: float | None
    no_proposal_reason: str | None = None
    proposal_hash: str = field(init=False)

    @property
    def research_only(self) -> bool:
        return True

    @property
    def candidate_admission_eligible(self) -> bool:
        return False

    @property
    def simnow_handoff_allowed(self) -> bool:
        return False

    def __post_init__(self) -> None:
        checkpoint_hash = _hash(self.checkpoint_hash, "proposal.checkpoint_hash")
        decision_at = _utc(self.decision_at, "proposal.decision_at")
        decision_session = _session(self.decision_session, "proposal.decision_session")
        if decision_session > decision_at.date():
            raise FactorResearchError("proposal.decision_session 不能晚于 decision_at")
        snapshot_id = _hash(self.snapshot_id, "proposal.snapshot_id")
        checkpoint_data_hash = _hash(self.checkpoint_data_hash, "proposal.checkpoint_data_hash")
        config_hash = _hash(self.config_hash, "proposal.config_hash")
        if not isinstance(self.status, ProposalStatus):
            raise FactorResearchError("proposal.status 必须是 ProposalStatus")
        weights = tuple(self.weights)
        if not all(isinstance(item, FactorPortfolioWeight) for item in weights):
            raise FactorResearchError("proposal.weights 类型无效")
        if tuple(sorted(weights, key=lambda item: item.symbol)) != weights:
            raise FactorResearchError("proposal.weights 必须按 symbol 排序")
        if len({item.symbol for item in weights}) != len(weights):
            raise FactorResearchError("proposal.weights 不能包含重复 symbol")
        if self.status is ProposalStatus.PROPOSAL:
            if not weights:
                raise FactorResearchError("proposal 状态必须至少包含一个权重")
            estimated_volatility = _number(
                self.estimated_volatility,
                "proposal.estimated_volatility",
                minimum=0.0,
            )
            volatility_scale = _number(
                self.volatility_scale,
                "proposal.volatility_scale",
                minimum=0.0,
                maximum=1.0,
            )
            if self.no_proposal_reason is not None:
                raise FactorResearchError("proposal 状态不得设置 no_proposal_reason")
            no_proposal_reason = None
        else:
            if weights or self.estimated_volatility is not None or self.volatility_scale is not None:
                raise FactorResearchError("no_proposal_warmup 不得包含权重或风险估计")
            if not isinstance(self.no_proposal_reason, str) or not self.no_proposal_reason.strip():
                raise FactorResearchError("no_proposal_warmup 必须说明拒绝原因")
            estimated_volatility = None
            volatility_scale = None
            no_proposal_reason = self.no_proposal_reason.strip()
        proposal_hash = canonical_json_sha256(
            {
                "candidate_admission_eligible": False,
                "checkpoint_data_hash": checkpoint_data_hash,
                "checkpoint_hash": checkpoint_hash,
                "config_hash": config_hash,
                "decision_at": decision_at.isoformat(),
                "decision_session": decision_session.isoformat(),
                "estimated_volatility": (
                    estimated_volatility.hex() if estimated_volatility is not None else None
                ),
                "format": "northstar.factor-portfolio-proposal.v1",
                "no_proposal_reason": no_proposal_reason,
                "research_only": True,
                "simnow_handoff_allowed": False,
                "snapshot_id": snapshot_id,
                "status": self.status.value,
                "volatility_scale": volatility_scale.hex() if volatility_scale is not None else None,
                "weights": [
                    {
                        "composite_score": item.composite_score.hex(),
                        "symbol": item.symbol,
                        "target_weight": item.target_weight.hex(),
                    }
                    for item in weights
                ],
            }
        )
        object.__setattr__(self, "checkpoint_hash", checkpoint_hash)
        object.__setattr__(self, "decision_at", decision_at)
        object.__setattr__(self, "decision_session", decision_session)
        object.__setattr__(self, "snapshot_id", snapshot_id)
        object.__setattr__(self, "checkpoint_data_hash", checkpoint_data_hash)
        object.__setattr__(self, "config_hash", config_hash)
        object.__setattr__(self, "weights", weights)
        object.__setattr__(self, "estimated_volatility", estimated_volatility)
        object.__setattr__(self, "volatility_scale", volatility_scale)
        object.__setattr__(self, "no_proposal_reason", no_proposal_reason)
        object.__setattr__(self, "proposal_hash", proposal_hash)


@dataclass(frozen=True, slots=True)
class FactorForwardOutcome:
    """决策完成后才产生的 ex-post 收益结果，不能回灌至同一期因子或提案。"""

    origin_checkpoint_hash: str
    decision_session: date
    evaluation_checkpoint_hash: str
    evaluation_session: date
    evaluation_at: datetime
    symbol: str
    forward_return: float
    outcome_hash: str = field(init=False)

    def __post_init__(self) -> None:
        origin_checkpoint_hash = _hash(self.origin_checkpoint_hash, "outcome.origin_checkpoint_hash")
        decision_session = _session(self.decision_session, "outcome.decision_session")
        evaluation_checkpoint_hash = _hash(
            self.evaluation_checkpoint_hash,
            "outcome.evaluation_checkpoint_hash",
        )
        evaluation_session = _session(self.evaluation_session, "outcome.evaluation_session")
        if evaluation_session <= decision_session:
            raise FactorResearchError("outcome.evaluation_session 必须晚于 decision_session")
        evaluation_at = _utc(self.evaluation_at, "outcome.evaluation_at")
        if evaluation_session > evaluation_at.date():
            raise FactorResearchError("outcome.evaluation_session 不能晚于 evaluation_at")
        if not isinstance(self.symbol, str) or _SYMBOL_RE.fullmatch(self.symbol.strip().upper()) is None:
            raise FactorResearchError("outcome.symbol 必须是规范大写研究标的")
        symbol = self.symbol.strip().upper()
        forward_return = _number(self.forward_return, "outcome.forward_return")
        if forward_return <= -1.0:
            raise FactorResearchError("outcome.forward_return 必须大于 -1")
        outcome_hash = canonical_json_sha256(
            {
                "decision_session": decision_session.isoformat(),
                "evaluation_at": evaluation_at.isoformat(),
                "evaluation_checkpoint_hash": evaluation_checkpoint_hash,
                "evaluation_session": evaluation_session.isoformat(),
                "format": "northstar.factor-forward-outcome.v1",
                "forward_return": forward_return.hex(),
                "origin_checkpoint_hash": origin_checkpoint_hash,
                "symbol": symbol,
            }
        )
        object.__setattr__(self, "origin_checkpoint_hash", origin_checkpoint_hash)
        object.__setattr__(self, "decision_session", decision_session)
        object.__setattr__(self, "evaluation_checkpoint_hash", evaluation_checkpoint_hash)
        object.__setattr__(self, "evaluation_session", evaluation_session)
        object.__setattr__(self, "evaluation_at", evaluation_at)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "forward_return", forward_return)
        object.__setattr__(self, "outcome_hash", outcome_hash)


@dataclass(frozen=True, slots=True)
class FactorAnalysisPeriod:
    """一个已到期截面的 IC / 分位收益结果。"""

    decision_session: date
    ic: float
    rank_ic: float
    quantile_returns: tuple[tuple[int, float], ...]
    period_hash: str = field(init=False)

    def __post_init__(self) -> None:
        session = _session(self.decision_session, "analysis_period.decision_session")
        ic = _number(self.ic, "analysis_period.ic", minimum=-1.0, maximum=1.0)
        rank_ic = _number(self.rank_ic, "analysis_period.rank_ic", minimum=-1.0, maximum=1.0)
        quantile_returns = tuple(self.quantile_returns)
        if not quantile_returns or any(
            isinstance(bucket, bool)
            or not isinstance(bucket, int)
            or bucket < 1
            or not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            for bucket, value in quantile_returns
        ):
            raise FactorResearchError("analysis_period.quantile_returns 无效")
        if tuple(sorted(quantile_returns)) != quantile_returns or len({bucket for bucket, _ in quantile_returns}) != len(quantile_returns):
            raise FactorResearchError("analysis_period.quantile_returns 必须按 bucket 排序且无重复")
        normalized = tuple((bucket, float(value)) for bucket, value in quantile_returns)
        period_hash = canonical_json_sha256(
            {
                "decision_session": session.isoformat(),
                "format": "northstar.factor-analysis-period.v1",
                "ic": ic.hex(),
                "quantile_returns": [(bucket, value.hex()) for bucket, value in normalized],
                "rank_ic": rank_ic.hex(),
            }
        )
        object.__setattr__(self, "decision_session", session)
        object.__setattr__(self, "ic", ic)
        object.__setattr__(self, "rank_ic", rank_ic)
        object.__setattr__(self, "quantile_returns", normalized)
        object.__setattr__(self, "period_hash", period_hash)


@dataclass(frozen=True, slots=True)
class FactorAnalysisResult:
    """因子统计分析；该结果始终是研究诊断，而不是调仓规则。"""

    factor_id: str
    quantile_count: int
    periods: tuple[FactorAnalysisPeriod, ...]
    mean_turnover: float
    analysis_hash: str = field(init=False)

    def __post_init__(self) -> None:
        factor_id = _identifier(self.factor_id, "analysis.factor_id")
        quantile_count = _positive_int(self.quantile_count, "analysis.quantile_count", minimum=2)
        periods = tuple(self.periods)
        if not periods or not all(isinstance(item, FactorAnalysisPeriod) for item in periods):
            raise FactorResearchError("analysis.periods 必须非空")
        if tuple(sorted(periods, key=lambda item: item.decision_session)) != periods:
            raise FactorResearchError("analysis.periods 必须按 decision_session 排序")
        if len({item.decision_session for item in periods}) != len(periods):
            raise FactorResearchError("analysis.periods 不能包含重复 session")
        expected_buckets = tuple(range(1, quantile_count + 1))
        if any(tuple(bucket for bucket, _ in item.quantile_returns) != expected_buckets for item in periods):
            raise FactorResearchError("analysis.periods 的 quantile bucket 必须完整一致")
        turnover = _number(self.mean_turnover, "analysis.mean_turnover", minimum=0.0)
        analysis_hash = canonical_json_sha256(
            {
                "factor_id": factor_id,
                "format": "northstar.factor-analysis-result.v1",
                "mean_turnover": turnover.hex(),
                "period_hashes": [item.period_hash for item in periods],
                "quantile_count": quantile_count,
            }
        )
        object.__setattr__(self, "factor_id", factor_id)
        object.__setattr__(self, "quantile_count", quantile_count)
        object.__setattr__(self, "periods", periods)
        object.__setattr__(self, "mean_turnover", turnover)
        object.__setattr__(self, "analysis_hash", analysis_hash)

    @property
    def mean_ic(self) -> float:
        return sum(item.ic for item in self.periods) / len(self.periods)

    @property
    def mean_rank_ic(self) -> float:
        return sum(item.rank_ic for item in self.periods) / len(self.periods)

    @property
    def quantile_spread(self) -> float:
        return sum(
            item.quantile_returns[-1][1] - item.quantile_returns[0][1] for item in self.periods
        ) / len(self.periods)

    @property
    def positive_ic_fraction(self) -> float:
        return sum(1 for item in self.periods if item.ic > 0) / len(self.periods)

    @property
    def ic_standard_deviation(self) -> float:
        if len(self.periods) == 1:
            return 0.0
        mean = self.mean_ic
        return math.sqrt(sum((item.ic - mean) ** 2 for item in self.periods) / (len(self.periods) - 1))


@dataclass(frozen=True, slots=True)
class FactorRobustnessScenarioResult:
    """Post-maturity factor evidence for one predeclared subperiod scenario."""

    scenario_id: str
    factor_id: str
    analysis_hash: str
    analysis_period_count: int
    mean_rank_ic: float
    positive_ic_fraction: float
    quantile_spread: float
    ic_standard_deviation: float
    mean_turnover: float
    passed: bool
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        scenario_id = _identifier(self.scenario_id, "robustness_scenario_result.scenario_id")
        factor_id = _identifier(self.factor_id, "robustness_scenario_result.factor_id")
        analysis_hash = _hash(self.analysis_hash, "robustness_scenario_result.analysis_hash")
        analysis_period_count = _positive_int(
            self.analysis_period_count,
            "robustness_scenario_result.analysis_period_count",
        )
        mean_rank_ic = _number(
            self.mean_rank_ic,
            "robustness_scenario_result.mean_rank_ic",
            minimum=-1.0,
            maximum=1.0,
        )
        positive_ic_fraction = _number(
            self.positive_ic_fraction,
            "robustness_scenario_result.positive_ic_fraction",
            minimum=0.0,
            maximum=1.0,
        )
        quantile_spread = _number(
            self.quantile_spread,
            "robustness_scenario_result.quantile_spread",
        )
        ic_standard_deviation = _number(
            self.ic_standard_deviation,
            "robustness_scenario_result.ic_standard_deviation",
            minimum=0.0,
        )
        mean_turnover = _number(
            self.mean_turnover,
            "robustness_scenario_result.mean_turnover",
            minimum=0.0,
        )
        if type(self.passed) is not bool:
            raise FactorResearchError("robustness_scenario_result.passed 必须是 bool")
        result_hash = canonical_json_sha256(
            {
                "analysis_hash": analysis_hash,
                "analysis_period_count": analysis_period_count,
                "factor_id": factor_id,
                "format": "northstar.factor-robustness-scenario-result.v1",
                "ic_standard_deviation": ic_standard_deviation.hex(),
                "mean_rank_ic": mean_rank_ic.hex(),
                "mean_turnover": mean_turnover.hex(),
                "passed": self.passed,
                "positive_ic_fraction": positive_ic_fraction.hex(),
                "quantile_spread": quantile_spread.hex(),
                "scenario_id": scenario_id,
            }
        )
        object.__setattr__(self, "scenario_id", scenario_id)
        object.__setattr__(self, "factor_id", factor_id)
        object.__setattr__(self, "analysis_hash", analysis_hash)
        object.__setattr__(self, "analysis_period_count", analysis_period_count)
        object.__setattr__(self, "mean_rank_ic", mean_rank_ic)
        object.__setattr__(self, "positive_ic_fraction", positive_ic_fraction)
        object.__setattr__(self, "quantile_spread", quantile_spread)
        object.__setattr__(self, "ic_standard_deviation", ic_standard_deviation)
        object.__setattr__(self, "mean_turnover", mean_turnover)
        object.__setattr__(self, "result_hash", result_hash)


@dataclass(frozen=True, slots=True)
class FactorRobustnessParameterVariantResult:
    """Full re-run evidence for one frozen one-factor parameter neighbour."""

    variant_id: str
    variant_hash: str
    factor_id: str
    config_hash: str
    analysis_hash: str
    analysis_period_count: int
    mean_rank_ic: float
    positive_ic_fraction: float
    quantile_spread: float
    ic_standard_deviation: float
    mean_turnover: float
    passed: bool
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        variant_id = _identifier(self.variant_id, "robustness_variant_result.variant_id")
        variant_hash = _hash(self.variant_hash, "robustness_variant_result.variant_hash")
        factor_id = _identifier(self.factor_id, "robustness_variant_result.factor_id")
        config_hash = _hash(self.config_hash, "robustness_variant_result.config_hash")
        analysis_hash = _hash(self.analysis_hash, "robustness_variant_result.analysis_hash")
        analysis_period_count = _positive_int(
            self.analysis_period_count,
            "robustness_variant_result.analysis_period_count",
        )
        mean_rank_ic = _number(
            self.mean_rank_ic,
            "robustness_variant_result.mean_rank_ic",
            minimum=-1.0,
            maximum=1.0,
        )
        positive_ic_fraction = _number(
            self.positive_ic_fraction,
            "robustness_variant_result.positive_ic_fraction",
            minimum=0.0,
            maximum=1.0,
        )
        quantile_spread = _number(self.quantile_spread, "robustness_variant_result.quantile_spread")
        ic_standard_deviation = _number(
            self.ic_standard_deviation,
            "robustness_variant_result.ic_standard_deviation",
            minimum=0.0,
        )
        mean_turnover = _number(
            self.mean_turnover,
            "robustness_variant_result.mean_turnover",
            minimum=0.0,
        )
        if type(self.passed) is not bool:
            raise FactorResearchError("robustness_variant_result.passed 必须是 bool")
        result_hash = canonical_json_sha256(
            {
                "analysis_hash": analysis_hash,
                "analysis_period_count": analysis_period_count,
                "config_hash": config_hash,
                "factor_id": factor_id,
                "format": "northstar.factor-robustness-parameter-variant-result.v1",
                "ic_standard_deviation": ic_standard_deviation.hex(),
                "mean_rank_ic": mean_rank_ic.hex(),
                "mean_turnover": mean_turnover.hex(),
                "passed": self.passed,
                "positive_ic_fraction": positive_ic_fraction.hex(),
                "quantile_spread": quantile_spread.hex(),
                "variant_id": variant_id,
                "variant_hash": variant_hash,
            }
        )
        object.__setattr__(self, "variant_id", variant_id)
        object.__setattr__(self, "variant_hash", variant_hash)
        object.__setattr__(self, "factor_id", factor_id)
        object.__setattr__(self, "config_hash", config_hash)
        object.__setattr__(self, "analysis_hash", analysis_hash)
        object.__setattr__(self, "analysis_period_count", analysis_period_count)
        object.__setattr__(self, "mean_rank_ic", mean_rank_ic)
        object.__setattr__(self, "positive_ic_fraction", positive_ic_fraction)
        object.__setattr__(self, "quantile_spread", quantile_spread)
        object.__setattr__(self, "ic_standard_deviation", ic_standard_deviation)
        object.__setattr__(self, "mean_turnover", mean_turnover)
        object.__setattr__(self, "result_hash", result_hash)


@dataclass(frozen=True, slots=True)
class FactorRobustnessCostScenarioResult:
    """Continuous research backtest result for one frozen cost scenario."""

    scenario_id: str
    scenario_hash: str
    backtest_result_hash: str
    total_return: float
    max_drawdown: float
    passed: bool
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        scenario_id = _identifier(self.scenario_id, "robustness_cost_result.scenario_id")
        scenario_hash = _hash(self.scenario_hash, "robustness_cost_result.scenario_hash")
        backtest_result_hash = _hash(
            self.backtest_result_hash,
            "robustness_cost_result.backtest_result_hash",
        )
        total_return = _number(self.total_return, "robustness_cost_result.total_return")
        max_drawdown = _number(
            self.max_drawdown,
            "robustness_cost_result.max_drawdown",
            minimum=-1.0,
            maximum=0.0,
        )
        if type(self.passed) is not bool:
            raise FactorResearchError("robustness_cost_result.passed 必须是 bool")
        result_hash = canonical_json_sha256(
            {
                "backtest_result_hash": backtest_result_hash,
                "format": "northstar.factor-robustness-cost-scenario-result.v1",
                "max_drawdown": max_drawdown.hex(),
                "passed": self.passed,
                "scenario_hash": scenario_hash,
                "scenario_id": scenario_id,
                "total_return": total_return.hex(),
            }
        )
        object.__setattr__(self, "scenario_id", scenario_id)
        object.__setattr__(self, "scenario_hash", scenario_hash)
        object.__setattr__(self, "backtest_result_hash", backtest_result_hash)
        object.__setattr__(self, "total_return", total_return)
        object.__setattr__(self, "max_drawdown", max_drawdown)
        object.__setattr__(self, "result_hash", result_hash)


@dataclass(frozen=True, slots=True)
class FactorRobustnessFactorSummary:
    """Aggregate stability gate result for one alpha across frozen subperiods."""

    factor_id: str
    scenario_count: int
    passed_scenario_count: int
    pass_fraction: float
    passed: bool
    summary_hash: str = field(init=False)

    def __post_init__(self) -> None:
        factor_id = _identifier(self.factor_id, "robustness_summary.factor_id")
        scenario_count = _positive_int(self.scenario_count, "robustness_summary.scenario_count")
        if (
            isinstance(self.passed_scenario_count, bool)
            or not isinstance(self.passed_scenario_count, int)
            or not 0 <= self.passed_scenario_count <= scenario_count
        ):
            raise FactorResearchError(
                "robustness_summary.passed_scenario_count 必须在 scenario_count 范围内"
            )
        pass_fraction = _number(
            self.pass_fraction,
            "robustness_summary.pass_fraction",
            minimum=0.0,
            maximum=1.0,
        )
        if not math.isclose(pass_fraction, self.passed_scenario_count / scenario_count, abs_tol=1e-12):
            raise FactorResearchError("robustness_summary.pass_fraction 与计数不一致")
        if type(self.passed) is not bool:
            raise FactorResearchError("robustness_summary.passed 必须是 bool")
        summary_hash = canonical_json_sha256(
            {
                "factor_id": factor_id,
                "format": "northstar.factor-robustness-factor-summary.v1",
                "pass_fraction": pass_fraction.hex(),
                "passed": self.passed,
                "passed_scenario_count": self.passed_scenario_count,
                "scenario_count": scenario_count,
            }
        )
        object.__setattr__(self, "factor_id", factor_id)
        object.__setattr__(self, "scenario_count", scenario_count)
        object.__setattr__(self, "pass_fraction", pass_fraction)
        object.__setattr__(self, "summary_hash", summary_hash)


@dataclass(frozen=True, slots=True)
class FactorRobustnessResult:
    """Hash-addressed result of every frozen robustness axis for one run."""

    plan: FactorRobustnessPlan
    config: FactorPipelineConfig
    experiment: FactorResearchExperiment
    checkpoint_data_hashes: tuple[str, ...]
    proposal_hashes: tuple[str, ...]
    outcome_hashes: tuple[str, ...]
    scenario_results: tuple[FactorRobustnessScenarioResult, ...]
    parameter_variant_results: tuple[FactorRobustnessParameterVariantResult, ...]
    cost_scenario_results: tuple[FactorRobustnessCostScenarioResult, ...]
    factor_summaries: tuple[FactorRobustnessFactorSummary, ...]
    plan_hash: str = field(init=False)
    config_hash: str = field(init=False)
    passed: bool = field(init=False)
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.plan) is not FactorRobustnessPlan:
            raise FactorResearchError("robustness_result.plan 必须是精确的 FactorRobustnessPlan")
        if type(self.config) is not FactorPipelineConfig:
            raise FactorResearchError("robustness_result.config 必须是精确的 FactorPipelineConfig")
        if type(self.experiment) is not FactorResearchExperiment:
            raise FactorResearchError(
                "robustness_result.experiment 必须是精确的 FactorResearchExperiment"
            )
        plan = self.plan
        config = self.config
        experiment = self.experiment
        if config.robustness_plan != plan:
            raise FactorResearchError("robustness_result.plan 必须精确绑定 config.robustness_plan")
        plan_hash = plan.plan_hash
        config_hash = config.config_hash
        if (
            experiment.config_hash != config_hash
            or experiment.code_revision != config.code_revision
        ):
            raise FactorResearchError(
                "robustness_result.experiment 必须精确绑定 config/code revision"
            )
        checkpoint_data_hashes = tuple(
            _hash(item, "robustness_result.checkpoint_data_hash")
            for item in self.checkpoint_data_hashes
        )
        proposal_hashes = tuple(
            _hash(item, "robustness_result.proposal_hash") for item in self.proposal_hashes
        )
        outcome_hashes = tuple(
            _hash(item, "robustness_result.outcome_hash") for item in self.outcome_hashes
        )
        for field_name, values in (
            ("checkpoint_data_hashes", checkpoint_data_hashes),
            ("proposal_hashes", proposal_hashes),
            ("outcome_hashes", outcome_hashes),
        ):
            if not values or len(set(values)) != len(values):
                raise FactorResearchError(
                    f"robustness_result.{field_name} 必须非空且无重复"
                )
        thresholds = plan.stability_thresholds
        scenario_results = tuple(self.scenario_results)
        parameter_results = tuple(self.parameter_variant_results)
        cost_results = tuple(self.cost_scenario_results)
        summaries = tuple(self.factor_summaries)
        if not scenario_results or not all(type(item) is FactorRobustnessScenarioResult for item in scenario_results):
            raise FactorResearchError("robustness_result.scenario_results 必须非空")
        if tuple(sorted(scenario_results, key=lambda item: (item.scenario_id, item.factor_id))) != scenario_results:
            raise FactorResearchError("robustness_result.scenario_results 必须稳定排序")
        if len({(item.scenario_id, item.factor_id) for item in scenario_results}) != len(scenario_results):
            raise FactorResearchError("robustness_result.scenario_results 不能重复")
        expected_scenario_keys = tuple(
            (scenario.scenario_id, definition.factor_id)
            for scenario in plan.subperiods
            for definition in config.alpha_factors
        )
        if tuple((item.scenario_id, item.factor_id) for item in scenario_results) != expected_scenario_keys:
            raise FactorResearchError("robustness_result.scenario_results 必须精确覆盖 frozen subperiod × alpha")
        if any(
            item.passed
            != _robustness_analysis_metrics_pass(
                analysis_period_count=item.analysis_period_count,
                mean_rank_ic=item.mean_rank_ic,
                positive_ic_fraction=item.positive_ic_fraction,
                quantile_spread=item.quantile_spread,
                ic_standard_deviation=item.ic_standard_deviation,
                mean_turnover=item.mean_turnover,
                thresholds=thresholds,
            )
            for item in scenario_results
        ):
            raise FactorResearchError("robustness_result.scenario_results.passed 必须由冻结阈值派生")
        if not parameter_results or not all(
            type(item) is FactorRobustnessParameterVariantResult for item in parameter_results
        ):
            raise FactorResearchError("robustness_result.parameter_variant_results 必须非空")
        if tuple(sorted(parameter_results, key=lambda item: item.variant_id)) != parameter_results:
            raise FactorResearchError("robustness_result.parameter_variant_results 必须按 variant_id 排序")
        if len({item.variant_id for item in parameter_results}) != len(parameter_results):
            raise FactorResearchError("robustness_result.parameter_variant_results 不能重复")
        expected_variants = plan.parameter_variants
        if tuple(item.variant_id for item in parameter_results) != tuple(
            item.variant_id for item in expected_variants
        ):
            raise FactorResearchError(
                "robustness_result.parameter_variant_results 必须精确覆盖 frozen parameter variants"
            )
        for result, variant in zip(parameter_results, expected_variants, strict=True):
            expected_config = config.with_parameter_variant(variant)
            if (
                result.variant_hash != variant.variant_hash
                or result.factor_id != variant.factor_id
                or result.config_hash != expected_config.config_hash
            ):
                raise FactorResearchError(
                    "robustness_result.parameter_variant_results 必须绑定声明的 variant/config"
                )
            if result.passed != _robustness_analysis_metrics_pass(
                analysis_period_count=result.analysis_period_count,
                mean_rank_ic=result.mean_rank_ic,
                positive_ic_fraction=result.positive_ic_fraction,
                quantile_spread=result.quantile_spread,
                ic_standard_deviation=result.ic_standard_deviation,
                mean_turnover=result.mean_turnover,
                thresholds=thresholds,
            ):
                raise FactorResearchError(
                    "robustness_result.parameter_variant_results.passed 必须由冻结阈值派生"
                )
        if not cost_results or not all(type(item) is FactorRobustnessCostScenarioResult for item in cost_results):
            raise FactorResearchError("robustness_result.cost_scenario_results 必须非空")
        if tuple(sorted(cost_results, key=lambda item: item.scenario_id)) != cost_results:
            raise FactorResearchError("robustness_result.cost_scenario_results 必须按 scenario_id 排序")
        if len({item.scenario_id for item in cost_results}) != len(cost_results):
            raise FactorResearchError("robustness_result.cost_scenario_results 不能重复")
        if tuple(item.scenario_id for item in cost_results) != tuple(
            item.scenario_id for item in plan.cost_scenarios
        ):
            raise FactorResearchError(
                "robustness_result.cost_scenario_results 必须精确覆盖 frozen cost scenarios"
            )
        for cost_result, scenario in zip(cost_results, plan.cost_scenarios, strict=True):
            if cost_result.scenario_hash != scenario.scenario_hash:
                raise FactorResearchError(
                    "robustness_result.cost_scenario_results 必须绑定声明的 cost scenario"
                )
            if cost_result.passed != _robustness_cost_metrics_pass(
                total_return=cost_result.total_return,
                max_drawdown=cost_result.max_drawdown,
                thresholds=thresholds,
            ):
                raise FactorResearchError(
                    "robustness_result.cost_scenario_results.passed 必须由冻结阈值派生"
                )
        if not summaries or not all(type(item) is FactorRobustnessFactorSummary for item in summaries):
            raise FactorResearchError("robustness_result.factor_summaries 必须非空")
        if tuple(sorted(summaries, key=lambda item: item.factor_id)) != summaries:
            raise FactorResearchError("robustness_result.factor_summaries 必须按 factor_id 排序")
        if len({item.factor_id for item in summaries}) != len(summaries):
            raise FactorResearchError("robustness_result.factor_summaries 不能重复")
        if tuple(item.factor_id for item in summaries) != tuple(
            item.factor_id for item in config.alpha_factors
        ):
            raise FactorResearchError(
                "robustness_result.factor_summaries 必须精确覆盖全部 alpha factor"
            )
        for summary in summaries:
            entries = tuple(
                item for item in scenario_results if item.factor_id == summary.factor_id
            )
            passed_count = sum(item.passed for item in entries)
            pass_fraction = passed_count / len(entries)
            if (
                summary.scenario_count != len(plan.subperiods)
                or summary.passed_scenario_count != passed_count
                or not math.isclose(summary.pass_fraction, pass_fraction, abs_tol=1e-12)
                or summary.passed
                != (pass_fraction >= thresholds.minimum_scenario_pass_fraction)
            ):
                raise FactorResearchError(
                    "robustness_result.factor_summaries 必须由 frozen scenario results 派生"
                )
        passed = (
            all(item.passed for item in summaries)
            and all(item.passed for item in parameter_results)
            and all(item.passed for item in cost_results)
        )
        result_hash = canonical_json_sha256(
            {
                "config_hash": config_hash,
                "checkpoint_data_hashes": list(checkpoint_data_hashes),
                "cost_scenario_result_hashes": [item.result_hash for item in cost_results],
                "experiment_hash": experiment.experiment_hash,
                "factor_summary_hashes": [item.summary_hash for item in summaries],
                "format": "northstar.factor-robustness-result.v1",
                "outcome_hashes": list(outcome_hashes),
                "parameter_variant_result_hashes": [item.result_hash for item in parameter_results],
                "passed": passed,
                "plan_hash": plan_hash,
                "proposal_hashes": list(proposal_hashes),
                "scenario_result_hashes": [item.result_hash for item in scenario_results],
            }
        )
        object.__setattr__(self, "plan", plan)
        object.__setattr__(self, "config", config)
        object.__setattr__(self, "experiment", experiment)
        object.__setattr__(self, "plan_hash", plan_hash)
        object.__setattr__(self, "config_hash", config_hash)
        object.__setattr__(self, "checkpoint_data_hashes", checkpoint_data_hashes)
        object.__setattr__(self, "proposal_hashes", proposal_hashes)
        object.__setattr__(self, "outcome_hashes", outcome_hashes)
        object.__setattr__(self, "scenario_results", scenario_results)
        object.__setattr__(self, "parameter_variant_results", parameter_results)
        object.__setattr__(self, "cost_scenario_results", cost_results)
        object.__setattr__(self, "factor_summaries", summaries)
        object.__setattr__(self, "passed", passed)
        object.__setattr__(self, "result_hash", result_hash)


def _robustness_analysis_metrics_pass(
    *,
    analysis_period_count: int,
    mean_rank_ic: float,
    positive_ic_fraction: float,
    quantile_spread: float,
    ic_standard_deviation: float,
    mean_turnover: float,
    thresholds: FactorStabilityThresholds,
) -> bool:
    return (
        analysis_period_count >= thresholds.minimum_analysis_periods
        and mean_rank_ic >= thresholds.minimum_mean_rank_ic
        and positive_ic_fraction >= thresholds.minimum_positive_ic_fraction
        and quantile_spread >= thresholds.minimum_quantile_spread
        and ic_standard_deviation <= thresholds.maximum_ic_standard_deviation
        and mean_turnover <= thresholds.maximum_mean_turnover
    )


def _robustness_cost_metrics_pass(
    *,
    total_return: float,
    max_drawdown: float,
    thresholds: FactorStabilityThresholds,
) -> bool:
    return (
        total_return >= thresholds.minimum_cost_scenario_total_return
        and max_drawdown >= thresholds.minimum_cost_scenario_max_drawdown
    )


@dataclass(frozen=True, slots=True)
class FactorWalkForwardResult:
    """一个预声明 OOS fold 的纯研究回测摘要。"""

    fold_id: str
    fold_hash: str
    backtest_result_hash: str
    session_count: int
    total_return: float
    max_drawdown: float
    result_hash: str = field(init=False)

    def __post_init__(self) -> None:
        fold_id = _identifier(self.fold_id, "walk_forward.fold_id")
        fold_hash = _hash(self.fold_hash, "walk_forward.fold_hash")
        backtest_result_hash = _hash(
            self.backtest_result_hash,
            "walk_forward.backtest_result_hash",
        )
        session_count = _positive_int(self.session_count, "walk_forward.session_count")
        total_return = _number(self.total_return, "walk_forward.total_return")
        if total_return <= -1.0:
            raise FactorResearchError("walk_forward.total_return 必须大于 -1")
        max_drawdown = _number(
            self.max_drawdown,
            "walk_forward.max_drawdown",
            minimum=-1.0,
            maximum=0.0,
        )
        result_hash = canonical_json_sha256(
            {
                "backtest_result_hash": backtest_result_hash,
                "fold_hash": fold_hash,
                "fold_id": fold_id,
                "format": "northstar.factor-walk-forward-result.v1",
                "max_drawdown": max_drawdown.hex(),
                "session_count": session_count,
                "total_return": total_return.hex(),
            }
        )
        object.__setattr__(self, "fold_id", fold_id)
        object.__setattr__(self, "fold_hash", fold_hash)
        object.__setattr__(self, "backtest_result_hash", backtest_result_hash)
        object.__setattr__(self, "session_count", session_count)
        object.__setattr__(self, "total_return", total_return)
        object.__setattr__(self, "max_drawdown", max_drawdown)
        object.__setattr__(self, "result_hash", result_hash)


@dataclass(frozen=True, slots=True)
class FactorResearchExperiment:
    """冻结一次 PIT 因子研究实验，不把因子误建模为 Strategy。"""

    experiment_id: str
    config_hash: str
    decision_replay_plan_hash: str
    dataset_version_hashes: tuple[str, ...]
    feature_version_hashes: tuple[str, ...]
    code_revision: str
    experiment_hash: str = field(init=False)

    @property
    def research_only(self) -> bool:
        return True

    @property
    def candidate_admission_eligible(self) -> bool:
        return False

    @property
    def simnow_handoff_allowed(self) -> bool:
        return False

    def __post_init__(self) -> None:
        experiment_id = _identifier(self.experiment_id, "experiment.experiment_id")
        config_hash = _hash(self.config_hash, "experiment.config_hash")
        plan_hash = _hash(
            self.decision_replay_plan_hash,
            "experiment.decision_replay_plan_hash",
        )
        datasets = tuple(
            sorted(_hash(item, "experiment.dataset_version_hash") for item in self.dataset_version_hashes)
        )
        feature_versions = tuple(
            sorted(_hash(item, "experiment.feature_version_hash") for item in self.feature_version_hashes)
        )
        if not datasets or len(set(datasets)) != len(datasets):
            raise FactorResearchError("experiment.dataset_version_hashes 必须非空且无重复")
        if not feature_versions or len(set(feature_versions)) != len(feature_versions):
            raise FactorResearchError("experiment.feature_version_hashes 必须非空且无重复")
        if not isinstance(self.code_revision, str) or not self.code_revision.strip() or "\n" in self.code_revision:
            raise FactorResearchError("experiment.code_revision 必须是非空单行文本")
        code_revision = self.code_revision.strip()
        experiment_hash = canonical_json_sha256(
            {
                "code_revision": code_revision,
                "config_hash": config_hash,
                "dataset_version_hashes": list(datasets),
                "decision_replay_plan_hash": plan_hash,
                "experiment_id": experiment_id,
                "feature_version_hashes": list(feature_versions),
                "format": "northstar.factor-research-experiment.v1",
                "research_only": True,
            }
        )
        object.__setattr__(self, "experiment_id", experiment_id)
        object.__setattr__(self, "config_hash", config_hash)
        object.__setattr__(self, "decision_replay_plan_hash", plan_hash)
        object.__setattr__(self, "dataset_version_hashes", datasets)
        object.__setattr__(self, "feature_version_hashes", feature_versions)
        object.__setattr__(self, "code_revision", code_revision)
        object.__setattr__(self, "experiment_hash", experiment_hash)


@dataclass(frozen=True, slots=True)
class FactorResearchRunManifest:
    """只含 hash/配置身份的可重放因子研究运行清单。"""

    config_hash: str
    feature_version_hashes: tuple[str, ...]
    code_revision: str
    decision_replay_plan_hash: str
    experiment_hash: str
    dataset_version_hashes: tuple[str, ...]
    checkpoint_data_hashes: tuple[str, ...]
    proposal_hashes: tuple[str, ...]
    analysis_hashes: tuple[str, ...]
    robustness_plan_hash: str
    robustness_result_hash: str
    backtest_result_hash: str
    walk_forward_result_hashes: tuple[str, ...]
    lookahead_certificate_hash: str
    manifest_hash: str = field(init=False)

    @property
    def research_only(self) -> bool:
        return True

    @property
    def candidate_admission_eligible(self) -> bool:
        return False

    @property
    def simnow_handoff_allowed(self) -> bool:
        return False

    def __post_init__(self) -> None:
        config_hash = _hash(self.config_hash, "manifest.config_hash")
        feature_versions = tuple(
            sorted(_hash(item, "manifest.feature_version_hash") for item in self.feature_version_hashes)
        )
        if not isinstance(self.code_revision, str) or not self.code_revision.strip() or "\n" in self.code_revision:
            raise FactorResearchError("manifest.code_revision 必须是非空单行文本")
        code_revision = self.code_revision.strip()
        plan_hash = _hash(self.decision_replay_plan_hash, "manifest.decision_replay_plan_hash")
        experiment_hash = _hash(self.experiment_hash, "manifest.experiment_hash")
        datasets = tuple(sorted(_hash(item, "manifest.dataset_version_hash") for item in self.dataset_version_hashes))
        checkpoints = tuple(sorted(_hash(item, "manifest.checkpoint_data_hash") for item in self.checkpoint_data_hashes))
        proposals = tuple(sorted(_hash(item, "manifest.proposal_hash") for item in self.proposal_hashes))
        analyses = tuple(sorted(_hash(item, "manifest.analysis_hash") for item in self.analysis_hashes))
        robustness_plan = _hash(self.robustness_plan_hash, "manifest.robustness_plan_hash")
        robustness_result = _hash(self.robustness_result_hash, "manifest.robustness_result_hash")
        backtest = _hash(self.backtest_result_hash, "manifest.backtest_result_hash")
        folds = tuple(sorted(_hash(item, "manifest.walk_forward_result_hash") for item in self.walk_forward_result_hashes))
        certificate = _hash(self.lookahead_certificate_hash, "manifest.lookahead_certificate_hash")
        if not datasets or len(set(datasets)) != len(datasets):
            raise FactorResearchError("manifest.dataset_version_hashes 必须非空且无重复")
        if not feature_versions or len(set(feature_versions)) != len(feature_versions):
            raise FactorResearchError("manifest.feature_version_hashes 必须非空且无重复")
        for field_name, values in (
            ("checkpoint_data_hashes", checkpoints),
            ("proposal_hashes", proposals),
            ("analysis_hashes", analyses),
            ("walk_forward_result_hashes", folds),
        ):
            if not values or len(set(values)) != len(values):
                raise FactorResearchError(f"manifest.{field_name} 必须非空且无重复")
        manifest_hash = canonical_json_sha256(
            {
                "analysis_hashes": list(analyses),
                "backtest_result_hash": backtest,
                "candidate_admission_eligible": False,
                "checkpoint_data_hashes": list(checkpoints),
                "code_revision": code_revision,
                "config_hash": config_hash,
                "dataset_version_hashes": list(datasets),
                "decision_replay_plan_hash": plan_hash,
                "experiment_hash": experiment_hash,
                "feature_version_hashes": list(feature_versions),
                "format": "northstar.factor-research-run-manifest.v1",
                "lookahead_certificate_hash": certificate,
                "proposal_hashes": list(proposals),
                "research_only": True,
                "robustness_plan_hash": robustness_plan,
                "robustness_result_hash": robustness_result,
                "simnow_handoff_allowed": False,
                "walk_forward_result_hashes": list(folds),
            }
        )
        object.__setattr__(self, "config_hash", config_hash)
        object.__setattr__(self, "feature_version_hashes", feature_versions)
        object.__setattr__(self, "code_revision", code_revision)
        object.__setattr__(self, "decision_replay_plan_hash", plan_hash)
        object.__setattr__(self, "experiment_hash", experiment_hash)
        object.__setattr__(self, "dataset_version_hashes", datasets)
        object.__setattr__(self, "checkpoint_data_hashes", checkpoints)
        object.__setattr__(self, "proposal_hashes", proposals)
        object.__setattr__(self, "analysis_hashes", analyses)
        object.__setattr__(self, "robustness_plan_hash", robustness_plan)
        object.__setattr__(self, "robustness_result_hash", robustness_result)
        object.__setattr__(self, "backtest_result_hash", backtest)
        object.__setattr__(self, "walk_forward_result_hashes", folds)
        object.__setattr__(self, "lookahead_certificate_hash", certificate)
        object.__setattr__(self, "manifest_hash", manifest_hash)


__all__ = [
    "FactorAnalysisPeriod",
    "FactorAnalysisResult",
    "FactorCheckpointData",
    "FactorDefinition",
    "FactorExposure",
    "FactorForwardOutcome",
    "FactorMarketSlice",
    "FactorMaterializationReference",
    "FactorPipelineConfig",
    "FactorPortfolioProposal",
    "FactorPortfolioWeight",
    "FactorResearchError",
    "FactorResearchExperiment",
    "FactorResearchRunManifest",
    "FactorRobustnessCostScenario",
    "FactorRobustnessCostScenarioResult",
    "FactorRobustnessFactorSummary",
    "FactorRobustnessParameterVariant",
    "FactorRobustnessParameterVariantResult",
    "FactorRobustnessPlan",
    "FactorRobustnessResult",
    "FactorRobustnessScenarioResult",
    "FactorRole",
    "FactorStabilityThresholds",
    "FactorRobustnessSubperiod",
    "FactorWalkForwardResult",
    "ProposalStatus",
]

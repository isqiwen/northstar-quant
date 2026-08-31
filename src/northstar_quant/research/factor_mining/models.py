"""Declarative, bounded contracts for AI-assisted factor-mining campaigns.

The objects in this module deliberately model a search policy, structured
candidate proposals, and hash-only screening evidence.  They do not expose an
expression string, a callable, a DataFrame, a dataset selector, a portfolio
target, or an execution object.  A trusted application composition root must
turn a validated candidate into a FactorResearchPipeline run.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
import json
import math
import re
from typing import TypeAlias

from northstar_quant.data.artifacts.fingerprints import (
    FingerprintError,
    canonical_json_sha256,
    require_sha256,
)
from northstar_quant.research.factors.models import (
    FactorDefinition,
    FactorPipelineConfig,
    FactorResearchError,
    FactorRobustnessParameterVariant,
    FactorRobustnessPlan,
    FactorRole,
)
from northstar_quant.research.validation.framework import WalkForwardFold


__all__ = [
    "CandidateValidationStatus",
    "FactorCandidateGenerationReceipt",
    "FactorCandidateGenerationRequest",
    "FactorCandidateProposal",
    "FactorCandidateValidation",
    "FactorMiningCostScenario",
    "FactorMiningMultipleTestingControl",
    "FactorMiningCampaignSpec",
    "FactorMiningError",
    "FactorParameterDomain",
    "FactorPipelineTemplate",
    "FactorPrimitive",
    "FactorMiningRunnerResourceBudget",
    "FactorSearchBudget",
    "FactorMiningSelectionPolicy",
    "FactorMiningStageBoundaryMode",
]


_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_FEATURE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REASON_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_FORBIDDEN_TOKENS = frozenset(
    {
        "__import__",
        "compile",
        "eval",
        "exec",
        "latest",
        "open",
        "select",
        "shell",
    }
)

ParameterScalar: TypeAlias = int | float | str


class FactorMiningError(ValueError):
    """Raised when a factor-mining search declaration is unsafe or incomplete."""


def _require_global_discovery_oos_layout(template: "FactorPipelineTemplate") -> None:
    """Freeze the one-shot local discovery/OOS layout at campaign creation.

    A generic factor pipeline can use rolling walk-forward folds, but a single
    factor-mining selection commitment cannot safely let one fold's OOS result
    become another fold's development evidence.  The campaign contract
    therefore accepts only shared IS/validation followed by ordered OOS folds.
    """

    folds = template.walk_forward_folds
    first = folds[0].split
    if any(
        fold.split.in_sample != first.in_sample or fold.split.validation != first.validation
        for fold in folds[1:]
    ):
        raise FactorMiningError(
            "campaign requires shared in-sample and validation periods across OOS folds"
        )
    earliest_oos = min(fold.split.out_of_sample.start for fold in folds)
    if first.validation.end >= earliest_oos:
        raise FactorMiningError("campaign OOS periods must begin after the shared validation period")


def _require_robustness_parameter_grid_matches_primitives(
    template: "FactorPipelineTemplate",
    primitives: tuple["FactorPrimitive", ...],
) -> None:
    """Bind the frozen robustness grid to every candidate primitive at campaign creation.

    ``FactorPipelineTemplate`` owns the one placeholder factor identity, while
    ``FactorPrimitive`` owns the finite candidate parameter domains. Neither
    object alone can establish that the sealed robustness re-runs are valid
    for every candidate the campaign may admit. Keep that cross-record
    invariant at the campaign seam, before a candidate can be generated or
    reach ``build_config``.
    """

    variants = template.robustness_plan.parameter_variants
    parameter_schema = tuple(sorted(variants[0].parameters))
    if any(tuple(sorted(item.parameters)) != parameter_schema for item in variants[1:]):
        raise FactorMiningError(
            "campaign robustness parameter variants must share exactly one parameter schema"
        )

    parameter_points = tuple(
        (
            variant.variant_id,
            {
                name: _parameter_scalar(
                    variant.parameters[name],
                    f"campaign.robustness_parameter_variant.{variant.variant_id}.{name}",
                )
                for name in parameter_schema
            },
        )
        for variant in variants
    )
    for primitive in primitives:
        if primitive.parameter_names != parameter_schema:
            raise FactorMiningError(
                "campaign primitive parameter schema must exactly match the frozen "
                "robustness parameter schema"
            )
        domains_by_name = {item.name: item for item in primitive.parameter_domains}
        for variant_id, parameters in parameter_points:
            for name, value in parameters.items():
                if not domains_by_name[name].allows(value):
                    raise FactorMiningError(
                        "campaign robustness parameter variant "
                        f"{variant_id} is outside primitive {primitive.primitive_id} "
                        f"finite domain for {name}"
                    )


def _identifier(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise FactorMiningError(f"{field_name} must be a lowercase snake_case identifier")
    if value == "latest":
        raise FactorMiningError(f"{field_name} cannot use the ambiguous latest selector")
    return value


def _safe_token(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise FactorMiningError(f"{field_name} must be a bounded opaque token")
    normalized = value.strip()
    if normalized != value or _SAFE_TOKEN_RE.fullmatch(normalized) is None:
        raise FactorMiningError(f"{field_name} must be a bounded opaque token")
    if normalized.casefold() in _FORBIDDEN_TOKENS:
        raise FactorMiningError(f"{field_name} contains a forbidden executable selector")
    return normalized


def _feature_id(value: object, field_name: str) -> str:
    if not isinstance(value, str) or _FEATURE_ID_RE.fullmatch(value) is None:
        raise FactorMiningError(f"{field_name} must be a lower-case dotted canonical feature ID")
    if value == "latest":
        raise FactorMiningError(f"{field_name} cannot use the ambiguous latest selector")
    return value


def _version(value: object, field_name: str) -> str:
    token = _safe_token(value, field_name)
    if "/" in token or "\\" in token:
        raise FactorMiningError(f"{field_name} cannot contain a filesystem path")
    return token


def _hash(value: object, field_name: str) -> str:
    try:
        return require_sha256(value, field_name=field_name)  # type: ignore[arg-type]
    except FingerprintError as exc:
        raise FactorMiningError(str(exc)) from exc


def _utc(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise FactorMiningError(f"{field_name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _finite_number(
    value: object,
    field_name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FactorMiningError(f"{field_name} must be a finite number")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise FactorMiningError(f"{field_name} must be a finite number")
    if minimum is not None and normalized < minimum:
        raise FactorMiningError(f"{field_name} must be at least {minimum}")
    if maximum is not None and normalized > maximum:
        raise FactorMiningError(f"{field_name} must be at most {maximum}")
    return normalized


def _positive_int(value: object, field_name: str, *, maximum: int = 1_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise FactorMiningError(f"{field_name} must be an integer between 1 and {maximum}")
    return value


def _nonnegative_int(value: object, field_name: str, *, maximum: int = 1_000_000) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        raise FactorMiningError(f"{field_name} must be an integer between 0 and {maximum}")
    return value


def _direction(value: object, field_name: str) -> float:
    normalized = _finite_number(value, field_name)
    if normalized not in {-1.0, 1.0}:
        raise FactorMiningError(f"{field_name} must be -1 or 1")
    return normalized


def _parameter_scalar(value: object, field_name: str) -> ParameterScalar:
    if type(value) is int:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise FactorMiningError(f"{field_name} must be a finite scalar")
        return value
    if type(value) is str:
        return _safe_token(value, field_name)
    raise FactorMiningError(
        f"{field_name} must be an integer, finite float, or bounded enum token"
    )


def _scalar_key(value: ParameterScalar) -> str:
    if type(value) is int:
        payload: dict[str, object] = {"kind": "int", "value": value}
    elif type(value) is float:
        payload = {"kind": "float", "value": value.hex()}
    else:
        payload = {"kind": "str", "value": value}
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _parameter_mapping_json(value: Mapping[str, object], field_name: str) -> str:
    if not isinstance(value, Mapping):
        raise FactorMiningError(f"{field_name} must be a mapping")
    if len(value) > 16:
        raise FactorMiningError(f"{field_name} exceeds the bounded parameter budget")
    normalized: dict[str, ParameterScalar] = {}
    for raw_key, raw_value in value.items():
        key = _identifier(raw_key, f"{field_name}.key")
        if key in normalized:
            raise FactorMiningError(f"{field_name} contains duplicate parameter names")
        normalized[key] = _parameter_scalar(raw_value, f"{field_name}.{key}")
    return json.dumps(
        {key: normalized[key] for key in sorted(normalized)},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _load_parameter_mapping(value: object, field_name: str) -> dict[str, ParameterScalar]:
    if not isinstance(value, str):
        raise FactorMiningError(f"{field_name} must be canonical JSON")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise FactorMiningError(f"{field_name} must be canonical JSON") from exc
    if not isinstance(decoded, dict):
        raise FactorMiningError(f"{field_name} must encode a JSON mapping")
    canonical = _parameter_mapping_json(decoded, field_name)
    if canonical != value:
        raise FactorMiningError(f"{field_name} must be canonical JSON")
    return {key: decoded[key] for key in sorted(decoded)}


def _hashes(value: object, field_name: str, *, minimum: int = 1) -> tuple[str, ...]:
    if isinstance(value, str):
        raise FactorMiningError(f"{field_name} must be an iterable of hashes")
    try:
        raw_values: tuple[object, ...] = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise FactorMiningError(f"{field_name} must be an iterable of hashes") from exc
    normalized = tuple(sorted(_hash(item, field_name) for item in raw_values))
    if len(normalized) < minimum or len(set(normalized)) != len(normalized):
        raise FactorMiningError(f"{field_name} must be non-empty and contain unique hashes")
    return normalized


@dataclass(frozen=True, slots=True)
class FactorParameterDomain:
    """A finite, trusted parameter grid for one canonical feature parameter."""

    name: str
    allowed_values: tuple[ParameterScalar, ...]
    domain_hash: str = field(init=False)

    def __post_init__(self) -> None:
        name = _identifier(self.name, "parameter_domain.name")
        values = tuple(
            sorted(
                (_parameter_scalar(item, f"parameter_domain.{name}") for item in self.allowed_values),
                key=_scalar_key,
            )
        )
        if not values or len(values) > 64:
            raise FactorMiningError("parameter_domain.allowed_values must contain 1 to 64 values")
        keys = tuple(_scalar_key(item) for item in values)
        if len(set(keys)) != len(keys):
            raise FactorMiningError("parameter_domain.allowed_values cannot contain duplicates")
        domain_hash = canonical_json_sha256(
            {
                "allowed_values": [
                    {"key": _scalar_key(item), "value": item}
                    for item in values
                ],
                "format": "northstar.factor-mining-parameter-domain.v1",
                "name": name,
            }
        )
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "allowed_values", values)
        object.__setattr__(self, "domain_hash", domain_hash)

    def allows(self, value: ParameterScalar) -> bool:
        return _scalar_key(value) in {_scalar_key(item) for item in self.allowed_values}


@dataclass(frozen=True, slots=True)
class FactorPrimitive:
    """One non-executable canonical-feature choice exposed to a mining agent."""

    primitive_id: str
    feature_id: str
    allowed_directions: tuple[float, ...]
    parameter_domains: tuple[FactorParameterDomain, ...]
    primitive_hash: str = field(init=False)

    def __post_init__(self) -> None:
        primitive_id = _identifier(self.primitive_id, "primitive.primitive_id")
        feature_id = _feature_id(self.feature_id, "primitive.feature_id")
        directions = tuple(sorted({_direction(item, "primitive.allowed_directions") for item in self.allowed_directions}))
        if not directions:
            raise FactorMiningError("primitive.allowed_directions cannot be empty")
        domains = tuple(self.parameter_domains)
        if not domains or not all(type(item) is FactorParameterDomain for item in domains):
            raise FactorMiningError("primitive.parameter_domains must be FactorParameterDomain records")
        if tuple(sorted(domains, key=lambda item: item.name)) != domains:
            raise FactorMiningError("primitive.parameter_domains must be sorted by name")
        if len({item.name for item in domains}) != len(domains):
            raise FactorMiningError("primitive.parameter_domains cannot contain duplicate names")
        primitive_hash = canonical_json_sha256(
            {
                "allowed_directions": [item.hex() for item in directions],
                "feature_id": feature_id,
                "format": "northstar.factor-mining-primitive.v1",
                "parameter_domain_hashes": [item.domain_hash for item in domains],
                "primitive_id": primitive_id,
            }
        )
        object.__setattr__(self, "primitive_id", primitive_id)
        object.__setattr__(self, "feature_id", feature_id)
        object.__setattr__(self, "allowed_directions", directions)
        object.__setattr__(self, "parameter_domains", domains)
        object.__setattr__(self, "primitive_hash", primitive_hash)

    @property
    def parameter_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.parameter_domains)


@dataclass(frozen=True, slots=True)
class FactorSearchBudget:
    """The immutable candidate-count budget for one campaign."""

    max_candidates: int
    budget_hash: str = field(init=False)

    def __post_init__(self) -> None:
        max_candidates = _positive_int(
            self.max_candidates,
            "budget.max_candidates",
            maximum=64,
        )
        budget_hash = canonical_json_sha256(
            {
                "format": "northstar.factor-mining-budget.v1",
                "max_candidates": max_candidates,
            }
        )
        object.__setattr__(self, "max_candidates", max_candidates)
        object.__setattr__(self, "budget_hash", budget_hash)


@dataclass(frozen=True, slots=True)
class FactorMiningRunnerResourceBudget:
    """Frozen local-compute limits for one campaign execution declaration.

    This is intentionally separate from :class:`FactorSearchBudget`: the
    latter limits what an AI may propose, while this record limits what the
    trusted local runner may consume.  It contains no scheduler, broker,
    account, provider credential, or execution setting.
    """

    max_candidates: int
    max_concurrent_runs: int
    max_cpu_seconds: int
    max_memory_bytes: int
    max_wall_clock_seconds: int
    max_data_rows: int
    max_artifact_bytes: int
    budget_hash: str = field(init=False)

    def __post_init__(self) -> None:
        max_candidates = _positive_int(
            self.max_candidates,
            "runner_budget.max_candidates",
            maximum=64,
        )
        max_concurrent_runs = _positive_int(
            self.max_concurrent_runs,
            "runner_budget.max_concurrent_runs",
            maximum=16,
        )
        max_cpu_seconds = _positive_int(
            self.max_cpu_seconds,
            "runner_budget.max_cpu_seconds",
            maximum=86_400,
        )
        max_memory_bytes = _positive_int(
            self.max_memory_bytes,
            "runner_budget.max_memory_bytes",
            maximum=1 << 50,
        )
        max_wall_clock_seconds = _positive_int(
            self.max_wall_clock_seconds,
            "runner_budget.max_wall_clock_seconds",
            maximum=86_400,
        )
        max_data_rows = _positive_int(
            self.max_data_rows,
            "runner_budget.max_data_rows",
            maximum=1_000_000_000,
        )
        max_artifact_bytes = _positive_int(
            self.max_artifact_bytes,
            "runner_budget.max_artifact_bytes",
            maximum=1 << 50,
        )
        budget_hash = canonical_json_sha256(
            {
                "format": "northstar.factor-mining-runner-resource-budget.v1",
                "max_artifact_bytes": max_artifact_bytes,
                "max_candidates": max_candidates,
                "max_concurrent_runs": max_concurrent_runs,
                "max_cpu_seconds": max_cpu_seconds,
                "max_data_rows": max_data_rows,
                "max_memory_bytes": max_memory_bytes,
                "max_wall_clock_seconds": max_wall_clock_seconds,
            }
        )
        object.__setattr__(self, "max_candidates", max_candidates)
        object.__setattr__(self, "max_concurrent_runs", max_concurrent_runs)
        object.__setattr__(self, "max_cpu_seconds", max_cpu_seconds)
        object.__setattr__(self, "max_memory_bytes", max_memory_bytes)
        object.__setattr__(self, "max_wall_clock_seconds", max_wall_clock_seconds)
        object.__setattr__(self, "max_data_rows", max_data_rows)
        object.__setattr__(self, "max_artifact_bytes", max_artifact_bytes)
        object.__setattr__(self, "budget_hash", budget_hash)


class FactorMiningMultipleTestingControl(str, Enum):
    """The frozen family-wise control used for development screening."""

    BONFERRONI_SIGN_TEST = "bonferroni_sign_test"


class FactorMiningStageBoundaryMode(str, Enum):
    """The only supported local stage accounting policy.

    Each stage begins flat and is force-closed after its final return.  This
    keeps delayed weights, entry costs, and terminal costs inside one declared
    stage instead of silently inheriting portfolio state across an IS,
    validation, or OOS boundary.
    """

    FLAT_START_FORCED_CLOSE = "flat_start_forced_close"


@dataclass(frozen=True, slots=True)
class FactorMiningCostScenario:
    """A predeclared continuous-research cost and delay scenario."""

    scenario_id: str
    commission_bps: float
    min_commission: float
    slippage_bps: float
    execution_delay_sessions: int
    scenario_hash: str = field(init=False)

    def __post_init__(self) -> None:
        scenario_id = _identifier(self.scenario_id, "cost_scenario.scenario_id")
        commission_bps = _finite_number(
            self.commission_bps,
            "cost_scenario.commission_bps",
            minimum=0.0,
        )
        min_commission = _finite_number(
            self.min_commission,
            "cost_scenario.min_commission",
            minimum=0.0,
        )
        slippage_bps = _finite_number(
            self.slippage_bps,
            "cost_scenario.slippage_bps",
            minimum=0.0,
        )
        delay = _positive_int(
            self.execution_delay_sessions,
            "cost_scenario.execution_delay_sessions",
        )
        scenario_hash = canonical_json_sha256(
            {
                "commission_bps": commission_bps.hex(),
                "execution_delay_sessions": delay,
                "format": "northstar.factor-mining-cost-scenario.v1",
                "min_commission": min_commission.hex(),
                "scenario_id": scenario_id,
                "slippage_bps": slippage_bps.hex(),
            }
        )
        object.__setattr__(self, "scenario_id", scenario_id)
        object.__setattr__(self, "commission_bps", commission_bps)
        object.__setattr__(self, "min_commission", min_commission)
        object.__setattr__(self, "slippage_bps", slippage_bps)
        object.__setattr__(self, "execution_delay_sessions", delay)
        object.__setattr__(self, "scenario_hash", scenario_hash)


@dataclass(frozen=True, slots=True)
class FactorMiningSelectionPolicy:
    """A complete, host-owned discovery policy frozen before generation.

    The policy deliberately requires every threshold and cost scenario.  It
    does not infer a safe default from a candidate, a provider response, or an
    observed OOS result.
    """

    policy_id: str
    cost_scenarios: tuple[FactorMiningCostScenario, ...]
    minimum_in_sample_periods: int
    minimum_validation_periods: int
    minimum_stage_backtest_sessions: int
    minimum_in_sample_mean_rank_ic: float
    minimum_validation_mean_rank_ic: float
    minimum_validation_quantile_spread: float
    maximum_validation_factor_turnover: float
    maximum_validation_portfolio_turnover: float
    minimum_validation_total_return: float
    minimum_validation_max_drawdown: float
    family_wise_alpha: float
    multiple_testing_control: FactorMiningMultipleTestingControl
    max_selected_candidates: int
    stage_boundary_mode: FactorMiningStageBoundaryMode
    policy_hash: str = field(init=False)

    def __post_init__(self) -> None:
        policy_id = _identifier(self.policy_id, "selection_policy.policy_id")
        scenarios = tuple(self.cost_scenarios)
        if len(scenarios) < 2 or not all(type(item) is FactorMiningCostScenario for item in scenarios):
            raise FactorMiningError("selection_policy.cost_scenarios must contain at least two exact scenarios")
        if tuple(sorted(scenarios, key=lambda item: item.scenario_id)) != scenarios:
            raise FactorMiningError("selection_policy.cost_scenarios must be sorted by scenario_id")
        if len({item.scenario_id for item in scenarios}) != len(scenarios):
            raise FactorMiningError("selection_policy.cost_scenarios cannot contain duplicate IDs")
        if "baseline" not in {item.scenario_id for item in scenarios}:
            raise FactorMiningError("selection_policy.cost_scenarios must include baseline")
        min_in_sample = _positive_int(
            self.minimum_in_sample_periods,
            "selection_policy.minimum_in_sample_periods",
        )
        min_validation = _positive_int(
            self.minimum_validation_periods,
            "selection_policy.minimum_validation_periods",
        )
        min_backtest = _positive_int(
            self.minimum_stage_backtest_sessions,
            "selection_policy.minimum_stage_backtest_sessions",
        )
        in_sample_rank_ic = _finite_number(
            self.minimum_in_sample_mean_rank_ic,
            "selection_policy.minimum_in_sample_mean_rank_ic",
            minimum=-1.0,
            maximum=1.0,
        )
        validation_rank_ic = _finite_number(
            self.minimum_validation_mean_rank_ic,
            "selection_policy.minimum_validation_mean_rank_ic",
            minimum=-1.0,
            maximum=1.0,
        )
        validation_spread = _finite_number(
            self.minimum_validation_quantile_spread,
            "selection_policy.minimum_validation_quantile_spread",
        )
        max_factor_turnover = _finite_number(
            self.maximum_validation_factor_turnover,
            "selection_policy.maximum_validation_factor_turnover",
            minimum=0.0,
        )
        max_portfolio_turnover = _finite_number(
            self.maximum_validation_portfolio_turnover,
            "selection_policy.maximum_validation_portfolio_turnover",
            minimum=0.0,
        )
        min_total_return = _finite_number(
            self.minimum_validation_total_return,
            "selection_policy.minimum_validation_total_return",
            minimum=-1.0,
        )
        if min_total_return <= -1.0:
            raise FactorMiningError("selection_policy.minimum_validation_total_return must exceed -1")
        min_drawdown = _finite_number(
            self.minimum_validation_max_drawdown,
            "selection_policy.minimum_validation_max_drawdown",
            minimum=-1.0,
            maximum=0.0,
        )
        alpha = _finite_number(
            self.family_wise_alpha,
            "selection_policy.family_wise_alpha",
            minimum=1e-12,
            maximum=1.0,
        )
        if alpha >= 1.0:
            raise FactorMiningError("selection_policy.family_wise_alpha must be below 1")
        if type(self.multiple_testing_control) is not FactorMiningMultipleTestingControl:
            raise FactorMiningError(
                "selection_policy.multiple_testing_control must be FactorMiningMultipleTestingControl"
            )
        max_selected = _positive_int(
            self.max_selected_candidates,
            "selection_policy.max_selected_candidates",
            maximum=64,
        )
        if type(self.stage_boundary_mode) is not FactorMiningStageBoundaryMode:
            raise FactorMiningError(
                "selection_policy.stage_boundary_mode must be FactorMiningStageBoundaryMode"
            )
        policy_hash = canonical_json_sha256(
            {
                "cost_scenario_hashes": [item.scenario_hash for item in scenarios],
                "family_wise_alpha": alpha.hex(),
                "format": "northstar.factor-mining-selection-policy.v1",
                "max_selected_candidates": max_selected,
                "maximum_validation_factor_turnover": max_factor_turnover.hex(),
                "maximum_validation_portfolio_turnover": max_portfolio_turnover.hex(),
                "minimum_in_sample_mean_rank_ic": in_sample_rank_ic.hex(),
                "minimum_in_sample_periods": min_in_sample,
                "minimum_stage_backtest_sessions": min_backtest,
                "minimum_validation_max_drawdown": min_drawdown.hex(),
                "minimum_validation_mean_rank_ic": validation_rank_ic.hex(),
                "minimum_validation_periods": min_validation,
                "minimum_validation_quantile_spread": validation_spread.hex(),
                "minimum_validation_total_return": min_total_return.hex(),
                "multiple_testing_control": self.multiple_testing_control.value,
                "policy_id": policy_id,
                "stage_boundary_mode": self.stage_boundary_mode.value,
            }
        )
        object.__setattr__(self, "policy_id", policy_id)
        object.__setattr__(self, "cost_scenarios", scenarios)
        object.__setattr__(self, "minimum_in_sample_periods", min_in_sample)
        object.__setattr__(self, "minimum_validation_periods", min_validation)
        object.__setattr__(self, "minimum_stage_backtest_sessions", min_backtest)
        object.__setattr__(self, "minimum_in_sample_mean_rank_ic", in_sample_rank_ic)
        object.__setattr__(self, "minimum_validation_mean_rank_ic", validation_rank_ic)
        object.__setattr__(self, "minimum_validation_quantile_spread", validation_spread)
        object.__setattr__(self, "maximum_validation_factor_turnover", max_factor_turnover)
        object.__setattr__(self, "maximum_validation_portfolio_turnover", max_portfolio_turnover)
        object.__setattr__(self, "minimum_validation_total_return", min_total_return)
        object.__setattr__(self, "minimum_validation_max_drawdown", min_drawdown)
        object.__setattr__(self, "family_wise_alpha", alpha)
        object.__setattr__(self, "max_selected_candidates", max_selected)
        object.__setattr__(self, "policy_hash", policy_hash)

    @property
    def baseline_cost_scenario(self) -> FactorMiningCostScenario:
        return next(item for item in self.cost_scenarios if item.scenario_id == "baseline")


@dataclass(frozen=True, slots=True)
class FactorPipelineTemplate:
    """All non-candidate factor-pipeline settings frozen by the trusted host."""

    template_id: str
    version: str
    feature_version: str
    code_revision: str
    risk_model_factor: FactorDefinition
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
    template_hash: str = field(init=False)

    def __post_init__(self) -> None:
        template_id = _identifier(self.template_id, "template.template_id")
        version = _version(self.version, "template.version")
        feature_version = _version(self.feature_version, "template.feature_version")
        if not isinstance(self.code_revision, str) or not self.code_revision.strip() or "\n" in self.code_revision:
            raise FactorMiningError("template.code_revision must be a non-empty single line")
        code_revision = self.code_revision.strip()
        if type(self.risk_model_factor) is not FactorDefinition:
            raise FactorMiningError("template.risk_model_factor must be an exact FactorDefinition")
        if self.risk_model_factor.role is not FactorRole.RISK_MODEL:
            raise FactorMiningError("template.risk_model_factor must be a risk-model factor")
        min_cross_section = _positive_int(self.min_cross_section, "template.min_cross_section")
        quantile_count = _positive_int(self.quantile_count, "template.quantile_count")
        if quantile_count > min_cross_section:
            raise FactorMiningError("template.quantile_count cannot exceed min_cross_section")
        target_volatility = _finite_number(
            self.target_volatility,
            "template.target_volatility",
            minimum=1e-12,
        )
        max_abs_weight = _finite_number(
            self.max_abs_weight,
            "template.max_abs_weight",
            minimum=1e-12,
            maximum=1.0,
        )
        max_gross_exposure = _finite_number(
            self.max_gross_exposure,
            "template.max_gross_exposure",
            minimum=max_abs_weight,
            maximum=1.0,
        )
        holding_period_sessions = _positive_int(
            self.holding_period_sessions,
            "template.holding_period_sessions",
        )
        initial_cash = _finite_number(self.initial_cash, "template.initial_cash", minimum=1e-12)
        commission_bps = _finite_number(self.commission_bps, "template.commission_bps", minimum=0.0)
        min_commission = _finite_number(
            self.min_commission,
            "template.min_commission",
            minimum=0.0,
        )
        slippage_bps = _finite_number(self.slippage_bps, "template.slippage_bps", minimum=0.0)
        execution_delay_sessions = _positive_int(
            self.execution_delay_sessions,
            "template.execution_delay_sessions",
        )
        folds = tuple(self.walk_forward_folds)
        if len(folds) < 2 or not all(type(item) is WalkForwardFold for item in folds):
            raise FactorMiningError("template.walk_forward_folds must contain at least two folds")
        if tuple(sorted(folds, key=lambda item: item.fold_id)) != folds:
            raise FactorMiningError("template.walk_forward_folds must be sorted by fold_id")
        if len({item.fold_id for item in folds}) != len(folds):
            raise FactorMiningError("template.walk_forward_folds cannot contain duplicate IDs")
        for previous, current in zip(folds, folds[1:]):
            if previous.split.out_of_sample.end >= current.split.out_of_sample.start:
                raise FactorMiningError("template OOS fold periods must not overlap")
        if type(self.robustness_plan) is not FactorRobustnessPlan:
            raise FactorMiningError("template.robustness_plan must be an exact FactorRobustnessPlan")
        if {item.factor_id for item in self.robustness_plan.parameter_variants} != {
            "candidate_alpha"
        }:
            raise FactorMiningError(
                "template robustness parameter variants must use the candidate_alpha placeholder"
            )
        baseline = self.robustness_plan.baseline_cost_scenario
        if (
            baseline.commission_bps != commission_bps
            or baseline.min_commission != min_commission
            or baseline.slippage_bps != slippage_bps
            or baseline.execution_delay_sessions != execution_delay_sessions
        ):
            raise FactorMiningError(
                "template robustness baseline cost scenario must match template costs"
            )
        template_hash = canonical_json_sha256(
            {
                "code_revision": code_revision,
                "commission_bps": commission_bps.hex(),
                "execution_delay_sessions": execution_delay_sessions,
                "feature_version": feature_version,
                "format": "northstar.factor-mining-pipeline-template.v1",
                "holding_period_sessions": holding_period_sessions,
                "initial_cash": initial_cash.hex(),
                "max_abs_weight": max_abs_weight.hex(),
                "max_gross_exposure": max_gross_exposure.hex(),
                "min_commission": min_commission.hex(),
                "min_cross_section": min_cross_section,
                "quantile_count": quantile_count,
                "risk_model_factor_hash": self.risk_model_factor.definition_hash,
                "robustness_plan_hash": self.robustness_plan.plan_hash,
                "slippage_bps": slippage_bps.hex(),
                "target_volatility": target_volatility.hex(),
                "template_id": template_id,
                "version": version,
                "walk_forward_fold_hashes": [item.fold_hash for item in folds],
            }
        )
        object.__setattr__(self, "template_id", template_id)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "feature_version", feature_version)
        object.__setattr__(self, "code_revision", code_revision)
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
        object.__setattr__(self, "template_hash", template_hash)

    def build_config(
        self,
        *,
        campaign_id: str,
        candidate_id: str,
        factor_definition: FactorDefinition,
    ) -> FactorPipelineConfig:
        """Materialize a fixed pipeline config around exactly one validated alpha."""

        campaign_id = _identifier(campaign_id, "campaign_id")
        candidate_id = _identifier(candidate_id, "candidate_id")
        if type(factor_definition) is not FactorDefinition:
            raise FactorMiningError("factor_definition must be an exact FactorDefinition")
        if factor_definition.role is not FactorRole.ALPHA or factor_definition.risk_budget != 1.0:
            raise FactorMiningError("candidate factor must be the sole alpha with risk_budget=1")
        robustness_plan = _bind_candidate_robustness_plan(
            self.robustness_plan,
            factor_definition=factor_definition,
        )
        factors = tuple(
            sorted(
                (factor_definition, self.risk_model_factor),
                key=lambda item: item.factor_id,
            )
        )
        try:
            return FactorPipelineConfig(
                pipeline_id=f"{self.template_id}_{campaign_id}_{candidate_id}",
                version=self.version,
                feature_version=self.feature_version,
                code_revision=self.code_revision,
                factors=factors,
                volatility_factor_id=self.risk_model_factor.factor_id,
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
                robustness_plan=robustness_plan,
            )
        except FactorResearchError as exc:
            raise FactorMiningError("trusted pipeline template cannot build a valid configuration") from exc


def _bind_candidate_robustness_plan(
    template_plan: FactorRobustnessPlan,
    *,
    factor_definition: FactorDefinition,
) -> FactorRobustnessPlan:
    """Bind the sole trusted placeholder to one validated campaign candidate.

    The template freezes each finite parameter point, subperiod, exclusion,
    cost scenario and threshold.  Only the host-generated factor identity is
    substituted after candidate validation; the candidate cannot add a
    parameter point or modify the study axes.
    """

    variants = tuple(
        FactorRobustnessParameterVariant.create(
            variant_id=item.variant_id,
            factor_id=factor_definition.factor_id,
            parameters=item.parameters,
        )
        for item in template_plan.parameter_variants
    )
    try:
        return FactorRobustnessPlan(
            plan_id=template_plan.plan_id,
            version=template_plan.version,
            subperiods=template_plan.subperiods,
            parameter_variants=variants,
            cost_scenarios=template_plan.cost_scenarios,
            stability_thresholds=template_plan.stability_thresholds,
        )
    except FactorResearchError as exc:
        raise FactorMiningError(
            "candidate parameters are incompatible with the sealed robustness template"
        ) from exc


@dataclass(frozen=True, slots=True)
class FactorMiningCampaignSpec:
    """A sealed search campaign; AI may not mutate any field in this declaration."""

    campaign_id: str
    selection_at: datetime
    decision_replay_plan_hash: str
    dataset_version_hashes: tuple[str, ...]
    template: FactorPipelineTemplate
    primitives: tuple[FactorPrimitive, ...]
    budget: FactorSearchBudget
    selection_policy: FactorMiningSelectionPolicy
    generator_id: str
    generator_model_revision_hash: str
    prompt_template_hash: str
    campaign_hash: str = field(init=False)

    def __post_init__(self) -> None:
        campaign_id = _identifier(self.campaign_id, "campaign.campaign_id")
        selection_at = _utc(self.selection_at, "campaign.selection_at")
        plan_hash = _hash(self.decision_replay_plan_hash, "campaign.decision_replay_plan_hash")
        datasets = _hashes(self.dataset_version_hashes, "campaign.dataset_version_hashes")
        if type(self.template) is not FactorPipelineTemplate:
            raise FactorMiningError("campaign.template must be an exact FactorPipelineTemplate")
        _require_global_discovery_oos_layout(self.template)
        primitives = tuple(self.primitives)
        if not primitives or not all(type(item) is FactorPrimitive for item in primitives):
            raise FactorMiningError("campaign.primitives must contain FactorPrimitive records")
        if tuple(sorted(primitives, key=lambda item: item.primitive_id)) != primitives:
            raise FactorMiningError("campaign.primitives must be sorted by primitive_id")
        if len({item.primitive_id for item in primitives}) != len(primitives):
            raise FactorMiningError("campaign.primitives cannot contain duplicate IDs")
        _require_robustness_parameter_grid_matches_primitives(self.template, primitives)
        if type(self.budget) is not FactorSearchBudget:
            raise FactorMiningError("campaign.budget must be an exact FactorSearchBudget")
        if type(self.selection_policy) is not FactorMiningSelectionPolicy:
            raise FactorMiningError(
                "campaign.selection_policy must be an exact FactorMiningSelectionPolicy"
            )
        if self.selection_policy.max_selected_candidates > self.budget.max_candidates:
            raise FactorMiningError(
                "campaign selection policy cannot select more candidates than the campaign budget"
            )
        baseline = self.selection_policy.baseline_cost_scenario
        if (
            baseline.commission_bps != self.template.commission_bps
            or baseline.min_commission != self.template.min_commission
            or baseline.slippage_bps != self.template.slippage_bps
            or baseline.execution_delay_sessions != self.template.execution_delay_sessions
        ):
            raise FactorMiningError(
                "campaign baseline cost scenario must exactly match the trusted pipeline template"
            )
        generator_id = _safe_token(self.generator_id, "campaign.generator_id")
        model_revision_hash = _hash(
            self.generator_model_revision_hash,
            "campaign.generator_model_revision_hash",
        )
        prompt_template_hash = _hash(self.prompt_template_hash, "campaign.prompt_template_hash")
        campaign_hash = canonical_json_sha256(
            {
                "budget_hash": self.budget.budget_hash,
                "dataset_version_hashes": list(datasets),
                "decision_replay_plan_hash": plan_hash,
                "format": "northstar.factor-mining-campaign.v2",
                "generator_id": generator_id,
                "generator_model_revision_hash": model_revision_hash,
                "prompt_template_hash": prompt_template_hash,
                "primitive_hashes": [item.primitive_hash for item in primitives],
                "selection_at": selection_at.isoformat(),
                "selection_policy_hash": self.selection_policy.policy_hash,
                "template_hash": self.template.template_hash,
            }
        )
        object.__setattr__(self, "campaign_id", campaign_id)
        object.__setattr__(self, "selection_at", selection_at)
        object.__setattr__(self, "decision_replay_plan_hash", plan_hash)
        object.__setattr__(self, "dataset_version_hashes", datasets)
        object.__setattr__(self, "primitives", primitives)
        object.__setattr__(self, "generator_id", generator_id)
        object.__setattr__(self, "generator_model_revision_hash", model_revision_hash)
        object.__setattr__(self, "prompt_template_hash", prompt_template_hash)
        object.__setattr__(self, "campaign_hash", campaign_hash)

    def primitive(self, primitive_id: str) -> FactorPrimitive | None:
        return next((item for item in self.primitives if item.primitive_id == primitive_id), None)


@dataclass(frozen=True, slots=True)
class FactorCandidateProposal:
    """A structured feature-and-parameter proposal, never source code or a signal."""

    campaign_id: str
    candidate_id: str
    primitive_id: str
    direction: float
    parameters_json: str
    candidate_hash: str = field(init=False)

    @classmethod
    def create(
        cls,
        *,
        campaign_id: str,
        candidate_id: str,
        primitive_id: str,
        direction: float,
        parameters: Mapping[str, object],
    ) -> "FactorCandidateProposal":
        return cls(
            campaign_id=campaign_id,
            candidate_id=candidate_id,
            primitive_id=primitive_id,
            direction=direction,
            parameters_json=_parameter_mapping_json(parameters, "candidate.parameters"),
        )

    @property
    def parameters(self) -> dict[str, ParameterScalar]:
        return _load_parameter_mapping(self.parameters_json, "candidate.parameters_json")

    def __post_init__(self) -> None:
        campaign_id = _identifier(self.campaign_id, "candidate.campaign_id")
        candidate_id = _identifier(self.candidate_id, "candidate.candidate_id")
        primitive_id = _identifier(self.primitive_id, "candidate.primitive_id")
        direction = _direction(self.direction, "candidate.direction")
        parameters = _load_parameter_mapping(self.parameters_json, "candidate.parameters_json")
        candidate_hash = canonical_json_sha256(
            {
                "campaign_id": campaign_id,
                "candidate_id": candidate_id,
                "direction": direction.hex(),
                "format": "northstar.factor-mining-candidate.v1",
                "parameters": parameters,
                "primitive_id": primitive_id,
            }
        )
        object.__setattr__(self, "campaign_id", campaign_id)
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "primitive_id", primitive_id)
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "candidate_hash", candidate_hash)


@dataclass(frozen=True, slots=True)
class FactorCandidateGenerationRequest:
    """The redacted request given to a generator: policy metadata, never market data."""

    campaign: FactorMiningCampaignSpec
    request_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self.campaign) is not FactorMiningCampaignSpec:
            raise FactorMiningError("generation request must bind an exact campaign spec")
        request_hash = canonical_json_sha256(
            {
                "campaign_hash": self.campaign.campaign_hash,
                "format": "northstar.factor-mining-generation-request.v1",
            }
        )
        object.__setattr__(self, "request_hash", request_hash)


@dataclass(frozen=True, slots=True)
class FactorCandidateGenerationReceipt:
    """Hash-only provider provenance plus one precommitted batch of candidates."""

    campaign_id: str
    campaign_hash: str
    generator_id: str
    generator_model_revision_hash: str
    prompt_template_hash: str
    provider_output_hash: str
    proposals: tuple[FactorCandidateProposal, ...]
    receipt_hash: str = field(init=False)

    def __post_init__(self) -> None:
        campaign_id = _identifier(self.campaign_id, "generation.campaign_id")
        campaign_hash = _hash(self.campaign_hash, "generation.campaign_hash")
        generator_id = _safe_token(self.generator_id, "generation.generator_id")
        model_revision_hash = _hash(
            self.generator_model_revision_hash,
            "generation.generator_model_revision_hash",
        )
        prompt_template_hash = _hash(self.prompt_template_hash, "generation.prompt_template_hash")
        provider_output_hash = _hash(self.provider_output_hash, "generation.provider_output_hash")
        proposals = tuple(self.proposals)
        if not proposals or not all(type(item) is FactorCandidateProposal for item in proposals):
            raise FactorMiningError("generation.proposals must contain FactorCandidateProposal records")
        if tuple(sorted(proposals, key=lambda item: item.candidate_id)) != proposals:
            raise FactorMiningError("generation.proposals must be sorted by candidate_id")
        if len({item.candidate_id for item in proposals}) != len(proposals):
            raise FactorMiningError("generation.proposals cannot contain duplicate candidate IDs")
        if any(item.campaign_id != campaign_id for item in proposals):
            raise FactorMiningError("generation proposals must bind the receipt campaign_id")
        receipt_hash = canonical_json_sha256(
            {
                "campaign_hash": campaign_hash,
                "candidate_hashes": [item.candidate_hash for item in proposals],
                "format": "northstar.factor-mining-generation-receipt.v1",
                "generator_id": generator_id,
                "generator_model_revision_hash": model_revision_hash,
                "prompt_template_hash": prompt_template_hash,
                "provider_output_hash": provider_output_hash,
            }
        )
        object.__setattr__(self, "campaign_id", campaign_id)
        object.__setattr__(self, "campaign_hash", campaign_hash)
        object.__setattr__(self, "generator_id", generator_id)
        object.__setattr__(self, "generator_model_revision_hash", model_revision_hash)
        object.__setattr__(self, "prompt_template_hash", prompt_template_hash)
        object.__setattr__(self, "provider_output_hash", provider_output_hash)
        object.__setattr__(self, "proposals", proposals)
        object.__setattr__(self, "receipt_hash", receipt_hash)

    def require_campaign(self, campaign: FactorMiningCampaignSpec) -> None:
        if type(campaign) is not FactorMiningCampaignSpec:
            raise FactorMiningError("campaign must be an exact FactorMiningCampaignSpec")
        if (
            self.campaign_id != campaign.campaign_id
            or self.campaign_hash != campaign.campaign_hash
            or self.generator_id != campaign.generator_id
            or self.generator_model_revision_hash != campaign.generator_model_revision_hash
            or self.prompt_template_hash != campaign.prompt_template_hash
        ):
            raise FactorMiningError("generation receipt does not exactly match the sealed campaign")
        if len(self.proposals) > campaign.budget.max_candidates:
            raise FactorMiningError("generation receipt exceeds the sealed candidate budget")


class CandidateValidationStatus(str, Enum):
    """Validation does not admit a strategy or a trading candidate."""

    VALIDATED_FOR_RESEARCH = "validated_for_research"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class FactorCandidateValidation:
    """A reason-coded result of validating one declaration against a sealed policy."""

    campaign_id: str
    campaign_hash: str
    candidate: FactorCandidateProposal
    status: CandidateValidationStatus
    reason_code: str
    factor_definition: FactorDefinition | None
    validation_hash: str = field(init=False)

    def __post_init__(self) -> None:
        campaign_id = _identifier(self.campaign_id, "validation.campaign_id")
        campaign_hash = _hash(self.campaign_hash, "validation.campaign_hash")
        if type(self.candidate) is not FactorCandidateProposal:
            raise FactorMiningError("validation.candidate must be an exact FactorCandidateProposal")
        if self.candidate.campaign_id != campaign_id:
            raise FactorMiningError("validation candidate must bind the campaign_id")
        if type(self.status) is not CandidateValidationStatus:
            raise FactorMiningError("validation.status must be CandidateValidationStatus")
        if not isinstance(self.reason_code, str) or _REASON_CODE_RE.fullmatch(self.reason_code) is None:
            raise FactorMiningError("validation.reason_code must be an upper-case stable reason code")
        definition = self.factor_definition
        if self.status is CandidateValidationStatus.VALIDATED_FOR_RESEARCH:
            if self.reason_code != "VALIDATED_FOR_RESEARCH" or type(definition) is not FactorDefinition:
                raise FactorMiningError("validated candidates require an exact factor definition")
            if definition.role is not FactorRole.ALPHA or definition.risk_budget != 1.0:
                raise FactorMiningError("validated factor must be the single research alpha")
        elif definition is not None:
            raise FactorMiningError("rejected validation must not carry a factor definition")
        validation_hash = canonical_json_sha256(
            {
                "campaign_hash": campaign_hash,
                "candidate_hash": self.candidate.candidate_hash,
                "factor_definition_hash": (
                    definition.definition_hash if definition is not None else None
                ),
                "format": "northstar.factor-mining-candidate-validation.v1",
                "reason_code": self.reason_code,
                "status": self.status.value,
            }
        )
        object.__setattr__(self, "campaign_id", campaign_id)
        object.__setattr__(self, "campaign_hash", campaign_hash)
        object.__setattr__(self, "validation_hash", validation_hash)

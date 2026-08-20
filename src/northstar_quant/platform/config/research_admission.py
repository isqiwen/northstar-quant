"""候选策略研究准入政策的严格配置加载器。

本模块只定义研究结论门槛，不授予模拟、实盘或券商访问权限。任何 ``PASS`` 仍须经过
后续人工评审和独立的交易安全门禁。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from northstar_quant.platform.config.data_sources import get_data_source
from northstar_quant.platform.config.settings import get_settings
from northstar_quant.platform.config.yaml_loader import load_yaml


class ResearchAdmissionConfigError(ValueError):
    """研究准入政策配置不完整或不安全。"""


_ROOT_FIELDS = frozenset(
    {
        "version",
        "policy_id",
        "name",
        "status",
        "scope",
        "source",
        "universe",
        "data",
        "sample",
        "risk",
        "stress",
        "robustness",
        "promotion",
    }
)
_SCOPE_FIELDS = frozenset({"market", "asset_type", "allowed_backtest_engines"})
_SOURCE_FIELDS = frozenset(
    {
        "required_source_id",
        "secondary_validation_source_id",
        "require_secondary_source_validation",
        "required_source_tier",
        "require_active_license",
        "required_purposes",
    }
)
_UNIVERSE_FIELDS = frozenset(
    {"universe_id", "required_member_tier", "min_product_coverage_ratio"}
)
_DATA_FIELDS = frozenset(
    {
        "min_complete_history_years",
        "target_history_years",
        "min_total_history_sessions",
        "require_actual_contract_data",
        "require_authoritative_trading_calendar",
        "require_complete_night_and_day_sessions",
        "require_authoritative_dynamic_rules",
        "max_unknown_missing_sessions",
        "max_unresolved_official_mismatches",
    }
)
_SAMPLE_FIELDS = frozenset(
    {
        "min_oos_years",
        "min_oos_trading_days",
        "min_walk_forward_folds",
        "min_positive_net_folds",
        "min_completed_oos_round_trips",
        "min_net_sharpe",
    }
)
_RISK_FIELDS = frozenset(
    {
        "max_oos_drawdown_fraction",
        "max_margin_to_equity",
        "min_available_funds_to_equity",
        "max_margin_call_count",
        "max_forced_liquidation_count",
    }
)
_STRESS_FIELDS = frozenset({"cost_stress_multipliers", "require_all_scenarios_pass"})
_ROBUSTNESS_FIELDS = frozenset(
    {
        "require_parameter_neighborhood_stability",
        "min_parameter_neighbor_count",
        "min_passing_neighbor_fraction",
        "require_immutable_trial_ledger",
    }
)
_PROMOTION_FIELDS = frozenset(
    {"allow_research_to_simulated", "requires_named_human_approval"}
)
_POLICY_STATUSES = frozenset({"draft", "pending_owner_approval", "active", "retired"})
_SOURCE_TIERS = frozenset({"commercial_licensed"})
_MEMBER_TIERS = frozenset({"core", "extension", "sample"})
_PURPOSES = frozenset({"internal_research", "historical_backtest", "model_validation"})


@dataclass(frozen=True, slots=True)
class AdmissionScope:
    market: str
    asset_type: str
    allowed_backtest_engines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AdmissionSourceRequirements:
    required_source_id: str
    secondary_validation_source_id: str | None
    require_secondary_source_validation: bool
    required_source_tier: str
    require_active_license: bool
    required_purposes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AdmissionUniverseRequirements:
    universe_id: str
    required_member_tier: str
    min_product_coverage_ratio: float


@dataclass(frozen=True, slots=True)
class AdmissionDataRequirements:
    min_complete_history_years: int
    target_history_years: int
    min_total_history_sessions: int
    require_actual_contract_data: bool
    require_authoritative_trading_calendar: bool
    require_complete_night_and_day_sessions: bool
    require_authoritative_dynamic_rules: bool
    max_unknown_missing_sessions: int
    max_unresolved_official_mismatches: int


@dataclass(frozen=True, slots=True)
class AdmissionSampleRequirements:
    min_oos_years: int
    min_oos_trading_days: int
    min_walk_forward_folds: int
    min_positive_net_folds: int
    min_completed_oos_round_trips: int
    min_net_sharpe: float


@dataclass(frozen=True, slots=True)
class AdmissionRiskRequirements:
    max_oos_drawdown_fraction: float
    max_margin_to_equity: float
    min_available_funds_to_equity: float
    max_margin_call_count: int
    max_forced_liquidation_count: int


@dataclass(frozen=True, slots=True)
class AdmissionStressRequirements:
    cost_stress_multipliers: tuple[float, ...]
    require_all_scenarios_pass: bool


@dataclass(frozen=True, slots=True)
class AdmissionRobustnessRequirements:
    require_parameter_neighborhood_stability: bool
    min_parameter_neighbor_count: int
    min_passing_neighbor_fraction: float
    require_immutable_trial_ledger: bool


@dataclass(frozen=True, slots=True)
class AdmissionPromotionRequirements:
    allow_research_to_simulated: bool
    requires_named_human_approval: bool


@dataclass(frozen=True, slots=True)
class ResearchAdmissionPolicy:
    """一份完整的候选策略准入政策。"""

    policy_id: str
    name: str
    status: str
    scope: AdmissionScope
    source: AdmissionSourceRequirements
    universe: AdmissionUniverseRequirements
    data: AdmissionDataRequirements
    sample: AdmissionSampleRequirements
    risk: AdmissionRiskRequirements
    stress: AdmissionStressRequirements
    robustness: AdmissionRobustnessRequirements
    promotion: AdmissionPromotionRequirements


def get_research_admission_directory(path: str | Path | None = None) -> Path:
    """返回研究准入政策目录。"""

    if path is None:
        return get_settings().project_root / "configs" / "research" / "admission"
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = get_settings().project_root / candidate
    return candidate.resolve()


def get_research_admission_policy_path(
    policy_id: str,
    directory: str | Path | None = None,
) -> Path:
    """按稳定 policy_id 返回唯一 YAML 路径。"""

    return get_research_admission_directory(directory) / f"{_required_text(policy_id, 'policy_id')}.yaml"


def load_research_admission_policy(
    policy_id: str,
    directory: str | Path | None = None,
) -> ResearchAdmissionPolicy:
    """读取并严格校验一份研究准入政策。"""

    config_path = get_research_admission_policy_path(policy_id, directory)
    if not config_path.is_file():
        raise ResearchAdmissionConfigError(f"研究准入政策不存在：{config_path}")
    payload = load_yaml(config_path)
    if not isinstance(payload, dict) or set(payload) != _ROOT_FIELDS:
        raise ResearchAdmissionConfigError("研究准入政策字段不完整或包含未知字段")
    if payload["version"] != 1:
        raise ResearchAdmissionConfigError("研究准入政策 version 当前必须为 1")
    configured_id = _required_text(payload["policy_id"], "policy_id")
    if configured_id != _required_text(policy_id, "policy_id"):
        raise ResearchAdmissionConfigError(
            f"研究准入文件与声明 ID 不一致：请求 {policy_id}，配置声明 {configured_id}"
        )
    policy = ResearchAdmissionPolicy(
        policy_id=configured_id,
        name=_required_text(payload["name"], "name"),
        status=_choice(payload["status"], _POLICY_STATUSES, "status"),
        scope=_parse_scope(payload["scope"]),
        source=_parse_source(payload["source"]),
        universe=_parse_universe(payload["universe"]),
        data=_parse_data(payload["data"]),
        sample=_parse_sample(payload["sample"]),
        risk=_parse_risk(payload["risk"]),
        stress=_parse_stress(payload["stress"]),
        robustness=_parse_robustness(payload["robustness"]),
        promotion=_parse_promotion(payload["promotion"]),
    )
    _validate_policy(policy)
    return policy


def research_admission_policy_sha256(policy: ResearchAdmissionPolicy) -> str:
    """计算准入政策指纹，供回测运行清单冻结。"""

    encoded = json.dumps(
        asdict(policy),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_scope(payload: Any) -> AdmissionScope:
    field = "scope"
    _object(payload, field, _SCOPE_FIELDS)
    return AdmissionScope(
        market=_required_text(payload["market"], f"{field}.market").upper(),
        asset_type=_required_text(payload["asset_type"], f"{field}.asset_type").upper(),
        allowed_backtest_engines=_text_list(
            payload["allowed_backtest_engines"], f"{field}.allowed_backtest_engines", minimum=1
        ),
    )


def _parse_source(payload: Any) -> AdmissionSourceRequirements:
    field = "source"
    _object(payload, field, _SOURCE_FIELDS)
    return AdmissionSourceRequirements(
        required_source_id=_required_text(payload["required_source_id"], f"{field}.required_source_id"),
        secondary_validation_source_id=(
            _required_text(
                payload["secondary_validation_source_id"],
                f"{field}.secondary_validation_source_id",
            )
            if payload["secondary_validation_source_id"] is not None
            else None
        ),
        require_secondary_source_validation=_boolean(
            payload["require_secondary_source_validation"],
            f"{field}.require_secondary_source_validation",
        ),
        required_source_tier=_choice(
            payload["required_source_tier"], _SOURCE_TIERS, f"{field}.required_source_tier"
        ),
        require_active_license=_boolean(
            payload["require_active_license"], f"{field}.require_active_license"
        ),
        required_purposes=_choice_list(
            payload["required_purposes"], _PURPOSES, f"{field}.required_purposes", minimum=1
        ),
    )


def _parse_universe(payload: Any) -> AdmissionUniverseRequirements:
    field = "universe"
    _object(payload, field, _UNIVERSE_FIELDS)
    return AdmissionUniverseRequirements(
        universe_id=_required_text(payload["universe_id"], f"{field}.universe_id"),
        required_member_tier=_choice(
            payload["required_member_tier"], _MEMBER_TIERS, f"{field}.required_member_tier"
        ),
        min_product_coverage_ratio=_ratio(
            payload["min_product_coverage_ratio"], f"{field}.min_product_coverage_ratio"
        ),
    )


def _parse_data(payload: Any) -> AdmissionDataRequirements:
    field = "data"
    _object(payload, field, _DATA_FIELDS)
    return AdmissionDataRequirements(
        min_complete_history_years=_positive_int(
            payload["min_complete_history_years"], f"{field}.min_complete_history_years"
        ),
        target_history_years=_positive_int(
            payload["target_history_years"], f"{field}.target_history_years"
        ),
        min_total_history_sessions=_positive_int(
            payload["min_total_history_sessions"], f"{field}.min_total_history_sessions"
        ),
        require_actual_contract_data=_boolean(
            payload["require_actual_contract_data"], f"{field}.require_actual_contract_data"
        ),
        require_authoritative_trading_calendar=_boolean(
            payload["require_authoritative_trading_calendar"],
            f"{field}.require_authoritative_trading_calendar",
        ),
        require_complete_night_and_day_sessions=_boolean(
            payload["require_complete_night_and_day_sessions"],
            f"{field}.require_complete_night_and_day_sessions",
        ),
        require_authoritative_dynamic_rules=_boolean(
            payload["require_authoritative_dynamic_rules"],
            f"{field}.require_authoritative_dynamic_rules",
        ),
        max_unknown_missing_sessions=_nonnegative_int(
            payload["max_unknown_missing_sessions"], f"{field}.max_unknown_missing_sessions"
        ),
        max_unresolved_official_mismatches=_nonnegative_int(
            payload["max_unresolved_official_mismatches"],
            f"{field}.max_unresolved_official_mismatches",
        ),
    )


def _parse_sample(payload: Any) -> AdmissionSampleRequirements:
    field = "sample"
    _object(payload, field, _SAMPLE_FIELDS)
    return AdmissionSampleRequirements(
        min_oos_years=_positive_int(payload["min_oos_years"], f"{field}.min_oos_years"),
        min_oos_trading_days=_positive_int(
            payload["min_oos_trading_days"], f"{field}.min_oos_trading_days"
        ),
        min_walk_forward_folds=_positive_int(
            payload["min_walk_forward_folds"], f"{field}.min_walk_forward_folds"
        ),
        min_positive_net_folds=_positive_int(
            payload["min_positive_net_folds"], f"{field}.min_positive_net_folds"
        ),
        min_completed_oos_round_trips=_positive_int(
            payload["min_completed_oos_round_trips"],
            f"{field}.min_completed_oos_round_trips",
        ),
        min_net_sharpe=_finite_number(payload["min_net_sharpe"], f"{field}.min_net_sharpe"),
    )


def _parse_risk(payload: Any) -> AdmissionRiskRequirements:
    field = "risk"
    _object(payload, field, _RISK_FIELDS)
    return AdmissionRiskRequirements(
        max_oos_drawdown_fraction=_ratio(
            payload["max_oos_drawdown_fraction"], f"{field}.max_oos_drawdown_fraction"
        ),
        max_margin_to_equity=_ratio(
            payload["max_margin_to_equity"], f"{field}.max_margin_to_equity"
        ),
        min_available_funds_to_equity=_ratio(
            payload["min_available_funds_to_equity"], f"{field}.min_available_funds_to_equity"
        ),
        max_margin_call_count=_nonnegative_int(
            payload["max_margin_call_count"], f"{field}.max_margin_call_count"
        ),
        max_forced_liquidation_count=_nonnegative_int(
            payload["max_forced_liquidation_count"], f"{field}.max_forced_liquidation_count"
        ),
    )


def _parse_stress(payload: Any) -> AdmissionStressRequirements:
    field = "stress"
    _object(payload, field, _STRESS_FIELDS)
    raw_multipliers = payload["cost_stress_multipliers"]
    if not isinstance(raw_multipliers, list) or not raw_multipliers:
        raise ResearchAdmissionConfigError(f"{field}.cost_stress_multipliers 必须是非空列表")
    multipliers = tuple(
        _positive_number(value, f"{field}.cost_stress_multipliers[]")
        for value in raw_multipliers
    )
    if tuple(sorted(set(multipliers))) != multipliers or multipliers[0] != 1.0:
        raise ResearchAdmissionConfigError(
            f"{field}.cost_stress_multipliers 必须从 1.0 开始且严格升序"
        )
    return AdmissionStressRequirements(
        cost_stress_multipliers=multipliers,
        require_all_scenarios_pass=_boolean(
            payload["require_all_scenarios_pass"], f"{field}.require_all_scenarios_pass"
        ),
    )


def _parse_robustness(payload: Any) -> AdmissionRobustnessRequirements:
    field = "robustness"
    _object(payload, field, _ROBUSTNESS_FIELDS)
    return AdmissionRobustnessRequirements(
        require_parameter_neighborhood_stability=_boolean(
            payload["require_parameter_neighborhood_stability"],
            f"{field}.require_parameter_neighborhood_stability",
        ),
        min_parameter_neighbor_count=_positive_int(
            payload["min_parameter_neighbor_count"], f"{field}.min_parameter_neighbor_count"
        ),
        min_passing_neighbor_fraction=_ratio(
            payload["min_passing_neighbor_fraction"], f"{field}.min_passing_neighbor_fraction"
        ),
        require_immutable_trial_ledger=_boolean(
            payload["require_immutable_trial_ledger"],
            f"{field}.require_immutable_trial_ledger",
        ),
    )


def _parse_promotion(payload: Any) -> AdmissionPromotionRequirements:
    field = "promotion"
    _object(payload, field, _PROMOTION_FIELDS)
    return AdmissionPromotionRequirements(
        allow_research_to_simulated=_boolean(
            payload["allow_research_to_simulated"], f"{field}.allow_research_to_simulated"
        ),
        requires_named_human_approval=_boolean(
            payload["requires_named_human_approval"],
            f"{field}.requires_named_human_approval",
        ),
    )


def _validate_policy(policy: ResearchAdmissionPolicy) -> None:
    if policy.data.target_history_years < policy.data.min_complete_history_years:
        raise ResearchAdmissionConfigError("data.target_history_years 不能小于最小完整历史年数")
    if policy.sample.min_positive_net_folds > policy.sample.min_walk_forward_folds:
        raise ResearchAdmissionConfigError("sample.min_positive_net_folds 不能大于 min_walk_forward_folds")
    if policy.status == "active" and policy.promotion.allow_research_to_simulated:
        raise ResearchAdmissionConfigError(
            "研究准入政策不得自动将 research 升级为 simulated；必须单独审批"
        )
    if not policy.promotion.requires_named_human_approval:
        raise ResearchAdmissionConfigError("研究准入政策必须要求具名人工审批")
    get_data_source(policy.source.required_source_id)
    if policy.source.secondary_validation_source_id is not None:
        if policy.source.secondary_validation_source_id == policy.source.required_source_id:
            raise ResearchAdmissionConfigError("备供应商不能与主供应商相同")
        get_data_source(policy.source.secondary_validation_source_id)
    elif policy.source.require_secondary_source_validation:
        raise ResearchAdmissionConfigError("要求备源验证时必须配置 secondary_validation_source_id")


def _object(payload: Any, field: str, expected_fields: frozenset[str]) -> None:
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise ResearchAdmissionConfigError(f"{field} 字段不完整或包含未知字段")


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchAdmissionConfigError(f"{field} 必须是非空字符串")
    return value.strip()


def _text_list(value: object, field: str, *, minimum: int = 0) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ResearchAdmissionConfigError(f"{field} 必须是列表")
    values = tuple(_required_text(item, f"{field}[]") for item in value)
    if len(values) < minimum or len(values) != len(set(values)):
        raise ResearchAdmissionConfigError(f"{field} 必须满足最小数量且不能重复")
    return values


def _choice(value: object, allowed: frozenset[str], field: str) -> str:
    normalized = _required_text(value, field).lower()
    if normalized not in allowed:
        raise ResearchAdmissionConfigError(
            f"{field} 取值无效；仅支持：{', '.join(sorted(allowed))}"
        )
    return normalized


def _choice_list(
    value: object,
    allowed: frozenset[str],
    field: str,
    *,
    minimum: int = 0,
) -> tuple[str, ...]:
    values = tuple(item.lower() for item in _text_list(value, field, minimum=minimum))
    if len(values) != len(set(values)):
        raise ResearchAdmissionConfigError(f"{field} 规范化后不能重复")
    invalid = sorted(set(values).difference(allowed))
    if invalid:
        raise ResearchAdmissionConfigError(f"{field} 包含不支持的值：{', '.join(invalid)}")
    return values


def _boolean(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ResearchAdmissionConfigError(f"{field} 必须是布尔值")
    return value


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ResearchAdmissionConfigError(f"{field} 必须是正整数")
    return value


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ResearchAdmissionConfigError(f"{field} 必须是非负整数")
    return value


def _finite_number(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ResearchAdmissionConfigError(f"{field} 必须是有限数值")
    if not isinstance(value, (int, float, str)):
        raise ResearchAdmissionConfigError(f"{field} 必须是有限数值")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ResearchAdmissionConfigError(f"{field} 必须是有限数值") from exc
    if not math.isfinite(numeric):
        raise ResearchAdmissionConfigError(f"{field} 必须是有限数值")
    return numeric


def _positive_number(value: object, field: str) -> float:
    numeric = _finite_number(value, field)
    if numeric <= 0:
        raise ResearchAdmissionConfigError(f"{field} 必须大于 0")
    return numeric


def _ratio(value: object, field: str) -> float:
    numeric = _finite_number(value, field)
    if not 0 <= numeric <= 1:
        raise ResearchAdmissionConfigError(f"{field} 必须位于 [0, 1]")
    return numeric

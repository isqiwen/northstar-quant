"""候选策略研究准入评估。

评估是纯读取函数：它不会改写画像、数据、账户或订单，也不会因不通过而阻止普通离线
回测生成报告。它的唯一作用是把“可继续人工研究”的证据和缺口明确写入制品。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import math
from typing import Literal, Mapping

import polars as pl

from northstar_quant.foundation.config.data_sources import (
    DataSourceConfig,
    data_source_config_sha256,
    get_data_source,
)
from northstar_quant.data.contracts.instrument_universes import InstrumentUniverse, load_instrument_universe
from northstar_quant.foundation.config.research_admission import (
    ResearchAdmissionPolicy,
    load_research_admission_policy,
    research_admission_policy_sha256,
)
from northstar_quant.foundation.config.trading_profile import TradingProfile
from northstar_quant.research.validation.lookahead import LookaheadCertificate


AdmissionCheckStatus = Literal["PASS", "FAIL", "MISSING", "NOT_APPLICABLE"]
AdmissionStatus = Literal[
    "PASS",
    "NOT_ELIGIBLE",
    "INSUFFICIENT_EVIDENCE",
    "NOT_APPLICABLE",
    "NOT_CONFIGURED",
]


@dataclass(frozen=True, slots=True)
class AdmissionCheck:
    """一个可定位的准入检查结果。"""

    check_id: str
    status: AdmissionCheckStatus
    actual: object
    threshold: object
    message: str


@dataclass(frozen=True, slots=True)
class ResearchAdmissionResult:
    """一份确定性准入结论；不含运行时钟，因而可进入回测指纹。"""

    policy_id: str | None
    policy_config_sha256: str | None
    status: AdmissionStatus
    eligible_for_human_review: bool
    source_id: str | None
    target_universe_id: str | None
    checks: tuple[AdmissionCheck, ...]
    summary: str

    def to_dict(self) -> dict[str, object]:
        """返回可直接写入 JSON/manifest 的结构。"""

        payload: dict[str, object] = {
            "policy_id": self.policy_id,
            "policy_config_sha256": self.policy_config_sha256,
            "status": self.status,
            "eligible_for_human_review": self.eligible_for_human_review,
            "source_id": self.source_id,
            "target_universe_id": self.target_universe_id,
            "checks": [asdict(check) for check in self.checks],
            "summary": self.summary,
        }
        payload["blocking_check_count"] = sum(
            check.status in {"FAIL", "MISSING"} for check in self.checks
        )
        return payload


def evaluate_research_admission(
    profile: TradingProfile,
    *,
    source_manifest: Mapping[str, object],
    raw_market_df: pl.DataFrame,
    equity_curve: list[dict[str, object]],
    performance: Mapping[str, object],
    execution: Mapping[str, object],
    evidence: Mapping[str, object] | None = None,
    policy_override: ResearchAdmissionPolicy | None = None,
    source_override: DataSourceConfig | None = None,
    universe_override: InstrumentUniverse | None = None,
    lookahead_certificate: LookaheadCertificate | None = None,
) -> ResearchAdmissionResult:
    """评估本次回测能否进入候选策略的人工研究评审。

    ``evidence`` 是未来 ExperimentSpec/压力回测可冻结的补充证据入口。当前普通回测不会
    凭空构造其中的 walk-forward、参数邻域或闭合交易数据，因此缺失时一律如实返回
    ``INSUFFICIENT_EVIDENCE``。
    """

    if not profile.research_admission.enabled:
        return ResearchAdmissionResult(
            policy_id=profile.research_admission.policy_id,
            policy_config_sha256=None,
            status="NOT_CONFIGURED",
            eligible_for_human_review=False,
            source_id=profile.data.source_id or None,
            target_universe_id=None,
            checks=(),
            summary="该画像未启用候选策略研究准入；回测结果仅可作探索或工程验收。",
        )
    policy_id = profile.research_admission.policy_id
    if policy_id is None:
        raise ValueError("research_admission 已启用但缺少 policy_id")
    policy = policy_override or load_research_admission_policy(policy_id)
    source = source_override or (
        get_data_source(profile.data.source_id) if profile.data.source_id else None
    )
    universe = universe_override or load_instrument_universe(policy.universe.universe_id)
    checks: list[AdmissionCheck] = []
    evidence = evidence or {}

    if profile.backtest.engine not in policy.scope.allowed_backtest_engines:
        checks.append(
            _check(
                "engine.applicability",
                "NOT_APPLICABLE",
                profile.backtest.engine,
                list(policy.scope.allowed_backtest_engines),
                "该回测器不具备实际合约候选策略准入口径。",
            )
        )
        return _result(policy, source, universe, checks, "NOT_APPLICABLE")

    _append_scope_checks(checks, profile, policy)
    _append_policy_activation_check(checks, policy)
    _append_source_checks(checks, source, source_manifest, policy, evidence)
    _append_data_checks(
        checks,
        profile,
        source,
        raw_market_df,
        source_manifest,
        universe,
        policy,
        evidence,
    )
    _append_point_in_time_check(checks, source_manifest, lookahead_certificate)
    _append_sample_checks(checks, equity_curve, performance, execution, policy, evidence)
    _append_risk_checks(checks, performance, execution, policy, evidence)
    _append_stress_and_robustness_checks(checks, policy, evidence)
    return _result(policy, source, universe, checks)


def _append_point_in_time_check(
    checks: list[AdmissionCheck],
    source_manifest: Mapping[str, object],
    lookahead_certificate: LookaheadCertificate | None,
) -> None:
    """阻止调用方用任意字典把 static as-of 提升为逐决策 PIT 策略证据。"""

    point_in_time = source_manifest.get("point_in_time")
    if not isinstance(point_in_time, Mapping):
        checks.append(
            _check(
                "data.point_in_time_decision_safety",
                "MISSING",
                None,
                True,
                "数据清单没有逐决策 PIT 安全声明，不能作为候选策略准入证据。",
            )
        )
        return
    safe = point_in_time.get("decision_time_safe")
    selection_mode = point_in_time.get("selection_mode")
    certificate_hash = point_in_time.get("certificate_hash")
    if lookahead_certificate is not None:
        if not isinstance(lookahead_certificate, LookaheadCertificate):
            raise ValueError("lookahead_certificate 必须是 LookaheadCertificate 或 null")
        if (
            safe is True
            and selection_mode == lookahead_certificate.selection_mode
            and certificate_hash == lookahead_certificate.certificate_hash
            and lookahead_certificate.decision_time_safe
        ):
            # P2-WP05 目前只建立了验证合同；严格 Application composition root、逐时点
            # 策略/目标重放及三种回测器的 guarded input 尚未接通。证书不能单独把一个
            # 调用方提交的 mapping 升级为候选资格，避免通过手工构造证据绕过未来入口。
            checks.append(
                _check(
                    "data.point_in_time_decision_safety",
                    "MISSING",
                    {
                        "certificate_hash": certificate_hash,
                        "decision_time_safe": safe,
                        "selection_mode": selection_mode,
                    },
                    "已接通的 strict Application decision-replay run",
                    "逐决策证书已绑定，但 strict 回测编排尚未接通；不能提升为候选策略。",
                )
            )
            return
        checks.append(
            _check(
                "data.point_in_time_decision_safety",
                "FAIL",
                {
                    "certificate_hash": certificate_hash,
                    "decision_time_safe": safe,
                    "selection_mode": selection_mode,
                },
                {
                    "certificate_hash": lookahead_certificate.certificate_hash,
                    "decision_time_safe": True,
                    "selection_mode": "PER_DECISION_POINT_IN_TIME_REPLAY",
                },
                "运行清单的逐决策 PIT 声明没有精确绑定 LookaheadCertificate。",
            )
        )
        return
    if safe is True and selection_mode == "PER_DECISION_POINT_IN_TIME_REPLAY":
        checks.append(
            _check(
                "data.point_in_time_decision_safety",
                "FAIL",
                {"decision_time_safe": safe, "selection_mode": selection_mode},
                "LookaheadCertificate",
                "逐决策 PIT 声明缺少 LookaheadCertificate，不能作为候选策略准入证据。",
            )
        )
        return
    checks.append(
        _check(
            "data.point_in_time_decision_safety",
            "FAIL" if safe is not None else "MISSING",
            {"decision_time_safe": safe, "selection_mode": selection_mode},
            {
                "decision_time_safe": True,
                "selection_mode": "PER_DECISION_POINT_IN_TIME_REPLAY",
            },
            "单一静态 as-of 或未声明 PIT 安全的数据不能进入候选策略准入。",
        )
    )


def _append_scope_checks(
    checks: list[AdmissionCheck],
    profile: TradingProfile,
    policy: ResearchAdmissionPolicy,
) -> None:
    checks.append(
        _check(
            "scope.dimensions",
            "PASS"
            if (profile.market.value, profile.asset_type.value)
            == (policy.scope.market, policy.scope.asset_type)
            else "FAIL",
            {"market": profile.market.value, "asset_type": profile.asset_type.value},
            {"market": policy.scope.market, "asset_type": policy.scope.asset_type},
            "画像维度必须与准入政策范围一致。",
        )
    )
    actual_contract_profile = profile.futures is not None and not profile.futures.symbols_are_continuous
    checks.append(
        _check(
            "data.actual_contract_input",
            "PASS" if actual_contract_profile else "FAIL",
            actual_contract_profile,
            policy.data.require_actual_contract_data,
            "候选准入只接受实际合约链，连续合约收益研究不能替代实际可交易输入。",
        )
    )


def _append_policy_activation_check(
    checks: list[AdmissionCheck], policy: ResearchAdmissionPolicy
) -> None:
    checks.append(
        _check(
            "policy.owner_activation",
            "PASS" if policy.status == "active" else "MISSING",
            policy.status,
            "active",
            "准入政策须由项目所有者在合同、证据和审批齐备后显式激活。",
        )
    )


def _append_source_checks(
    checks: list[AdmissionCheck],
    source: DataSourceConfig | None,
    source_manifest: Mapping[str, object],
    policy: ResearchAdmissionPolicy,
    evidence: Mapping[str, object],
) -> None:
    if source is None:
        checks.append(
            _check(
                "source.binding",
                "MISSING",
                None,
                "profile.data.source_id",
                "画像没有绑定数据法律/运营来源，不能作候选策略准入。",
            )
        )
        return
    checks.extend(
        (
            _check(
                "source.primary_identity",
                "PASS" if source.source_id == policy.source.required_source_id else "FAIL",
                source.source_id,
                policy.source.required_source_id,
                "候选准入主数据源必须是政策指定的首选供应商；备源验证须另行冻结证据。",
            ),
            _check(
                "source.tier",
                "PASS" if source.tier == policy.source.required_source_tier else "FAIL",
                source.tier,
                policy.source.required_source_tier,
                "候选准入需要已授权商业数据源，公开参考源仅可作探索研究。",
            ),
            _check(
                "source.license_active",
                "PASS" if (not policy.source.require_active_license or source.license.is_active) else "MISSING",
                source.license.status,
                "active",
                "供应商合同、有效期和脱敏合同引用必须齐备。",
            ),
            _check(
                "source.permitted_purposes",
                "PASS"
                if set(policy.source.required_purposes).issubset(source.license.permitted_purposes)
                else "MISSING",
                sorted(source.license.permitted_purposes),
                list(policy.source.required_purposes),
                "合同必须明确覆盖内部研究、历史回测和模型验证。",
            ),
        )
    )
    governance = source_manifest.get("governance")
    if not isinstance(governance, Mapping):
        checks.append(
            _check(
                "source.manifest_binding",
                "MISSING",
                None,
                source.source_id,
                "数据 manifest 缺少治理绑定，需重新发布数据制品。",
            )
        )
        return
    checks.append(
        _check(
            "source.manifest_binding",
            "PASS"
            if governance.get("source_id") == source.source_id
            and governance.get("source_config_sha256") == data_source_config_sha256(source)
            else "FAIL",
            {
                "source_id": governance.get("source_id"),
                "source_config_sha256": governance.get("source_config_sha256"),
            },
            {
                "source_id": source.source_id,
                "source_config_sha256": data_source_config_sha256(source),
            },
            "数据制品必须冻结当前数据源身份与配置指纹。",
        )
    )
    if policy.source.require_secondary_source_validation:
        secondary = evidence.get("secondary_source_validation")
        is_valid = (
            isinstance(secondary, Mapping)
            and secondary.get("source_id") == policy.source.secondary_validation_source_id
            and secondary.get("status") == "PASS"
        )
        checks.append(
            _check(
                "source.secondary_validation",
                "PASS" if is_valid else "MISSING",
                dict(secondary) if isinstance(secondary, Mapping) else secondary,
                {
                    "source_id": policy.source.secondary_validation_source_id,
                    "status": "PASS",
                },
                "必须冻结独立备供应商的逐日对账/交叉验证结论。",
            )
        )


def _append_data_checks(
    checks: list[AdmissionCheck],
    profile: TradingProfile,
    source: DataSourceConfig | None,
    raw_market_df: pl.DataFrame,
    source_manifest: Mapping[str, object],
    universe: InstrumentUniverse,
    policy: ResearchAdmissionPolicy,
    evidence: Mapping[str, object],
) -> None:
    products = _observed_products(raw_market_df)
    coverage = universe.product_coverage(products, tier=policy.universe.required_member_tier)
    checks.append(
        _check(
            "universe.product_coverage",
            "PASS" if coverage >= policy.universe.min_product_coverage_ratio else "FAIL",
            {"products": sorted(products), "coverage_ratio": coverage},
            {
                "universe_id": universe.universe_id,
                "member_tier": policy.universe.required_member_tier,
                "min_coverage_ratio": policy.universe.min_product_coverage_ratio,
            },
            "核心品种必须完整覆盖；扩展品种不能用来补足核心准入样本。",
        )
    )
    session_dates = _dataset_session_dates(raw_market_df)
    history_years = _calendar_years(session_dates)
    checks.extend(
        (
            _check(
                "data.history_sessions",
                "PASS" if len(session_dates) >= policy.data.min_total_history_sessions else "FAIL",
                len(session_dates),
                policy.data.min_total_history_sessions,
                "实际合约原始历史 session 数必须达到政策下限。",
            ),
            _check(
                "data.history_years",
                "PASS" if history_years >= policy.data.min_complete_history_years else "FAIL",
                history_years,
                policy.data.min_complete_history_years,
                "实际合约数据的自然日覆盖长度必须达到最小完整历史年数。",
            ),
        )
    )
    schema = source_manifest.get("schema")
    if not isinstance(schema, Mapping):
        schema = {}
    checks.append(
        _check(
            "data.complete_sessions",
            "PASS"
            if not policy.data.require_complete_night_and_day_sessions
            or schema.get("complete_trading_sessions") is True
            else "MISSING",
            schema.get("complete_trading_sessions"),
            policy.data.require_complete_night_and_day_sessions,
            "夜盘和日盘覆盖需由已冻结的数据校验结果证明。",
        )
    )
    checks.extend(
        (
            _check(
                "data.authoritative_calendar",
                "PASS"
                if source is not None and source.supported.authoritative_calendar
                else "MISSING",
                source.supported.authoritative_calendar if source is not None else None,
                True,
                "必须接入并冻结交易所权威交易日历与会话表。",
            ),
            _check(
                "data.authoritative_dynamic_rules",
                "PASS"
                if source is not None and source.supported.authoritative_dynamic_rules
                else "MISSING",
                source.supported.authoritative_dynamic_rules if source is not None else None,
                True,
                "动态保证金、手续费、涨跌停和限仓须有权威快照证据。",
            ),
        )
    )
    _append_evidence_limit_check(
        checks,
        "data.unknown_missing_sessions",
        evidence,
        "unknown_missing_sessions",
        policy.data.max_unknown_missing_sessions,
        "未知缺失交易时段数量不得超过阈值。",
    )
    _append_evidence_limit_check(
        checks,
        "data.unresolved_official_mismatches",
        evidence,
        "unresolved_official_mismatches",
        policy.data.max_unresolved_official_mismatches,
        "与交易所权威数据未解决的不一致数量不得超过阈值。",
    )


def _append_sample_checks(
    checks: list[AdmissionCheck],
    equity_curve: list[dict[str, object]],
    performance: Mapping[str, object],
    execution: Mapping[str, object],
    policy: ResearchAdmissionPolicy,
    evidence: Mapping[str, object],
) -> None:
    return_count = _finite_number(performance.get("return_observation_count"))
    checks.append(
        _check(
            "sample.oos_trading_days",
            "PASS"
            if return_count is not None and return_count >= policy.sample.min_oos_trading_days
            else "FAIL",
            return_count,
            policy.sample.min_oos_trading_days,
            "冻结样本外权益收益观测数必须达到门槛。",
        )
    )
    oos_years = _calendar_years(_equity_dates(equity_curve))
    checks.append(
        _check(
            "sample.oos_years",
            "PASS" if oos_years >= policy.sample.min_oos_years else "FAIL",
            oos_years,
            policy.sample.min_oos_years,
            "冻结样本外区间的自然日长度必须达到门槛。",
        )
    )
    _append_evidence_minimum_check(
        checks,
        "sample.walk_forward_folds",
        evidence,
        "walk_forward_fold_count",
        policy.sample.min_walk_forward_folds,
        "必须提供冻结的 walk-forward 折数。",
    )
    _append_evidence_minimum_check(
        checks,
        "sample.positive_net_folds",
        evidence,
        "positive_net_fold_count",
        policy.sample.min_positive_net_folds,
        "达到净正收益的样本外折数必须满足门槛。",
    )
    round_trips = evidence.get("completed_oos_round_trip_count")
    if round_trips is None:
        round_trips = execution.get("completed_oos_round_trip_count")
    _append_value_minimum_check(
        checks,
        "sample.completed_oos_round_trips",
        round_trips,
        policy.sample.min_completed_oos_round_trips,
        "当前回测只统计成交事件时必须补充期货闭合 round-trip 归因。",
    )
    sharpe = _finite_number(performance.get("sharpe_ratio"))
    checks.append(
        _check(
            "sample.net_sharpe",
            "PASS" if sharpe is not None and sharpe >= policy.sample.min_net_sharpe else "MISSING",
            sharpe,
            policy.sample.min_net_sharpe,
            "净夏普须在充足的冻结样本外数据上计算。",
        )
    )


def _append_risk_checks(
    checks: list[AdmissionCheck],
    performance: Mapping[str, object],
    execution: Mapping[str, object],
    policy: ResearchAdmissionPolicy,
    evidence: Mapping[str, object],
) -> None:
    drawdown = _finite_number(performance.get("max_drawdown"))
    checks.append(
        _check(
            "risk.max_drawdown",
            "PASS"
            if drawdown is not None and abs(min(drawdown, 0.0)) <= policy.risk.max_oos_drawdown_fraction
            else "MISSING",
            drawdown,
            policy.risk.max_oos_drawdown_fraction,
            "样本外最大回撤不得超过政策上限。",
        )
    )
    _append_value_limit_check(
        checks,
        "risk.max_margin_to_equity",
        execution.get("max_margin_ratio"),
        policy.risk.max_margin_to_equity,
        "最大保证金/权益不得超过政策上限。",
    )
    _append_value_minimum_check(
        checks,
        "risk.min_available_funds_to_equity",
        execution.get("min_available_funds_ratio"),
        policy.risk.min_available_funds_to_equity,
        "最低可用资金/权益不得低于政策下限。",
    )
    _append_evidence_limit_check(
        checks,
        "risk.margin_calls",
        evidence,
        "margin_call_count",
        policy.risk.max_margin_call_count,
        "保证金追缴次数不得超过政策上限。",
    )
    _append_evidence_limit_check(
        checks,
        "risk.forced_liquidations",
        evidence,
        "forced_liquidation_count",
        policy.risk.max_forced_liquidation_count,
        "强平次数不得超过政策上限。",
    )


def _append_stress_and_robustness_checks(
    checks: list[AdmissionCheck],
    policy: ResearchAdmissionPolicy,
    evidence: Mapping[str, object],
) -> None:
    stress = evidence.get("cost_stress")
    if not isinstance(stress, Mapping):
        checks.append(
            _check(
                "stress.cost_scenarios",
                "MISSING",
                None,
                list(policy.stress.cost_stress_multipliers),
                "必须冻结各成本压力情景的独立回测结果。",
            )
        )
    else:
        passed = all(stress.get(str(multiplier)) is True for multiplier in policy.stress.cost_stress_multipliers)
        checks.append(
            _check(
                "stress.cost_scenarios",
                "PASS" if passed else "FAIL",
                dict(stress),
                list(policy.stress.cost_stress_multipliers),
                "所有要求的成本压力情景均须通过。",
            )
        )
    _append_evidence_minimum_check(
        checks,
        "robustness.parameter_neighbors",
        evidence,
        "parameter_neighbor_count",
        policy.robustness.min_parameter_neighbor_count,
        "必须提供参数邻域数量和冻结结果。",
    )
    _append_evidence_minimum_check(
        checks,
        "robustness.passing_neighbor_fraction",
        evidence,
        "passing_neighbor_fraction",
        policy.robustness.min_passing_neighbor_fraction,
        "参数邻域通过比例必须满足政策下限。",
    )
    trial_ledger = evidence.get("immutable_trial_ledger")
    checks.append(
        _check(
            "robustness.immutable_trial_ledger",
            "PASS" if trial_ledger is True else "MISSING",
            trial_ledger,
            policy.robustness.require_immutable_trial_ledger,
            "必须保存不可变实验台账，避免只挑选最优参数。",
        )
    )


def _append_evidence_limit_check(
    checks: list[AdmissionCheck],
    check_id: str,
    evidence: Mapping[str, object],
    field: str,
    limit: int,
    message: str,
) -> None:
    value = evidence.get(field)
    _append_value_limit_check(checks, check_id, value, limit, message)


def _append_evidence_minimum_check(
    checks: list[AdmissionCheck],
    check_id: str,
    evidence: Mapping[str, object],
    field: str,
    minimum: float,
    message: str,
) -> None:
    _append_value_minimum_check(checks, check_id, evidence.get(field), minimum, message)


def _append_value_limit_check(
    checks: list[AdmissionCheck],
    check_id: str,
    value: object,
    limit: float,
    message: str,
) -> None:
    numeric = _finite_number(value)
    checks.append(
        _check(
            check_id,
            "MISSING" if numeric is None else ("PASS" if numeric <= limit else "FAIL"),
            numeric,
            limit,
            message,
        )
    )


def _append_value_minimum_check(
    checks: list[AdmissionCheck],
    check_id: str,
    value: object,
    minimum: float,
    message: str,
) -> None:
    numeric = _finite_number(value)
    checks.append(
        _check(
            check_id,
            "MISSING" if numeric is None else ("PASS" if numeric >= minimum else "FAIL"),
            numeric,
            minimum,
            message,
        )
    )


def _result(
    policy: ResearchAdmissionPolicy,
    source: DataSourceConfig | None,
    universe: InstrumentUniverse,
    checks: list[AdmissionCheck],
    status_override: AdmissionStatus | None = None,
) -> ResearchAdmissionResult:
    if status_override is not None:
        status = status_override
    elif any(check.status == "MISSING" for check in checks):
        status = "INSUFFICIENT_EVIDENCE"
    elif any(check.status == "FAIL" for check in checks):
        status = "NOT_ELIGIBLE"
    else:
        status = "PASS"
    summary = {
        "PASS": "已满足候选策略人工研究评审的配置化证据门槛；不构成模拟或实盘授权。",
        "NOT_ELIGIBLE": "证据完整但至少一项准入阈值未满足，不得提升为候选策略。",
        "INSUFFICIENT_EVIDENCE": "缺少必需的数据、授权、实验或压力证据，不得提升为候选策略。",
        "NOT_APPLICABLE": "当前回测器不适用于实际合约候选策略准入口径。",
        "NOT_CONFIGURED": "当前画像未配置研究准入政策。",
    }[status]
    return ResearchAdmissionResult(
        policy_id=policy.policy_id,
        policy_config_sha256=research_admission_policy_sha256(policy),
        status=status,
        eligible_for_human_review=status == "PASS",
        source_id=source.source_id if source is not None else None,
        target_universe_id=universe.universe_id,
        checks=tuple(checks),
        summary=summary,
    )


def _check(
    check_id: str,
    status: AdmissionCheckStatus,
    actual: object,
    threshold: object,
    message: str,
) -> AdmissionCheck:
    return AdmissionCheck(
        check_id=check_id,
        status=status,
        actual=actual,
        threshold=threshold,
        message=message,
    )


def _observed_products(frame: pl.DataFrame) -> set[str]:
    if "product" not in frame.columns:
        return set()
    return {
        str(value).strip().upper()
        for value in frame.get_column("product").unique().to_list()
        if str(value).strip()
    }


def _dataset_session_dates(frame: pl.DataFrame) -> list[date]:
    if "date" not in frame.columns:
        return []
    values = frame.get_column("date").unique().sort().to_list()
    return [value for value in values if isinstance(value, date)]


def _equity_dates(equity_curve: list[dict[str, object]]) -> list[date]:
    dates: list[date] = []
    for row in equity_curve:
        raw = row.get("date")
        if not isinstance(raw, str):
            continue
        try:
            dates.append(date.fromisoformat(raw[:10]))
        except ValueError:
            continue
    return sorted(set(dates))


def _calendar_years(dates: list[date]) -> float:
    if len(dates) < 2:
        return 0.0
    return (dates[-1] - dates[0]).days / 365.25


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None

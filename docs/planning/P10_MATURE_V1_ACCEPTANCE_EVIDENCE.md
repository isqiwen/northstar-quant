# P10 Mature v1 Acceptance Evidence Register

> 建立包：P10-WP01。最近验收更新：P10-WP07（2026-08-23）；开发期 schema baseline 注记：2026-08-25。
> 此登记表是 P10 checklist 的证据索引，不是生产准入、真实资金批准或数据授权。除非某一项明确
> 标记为外部验收完成，所有证据都只能说明受控的 offline、paper 或 `ctp_sim` 边界。
>
> 2026-08-25：用户授权将开发期 Alembic 历史压缩为当前唯一
> `0001_current_schema_baseline`。以下出现的 `0008`、`0009` 和 `0010` 是原验收时的历史 revision 标识，
> 不再是当前仓库可升级的 migration 文件；它们表达的 schema/约束/触发器均已并入现行 baseline。

## 状态语义

| 状态 | 含义 |
|---|---|
| `VERIFIED_OFFLINE` | 受控本地实现和自动化测试已验证；不代表真实数据、账户或生产主机。 |
| `VERIFIED_SIMULATION` | 已在 paper 或本地 `ctp_sim` 验证；绝不等同真实 broker 或实盘。 |
| `SAFE_BOUNDARY` | 安全拒绝路径已经验证；它证明系统不会越权，不证明被拒绝能力已可用。 |
| `PARTIAL` | 基础组件存在，但 P10 所需的跨组件/运营级验收证据尚不完整。 |
| `INCOMPLETE` | 可离线实现的功能或 golden/contract 证据缺失，应由后续 WP 完成。 |
| `BLOCKED_EXTERNAL` | 需要数据 license、权威制品、账户、凭据、生产主机或人工批准；未满足时必须 `NO NEW RISK`。 |
| `HOSTED_EVIDENCE_PENDING` | 工作流/部署资产已在仓库中，但最终外部运行结果尚未由受控环境提供。 |

## 审计基线

- P10-WP04 的 focused unit/failure/e2e/golden/architecture suite 为 `16 passed`；`tests/portfolio_risk -q` 为
  `36 passed`；P8 provenance/execution regression subset 为 `14 passed`；全量回归为
  `1564 passed, 23 skipped`；Ruff 与 mypy baseline 通过。
- P10-WP05 完整门禁为 `-I -m pytest -q: 1682 passed, 23 skipped`；`ruff check .` 通过；mypy baseline
  保持 `33` 个既有诊断且 ratchet 通过；当时 Alembic 单一 head 为 `0008_portfolio_risk_approval`。这些是本地
  `ctp_sim` / PostgreSQL test evidence，不是 live CTP、真实账户或真实人工身份认证。
- P10-WP06 完整门禁为 `-I -m pytest -q: 1709 passed, 23 skipped`；Ruff、mypy baseline（33 个既有诊断）
  与 `git diff --check` 通过；当时 Alembic 单一 head 为 `0010_agent_run_audit_hardening`。该证据来自隔离本地
  PostgreSQL 测试实例，不是生产部署、真实 CTP、真实账户或真实资金操作。
- P10-WP03 focused E2E/failure/architecture suite：10 passed；`tests/research -q`：234 passed；Intelligence + fixture architecture：49 passed（隔离 P9 venv）。
- 全量回归：`1553 passed, 23 skipped`；Ruff、mypy baseline、hermetic bootstrap、offline dependency policy、lock 与 secret gate 已通过。本表只记录已核验的受控证据。
- 本文件只记录可复核路径与测试，不把 synthetic fixture、候选回执或文档声明升级为真实运营事实。

## Data

| ID / P10 验收项 | 状态 | 可复核证据 | 边界或后续动作 |
|---|---|---|---|
| D01 可信合约 / 日历 / 规则链 | `BLOCKED_EXTERNAL` | `data/contracts/contract_master.py`、`artifact_rulebook.py`、`calendars/service.py` 与 `test_contract_master.py`、`test_artifact_rulebook.py`、`test_cn_futures_calendar.py`、`test_calendar_submission_gate.py` 已验证 fail-closed。 | `configs/instruments/contract_master.yaml` 的 `contracts` 与 `rule_snapshots` 为空；`configs/calendars/README.md` 明确没有 runtime artifact；`configs/data/sources.yaml` 只有 unverified/procurement-pending 来源。需授权的权威制品；此前保持 `NO NEW RISK`。 |
| D02 数据版本不可覆盖 | `VERIFIED_OFFLINE` | `data/artifacts/immutable_store.py`；`test_immutable_artifact_store.py` 覆盖 no-replace、并发、篡改、PIT 和符号链接失败路径。 | 受控 ArtifactStore 范围；legacy mutable cache 不构成可信输入。 |
| D03 PIT correctness | `VERIFIED_OFFLINE` | `data/market/pit.py`；`test_market_pit.py`、`test_point_in_time_semantics.py`、`test_no_lookahead.py`、`test_lookahead_guard.py`、`test_decision_replay_composition.py`。 | 已验证 candidate/research replay；真实生产 PIT 仍依赖 D01 的外部制品。 |
| D04 质量 failure 阻断下游 | `VERIFIED_OFFLINE` | `data/quality/engine.py`、`sources/publisher.py`；`test_quality_engine.py`、`test_source_publisher.py` 与 immutable-store failure tests。 | FAIL 或关键 UNKNOWN 不能发布/重放；不替代真实来源授权。 |

## Intelligence

| ID / P10 验收项 | 状态 | 可复核证据 | 边界或后续动作 |
|---|---|---|---|
| I01 六个核心商品事件链 | `VERIFIED_OFFLINE` | `tests/intelligence/golden/six_commodity_fixture_only_v1.json`、`_fixture_corpus.py` 与 `test_six_commodity_intelligence_e2e.py` 对 copper、crude_oil、gold、iron_ore、soybean_meal、palm_oil 重放 Document→Event→merge→Mechanism→Impact，并断言 content/evidence span、Event 与 Feature-definition handoff commitments。 | 全部 URL、identity 与 crosswalk 均为 fixture-only；不代表授权 source、市场数据、日历、动态规则、真实合约、`FeatureValue` 或交易输入。 |
| I02 Evidence 可追溯 | `VERIFIED_OFFLINE` | `intelligence/domain/models.py` 的 Document/Event evidence；`intelligence/feature_projection/projection.py`；`test_feature_projection.py`、`test_intelligence_feature_projection_evidence.py`。 | 仅受控 Document→Feature projection；真实 source catalog/授权/持久摄取仍受 D01 阻断。 |
| I03 Ontology versioned | `VERIFIED_OFFLINE` | `ontology/loader.py` 与五份 version-locked ontology；`test_ontology.py::test_ontology_v1_has_required_event_categories_and_validates_unknowns`。 | 未知类型（包括交易动作）继续 fail-closed。 |
| I04 Event merge golden corpus | `VERIFIED_OFFLINE` | `test_six_commodity_merge.py`、`test_event_merge.py` 与 corpus 场景覆盖 multi-source、幂等 duplicate、out-of-order 新来源保留、OPEN→CONFIRMED→UPDATED→RESOLVED、retraction，且 stale confirmation 不能复活 `RETRACTED`。 | 这是 fixture-only 事件语义回归，不是外部授权 source ingestion 的运营证据。 |
| I05 Impact graph 可解释 | `VERIFIED_OFFLINE` | `FixtureOnlyCrosswalk`、`test_six_commodity_intelligence_e2e.py` 和 `test_six_commodity_fixture_contract.py` 重建 typed Event→Mechanism→Entity→Commodity→Market→Instrument→Contract `ImpactPath`；缺失/不一致 crosswalk、hash/authority/schema drift 均失败关闭，并将完整路径 hash 绑定到 Feature-definition handoff。 | crosswalk 仅 fixture-only/research-only，不是 P1 artifact 或权威 market/contract/calendar/rule mapping；D01 仍 `BLOCKED_EXTERNAL`。I06 仅在相同 fixture-only 语义下验证 synthetic replay，handoff 仍不是 `FeatureValue`、真实 PIT 或交易能力。 |
| I06 Event features 可回测 | `VERIFIED_OFFLINE` | `research/intelligence_fixture_replay.py`、`p10_intelligence_fixture_replay_v1.json`、`test_p10_intelligence_feature_research_e2e.py`、failure/architecture tests 将六个 WP02 Feature-definition handoff + corpus SHA-256 绑定到逐决策 `available_at <= decision_at` observation；其独立 synthetic outcome 仅在决策后可用，再确定性形成 Validation 与 `RESEARCH_ONLY` Research Card。 | 这是 fixture-only synthetic alignment replay，不是授权市场数据、真实收益、真实合约/日历/规则或 P1 `FeatureValue` 回测。`FIXTURE_ONLY_INTELLIGENCE_REPLAY` 永远不可 candidate admission/交易，P3 activation 在 target 构造前拒绝；D01/P10-WP09 继续 `BLOCKED_EXTERNAL`。 |

## Research

| ID / P10 验收项 | 状态 | 可复核证据 | 边界或后续动作 |
|---|---|---|---|
| R01 Feature Registry | `VERIFIED_OFFLINE` | `research/features/registry.py`；`test_feature_registry.py` 覆盖 immutable lineage 与 future-input rejection。 | 仅研究输入。 |
| R02 Canonical Feature Families | `VERIFIED_OFFLINE` | `research/features/canonical.py`；`test_canonical_feature_families.py` 和 PIT integration。 | static PIT / synthetic fixture 范围。 |
| R03 Experiment Registry | `VERIFIED_OFFLINE` | `research/experiments/registry.py`；`test_experiment_registry.py`。 | static reproducibility，不产生准入或交易权限。 |
| R04 Validation | `VERIFIED_OFFLINE` | `research/validation/framework.py`；`test_validation_framework.py` 覆盖 IS/OOS、walk-forward、rolling、stress、bootstrap、Monte Carlo 与 regime failure paths。 | 输出报告不可自动晋级。 |
| R05 Lookahead guard | `VERIFIED_OFFLINE` | `research/validation/lookahead.py`；`test_lookahead_guard.py` 覆盖 market/feature/event/rule/target future data。 | 真实数据基础仍受 D01 限制。 |
| R06 Research Decision State | `VERIFIED_OFFLINE` | `research/validation/research_decision.py` 及具名审批/不可自动晋级 tests。 | 不直接交易。 |
| R07 Research Card reproducible | `VERIFIED_OFFLINE` | `research/reports.py`；`test_research_card.py`、`test_research_card_reproducibility.py`。 | 离线重复结果 hash/JSON 一致；不授予交易权限。 |

## Portfolio / Risk

| ID / P10 验收项 | 状态 | 可复核证据 | 边界或后续动作 |
|---|---|---|---|
| PR01 Canonical StrategyTarget → PortfolioTarget → ApprovedPortfolioTarget | `VERIFIED_OFFLINE` | `portfolio_risk/portfolio/targets.py`；`test_portfolio_targets.py`。PortfolioTarget 已升级为 v2，target identity 绑定 `composition_hash`。 | 手工 P3 object 仍不是可重放的组合证明；P10-WP05 只能消费 P10-WP04 的 typed composition evidence。 |
| PR02 Research candidate → named activation → StrategyTarget v2 | `VERIFIED_OFFLINE` | `application/research_strategy_activation.py`；`test_research_strategy_activation.py`。 | hash-only、non-tradable receipt。 |
| PR03 Allocation core | `VERIFIED_OFFLINE` | `portfolio_risk/allocation/models.py`、`portfolio/composition.py`；`test_allocation_engine.py`、`test_p10_multi_strategy_composition_*`。 | policy/input 仅在 P3 内部重放；不表示 portfolio-wide risk approval。 |
| PR04 Exposure core | `VERIFIED_SIMULATION` | `portfolio_risk/exposure/models.py`、`portfolio_risk/portfolio/approval.py` 与 `application/portfolio_risk_authority.py` 从 exact `PortfolioCompositionEvidence`、profile policy、账户和 `ctp_sim` snapshot 重新派生并 hash-bind exposure；`test_exposure_engine.py`、`test_p10_portfolio_risk_approval_unit.py`、`test_p10_portfolio_risk_approval_golden.py` 覆盖稳定身份和 caller aggregate 拒绝。 | 只在受控本地 `ctp_sim` / PostgreSQL test path 验证；无权威生产市场/账户输入时仍不得增加风险，绝非真实 CTP 或实盘。 |
| PR05 Typed limits core | `VERIFIED_SIMULATION` | `portfolio_risk/limits/evaluator.py`、`portfolio_risk/portfolio/approval.py`、`application/portfolio_risk_authority.py` 与 profile-owned `ProfilePortfolioRiskApprovalConfig` 派生 nine-limit measurements/evidence；`test_limit_evaluator.py`、`test_p10_portfolio_risk_approval_unit.py`、`test_p10_portfolio_risk_approval_failure.py` 覆盖 UNKNOWN/WARN/BLOCK。 | 组合级 gate 不能接受 caller-supplied measurements；UNKNOWN/WARN/BLOCK 不能创建 approval、P8 receipt、intent 或 broker mutation，且仅为 simulation evidence。 |
| PR06 Risk state core | `VERIFIED_SIMULATION` | `portfolio_risk/risk/state_machine.py`、`application/portfolio_risk_authority.py`、`trading_execution/reconciliation/reconciliation.py` 将 profile/broker/account-scoped persisted reconciliation safety state bind 入 authority/review；`test_reconciliation_state.py`、`test_reconciliation_recovery.py`、`test_p10_portfolio_risk_approval_failure.py` 与 candidate fence regressions 覆盖 HALT/MANUAL_RECOVERY/UNKNOWN。 | 不自动恢复 HALT；缺失、不同账户、陈旧或未知 state 均 `NO NEW RISK`。这不是生产 broker reconciliation 认证。 |
| PR07 Seven stress scenarios core | `VERIFIED_SIMULATION` | `portfolio_risk/risk/scenarios.py`、`portfolio_risk/portfolio/approval.py` 与 profile scenario policy 将 gap、涨跌停、波动、流动性、相关商品、保证金、FX 的完整 typed result hash-bind 至 review；`test_stress_scenarios.py`、`test_p10_portfolio_risk_approval_unit.py`、`test_p10_portfolio_risk_approval_failure.py` 覆盖每类与缺失/重复/WARN/BLOCK。 | 任一不通过或不完整 scenario 在 P3 approval 前失败关闭；结果不构成真实压力测试或实盘资格。 |
| PR08 P3 risk E2E | `VERIFIED_SIMULATION` | `portfolio_risk/portfolio/approval.py` 的 `PortfolioRiskApprovalGate`、`application/portfolio_risk_authority.py` 与 `application/ctp_sim_candidate_execution.py`；`test_p10_portfolio_risk_approval_e2e.py`、`test_p10_portfolio_risk_authority_candidate.py`、`test_ctp_sim_candidate_execution.py`、`test_trading_execution_e2e.py` 覆盖 multi-strategy composition→review→P8 guarded `ctp_sim` chain。 | P3 attestation 是可重放 claim，不是人工身份凭据；任何 BLOCK 或 drift 都无 plan/intent/mutation，成功路径也只证明 local `ctp_sim` seam。 |
| PR09 Canonical multi-strategy portfolio | `VERIFIED_OFFLINE` | `portfolio/composition.py` 的 `CanonicalPortfolioComposer`/`PortfolioCompositionEvidence`，`PortfolioTarget v2`、`p10_canonical_multi_strategy_composition_v1.json`，unit/failure/e2e/golden/architecture tests。 | strict typed P3 replay：exact source/allocation sets、identity/window/replay drift 失败关闭；cash 与 net-zero 均 hash-bind。legacy `portfolio/multi_strategy.py` 保持隔离，不能作为 canonical source。它不做 exposure/limits/stress/risk-state approval（P10-WP05）。 |
| PR10 Portfolio-wide exposure evidence | `VERIFIED_SIMULATION` | `portfolio_risk/portfolio/approval.py` reconstructs exposure only from `PortfolioCompositionEvidence`; `application/portfolio_risk_authority.py` binds exact CTP-sim snapshot/account/authority; `test_p10_portfolio_risk_approval_unit.py`、`test_p10_portfolio_risk_approval_golden.py` 和 `test_ctp_sim_candidate_execution.py` prove derived identity rather than caller-supplied aggregate. | The evidence is local simulation-only. Different composition, instrument, account, snapshot, hash or time fails closed before P8 receipt, intent or broker mutation. |
| PR11 Portfolio-wide limits evidence | `VERIFIED_SIMULATION` | `portfolio_risk/limits/evaluator.py`、`portfolio_risk/portfolio/approval.py`、`foundation/config/trading_profile.py` derive profile-owned limit evidence and bind its policy hash; `test_portfolio_risk_approval_profile_config.py`、`test_p10_portfolio_risk_approval_failure.py` 与 candidate no-mutation tests cover incomplete/UNKNOWN/WARN/BLOCK. | No non-PASS result can create `ApprovedPortfolioTarget`, durable manual grant, P8 receipt, intent or mutation; this does not authorize real market/account risk. |
| PR12 Portfolio-wide stress evidence | `VERIFIED_SIMULATION` | `portfolio_risk/risk/scenarios.py`、`portfolio_risk/portfolio/approval.py` and `ProfilePortfolioRiskApprovalConfig` require exactly seven typed scenarios; `test_stress_scenarios.py`、`test_p10_portfolio_risk_approval_unit.py`、`test_p10_portfolio_risk_approval_failure.py` cover deterministic result hashes and missing/duplicate/WARN/BLOCK refusal. | Every scenario is bound to the P3 review before P8; it is a local simulation stress model, not a production risk model or live approval. |
| PR13 Account-scoped risk-state approval boundary | `VERIFIED_SIMULATION` | `application/portfolio_risk_authority.py` binds persisted reconciliation safety; `application/portfolio_risk_manual_approval.py` requires exact durable binding/record hashes; `foundation/db/models.py`、`repositories.py` and current `0001_current_schema_baseline` persist append-only hash-only records（原验收 revision 为 `0008_portfolio_risk_approval`）；`test_portfolio_risk_manual_approval.py`、`test_portfolio_risk_manual_approval_repository.py`、`test_portfolio_risk_manual_approval_boundaries.py`、`test_ctp_sim_candidate_execution.py` cover expiry/tamper/scope/HALT/final-fence refusal. | The shipped production composition has no issuer. Private test composition issuance proves no human identity. A deployed authenticated human-approval issuer, dedicated DB writer role, and SELECT-only CTP-sim candidate reader role remain `BLOCKED_EXTERNAL`; no raw verifier receipt, real CTP or live order is present. |

## Trading / Execution

| ID / P10 验收项 | 状态 | 可复核证据 | 边界或后续动作 |
|---|---|---|---|
| T01 paper | `VERIFIED_SIMULATION` | `test_paper_broker.py`。 | paper-only。 |
| T02 ctp_sim | `VERIFIED_SIMULATION` | `test_ctp_sim_broker.py`、`test_ctp_sim_recovery.py`、`tests/e2e/test_trading_execution_e2e.py`。 | local CTP semantics，不连接期货公司。 |
| T03 Reconciliation / sticky HALT | `VERIFIED_SIMULATION` | `test_reconciliation_state.py`、`test_reconciliation_recovery.py`。 | 差异保持 HALT，具名人工恢复。 |
| T04 Ledger / settlement / controlled adjustment | `VERIFIED_OFFLINE` | `test_ledger_repository.py`。 | PostgreSQL integration，非真实券商账本。 |
| T05 Failure matrix | `VERIFIED_SIMULATION` | `P10_TRADING_FAILURE_MATRIX.md` 将 disconnect/restart、duplicate/out-of-order、unknown order、stale facts、DB unavailable、timeout/network partition、identity mismatch、margin、price limit、cancel reject、rollover、P3 `BLOCK` no-mutation 和 real-CTP refusal 映射至精确 tests。 | 所有正向故障证据均为受控本地 `ctp_sim` / 隔离 PostgreSQL；real CTP 项仅是 `SAFE_BOUNDARY`，不等同真实 CTP 或实盘。 |
| T06 Real CTP 默认不能真实下单 | `SAFE_BOUNDARY` | `application/live_service.py` 拒绝 `ctp`；`broker/ctp_broker.py` 仅允许 `FakeCtpFront`；对应 skeleton/live-service tests。 | 这是已验证的拒绝，不是 real CTP integration。 |
| T07 Production enable 人工确认 | `SAFE_BOUNDARY` | `.env.example` 默认 `paper`/false；`live_service.py` 要求显式 enable。 | 不等同真实 CTP/真实资金授权。 |

## Platform

| ID / P10 验收项 | 状态 | 可复核证据 | 边界或后续动作 |
|---|---|---|---|
| PL01 Windows/Linux 开发 | `VERIFIED_OFFLINE` | `scripts/dev/check_env.py`、`scripts/dev/setup.py`；`test_cross_platform_scripts.py`、`test_dev_tool_bootstrap.py`；`scripts/dev/README.md`。 | Linux 工具 bootstrap 仅支持 Ubuntu/Debian，其他系统失败关闭。 |
| PL02 Windows/Linux deployment control | `VERIFIED_OFFLINE` | `scripts/deploy/deploy.py`、`scripts/README.md`、`docs/OPERATIONS.md`、cross-platform contracts。 | controller 可跨平台；Linux target 另见 PL03。 |
| PL03 Linux production | `BLOCKED_EXTERNAL` | FHS/root-gate/systemd/release assets和 Docker validation 已存在。 | 需要真实 Linux host、root gate/signer、known_hosts、managed Python、production PostgreSQL 与人工批准；未满足前不可声称 production accepted。 |
| PL04 health/logs | `VERIFIED_OFFLINE` | `application/health.py`、logger、operational snapshot、Prometheus metrics；`test_health_cli.py`、`test_logging.py`、`test_metrics.py`、`test_operational_snapshot.py`。 | fail-closed local evidence。 |
| PL05 backup/restore | `PARTIAL` | `foundation/backup/bundle.py`、`restore_drill.py` 和 integration tests。 | 仅六类 local bundle + loopback `northstar_test` drill；缺 production restore、offsite/encryption/WAL/PITR/RPO/RTO。 |
| PL06 rollback | `SAFE_BOUNDARY` | deployment rollback contracts 与 `scripts/deploy/remote/linux/README.md`。 | 仅 pre-migration auto rollback；migration 后严格人工恢复，不执行 DB downgrade/autorestart。 |
| PL07 CI | `HOSTED_EVIDENCE_PENDING` | `.github/workflows/ci.yml` 定义 Linux/Windows jobs。 | 最终 commit 的 hosted run 是外部状态，未取得前不可标记为已验收。 |

## AI

| ID / P10 验收项 | 状态 | 可复核证据 | 边界或后续动作 |
|---|---|---|---|
| A01 Agent 无生产交易越权 | `SAFE_BOUNDARY` | `application/agent_tools.py`、agent result `eligible_for_trading=False`、`test_agent_tool_api_boundaries.py`、agent contracts。 | 没有 portfolio/trading/live capability。 |
| A02 AI conclusion 有 evidence | `VERIFIED_OFFLINE` | `research_agent.py` 与 intelligence/data-quality/ops agent evidence hashes；`docs/GOVERNANCE.md` 的限制和相应 tests。 | 只说明 constrained typed output，有证据不等于任意 LLM 结论为真。 |
| A03 Research Agent 产物可追踪 | `VERIFIED_OFFLINE` | `application/research_agent_evidence_audit.py` 的独立 wrapper、`foundation/db/models.py` / `repositories.py`、当前 `0001_current_schema_baseline`（原验收 revision 为 `0009_agent_run_audit` / `0010_agent_run_audit_hardening`）；`test_research_agent_evidence_audit.py`、`test_research_agent_run_audit_repository.py`、architecture/contract tests 覆盖跨进程 reservation、hash chain、default-expiring session、unknown outcome、DB direct-insert 与 UPDATE/DELETE/TRUNCATE 拒绝。 | 仅隔离本地 PostgreSQL。始终 `RESEARCH_ONLY` / `eligible_for_trading=False`；不持久化 raw prompt 或 CoT，也不保存 query/Document/result/rationale/exception message/payload；不是 real CTP、实盘、生产部署或新 Agent capability。 |
| A04 AI 无法绕过风险门禁 | `SAFE_BOUNDARY` | closed typed facade、architecture contracts、ops-agent restrictions 和 `docs/GOVERNANCE.md`。 | 无 Agent API 可 approve、enable-live、resume-risk、submit 或连接 broker。 |

## 后续依赖图

| Work package | 状态 | 解决范围 | 依赖 / 外部条件 |
|---|---|---|---|
| P10-WP02 六商品情报证据语料 | `DONE` | I01、I04、I05 的 fixture-only golden/crosswalk/Feature-definition handoff 证据。 | 仅 fixture/ontology/contract，不能伪造授权 source。 |
| P10-WP03 情报 Feature 研究回测闭环 | `DONE` | I06 的 WP02 handoff→fixture PIT replay→synthetic outcome→Validation→RESEARCH_ONLY Card golden E2E。 | 仅 fixture-only；不伪造 P1/data authorization 或交易资格。 |
| P10-WP04 Canonical Multi-Strategy Portfolio Composition | `DONE` | PR03/PR09 的 strict typed composition、PortfolioTarget v2 `composition_hash`、golden and failure evidence。 | 只生成非审批、非执行、非 broker 的 P3 composition evidence。 |
| P10-WP05 Portfolio-Wide Risk Evidence & Approval Gate | `DONE` | PR04–PR08、PR10–PR13 与 portfolio-level BLOCK exit 已 `VERIFIED_SIMULATION`：canonical P3 derivation、hash-bound account state、hash-only durable manual record 和 P8 final-fence `ctp_sim` enforcement。 | P10-WP04；仍不连接真实 CTP。认证人工批准 issuer 与 DB least-privilege roles 是 `BLOCKED_EXTERNAL`，不得被 private test issuer 取代。 |
| P10-WP06 Durable Agent Evidence Audit | `DONE` | A03 的 append-only/hash-only durable audit，`0009`/`0010` 前向 PostgreSQL migrations 与完整本地门禁。 | P10-WP01；只验证本地 PostgreSQL，绝不授予 Agent 或交易权限。 |
| P10-WP07 Trading Acceptance Evidence Closure | `DONE` | T05 的 `P10_TRADING_FAILURE_MATRIX.md`、direct P3 `BLOCK` no-mutation contract 和 local failure suite。 | P10-WP05；没有真实 CTP 连接、真实账户或实盘操作。 |
| P10-WP08 Platform Production/DR Acceptance | `BLOCKED_EXTERNAL` | PL03、PL05、PL07。 | production host/credentials/hosted CI/DR policy；保持 no live action。 |
| P10-WP09 Authoritative Data & Source Onboarding | `BLOCKED_EXTERNAL` | D01 和真实 production PIT/source evidence。 | data license、权威 contract/calendar/rule artifacts；保持 `NO NEW RISK`。 |

## Non-escalation rule

任何 `VERIFIED_OFFLINE`、`VERIFIED_SIMULATION`、`PARTIAL` 或 `SAFE_BOUNDARY` 条目都不得单独改变
`NORTHSTAR_BROKER=paper`、`NORTHSTAR_LIVE_TRADING_ENABLED=false` 的默认值，也不得被用于恢复 HALT、
连接真实 CTP、创建真实订单或声明真实资金生产就绪。

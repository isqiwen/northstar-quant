# Northstar Quant — Codex 主实施计划、功能规范与验收标准

> 文档性质：**唯一主实施规范（Single Source of Implementation Truth）**
> 适用仓库：`isqiwen/northstar-quant`
> 目标读者：Codex / AI Agent / 项目维护者
> 架构视野：未来 10 年
> 实施方式：Codex 高强度持续开发，按 Work Package 逐项完成
> 文档状态：ACTIVE
> 首版日期：2026-08-19
> 生产边界：Linux-only production target；Windows/Linux 均可作为开发与部署控制端
> 默认交易安全模式：`paper` / fail-closed

---

# 0. 文档使用协议

本文件不是静态规划文档，而是 **Codex 的持续开发控制面**。

Codex 每次进入仓库必须：

1. 读取根目录 `AGENTS.md`。
2. 读取本文件。
3. 查看：
   - `当前总状态`
   - `当前活动阶段`
   - `next_task`
   - `blocked_work_packages`
4. 执行 `git status`，不得覆盖用户已有修改。
5. 一次只领取一个可独立验收的 Work Package（WP）。
6. 一个 WP 必须同时处理所有受影响的：
   - 代码；
   - 测试；
   - 配置；
   - Alembic migration；
   - CLI；
   - 脚本；
   - 文档。
7. 先跑局部测试，再跑该 WP 规定的完整门禁。
8. 只有全部验收通过，才能：
   - `[ ]` 改成 `[x]`
   - `TODO/READY/VERIFY` 改成 `DONE`
   - 写入完成日期、验证命令、commit/PR（若存在）
9. 更新 `next_task` 后，再开始下一项。
10. 不允许跳过阻塞依赖。

## 状态枚举

- `TODO`：尚未开始
- `READY`：依赖满足，可以开始
- `IN_PROGRESS`：正在实施
- `VERIFY`：实现完成，等待完整验收
- `BLOCKED`：外部条件或前置依赖阻塞
- `DONE`：全部验收通过
- `DEFERRED`：主动推迟
- `REJECTED`：决定不再实施

---

# 1. 当前总状态

| Phase | 名称 | 状态 | 完成度 |
|---|---|---|---:|
| P0 | 架构与工程治理 | DONE | 100% |
| P1 | Data Platform | DONE | 100% |
| P2 | Research & Strategy Platform | DONE | 100% |
| P3 | Portfolio & Risk | DONE | 100% |
| P4 | Intelligence / EventAlpha | DONE | 100% |
| P5 | Trading & Execution Production Grade | DONE | 100% |
| P6 | Platform Foundation & Automation | DONE | 100% |
| P7 | AI-assisted Research Automation | DONE | 100% |
| P8 | Integrated Production Candidate | DONE | 100% |
| P9 | Hardening / Performance / Security | DONE | 100% |
| P10 | Mature v1 Acceptance | IN_PROGRESS | 78% |

```yaml
active_phase: P10
active_work_package: null
next_task:
  id: P10-WP08
  title: Platform Production / DR Acceptance
  status: BLOCKED
blocked_work_packages: [P10-WP08, P10-WP09]
```

P10 已完成 `7/9` 个 Work Package（78%）；其余 P10-WP08 与 P10-WP09 均需外部前提。用户请求的跨阶段文档治理工作包 `DOC-WP01` 已完成，不改变 P10 的验收计数或外部阻塞状态。

---

# 2. 项目最终目标

Northstar Quant 最终定义为：

> **面向中国商品期货、融合全球宏观与商品事件情报、支持数据—研究—组合—风险—执行完整闭环的可审计量化平台。**

最终闭环：

```text
Market / World
      ↓
Data Platform
      ↓
Intelligence
      ↓
Research & Strategy
      ↓
Portfolio & Risk
      ↓
Trading & Execution
      ↓
Market
      ↓
Data Platform
```

Platform Foundation 横向提供：

```text
Config / DB / Messaging / Scheduling / Observability
Security / Reporting / CLI / Deployment / Audit
```

明确非目标：

- 不是单纯策略仓库；
- 不是单次回测 Demo；
- 不是 LLM 自动下单系统；
- 不是追求高频交易的低延迟框架；
- 不是为“未来可能需要”提前微服务化；
- 不是为了炫技堆技术栈。

---

# 3. 十年稳定的六大领域

```text
src/northstar_quant/
├── application/            # 根级 composition root，不属于六个业务领域
├── data_platform/
├── intelligence/
├── research/
├── portfolio_risk/
├── trading_execution/
└── platform/
```

## 3.1 Data Platform

负责：

- 数据源接入；
- raw / normalized / derived artifact；
- 数据质量；
- 数据版本；
- point-in-time 时间语义；
- 合约、日历、手续费、保证金、涨跌停等事实；
- lineage；
- license metadata。

禁止：

- 策略逻辑；
- Event→Signal；
- 下单；
- 组合与风险决策。

## 3.2 Intelligence

稳定语义链：

```text
Document
→ Entity
→ Event
→ Economic Mechanism
→ Impact
→ Commodity / Market / Contract
```

负责：

- 文档接入；
- 去重；
- entity extraction / resolution；
- event extraction / merge；
- ontology；
- source trust；
- evidence；
- knowledge/impact graph；
- market context；
- analogue；
- event study。

禁止直接产生真实订单。

## 3.3 Research & Strategy

稳定链：

```text
Data / Intelligence
→ Feature
→ Experiment
→ Strategy
→ Backtest
→ Validation
→ Research Decision
```

核心对象：

- FeatureSpec / FeatureVersion
- StrategySpec / StrategyVersion
- ExperimentSpec / ExperimentRun
- ResearchDecision
- DatasetVersion binding

## 3.4 Portfolio & Risk

```text
Strategy Targets
→ Portfolio Construction
→ Risk Adjustment
→ Approved Portfolio Target
```

负责：

- allocation；
- exposure；
- risk budget；
- leverage；
- limits；
- stress；
- scenario；
- drawdown；
- risk state machine。

## 3.5 Trading & Execution

```text
Approved Portfolio
→ Execution Plan
→ Orders
→ Broker
→ Fills
→ Positions
→ Reconciliation
→ Settlement
```

包括：

- broker adapters；
- execution；
- order lifecycle；
- position/account；
- ledger；
- reconciliation；
- settlement；
- live orchestration。

## 3.6 Platform Foundation

负责：

- common types；
- config；
- DB infrastructure；
- messaging；
- scheduling；
- logging/observability；
- security；
- reporting infrastructure；
- CLI；
- deployment contracts。

`platform` 不得反向依赖业务领域。

## 3.7 Application Composition Root

`application/` 不是第七个业务领域。它只负责装配跨领域用例，例如 CLI、回测工作流、
运行编排、报告、健康检查与看板；因此可以调用六个领域和 Platform Foundation。反向依赖
一律禁止：任一领域和 `platform/` 都不得导入 `application/`。
---

# 4. 目标工程结构

```text
northstar-quant/
├── src/northstar_quant/
│   ├── application/          # CLI、工作流和运行编排；不承载领域模型
│   ├── data_platform/
│   │   ├── sources/
│   │   ├── market/
│   │   ├── fundamentals/
│   │   ├── contracts/
│   │   ├── calendar/
│   │   ├── artifacts/
│   │   ├── lineage/
│   │   └── quality/
│   ├── intelligence/
│   │   ├── domain/
│   │   ├── ingestion/
│   │   ├── ontology/
│   │   ├── extraction/
│   │   ├── entity_resolution/
│   │   ├── event_merge/
│   │   ├── knowledge_graph/
│   │   ├── impact_graph/
│   │   ├── context/
│   │   ├── analogue/
│   │   └── event_study/
│   ├── research/
│   │   ├── features/
│   │   ├── registry/
│   │   ├── experiments/
│   │   ├── strategies/
│   │   ├── backtest/
│   │   ├── validation/
│   │   └── statistics/
│   ├── portfolio_risk/
│   │   ├── allocation/
│   │   ├── portfolio/
│   │   ├── exposure/
│   │   ├── risk_model/
│   │   ├── limits/
│   │   ├── scenario/
│   │   └── state_machine/
│   ├── trading_execution/
│   │   ├── broker/
│   │   ├── market_gateway/
│   │   ├── orders/
│   │   ├── execution/
│   │   ├── positions/
│   │   ├── account/
│   │   ├── ledger/
│   │   ├── reconciliation/
│   │   ├── settlement/
│   │   └── live/
│   └── platform/
│       ├── common/
│       ├── config/
│       ├── db/
│       ├── messaging/
│       ├── scheduling/
│       ├── observability/
│       ├── security/
│       ├── reporting/
│       └── cli/
├── configs/
│   ├── data_platform/
│   ├── intelligence/
│   ├── research/
│   ├── portfolio_risk/
│   ├── trading_execution/
│   └── platform/
├── ontology/
│   ├── events.yaml
│   ├── mechanisms.yaml
│   ├── entities.yaml
│   ├── commodities.yaml
│   └── relations.yaml
├── tests/
│   ├── architecture/
│   ├── data_platform/
│   ├── intelligence/
│   ├── research/
│   ├── portfolio_risk/
│   ├── trading_execution/
│   ├── platform/
│   ├── e2e/
│   ├── fixtures/
│   ├── golden/
│   └── helpers/
├── docs/
├── scripts/
│   ├── dev/
│   ├── build/
│   ├── data/
│   ├── db/
│   ├── ci/
│   ├── deploy/
│   ├── ops/
│   ├── release/
│   ├── maintenance/
│   └── tools/
├── infra/
│   ├── compose/
│   ├── systemd/
│   ├── ansible/
│   ├── monitoring/
│   ├── backup/
│   └── nginx/
└── justfile
```

迁移原则：

> 禁止仅为目录美观进行一次性大搬家。每次重构必须有明确领域收益、测试保护和独立验收点。

---

# 5. 强制架构原则

Codex 永远不得违反：

1. `Document != Event`
2. `Event != Feature`
3. `Feature != Strategy`
4. `Strategy Target != Order`
5. `Execution Plan != Broker Order`
6. `Commodity != Instrument != Contract`
7. `event_time != published_time != ingested_time != available_time`
8. 回测只能使用 `available_time <= simulation_time` 的数据。
9. 研究必须完全可复现。
10. 所有 Feature 必须保存 lineage。
11. 所有 Intelligence Event 必须保存 evidence。
12. LLM 输出不是 ground truth。
13. 交易 fail-closed。
14. 未知账户/持仓/行情/订单状态不得增加风险。
15. Data adapter 可替换。
16. Broker adapter 可替换。
17. Ontology 版本化。
18. Platform 不反向依赖业务域。
19. AI 不得绕过 Research Gate / Risk Gate / Trading Gate。
20. 未发布系统不为旧错误设计兼容层；应直接修正并同步所有调用方。

---

# 6. 时间语义规范

适用对象必须根据语义保存：

```text
event_time
source_time
published_time
ingested_time
processed_time
available_time
```

强制要求：

- 回测以 `available_time` 为可见边界；
- `published_time` 缺失不得自动等同 `event_time`；
- 所有时间必须时区明确；
- 中国期货夜盘归属由交易日历计算；
- revision 数据不得覆盖历史旧版本；
- 当前未知的数据时间语义必须 fail 或标记 UNKNOWN，不得猜测。

专项测试必须存在：

```text
tests/research/statistical/test_no_lookahead.py
tests/data_platform/contract/test_point_in_time_semantics.py
```

---

# 7. 测试与质量体系

```text
tests/
├── architecture/
├── data_platform/
│   ├── unit/
│   ├── integration/
│   └── contract/
├── intelligence/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   └── golden/
├── research/
│   ├── unit/
│   ├── integration/
│   ├── regression/
│   └── statistical/
├── portfolio_risk/
│   ├── unit/
│   ├── integration/
│   └── scenario/
├── trading_execution/
│   ├── unit/
│   ├── integration/
│   ├── simulation/
│   └── failure/
├── platform/
│   ├── unit/
│   ├── integration/
│   └── contract/
├── e2e/
├── fixtures/
├── golden/
└── helpers/
```

## 每个普通 WP 的最低门禁

```bash
just env-bootstrap
uv run --offline --no-sync pytest <focused tests>
uv run --offline --no-sync ruff check .
uv run --offline --no-sync python scripts/ci/check_mypy_baseline.py check
```

涉及公共模块、DB、config、execution、live：

```bash
just env-bootstrap
uv run --offline --no-sync pytest
uv run --offline --no-sync ruff check .
uv run --offline --no-sync python scripts/ci/check_mypy_baseline.py check
```

不得通过以下方式让测试变绿：

- 用 SQLite 代替 PostgreSQL；
- 删除安全检查；
- 更改真实语义以迎合旧测试；
- mock 掉本应验证的核心逻辑；
- 静默吞异常。

---

# 8. Definition of Done

任何 WP 只有全部满足才允许 `DONE`：

- [ ] 功能已实现。
- [ ] 架构边界正确。
- [ ] 正常路径测试存在。
- [ ] 必要失败路径测试存在。
- [ ] 必要 integration/contract/golden/e2e 测试存在。
- [ ] schema 变化有 Alembic migration。
- [ ] 配置同步。
- [ ] CLI/脚本同步（如受影响）。
- [ ] 文档同步。
- [ ] 无真实 secret 写入。
- [ ] pytest 规定范围通过。
- [ ] Ruff 通过。
- [ ] mypy baseline 未增加。
- [ ] 未弱化任何交易安全门禁。
- [ ] 本文件状态已经更新。
- [ ] 下一 WP 已正确设置。

---

# 9. P0 — 架构与工程治理

## P0-WP01 — Master Plan 追踪机制

**Status:** DONE

- [x] 将本文件放入 `docs/planning/MASTER_IMPLEMENTATION_PLAN.md`
- [x] README 引用本文件
- [x] AGENTS.md 强制跨模块开发先读本文件
- [x] contract test 验证 README/AGENTS 均引用主计划
- [x] 固化状态与 WP 更新规则

验收：

```bash
just env-bootstrap
uv run --offline --no-sync pytest tests/platform/contract tests/architecture
uv run --offline --no-sync ruff check .
```

完成条件：
- Codex 新会话能从文档中的 `next_task` 恢复工作。

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-19
  commit: null
  notes: 将主计划收敛为唯一固定路径，并以 README、AGENTS 与 contract test 保护引用；下一项为 P0-WP02。
```

## P0-WP02 — 六领域依赖契约

**Depends on:** P0-WP01

**Status:** DONE

- [x] 建立 architecture tests
- [x] platform → 无业务依赖
- [x] data_platform 不依赖 research/trading
- [x] intelligence 不依赖 trading
- [x] research 不直接调用 broker
- [x] portfolio_risk 不依赖 live orchestration
- [x] trading_execution 可依赖已批准的 portfolio/risk contract，但不能反向污染 research

验收：
- 故意加入违规 import 时 architecture test 必须失败。

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-19
  commit: null
  notes: 建立 AST 架构契约与六领域 DAG；跨领域 CLI、回测与 live 编排收敛到根级 application composition root，Platform Foundation 保持无业务运行时依赖。
```

## P0-WP03 — scripts / infra / just

**Status:** DONE

- [x] 规范 scripts 子目录
- [x] 规范 infra
- [x] 增加 justfile
- [x] `just --list`
- [x] Windows/Linux 均支持开发/控制端命令
- [x] Production target 只支持 Linux

已提供的统一命令：

```text
just dev-check
just dev-bootstrap
just dev-bootstrap-docker
just dev-setup
just dev-postgres
just test-unit
just test-backtest
just test-cli
just candidate-acceptance
just check
just deploy-prod
just ops-health
```

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-19
  commit: null
  notes: scripts 与 infra 按职责分层；Just 成为 Windows/Linux 工作站统一命令面，Python 负责跨平台控制面，Linux shell 仅在生产目标端执行。开发工具 bootstrap 默认只展示安装计划，Docker 与系统安装均需显式双确认。
  verification:
    - just --list
    - bash -n scripts/deploy/install-release.sh scripts/deploy/remote/linux/*.sh scripts/ops/remote/linux/*.sh
    - pytest 平台 unit / scripts / deploy / docs / 环境契约：153 passed（临时安全配置）
    - pytest tests/architecture：16 passed
    - ruff check .；mypy baseline check
```

## P0-WP04 — Platform Support Contract

**Status:** DONE

当前子任务：让开发工具 bootstrap、配置初始化与本地服务初始化可安全重复执行；
已满足状态复用，未知或冲突状态保持 fail-closed。

本回合完成：

- [x] 缺失工具检测、Windows/Linux 安装计划与 Docker APT 已验证状态的重复执行/中断恢复
- [x] `.env` schema、paper 本地值与 `configs/app.yaml` 的无额外改写初始化
- [x] 固定本地 Compose 项目、清理继承环境、保留卷缺少密码时 fail-closed、数据库创建竞争收敛
- [x] 平台 unit / contract 覆盖正常重跑、半完成恢复和冲突拒绝

定义并测试：

```text
Development:
  Windows x86_64 Tier 1
  Linux x86_64 Tier 1

Production:
  Linux x86_64 only

Deployment control:
  Windows / Linux

CI:
  Linux full
  Windows compatibility
```

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-19
  commit: null
  notes: 开发工具 bootstrap、活动配置初始化和本地 PostgreSQL 初始化均具备重复执行与中断恢复语义；未知、冲突、非本地 Docker 目标和保留卷密码缺失均失败关闭。数据库自动化只允许创建、复用与前向迁移，禁止删除、清空或回滚。
  verification:
    - pytest P0-WP04 平台 unit / 跨平台脚本 / 环境 schema / 数据库保全契约：67 passed, 1 skipped（未创建本地活动配置）
    - ruff check . --no-cache
    - mypy baseline check（86 条历史诊断无新增）
    - setup.py --bootstrap-tools（仅安装计划，未执行安装）
    - check_env.py --json
    - git diff --check
```
---

# 10. P1 — Data Platform

目标：

> 可授权、可版本、可回放、PIT 正确的数据基础。

## P1-WP01 — Data Domain Core

**Status:** DONE

实现：

```text
DataSource
RawArtifact
NormalizedArtifact
DerivedArtifact
DatasetVersion
DataQualityResult
DataLineage
LicenseMetadata
```

必须包含：

- source id
- acquired_at
- available_at
- schema version
- content hash
- transform version
- quality status
- provenance

验收：
- 同一 raw input + 同一 transform version 可复现同一 normalized hash。

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-19
  commit: null
  notes: 新增纯值、不可变的数据领域契约；不改写现有下载、manifest、数据库或交易链路。ArtifactSnapshot 将内容、来源、PIT 时间、质量和 provenance 绑定为可验证快照；DatasetVersion 仅引用已校验快照。标准化确定性工厂校验 raw bytes，并对同一转换双执行，不一致即失败关闭。
  verification:
    - pytest tests/data_platform/unit/test_data_domain_core.py tests/data_platform/contract/test_data_domain_contract.py：14 passed
    - pytest tests/data_platform（临时、无密钥配置根；未启动数据库）：50 passed
    - pytest tests/architecture、文档与主计划契约：26 passed
    - ruff check .；mypy baseline check（86 条历史诊断无新增）；git diff --check
  residual_boundary:
    - SHA-256 是完整性身份，不是来源签名；P1-WP02/06 已提供不可变存储、跨进程 raw+transform 到 normalized 的发布映射和 snapshot-level lineage，但来源签名/外部信任根仍不在当前范围。
```

## P1-WP02 — Artifact Storage

**Status:** DONE

完成：将 P1-WP01 的纯领域对象接入本地、内容寻址且追加式的制品存储；不得覆盖旧版本，
不得删除或清空任何数据库。

- [x] raw / normalized / derived
- [x] immutable version
- [x] hash dedup
- [x] manifest
- [x] old version 不覆盖
- [x] 可按 dataset version 重放
- [x] 持久化并唯一约束 raw + transform/schema → normalized content 的发布映射
- [x] 将持久化 lineage 升级为 snapshot-level identity，而非仅 content-level identity

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-19
  commit: null
  notes: 新增独立 immutable_store，不复用 legacy storage.py 的可覆盖 market/cache 投影，也不接入数据库或迁移。永久对象固定在 <storage_dir>/artifacts，以 SHA-256 路径、canonical JSON、no-replace hard link 与 create-or-verify 语义保存。POSIX 发布与读取使用 root/parent dir_fd、O_NOFOLLOW 和回读校验；normalized 绑定在 record 前作为并发唯一闸门。DatasetVersion 逐项验证 blob、record、snapshot-level lineage、PIT 与质量状态后回放。output cleanup 始终保护 artifacts 根。
  verification:
    - pytest tests/data_platform（临时、无密钥配置根；未启动数据库）：69 passed
    - pytest 核心 immutable store、cleanup、领域和存储 contract：37 passed
    - pytest tests/architecture、文档与主计划 contract：26 passed
    - ruff check .；mypy baseline check（86 条历史诊断无新增）；git diff --check
  residual_boundary:
    - P1-WP06 已接入受控 raw bytes 发布器；legacy downloads/market/data_manifest_v3 仍保持不变，现有未授权来源不会通过该发布器。
    - Windows 开发机使用 reparse-point 检查与仅当前用户可写的 artifacts 根 ACL 前提；Linux 生产路径使用 dir_fd 防竞态。
    - 首次目录创建在断电时最多留下缺失或未完成对象，重开会失败关闭；目录耐久性进一步强化留给后续 hardening。
```

## P1-WP03 — Contract Master

**Status:** DONE

```text
Commodity
Exchange
Instrument
Contract
ContractRuleSnapshot
```

规则包括：

- multiplier
- tick_size
- expiry
- listing state
- margin
- fees
- price limit
- session
- delivery restriction

硬性验收：
- continuous contract 永远不能成为真实 broker order contract。
- contract mapping UNKNOWN => fail-closed。

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-19
  commit: null
  notes: 新增 Data Platform 的 Commodity、Exchange、Instrument、ContinuousResearchSeries、实际 Contract、ContractRuleSnapshot 与带 Master 指纹的 ContractResolution。静态 Contract Master YAML 只保存稳定身份并拒绝任何 rule_snapshots；执行解析以显式 decision_at、PIT、质量、挂牌/到期、交割限制和执行资格失败关闭。CTP 映射、执行计划、实盘服务及 ctp_sim 都拒绝连续研究序列，实际月份合约才可进入券商身份查询。
  verification:
    - pytest tests/data_platform（临时、无密钥配置根；未启动数据库）：89 passed
    - pytest Contract Master、CTP mapping/sim、execution registry、live service、architecture 与文档契约（临时安全配置根）：62 passed
    - ruff check .；mypy contract master / static loader / CTP mapping；git diff --check
    - 独立安全复审：无 blocker，可标记 DONE
  residual_boundary:
    - 当前静态 Master 不包含任何实际 Contract 或规则快照；授权的不可变制品发布链尚未实现，因此默认配置不能生成可执行合约。
    - Contract Master 尚未接入 application 的真实 broker-binding composition root；未来接入必须在提交时重新 resolve_for_execution() 并 require_execution_contract()，不得信任旧解析结果。
    - source/snapshot SHA-256 仅提供完整性身份，不是来源签名；P1-WP06 已完成通用数据制品发布器，但 execution-eligible 规则快照仍须由后续受验证 artifact publisher 唯一生成。
    - 真实 CTP adapter 仍未实现；Paper 及遗留状态文件的通用连续合约身份门禁留给后续执行工作包统一收口。
```

## P1-WP04 — Trading Calendar

**Status:** DONE

覆盖：

- 中国交易日
- 夜盘归属
- 节假日
- 特殊开闭市
- product session
- expiry/delivery restriction

Golden fixtures：
- 周末
- 春节/国庆等长假
- 夜盘跨日
- 跨年
- 特殊交易日

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-19
  commit: null
  notes: 新增 Data Platform 离线、版本化、PIT 可见的交易日历模型与 CalendarService；运行时只从按交易所绑定的不可变 normalized calendar artifact payload 读取，并逐项复核内容哈希、record 血缘、质量、来源配置哈希和 live 授权范围。最终 non-paper 订单以同一 submission_at 无缓存重读画像，重验数据治理、券商/生命周期资格、Contract Master 的实际月份合约/到期/交割/规则质量，并要求产品绝对 Calendar Session 与实际 ContractRuleSnapshot 同名规则会话同时开放。连续合约、未来快照、未知会话、授权撤销、合约规则缩短时段或任意配置缺失均失败关闭；移除了未使用的工作日降级日历模块。
  verification:
    - pytest 日历核心、不可变制品、配置、调度、画像、文档、主计划、类型基线和架构组合（临时安全配置根）：124 passed
    - pytest calendar submission guard：22 passed；覆盖夜盘归属、节假日、来源/制品篡改、画像撤销、合约到期/交割/规则 PIT 与实际规则会话收窄
    - ruff check .；mypy calendar/application/config 范围；mypy baseline contract（79 条）及 rename-aware ratchet；git diff --check
    - 独立安全复审：无 fail-open blocker
  residual_boundary:
    - 当前静态 Contract Master 没有实际 Contract 或规则快照，且仓库尚无授权 runtime calendar artifact；因此 ctp/ctp_sim 的 non-paper 新订单仍会安全地失败关闭。P1-WP06 已提供通用不可变数据发布链，但尚未有获授权的 calendar/rule publisher，不能把该状态误报为可交易。
    - 调度器的执行会话预过滤目前要求画像全部已启用品种同时 OPEN；混合夜盘/日盘 universe 可能保守地跳过本可执行的夜盘订单。最终逐订单门禁仍正确；后续改为按订单或批次过滤。
    - 本机 northstar_test PostgreSQL 未启动；完整 pytest 中数据库/迁移集成用例因 connection refused 未完成，未执行任何数据库删除或清空操作。
```

## P1-WP05 — Data Quality Engine

**Status:** DONE

规则：

```text
completeness
uniqueness
ordering
schema
range
calendar_consistency
contract_consistency
staleness
gap
revision
```

状态：

```text
PASS
WARN
FAIL
UNKNOWN
```

Production 所需关键事实 UNKNOWN => BLOCK。

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-19
  commit: null
  notes: 新增纯内存、PIT-aware 的预发布质量评估核心。QualityRequest 以 canonical frame payload 逐字节绑定候选 Artifact 内容哈希，并在构造和执行时防御 Polars 原地变异；Calendar/Contract/coverage 只能通过带 available_at 与 immutable reference_hash 的注入事实参与判断。十条固定规则完整覆盖，缺失、未来或不可信证据均为 UNKNOWN；gap 明确检查覆盖窗口首尾、内部区间和每个分组，revision 只接受可见且质量/来源/schema/transform/kind 兼容的 prior immutable baseline。正式绑定不得改善候选制品已有的质量状态。
  verification:
    - pytest quality engine、质量公开契约与 architecture（临时安全配置根）：36 passed
    - pytest tests/data_platform tests/architecture（临时安全配置根）：141 passed
    - ruff check quality 与对应测试；mypy src/northstar_quant/data_platform/quality；git diff --check
    - 独立安全复审：修复 frame/artifact 错绑、可变 frame、gap、revision baseline 与质量状态洗白后，无剩余 blocker
  residual_boundary:
    - assessment 已由 P1-WP06 作为不可变 sidecar/binding 写入 ArtifactStore 和 DatasetVersion manifest；legacy 下载缓存仍不具有该证据。
    - 当前 revision 只具制品级 PIT 与显式 prior baseline 比较；bar/tick 行级 available_at、revisions 的 as-of 选择与版本选择策略留给 P1-WP07。
    - canonical frame payload 是本质量核心唯一可发布评估格式；P1-WP06 已提供 raw bytes 到该格式的受控发布器，供应商私有解析器仍须逐个获得授权并实现 adapter 后才能接入。
```

## P1-WP06 — Data Source Adapter Protocol

**Status:** DONE

```python
class DataSourceAdapter(Protocol):
    def fetch(...): ...
    def normalize(...): ...
    def metadata(...): ...
```

首批适配：

- AKShare（exploration）
- local import
- domestic exchange official interface
- CTP market adapter boundary
- EIA
- CFTC
- future premium vendor boundary

每个 source 必须记录用途/授权分类。
`normalize(...)` 必须产生规范 bytes，并通过 `NormalizedArtifact.from_deterministic_transform` 发布；
不得绕过确定性与 raw-content-hash 校验。

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-19
  commit: null
  notes: 新增纯值 DataSourceAdapter 协议、PublicationScope/PublicationAuthorization 授权模型、受控 DataSourcePublisher 及 adapter 注册表。发布器只接受 adapter 产生的 raw bytes；在 fetch 前后、raw/normalized 写入前和 DatasetVersion 释放前重新核验当前来源配置、许可范围和 adapter metadata。从已存 raw 再读取后双执行 normalized 转换，再由发布器自身调用 Quality Engine；最终 assessment、唯一 snapshot binding 和无密钥授权收据均追加式写入 ArtifactStore，DatasetVersion manifest 精确引用它们。
  verification:
    - pytest 发布器、协议、不可变制品库、质量与公开契约（临时安全配置根）：55 passed
    - pytest tests/data_platform tests/architecture（临时安全配置根）：169 passed
    - ruff check Data Platform 与对应测试；mypy protocol/publisher/immutable_store/quality；mypy baseline check；git diff --check
    - 合成 commercial_licensed 来源完整回放 raw → normalized → assessment → DatasetVersion；未授权 AKShare 在 fetch 前拒绝，撤销、非确定性、FAIL 质量和篡改 evidence 均失败关闭
  residual_boundary:
    - `configs/data/sources.yaml` 中现有 AKShare、本地导入和采购候选来源均未获发布授权；它们继续作为 exploration/legacy 边界，不能产生研究或交易可用 DatasetVersion。
    - 仓库尚无获授权的 runtime calendar 或 ContractRuleSnapshot 制品 publisher；ctp/ctp_sim non-paper 新订单因此继续失败关闭，不能据此声明真实交易可用。
    - P1-WP07 已实现受控 DatasetVersion 的行级 PIT snapshot；逐决策时点的完整回放仍须由后续研究 look-ahead guard 构造，不能把静态 as-of 视图当作该能力。
```

## P1-WP07 — Market Data PIT Snapshot

**Status:** DONE

- [x] bar/tick/snapshot 有 available_at
- [x] revision aware
- [x] snapshot id
- [x] research manifest 引用 snapshot
- [x] future revision 不污染旧回测

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-19
  commit: null
  notes: 新增 MarketDataPITSpec、MarketDataRevision、MarketDataSnapshot 与 MarketDataPITSelector。选择器只回放明确 immutable DatasetVersion，要求唯一 normalized 制品、PASS 质量、不可变 quality assessment 和 publication authorization；行级 available_at 不得晚于其制品可读时间，未声明列、并列冲突修订、future row、无授权/无质量证据、schema 不一致均失败关闭。快照只保存 canonical bytes 并逐次重建 frame，revision 与 frame 逐行绑定。授权 hash 与完整脱敏 scope 会冻结进 snapshot/manifest；应用回测入口重新计算 snapshot，并严格核对 historical_backtest 用途、画像维度和 profile universe 的产品/交易所覆盖，不能扩大仅内部研究或部分品种授权。
  verification:
    - pytest tests/data_platform/unit/test_market_pit.py tests/data_platform/contract/test_point_in_time_semantics.py tests/research/statistical/test_no_lookahead.py tests/research/unit/test_admission.py（临时安全配置根）：18 passed
    - pytest tests/data_platform tests/architecture tests/research（临时安全配置根；未启动数据库）：260 passed
    - ruff check PIT、应用回测、研究准入与对应测试；mypy PIT/回测/研究准入与对应测试；git diff --check
  residual_boundary:
    - 当前 MarketDataSnapshot 是单一 STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY，manifest 明确 decision_time_safe=false，研究准入会阻断它进入候选策略；完整逐决策 PIT replay/Look-ahead guard 留给后续研究工作包。
    - legacy storage/market 和 data_manifest_v3 没有行级 available_at、不可变质量或授权证据，PIT selector 不会回退读取它们。
    - 现有真实来源未获发布授权；本完成记录只使用离线、合成 commercial_licensed fixture，未产生真实研究或交易数据。
```

## P1-WP08 — Data Platform E2E

**Status:** DONE

```text
Source
→ Raw
→ Normalize
→ Validate
→ DatasetVersion
→ Research Consumer
```

必须能用 fixtures 完全离线运行。

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-19
  commit: null
  notes: 完成 Source → Raw → Normalize → Validate → DatasetVersion → Research Consumer 的单条受控离线闭环。测试 adapter 只从持久化 raw JSON bytes 重新解析标准化表，Publisher 对同一 raw 双执行 normalize；真实 DataQualityEngine 运行全部十项规则并把 PASS assessment 作为不可变 sidecar 写入制品库。随后通过 DatasetVersion replay、MarketDataPITSelector 与真实研究回测入口，且回测 manifest 冻结授权范围和静态 PIT 标记。
  verification:
    - pytest tests/data_platform/integration/test_data_platform_e2e.py（临时安全配置根）：1 passed
    - pytest tests/data_platform tests/architecture tests/research tests/platform/contract/test_master_plan_contract.py tests/platform/contract/test_documentation_contracts.py（临时安全配置根；未启动数据库）：271 passed
    - ruff check .；ruff check P1-WP08 测试；git diff --check
    - 独立终审：确认未使用 _PassQualityEngine 或预置 DataFrame；raw→normalize、真实质量十规则、不可变 DatasetVersion/PIT/研究消费者链均已覆盖
  residual_boundary:
    - 该 E2E 仅使用测试内合成 commercial_licensed 来源、固定的日历/合约/coverage 引用事实和本地临时制品根；不下载真实市场数据，不写数据库，也不增加交易资格。
    - 当前测试通过的是单一 STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY，仍明确 decision_time_safe=false；逐决策时点 look-ahead guard 属于后续 Research 工作包。
    - 仓库现有真实数据来源、runtime 日历和执行规则制品仍未获得可发布授权；真实 CTP 继续失败关闭。
```

---

# 11. P2 — Research & Strategy Platform

## P2-WP01 — Feature Registry

**Status:** DONE

核心对象：

```text
FeatureSpec
FeatureVersion
FeatureDependency
FeatureLineage
FeatureValue
```

要求：
- versioned
- PIT safe
- input dataset traceable
- parameter traceable
- deterministic backfill

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-20
  commit: null
  notes: 新增不可变 FeatureSpec、FeatureVersion、FeatureDependency、FeatureDatasetEvidence、FeatureLineage、FeatureValue 与 FeatureBackfill，并以受控 FeatureRegistry 管理。Registry 只接受已登记的 FeatureComputer，按 FeatureVersion/implementation_hash 绑定实现；创建血缘和物化回填时均从其自持的 ArtifactStore 重新执行 P1 MarketDataPITSelector，完整比对 DatasetVersion、PIT spec、revision、来源可用时间、授权与 scope 证据。参数、输入 schema/列/实体键/时间列必须与重放快照精确匹配；同一 lineage 双执行结果不同或后续结果企图覆盖既有 backfill 时失败关闭。
  verification:
    - pytest tests/research/unit/test_feature_registry.py tests/research/integration/test_feature_registry_pit.py：16 passed
    - pytest tests/research/unit tests/research/integration/test_feature_registry_pit.py tests/architecture：86 passed
    - ruff format --check、ruff check 与 mypy src/northstar_quant/research/features（P2-WP01 变更范围）：通过
    - 独立复审：确认外部 selector/临时 lambda、手工 lineage、未发布 DatasetVersion 和可变实现身份均不能绕过受控物化路径
  residual_boundary:
    - 当前只支持单一 P1 STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY 输入，FeatureLineage/FeatureBackfill 固定 decision_time_safe=false；不得把它作为逐决策无前视、候选策略或生产资格证据。
    - P2-WP01 不接受 feature-to-feature、多输入或持久化 FeatureBackfill，也未接入 Experiment、Backtest、Research Admission 或交易路径；后续消费者只能接纳 Registry 发出的、可重放的证据。
    - implementation_hash 是受信任构建/代码审查的实现身份，不是 Python 沙箱或跨进程代码证明；后续实验和持久化工作包需继续绑定构建制品。
```

## P2-WP02 — Canonical Feature Families

**Status:** DONE

```text
technical/
momentum/
carry/
basis/
inventory/
positioning/
macro/
intelligence/
```

第一批至少：

- momentum
- realized volatility
- carry
- term structure
- basis
- volume/OI
- inventory
- positioning

每个 feature 必须写：
- 定义
- 输入
- lookback
- 缺失值语义
- 时间语义
- 单元测试

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-20
  commit: null
  notes: 新增受控、代码内 catalog 的九个 canonical feature：momentum.roc、technical.realized_volatility、technical.volume_ratio、technical.open_interest_change、carry.annualized_roll_yield、carry.term_structure_slope、basis.relative_basis、inventory.level_change 与 positioning.net_position_ratio。每个定义冻结输入 schema、实体键、事件/可用时间、参数、lookback、缺失原因与 implementation/spec identity；computer 只能从 P1 immutable MarketDataSnapshot 读取并经 FeatureRegistry 物化。
  verification:
    - pytest tests/research/unit/test_canonical_feature_families.py tests/research/integration/test_canonical_feature_families_pit.py：24 passed
    - pytest tests/research/unit tests/research/integration tests/research/statistical tests/architecture、主计划与文档契约：148 passed
    - ruff format --check、ruff check 与 mypy src/northstar_quant/research/features（P2-WP02 范围）：通过
    - 独立复审：确认未绕过 P1 DatasetVersion/PIT/Registry，实际合约 ID、授权 scope、产品/交易所及跨交易所曲线均失败关闭。
  residual_boundary:
    - cn_futures_feature_bar_v1、cn_futures_actual_contract_feature_bar_v1、cn_futures_curve_triplet_v1、cn_futures_basis_daily_v1、cn_futures_inventory_v1 与 cn_futures_positioning_v1 目前只是 Research consumer contract 加合成授权 fixture；仓库没有真实来源投影、已获授权的发布器或画像接入，不能称为真实数据支持。
    - actual_contract_id_in_scope() 只验证 Contract Master 形式的 ID、冻结授权 scope 与行 product；当前 Contract Master 的实际 contracts 仍为空，因此它不是 Contract Master 成员实证。未来 Data Platform 投影必须以受验证的 Master/来源事实建立该绑定。
    - curve/basis 的 available_at 表示制品发布可用时间，不是报价对齐本身的证明；未来 Data Platform 必须发布带来源报价/对齐事实的不可变投影，才可把它用于真实研究输入。
    - 所有输出仍是 STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY 且 decision_time_safe=false；没有接入 Experiment、逐决策 PIT、策略、回测准入或交易路径。
```

## P2-WP03 — Experiment Model

**Status:** DONE

```text
ExperimentSpec
ExperimentRun
```

记录：

```text
strategy_version
feature_versions
dataset_versions
parameters
train_period
validation_period
oos_period
cost_model
slippage_model
random_seed
code_revision
```

```yaml
completion:
  completed_at: 2026-08-20
  commit: null
  notes: 新增纯内存 ExperimentRegistry、不可变 ExperimentSpec/ExperimentRun、StrategyVersionReference 与完整的 feature/dataset 输入绑定。Registry 只能从同一受控 FeatureRegistry 读取已登记的 FeatureLineage/FeatureBackfill；每项输入保留 DatasetVersion、PIT spec/snapshot/revision、来源可用时间、授权 scope 与 evidence hash，且所有输入必须使用同一个 static as_of、同一代码 revision。训练/验证/OOS 闭区间严格不重叠，随机种子显式且非负；Spec 内容 hash 不含展示性 experiment_id，避免同一声明被伪装成两份独立研究证据。
  verification:
    - pytest tests/research/unit/test_experiment_registry.py tests/research/contract/test_experiment_model_contract.py：19 passed
    - pytest tests/research/unit tests/research/contract：102 passed
    - pytest Feature Registry、canonical family、Experiment、architecture：64 passed
    - ruff format --check、ruff check 与 mypy src/northstar_quant/research/experiments src/northstar_quant/research/features/registry.py：通过
    - 独立对抗复审：Mapping/字符串/任意 iterable 不能静默降格为 hash 元组；路径、凭据、原始数据结构和裸运行结果均失败关闭
  residual_boundary:
    - ExperimentSpec/Run 固定为 STATIC_REPRODUCIBILITY_ONLY、decision_time_safe=false、eligible_for_backtest=false、eligible_for_admission=false；它们只记录静态可复现证据，不能触发回测、候选策略准入、Research Decision 或交易。
    - StrategyVersionReference 仍是声明性构建身份；现有 Strategy Registry 尚未提供不可变 StrategyVersion。ExperimentRun 只保存 configuration/outcome/evidence 的 SHA-256 引用，P2-WP03 不持久化这些制品，也不把 hash 当作真实性签名。
    - 参数、成本和滑点模型目前只接受扁平有限标量；参数空间、walk-forward、真实 BacktestRequest/Result、逐决策 PIT replay、Validation 与持久化实验账本留给后续 P2 工作包。
```

## P2-WP04 — Backtest Interface Unification

**Status:** DONE

保留三种能力：

```text
weight_return
futures_daily
futures_intraday_replay
```

统一输出：

```text
BacktestRequest
BacktestResult
RunManifest
```

不得把三种引擎包装成相同真实性。

```yaml
completion:
  completed_at: 2026-08-20
  commit: null
  notes: 新增 research.backtest 的 BacktestRequest、BacktestResult 与 RunManifest v4。请求冻结 target_weight 输入、画像/频率链、完整数据/PIT replay 证据、成本假设与代码身份；结果按 engine 固定 fidelity、数据语义、执行审计层级和限制，不能由调用方伪造。RunManifest 只做可审计的离线研究记录，candidate_admission_eligible 永远为 false，上游准入评估仅以 observed_policy_status 保存。正式报告不记录墙钟时间，必须由冻结运行重建 Markdown/JSON 后逐字节复用，文件内自报 hash 不是信任根。
  verification:
    - pytest P2-WP04 模型、三引擎、报告、PIT 与应用工作流定向集合：44 passed
    - ruff check P2-WP04 生产/测试范围、mypy research/backtest/models.py、mypy baseline check/ratchet、git diff --check：通过
    - 独立复审：修正代码溯源仓库根，冻结完整 PIT replay evidence，封存回测器注册表，正式报告只接受 BacktestRun；联动篡改 report.json、report.md 与自报 hash 也会拒绝复用。
  residual_boundary:
    - weight_return 是连续研究序列收益近似，不含订单、成交、保证金或换月；futures_daily 是实际合约逐日状态机，只含目标/模拟成交事件；futures_intraday_replay 才含分钟级订单生命周期和部分成交。三者的订单、成交、成本和数量不得横向等价比较。
    - 当前 legacy 数据与 P1 STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY 都不是逐决策 PIT replay；P2-WP04 明确拒绝将其写成候选策略准入。P2-WP05 的 Guard 与 P2-WP06 的验证报告仍不会提升该路径；之后仍需 P2-WP07 的人工 Research Decision。
    - RunManifest v4 保存数据、目标、代码和输出 checksum，不写入裸行情、路径、凭据、数据库记录或交易对象；没有 Docker、数据库迁移、券商或订单提交改动。
```

## P2-WP05 — Lookahead Guard

**Status:** DONE

自动检测：

- future feature
- revised historical data
- future contract knowledge
- future fee/margin rule
- future event
- future target

出现即失败。

当前实施边界：

- 只接受显式、排序且无重复的逐决策 replay plan；每个决策时点必须重新选择 immutable
  DatasetVersion/PIT snapshot，不能从回测结束时的最终快照反推历史可见信息；
- 对提交的市场 revision、特征、合约知识、费率/保证金规则、事件和目标证据逐项检查；任一
  可用时间晚于对应决策即产生确定性违规，手工 MarketDataSnapshot 必须与 ArtifactStore 重放结果
  精确一致；
- 当前 `LookaheadGuard` 只能签发可重算的证据一致性回执，固定
  `PER_DECISION_POINT_IN_TIME_REPLAY`、`decision_time_safe=false` 与
  `candidate_admission_eligible=false`。Research Admission 不信任调用方手写的安全布尔字段；
- `application/decision_replay_backtest.py` 已建立首个隔离的 Application target producer：仅允许
  `cn_futures_daily_trend_offline` 的 `weight_return + futures_trend` 连续日线语义；每个 checkpoint
  必须声明 `decision_event_time`，从 immutable 前缀重放后只取同一 event-time 的 target slice，并冻结
  市场 revision、完整本地 target 代码闭包/`uv.lock` 依赖摘要、策略参数和 target hash。组合根只接受精确
  `DecisionReplayPlan` 与 `ArtifactStore`，每份重放快照必须精确匹配 checkpoint 的 DatasetVersion/PIT spec/as-of；
  当前市场 bar、策略输出以及组合/风控后的最终 target 均必须完整覆盖画像品种池，历史缺口或任何删行都会失败关闭。
  `build_profile_decision_replay_receipt` 会将该 immutable trace 的每个 target slice 与重新选择的市场快照
  精确绑定，并调用 `LookaheadGuard` 重算 hash-only receipt；`build_profile_decision_replay_backtest_request`
  会再次完整重算 certificate 后，构造只允许 `weight_return` 的 `decision_replay_receipt` BacktestRequest。
  该数据类型保存每个 checkpoint 的完整 immutable PIT 证据、receipt/certificate/trace/schedule/target hash，
  并要求 BacktestRequest 的 `strategy_identity_hash` 精确匹配 receipt 中冻结的策略代码闭包/参数身份；
  不能伪装成单一 static PIT snapshot，且不执行回测器、Research Admission、CLI、数据库或交易入口。每个 receipt
  必须显式声明 Feature、Event、Contract、Fee/Margin Rule
  输入为 `provided` 或由冻结策略身份证明的 `not_used`；缺任何类别、或 `not_used` 与同类证据冲突都会失败关闭。
  receipt 的稳定 hash 精确绑定 trace 与 certificate，供未来 BacktestRequest 以 hash-only 方式引用；
  当前 `decision_replay_receipt` 请求是 construction-only，`BacktestResult.bind_request` 会明确拒绝绑定结果；
  receipt/trace 仍固定 `decision_time_safe=false` 与不可准入；
- 现有 static PIT、FeatureLineage/FeatureBackfill、Experiment、legacy 和普通回测路径继续
  保持 `decision_time_safe=false`，不得升级为候选策略或交易证据。

completion:
  completed_at: 2026-08-21
  commit: null
  notes: >-
    LookaheadGuard 逐 checkpoint 重放市场 DatasetVersion；受控 FeatureRegistry 会重放同一 PIT
    snapshot 并双执行已登记计算器，Event fact 与 Contract RuleBook 均只能从 immutable DatasetVersion
    重放。签发/验证证书时会重新选择上述来源，手工 Feature/Event/Contract/Rule 证据被拒绝。连续
    futures_trend target trace 与代码闭包、策略参数、target hash、receipt 和 construction-only
    BacktestRequest 精确绑定；未使用类别也必须由该冻结策略身份显式声明。当前该回执仍为研究审计
    证据，固定不可准入、不可交易，后续准入由 P2-WP06/P2-WP07 继续处理。
  verification:
    - uv run pytest tests/research -q: 195 passed
    - uv run ruff check src/northstar_quant/research/features src/northstar_quant/research/validation tests/research/unit/test_lookahead_guard.py tests/research/integration/test_feature_registry_pit.py
    - uv run python scripts/ci/check_mypy_baseline.py check
    - git diff --check

residual_boundary:
  - >-
    Artifact Event fact 只保存可回放的事件可用性与 source artifact hash，不替代 P4 的 Document、
    Event merge、ontology、impact 或 feature 语义链。
  - >-
    Artifact RuleBook 的历史规则固定 execution_eligible=false；它不能开启任何 execution 或 broker
    路径。候选与实盘资格仍必须经过 P2-WP06/P2-WP07、P3 风险和 P5 交易门禁。

## P2-WP06 — Validation Framework

**Status:** DONE

支持：

- IS / Validation / OOS
- walk-forward
- rolling window
- parameter sensitivity
- transaction-cost stress
- slippage stress
- latency stress
- bootstrap
- Monte Carlo（适用）
- regime split

completion:
  completed_at: 2026-08-21
  commit: null
  notes: >-
    新增纯研究、确定性的 Validation Framework：冻结 DatasetVersion、FeatureVersion、策略、
    回测结果和代码身份；严格计算 IS/Validation/OOS、非重叠 walk-forward OOS、rolling
    window、成本/滑点/延迟压力、参数邻域、bootstrap、Monte Carlo 与 OOS regime 分组。报告只
    保存上游 hash 与指标，输入收益序列不被写入报告，并固定 eligible_for_admission=false。
  verification:
    - uv run pytest tests/research -q: 200 passed
    - uv run ruff check .
    - uv run python scripts/ci/check_mypy_baseline.py check
    - git diff --check

residual_boundary:
  - >-
    验证框架不会重跑策略或回测引擎；每个参数邻域必须携带实际重跑的 immutable 收益序列，
    不接受布尔“通过”摘要。
  - >-
    ValidationReport 永远不授予候选、模拟或生产资格；P2-WP07 必须将它与人工决策状态机
    及完整准入门禁组合。

## P2-WP07 — Research Decision State

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-21
  commit: null
  notes: >-
    Immutable, forward-only research decision states are bound to a complete
    validation/admission evidence chain. Candidate and higher states require a
    named human approval targeted at the exact state and a PASS admission result;
    no metric, including a single high Sharpe, can promote a strategy automatically.
    Research decisions are permanently ineligible for direct trading.
```

```text
DRAFT
REJECTED
RESEARCH_ONLY
CANDIDATE
PAPER_ELIGIBLE
SIM_ELIGIBLE
PRODUCTION_CANDIDATE
```

任何单次高 Sharpe 回测不得自动升级。

## P2-WP08 — Research Report

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-21
  commit: null
  notes: >-
    Immutable Research Cards now bind the RunManifest, validation report and
    research decision to the same experiment/backtest evidence chain. They expose
    all required reproducibility, IS/OOS, execution-assumption, turnover,
    drawdown, product-contribution, regime, stress, limitation and decision data,
    while remaining permanently ineligible for trading.
```

必须包含：

- dataset version
- feature versions
- strategy version
- code revision
- IS/OOS
- cost/slippage
- turnover
- drawdown
- product contribution
- regimes
- stress
- limitations
- decision

## P2-WP09 — Research E2E

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-21
  commit: null
  notes: >-
    Offline E2E proves two independent Dataset→Feature→Experiment→Backtest→
    Validation→Research Card executions yield the same card hash and JSON. The
    static Experiment remains ineligible for backtest/admission, and the final
    Research Card remains ineligible for trading.
```

```text
Dataset
→ Feature
→ Experiment
→ Strategy
→ Backtest
→ Validation
→ Research Card
```

要求完全可复现。

---

# 12. P3 — Portfolio & Risk

## P3-WP01 — Canonical Portfolio Targets

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-21
  commit: null
  notes: >-
    Added immutable StrategyTarget, PortfolioTarget and ApprovedPortfolioTarget
    contracts. Every target records generation/effective/expiry times and source
    strategy/version lineage; approval requires risk-evidence identity and remains
    explicitly ineligible for direct broker orders.
```

定义：

```text
StrategyTarget
PortfolioTarget
ApprovedPortfolioTarget
```

必须包含：
- generated_at
- effective_at
- expires_at
- source strategy/version

## P3-WP02 — Allocation Engine

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-21
  commit: null
  notes: >-
    Added deterministic, fail-closed first-stage allocation policy and result
    models. Fixed budget, realized-volatility scaling, normalized risk budget,
    per-strategy cap and cash reserve are explicit; constrained capacity remains
    cash and is never rescaled into new risk.
```

第一阶段：

- fixed budget
- volatility target
- risk budget
- capped allocation
- cash reserve

避免一开始上复杂优化器。

## P3-WP03 — Exposure Engine

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-21
  commit: null
  notes: >-
    Added deterministic, fail-closed exposure snapshots for gross/net, commodity,
    sector, exchange, direction, correlation cluster, margin and concentration.
    Missing classifications and duplicate instruments are rejected rather than
    silently assigned to a default risk bucket.
```

统一计算：

- gross
- net
- commodity
- sector
- exchange
- direction
- correlation cluster
- margin
- concentration

## P3-WP04 — Risk Limits

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-21
  commit: null
  notes: >-
    Added typed limit and measurement snapshots covering contract, commodity,
    sector, exchange, strategy, account, gross/net leverage and margin
    utilization. Every check emits PASS/WARN/BLOCK plus deterministic evidence;
    unknown or non-finite observations are always BLOCK.
```

至少：

- per contract
- per commodity
- per sector
- per exchange
- per strategy
- per account
- gross leverage
- net leverage
- margin utilization

每条规则输出：
`PASS / WARN / BLOCK + evidence`

## P3-WP05 — Risk State Machine

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-21
  commit: null
  notes: >-
    Added immutable, hash-chained risk state snapshots. HALT cannot transition
    directly to NORMAL; recovery must first enter MANUAL_RECOVERY with a named
    approver, and transition times cannot move backward.
```

```text
NORMAL
LIMIT_ONLY
REDUCE_ONLY
HALT
MANUAL_RECOVERY
```

要求：
- 状态转换审计
- HALT 不自动恢复 NORMAL
- Manual recovery 需要明确人工动作

## P3-WP06 — Stress & Scenario

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-21
  commit: null
  notes: >-
    Added deterministic, offline gap, limit-move, volatility, liquidity,
    correlated-commodity, margin-increase and FX scenarios. Gross notional or
    margin inputs that are unknown or invalid fail closed instead of estimating a loss.
```

至少：

- gap
- limit-up/down
- volatility shock
- liquidity collapse
- correlated commodity shock
- margin increase
- FX shock

## P3-WP07 — Portfolio/Risk E2E

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-21
  commit: null
  notes: >-
    Added offline target→allocation→exposure→risk→approval E2E coverage. A
    target is approved only when every limit passes, and even then it remains
    ineligible for direct broker orders; a BLOCK has no approval or execution exit.
```

```text
Multi Strategy Targets
→ Allocate
→ Exposure
→ Risk
→ Approved Portfolio
```

BLOCK 后不得进入 execution。
---

# 13. P4 — Intelligence / EventAlpha

目标：

> 全球事件 → 经济机制 → 商品/合约影响 → 可回测 Feature。

## P4-WP01 — Intelligence Domain

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-21
  commit: null
  notes: >-
    Added immutable Source, Document, Entity, Event, Mechanism, Impact and
    Evidence contracts. Documents are source records; Events require non-empty
    evidence spans, ontology-version-aligned mechanisms and impacts, and never
    collapse a Document into an Event.
```

```text
Source
Document
Entity
Event
Mechanism
Impact
Evidence
```

必须确保 Document 与 Event 分离。

## P4-WP02 — Ontology v1

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-21
  commit: null
  notes: >-
    Added version-locked events, mechanisms, entities, commodities and relations
    resources plus a fail-closed loader. All five files must share one version;
    unknown event types, including trading actions, are rejected.
```

文件：

```text
ontology/events.yaml
ontology/mechanisms.yaml
ontology/entities.yaml
ontology/commodities.yaml
ontology/relations.yaml
```

Event 一级：

```text
SUPPLY
DEMAND
INVENTORY
LOGISTICS
WEATHER
POLICY
MACRO
GEOPOLITICS
POSITIONING
FINANCIAL
```

必须带 `ontology_version`。

## P4-WP03 — Document Ingestion

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-21
  commit: null
  notes: >-
    Added offline SourceAdapter poll/stream contracts and deterministic Document
    normalization. Documents require published/collected times, source, license,
    canonical HTTPS URL and content hash; adapters returning untyped collections
    are rejected.
```

统一 SourceAdapter：

```python
class SourceAdapter(Protocol):
    def poll(...): ...
    def stream(...): ...
```

首批：
- RSS
- GDELT
- EIA
- CFTC
- 国内交易所公告
- 重点商品公司公告

每条文档保存：

```text
published_at
collected_at
source
license_classification
content_hash
```

## P4-WP04 — Document Dedup

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >-
    Added deterministic document clusters based on canonical URL, exact content
    hash, normalized title overlap, explicit semantic key and repost relation.
    Clusters remain document-only and cannot create Events or Features.
```

利用：

- canonical URL
- exact hash
- title similarity
- semantic similarity
- repost detection

同一事件的转载不得产生多个独立交易 Feature。

## P4-WP05 — Entity Extraction / Resolution

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >-
    Added canonical entity records and a deterministic case-insensitive alias
    resolver for all required entity types. Unknown aliases, duplicate canonical
    IDs and aliases that point to multiple entities are rejected.
```

Entity：

```text
Country
Region
Company
Mine
Refinery
Port
Pipeline
Commodity
Exchange
Instrument
Contract
GovernmentAgency
```

必须支持 alias/canonical ID。

## P4-WP06 — Event Extraction

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >-
    Added schema- and ontology-validated ExtractedEvent candidates. Every
    candidate is bound to its source Document content hash, explicit evidence
    span and confidence; unknown ontology types or mismatched versions fail closed.
```

输入：
`Document`

输出：
`ExtractedEvent`

LLM 可以参与，但必须：
- schema validation
- ontology validation
- evidence span
- extraction confidence

## P4-WP07 — Event Merge

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >-
    Added CanonicalEvent merge keyed by explicit semantic identity and lifecycle.
    Out-of-order candidates older than the current observation cannot overwrite
    current state; merged extraction identities are retained.
```

```text
Document A ┐
Document B ├→ CanonicalEvent
Document C ┘
```

生命周期：

```text
OPEN
CONFIRMED
UPDATED
RESOLVED
RETRACTED
```

必须处理 out-of-order arrival。

## P4-WP08 — Confidence Model

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >-
    Added a bounded, deterministic final confidence model: source trust ×
    cross-source confirmation × extraction confidence × entity-resolution
    confidence. A zero independent factor yields zero final confidence, so an
    extraction or LLM confidence alone cannot establish an Event.
```

至少：

```text
SourceTrust
× CrossSourceConfirmation
× ExtractionConfidence
× EntityResolutionConfidence
```

禁止仅采用 LLM 自报 confidence。

## P4-WP09 — Mechanism Engine

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >-
    Added all eleven versioned economic mechanisms and a fail-closed assessment
    engine. An assessment is Document evidence-backed, rationale-bearing and
    ontology-valid; incompatible Event/mechanism pairs cannot be classified and
    the engine exposes no trading signal.
```

至少：

```text
SUPPLY_REDUCTION
SUPPLY_INCREASE
DEMAND_INCREASE
DEMAND_REDUCTION
INVENTORY_DRAW
INVENTORY_BUILD
TRANSPORT_DISRUPTION
IMPORT_COST_INCREASE
EXPORT_AVAILABILITY_REDUCTION
RISK_PREMIUM_INCREASE
LIQUIDITY_SHOCK
```

## P4-WP10 — Impact Graph

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >-
    Added typed Event → Mechanism → Entity → Commodity → Market → Instrument →
    Contract impact paths for the six v1 commodities. Ontology versions,
    affected commodities and each downstream mapping must agree; the graph has
    no price, target, order or execution semantics.
```

```text
Event
→ Mechanism
→ Entity
→ Commodity
→ Market
→ Instrument
→ Contract
```

首批商品：
- Copper
- Crude Oil
- Gold
- Iron Ore
- Soybean Meal
- Palm Oil

## P4-WP11 — Market Context

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >-
    Added immutable, DatasetVersion-bound MarketContextSnapshot records for
    inventory, curve, basis, positioning, volatility, USD, CNY, macro regime
    and seasonality. A context can only be consumed when available_at is no
    later than the simulation time; future and unmodelled inputs fail closed.
```

输入：

```text
inventory
term structure
basis
positioning
volatility
USD
CNY
macro regime
seasonality
```

输出：
`MarketContextSnapshot`

## P4-WP12 — Event Study

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >-
    Added immutable DatasetVersion-bound event-study results for all six
    specified windows and return, volatility, volume, OI, spread, basis, MFE
    and MAE. Results are accessible only after the exact window completes and
    available_at is no later than the research simulation time.
```

窗口：

```text
T+15m
T+1h
T+4h
T+1d
T+3d
T+5d
```

指标：

```text
return
volatility
volume
OI
spread
basis
MFE
MAE
```

必须 PIT correct。

## P4-WP13 — Analogue Engine

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >-
    Added PIT-gated structured analogue matching over event type, severity,
    geography, commodity, inventory, USD, volatility and curve regimes.
    Optional embeddings remain a bounded 15% supplemental component and cannot
    replace the mandatory structured distance or ontology validation.
```

相似度至少考虑：

- event type
- severity
- geography
- commodity
- inventory regime
- USD regime
- volatility regime
- curve regime

Embedding 只能作为辅助，结构化距离必须存在。

## P4-WP14 — Intelligence Features

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >-
    Added all nine specified intelligence.* canonical Feature Registry entries,
    their immutable versions and controlled computers. They consume only typed
    Event-feature snapshots with explicit event_time/available_at and bounded
    scores; they are research features, not targets, orders or trading signals.
```

第一批：

```text
supply_risk_1h
supply_risk_6h
supply_risk_24h
demand_shock
geopolitical_risk
inventory_stress
event_novelty
event_confidence
contextual_impact
```

必须进入 Feature Registry。

## P4-WP15 — Intelligence E2E

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >-
    Added a fully offline golden copper-supply-outage corpus covering Document
    normalization/deduplication, entity resolution, extracted Event validation,
    confidence, mechanism, impact path, PIT context, event study and registered
    intelligence Feature handoff. The corpus asserts there is no direct target,
    broker-order or execution path from intelligence evidence.
```

```text
Document
→ Event
→ Mechanism
→ Impact
→ Context
→ Feature
→ Backtest
```

至少一个完全离线 golden corpus。

---

# 14. P5 — Trading & Execution Production Grade

## P5-WP01 — Broker Contract

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >-
    Added typed broker identity, safe modes, capabilities, connection state,
    error codes and MarketGateway contracts. Paper, CTP Sim and durable wrappers
    expose the same status boundary; UNKNOWN or DISCONNECTED never permits new
    risk. CTP Sim remains a local-only simulator.
```

统一：
- BrokerAdapter
- MarketGateway
- typed errors/status

## P5-WP02 — Order Model

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >-
    Added a canonical, typed order lifecycle and immutable broker/client identity.
    Durable submission, replay and single-order callback boundaries now normalize
    adapter wording before persistence. Duplicate callbacks are idempotent, and
    terminal regressions or unknown recovery attempts fail closed.
```

状态：

```text
CREATED
SUBMITTING
ACCEPTED
PARTIALLY_FILLED
FILLED
CANCEL_PENDING
CANCELLED
REJECTED
UNKNOWN
```

要求：
- idempotency
- broker/client IDs
- invalid transition tests

## P5-WP03 — Position Model

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >-
    Added long/short today-yesterday, frozen and closable quantity, margin,
    realized PnL and unrealized PnL to position snapshots and a forward-only
    PostgreSQL migration. CTP Sim reserves pending closes and rejects requests
    beyond unfrozen closable quantity.
```

支持：

- long/short
- today/yesterday
- frozen
- closable
- average price
- margin
- realized/unrealized PnL

## P5-WP04 — Execution Planning

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >-
    Added a typed ExecutionPlan envelope bound to ApprovedPortfolioTarget,
    account and market snapshots, contract rules and immutable plan items.
    The existing CTP Sim futures planner now builds through that boundary;
    ExecutionPlan remains explicitly ineligible for broker submission.
```

输入：

```text
ApprovedPortfolioTarget
AccountSnapshot
PositionSnapshot
MarketSnapshot
ContractRules
```

输出：
`ExecutionPlan`

ExecutionPlan 不是 BrokerOrder。

## P5-WP05 — Pre-trade Gate

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >-
    Added a fail-closed, one-time PlanPreTradeGate that binds each submitted
    order to a passing PreflightResult and an immutable ExecutionPlan item.
    The guard is injected at the OrderRouter's final pre-broker boundary;
    mismatched, replayed, blocked-preflight, or wrong-plan orders are rejected.
```

检查：

- target validity
- price freshness
- contract state
- price limit
- available funds
- available position
- lot size
- max order
- risk state
- kill switch
- production eligibility

## P5-WP06 — CTP Sim Hardening

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >-
    CTP Sim now models asynchronous cancel acknowledgement as
    PendingCancel to Cancelled across reconnect, plus one-time front rejection
    without fabricated fills or terminal-state rewrites. Existing deterministic
    partial fills, duplicate/idempotent transitions, recovery, and explicit
    SHFE/INE today-yesterday close semantics are covered by unit and PostgreSQL
    reconciliation scenarios.
```

覆盖：

- async callback
- partial fill
- reject
- cancel
- reconnect
- duplicate callback
- recovery
- today/yesterday close

## P5-WP07 — Real CTP Adapter Skeleton

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >-
    Added a typed CTP front protocol, deterministic in-memory FakeCtpFront,
    and CtpBrokerAdapter skeleton. The adapter only accepts that exact fake
    type, binds each order to an enabled actual-contract mapping, verifies
    complete account-matched snapshots, and rejects all non-fake fronts before
    connect. The application composition root still rejects broker=ctp.
```

允许：
- adapter
- fake front
- protocol tests
- configs schema

禁止：
- 自动创建 production credentials
- 自动启用 live
- 自动连接真实账户并下单

## P5-WP08 — Reconciliation

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >-
    Broker orders, fills, positions, account snapshots and the internal ledger are reconciled in
    account scope. Unknown or conflicting facts roll back unsafe state writes and append a
    PostgreSQL reconciliation safety-state hash chain; successful reconciliation and runtime-risk
    PASS never automatically clear HALT, and non-paper recovery requires named HALT →
    MANUAL_RECOVERY → NORMAL action.
```

持续：

```text
Broker Orders ↔ Internal Orders
Broker Positions ↔ Internal Positions
Broker Account ↔ Internal Ledger
```

无法解释差异 => HALT / MANUAL_RECOVERY。

## P5-WP09 — Ledger

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >
    订单、成交、撤单、账户/持仓快照及归因既有账本持续保留；新增不可变券商结算事实与
    具名审批的 controlled adjustment，二者仅允许完全一致的幂等重放。迁移、PostgreSQL
    集成测试、完整 pytest、Ruff、mypy 基线和 diff 检查均已通过。
```

记录：

- orders
- fills
- fees
- margin
- cash
- settlement
- PnL
- controlled adjustments

## P5-WP10 — Failure Matrix

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >
    覆盖 disconnect、restart、duplicate fill、unknown order、stale price、DB unavailable、
    timeout、network partition、position mismatch、insufficient margin、price limit、cancel reject
    与 session rollover。未知、持久化失败和状态冲突均在创建新风险前失败关闭。
```

必须覆盖：

- disconnect
- restart
- duplicate fill
- unknown order
- stale price
- DB unavailable
- timeout
- network partition
- position mismatch
- insufficient margin
- price limit
- cancel reject
- session rollover

## P5-WP11 — Trading E2E

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >
    本地 ctp_sim E2E 覆盖 StrategyTarget → PortfolioTarget → risk approval → ExecutionPlan →
    plan-bound pre-trade gate → durable submit → Fill → Position → Reconciliation；全量 pytest、
    Ruff、mypy 基线与 diff 检查通过。未连接真实 CTP，也未启用实盘。
```

```text
Strategy
→ Portfolio
→ Risk
→ ExecutionPlan
→ SimBroker
→ Fill
→ Position
→ Reconciliation
```

必须无需真实资金即可完整验收。
---

# 15. P6 — Platform Foundation & Automation

## P6-WP01 — Config Unification

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >
    新增统一 RuntimeConfiguration 组合入口，严格绑定 NORTHSTAR_ Settings、唯一活动 app.yaml、
    生命周期画像、受管数据源、研究准入政策和完整 intelligence ontology。运行时目录、adapter/范围、
    准入状态/范围或 ontology 缺失均失败关闭；health 组合入口已执行同一校验。全量 pytest、Ruff、
    mypy 基线与 diff 检查通过，未启用实盘或改变交易安全开关。
```

统一：
- environment
- profiles
- data
- intelligence
- research
- risk
- execution

要求：
- `NORTHSTAR_` prefix
- safe defaults
- no hidden fallback
- schema validation

## P6-WP02 — Messaging Abstraction

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >
    新增 typed 进程内 FIFO 通知总线，覆盖 data、intelligence、research、portfolio、risk、order
    与 reconciliation topic。消息信封、topic、JSON payload、重复 ID、无订阅者和 handler 失败均显式
    校验；handler 失败保留队首，重试采用 message_id 幂等 at-least-once 语义。它不持久化，且不拥有
    ExecutionPlan、订单或任何风险放行权。全量 pytest、Ruff、mypy 基线与 diff 检查通过。
```

事件示例：

```text
data.ingested
data.validated
event.extracted
event.merged
feature.generated
research.completed
portfolio.approved
risk.blocked
order.updated
reconciliation.failed
```

第一版优先进程内/简单队列；不得提前 Kafka 化。

## P6-WP03 — Scheduling

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >
    新增 domain-neutral ScheduledJob/JobRegistry，统一 data、intelligence、feature、research、
    maintenance 与 live 作业类型。application 日频调度器的全部既有任务已经由该 registry 注册；
    LIVE 作业强制先执行 lifecycle gate，gate 异常绝不执行 action。cron 仍仅为候选触发，不能替代
    profile、calendar、preflight、plan gate、broker capability 或 kill switch。全量 pytest、Ruff、
    mypy 基线与 diff 检查通过。
```

统一调度：

- data jobs
- intelligence jobs
- feature jobs
- research jobs
- maintenance
- live loops

Scheduler 不得绕过生命周期门禁。

## P6-WP04 — Observability

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >
    统一 structured logging、health 与 fail-closed OperationalSnapshot；快照读取最近作业、
    runtime-risk 和 reconciliation 审计证据，缺失/查询失败均为 UNKNOWN。新增线程安全 metrics
    registry 与确定性 Prometheus 文本导出；health 输出 snapshot 和 metrics。broker sync/订单轮询
    显式写入 profile 作用域，未改变任何交易门禁。全量 pytest、Ruff、mypy 基线和 diff 检查通过。
```

统一：

- structured logging
- metrics
- health
- job state
- broker state
- data staleness
- risk state
- reconciliation status

## P6-WP05 — Security

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >
    建立统一机密防护边界：仓库密钥扫描覆盖 token、DSN 用户信息、认证头、私钥和常见供应商令牌，
    并已纳入 just check 与 CI。日志、CLI、报告、邮件导出和部署审计事件共享递归脱敏逻辑；发现既有
    报告含机密时邮件导出失败关闭。部署端验证远端 SSH 实际身份，拒绝 root 和服务账户；systemd 服务
    以受限服务用户运行。所有审计事件采用稳定、已脱敏 JSON。未启用真实 broker、实盘或生产凭据。
  verification:
    - "uv run pytest — 882 passed, 11 skipped"
    - "uv run ruff check ."
    - "uv run python scripts/ci/check_secrets.py"
    - "uv run python scripts/ci/check_mypy_baseline.py check"
    - "git diff --check"
```

- secrets untracked
- least privilege
- deploy user separation
- broker secret redaction
- audit
- export redaction

## P6-WP06 — Cross-platform Deployment Control

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >
    删除旧 Bash 本地部署控制面及其兼容入口，Windows/Linux 统一以 Python 编排预检、制品、SSH/SCP、
    Linux install/upgrade、迁移、切换后健康检查和失败自动回退。DEPLOY_HOST 改为 typed、严格校验的
    SSH target；强制 known-host 校验、连接/命令超时、非 root/非服务账户身份、空白远端环境、私有暂存
    清理和并发发布锁。实际发布不可跳过干净工作区、Ruff、密钥扫描、mypy 基线或完整 pytest；未启用
    实盘、真实 broker 或生产凭据。
  verification:
    - "Python deployment/inventory/security/contract focused tests"
    - "bash -n Linux target deployment and ops scripts"
    - "uv run ruff check ."
    - "uv run python scripts/ci/check_secrets.py"
    - "uv run python scripts/ci/check_mypy_baseline.py check"
    - "git diff --check"
```

控制端：
`Windows / Linux`

生产目标：
`Linux only`

链：

```text
just deploy-prod
→ Python orchestrator
→ build/package
→ SSH/SCP
→ Linux install/upgrade
→ health check
→ rollback if needed
```

## P6-WP07 — Linux Production Layout

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-22
  commit: null
  notes: >
    固定 Linux 生产 FHS 为 /opt/northstar、/etc/northstar、/var/lib/northstar、/var/cache/northstar
    和 /var/log/northstar；release、环境快照和 systemd 单元均与 release 版本绑定。root 目录从 / 开始
    逐级验证所有权、非链接和不可被 group/other 写入，部署制品/机密通过 root receiver 从已打开流交接，
    root 解压前执行有界 tar 策略。部署锁迁至 root-only deploy-state，未知中断或清理失败保留证据；
    root 递归写入前拒绝 mount/bind mount。特权 shell 与远程运维入口使用固定解释器、PATH 与空白环境。
    未启用真实 broker、实盘或生产凭据；root-owned immutable release runner 与持久发布事务仍明确留给 P6-WP09。
  verification:
    - "P6-WP07 focused deployment/contract/unit tests — 207 passed, 2 skipped"
    - "uv run pytest -q — 1053 passed, 13 skipped"
    - "uv run ruff check ."
    - "uv run python scripts/ci/check_secrets.py"
    - "uv run python scripts/ci/check_mypy_baseline.py check"
    - "git diff --check"
    - "bash -n all deployment and Linux ops shell scripts"
```

定义稳定布局，例如：

```text
/opt/northstar/releases/
/opt/northstar/current
/etc/northstar/
/var/lib/northstar/
/var/log/northstar/
```

systemd 配置版本化。

## P6-WP08 — Backup / Restore

**Status:** DONE

```yaml
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >
    Delivered an explicit six-category PostgreSQL logical backup bundle with versioned SHA-256
    manifest, secret rejection, complete-tree verification, and platform-native no-overwrite
    publication. Maintenance creation requires two explicit confirmations, a private external
    output parent, and inactive-service checks both before capture and before publication.
    The release-root binding is explicit so an immutable release venv never discovers config from
    installed-package paths or the caller CWD. The restore drill accepts only loopback northstar_test,
    restricts pg_restore to its generated schema within a BEGIN/ROLLBACK transaction, and proves
    schema/table identity plus sentinel data survive the rollback. No production restore path,
    credentials, live broker action, database deletion, schema cleanup, or data-volume deletion
    was added.
  verification:
    - "P6-WP08 focused unit/contract/architecture tests — 95 passed, 8 skipped"
    - "Docker PostgreSQL 17 isolated restore drill — pg_dump -> pg_restore --schema -> psql ROLLBACK PASS"
    - "Linux container no-replace publication probe — PASS"
    - "uv run pytest -q — 1082 passed, 21 skipped"
    - "uv run ruff check ."
    - "uv run python scripts/ci/check_mypy_baseline.py check"
    - "uv run python scripts/ci/check_secrets.py"
    - "git diff --check"
```

覆盖：

- PostgreSQL
- configs
- ontology
- run manifests
- critical runtime state
- release metadata

必须执行 restore drill。

## P6-WP09 — Release Pipeline

**Status:** DONE

```text
check
→ test
→ package
→ immutable manifest
→ deploy
→ health
→ promote
```

```yaml
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >
    Delivered a fixed root-owned signed release gate, canonical immutable release manifests,
    separately release-bound environment signatures, bounded runtime/control archive verification,
    and an append-only durable transaction lifecycle. The normal deployment identity can invoke
    only gate identity/submit verbs and streams bytes over SSH stdin; it cannot cause root to run
    deployer-writable temporary code. Signature authority, gate bootstrap, post-migration recovery,
    and private ntfy provisioning remain explicit root-operated workflows. No live broker, production
    credential, database downgrade, database deletion, or automatic post-migration service recovery
    was added.
  verification:
    - "P6-WP09 deployment/release focused suite — 270 passed, 2 skipped"
    - "uv run pytest -q — 1145 passed, 21 skipped"
    - "uv run ruff check ."
    - "uv run python scripts/ci/check_mypy_baseline.py check"
    - "uv run python scripts/ci/check_secrets.py"
    - "git diff --check"
```

---

# 16. P7 — AI-assisted Research Automation

**Status:** DONE

AI 目标：

> 提高研究吞吐，不获得未经批准的实盘权限。

## P7-WP01 — Typed Tool API

**Status:** DONE

P7-WP01 建立的原始闭合工具基线为：

```text
search_datasets
search_events
get_feature
create_experiment
run_backtest
run_validation
compare_experiments
generate_research_card
```

P7-WP04 在同一个 `TypedResearchToolApi` 闭合边界内正式增加唯一的只读
`inspect_dataset_quality`；当前 research-only allowlist 因此为九项。不得以此为由为研究、情报或
数据质量 Agent 增加平行 facade、动态 executor 或未审计的 domain access。P7-WP05 的运维观测使用
独立、最小权限的 `TypedOpsToolApi`，不得注入或暴露给上述 research-only Agent。

实现边界：

- 入口位于 `northstar_quant.application.agent_tools`，以冻结 typed request / response 和
  显式 injected read-only catalog / research workflow ports 组成；不得提供 HTTP、CLI、通用
  executor、动态字符串别名或隐式全局依赖。
- 所有 Dataset / Event / Feature / workflow result 必须以带时区 `as_of` / `available_at`
  校验 point-in-time 可见性；未知、未来、`latest`、无 evidence 或不完整绑定均 fail closed。
- API 不得读取环境、Settings、文件系统、网络或数据库，也不得可达 portfolio/risk、broker、
  execution、live service、deployment 或任何 credential 路径。
- `create_experiment` 保持 `STATIC_REPRODUCIBILITY_ONLY`；`run_backtest`、`run_validation`
  只能引用受控研究请求/证据链，不能接收裸数据、代码、SQL、路径、callable 或收益序列。
- `compare_experiments` 只在受控 workflow 证明可比时返回；`generate_research_card` 只能绑定
  已有 `RESEARCH_ONLY` decision。所有工具输出必须显式为 `eligible_for_trading=False`，且不创建
  target、ExecutionPlan、BrokerOrder 或任何人工批准状态。

验收标准：

- [x] P7-WP01 完成时 `ToolName` 为上述八项精确封闭枚举；P7-WP04 按独立 WP 将同一闭合 allowlist
  扩展为九项 `inspect_dataset_quality`，仍无特权 public operation；
- [x] 每项工具具有 typed contract、正常路径与必要 fail-closed 测试；
- [x] future `available_at`、feature/dataset 不匹配、无效 dispatch、secret-like 输入和
  非 `RESEARCH_ONLY` 输出均被拒绝；
- [x] 递归 architecture contract 证明工具模块不触达交易/风险/实时运行时/环境/网络/进程/DB；
- [x] 文档、主计划、Ruff、mypy baseline、secret scan 与完整 pytest 同步通过。

```yaml
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >
    Delivered a closed, explicitly injected research-only tool facade with immutable typed
    request/response contracts, exact version/hash references, point-in-time visibility checks,
    and no route to configuration, filesystem, network, database, portfolio/risk, broker, order,
    execution, live service, approval, or trading authority. Current durable Dataset/Event/Evidence
    adapters remain intentionally out of scope rather than pretending process-local registries are
    an audited persistence layer.
  verification:
    - "P7 typed-tool focused suite — 29 passed"
    - "uv run pytest -q — 1159 passed, 21 skipped"
    - "uv run ruff check ."
    - "uv run python scripts/ci/check_mypy_baseline.py check"
    - "uv run python scripts/ci/check_secrets.py"
    - "git diff --check"
```

## P7-WP02 — Research Agent

**Status:** DONE

允许：

```text
Hypothesis
→ FeatureSpec proposal
→ ExperimentSpec
→ Backtest
→ Validation
→ Research Card
```

禁止：
- production approve
- live trade
- credentials

实现边界：

- `ResearchAgent` 只能注入并调用 `TypedResearchToolApi.invoke(ToolName, request)`；不得直接导入
  research domain、platform、配置、DB、文件系统、网络、provider、portfolio/risk、broker 或 live runtime。
- Hypothesis 与 FeatureSpec proposal 仅是带 evidence/hash 的不可执行研究提案；不得注册 Feature、
  生成/执行策略代码、SQL、路径或 callable。
- 每次 run 必须固定一个带时区 `as_of`，并按
  `search_events → search_datasets → get_feature → create_experiment → run_backtest →
  run_validation → generate_research_card` 顺序执行；任一失败、未来可见性、身份不匹配或未知副作用
  均立即停止，不自动重试或继续下一步。
- backtest、validation 和 card 必须精确绑定先前观察到的 experiment/run/report；card 只能绑定
  预先存在的 `RESEARCH_ONLY` decision。ResearchAgent 不能创建或迁移 ResearchDecision、HumanApproval
  或任意 portfolio/execution/order 对象。
- 输出必须附带无敏感、可重算的 ordered trace（tool name、request/response hash 与 predecessor），
  全程保持 `RESEARCH_ONLY` / `eligible_for_trading=False`。

验收标准：

- [x] `ResearchAgent(TypedResearchToolApi)` 仅公开 `run(request) -> result`，并且递归架构边界无
  第二能力路径；
- [x] 正常链覆盖 hypothesis evidence、exact FeatureReference、static experiment、trusted backtest、
  validation 与 `RESEARCH_ONLY` card；
- [x] 乱序/伪造 ID、event evidence 不匹配、dataset/feature 不匹配、future availability、错误
  tool response、重复/超限 action 与 secret-like 输入均 fail closed，且不触发后续 workflow mutation；
- [x] trace 不含原始 query、Document、路径、凭据或 chain-of-thought，且 hash predecessor 链完整；
- [x] 文档、主计划、Ruff、mypy baseline、secret scan 与完整 pytest 同步通过。

```yaml
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >
    Delivered a deterministic ResearchAgent with TypedResearchToolApi as its only runtime capability.
    It accepts only evidence-bound, non-executable hypothesis/Feature proposals and executes the fixed
    seven-step research chain with exact as-of, identity, evidence, dataset/feature, static-experiment,
    backtest/validation/card predecessor checks. It creates neither Feature registrations, code, decisions,
    approvals, targets, execution plans nor orders; all output stays RESEARCH_ONLY and non-tradable. Every
    invocation records a secret-free hash-linked trace and is consumed before its first tool call, so failures
    and unknown side effects cannot be automatically retried.
  verification:
    - "P7 research-agent focused suite — 48 passed"
    - "uv run pytest -q — 1178 passed, 21 skipped"
    - "uv run ruff check ."
    - "uv run python scripts/ci/check_mypy_baseline.py check"
    - "uv run python scripts/ci/check_secrets.py"
    - "git diff --check"
```

## P7-WP03 — Intelligence Agent

**Status:** DONE

允许：
- source research
- event summary
- analogue
- impact explanation

所有结论引用 evidence。

验收：

- 仅通过既有 `TypedResearchToolApi.invoke(ToolName.SEARCH_EVENTS, ...)` 工作；P7-WP03 本身不扩大
  当时的八项 ToolName allowlist，也不新增平行 capability path；
- source research 仅返回授权 source/document/content hash/evidence hash/span/PIT 元数据，不能抓取
  外网或暴露正文、URL、路径、凭据；
- focus 必须精确绑定 Event ID、Event hash、evidence hash 与统一带时区 `as_of`；任一修订、歧义、
  未来可见或未知证据均 fail-closed；
- analogue 必须是不同且严格历史的 Event，具有独立授权 evidence、受控方法血缘和有界相似度；
- impact 只能说明 evidence-bound Event → mechanism → commodity 的经济影响，不含价格、合约选择、
  signal、target、approval、Feature、order 或 execution 语义；
- 输出固定 `RESEARCH_ONLY` / `eligible_for_trading=False`，带 secret-free hash trace；Agent 内部不自动
  retry 或用第二次查询填补缺口；
- 需有正常、PIT、citation/identity、analogue、impact、未知 response、失败停止与架构/公开 contract 测试，
  且全量 pytest、Ruff、mypy baseline、secret scan、diff check 均通过。

```yaml
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >
    application.intelligence_agent uses only TypedResearchToolApi.invoke(search_events) and a
    richer, immutable EventSummary projection to produce authorized source/document/span research,
    exact event summaries, independently evidenced historical analogues, and Event-to-mechanism-to-
    commodity impact explanations. No direct intelligence-domain, provider, database, configuration,
    filesystem, network, trading, risk, target, approval, feature, order, or execution capability exists.
    All findings are PIT/evidence-bound, RESEARCH_ONLY, non-tradable, and recorded in a secret-free
    hash trace; no automatic retry or gap-filling second query occurs.
  verification:
    - "P7 intelligence-agent focused suite — 69 passed"
    - "uv run pytest -q — 1199 passed, 21 skipped"
    - "uv run ruff check ."
    - "uv run python scripts/ci/check_mypy_baseline.py check"
    - "uv run python scripts/ci/check_secrets.py"
    - "git diff --check"
```

## P7-WP04 — Data Quality Agent

**Status:** DONE

允许检测：
- gap
- revision
- anomaly
- stale source
- contract mismatch
- broken lineage

不得伪造修复数据。

实现边界：

- `DataQualityAgent` 只能注入并通过 `TypedResearchToolApi.invoke` 调用
  `search_datasets → inspect_dataset_quality`；P7-WP04 仅在同一 facade 内增加第九项、只读且
  typed 的 `inspect_dataset_quality`，不得出现第二个质量 facade 或直接 Data Platform 访问。
- inspection request 必须精确绑定已授权的 Dataset ID/version/schema/lineage 与带时区 `as_of`；
  返回的 immutable assessment/lineage-verification/evidence hashes 和全部 finding 的
  `available_at` 均须在 `as_of` 前可见。
- report 必须恰好覆盖 `gap`、`revision`、`anomaly`、`stale_source`、`contract_mismatch`、
  `broken_lineage` 六类且不得重复；状态只能为 `DETECTED`、`NOT_DETECTED` 或 `UNKNOWN`。无
  anomaly certificate、lineage verification 或其它冻结证据时只能是 `UNKNOWN`，绝不推断为通过。
- Agent 与 Tool API 不得读取 raw payload、DataFrame、SQL、路径、URL、当前配置或数据库；不导入
  ArtifactStore、quality engine、Data Platform、网络、文件系统或 provider，且不得采集、重算、
  删除、修复、重发、发布或改写数据。
- 输出固定 `DIAGNOSTIC_ONLY` / `eligible_for_trading=False`，带两段 secret-free hash trace；不含
  remediation、approval、target、order、execution 或交易语义。失败、未知副作用或 response 异常
  立即停止，不能自动 retry。

验收标准：

- [x] `ToolName` 是当前九项精确封闭枚举；第九项只能是 `inspect_dataset_quality`，API 无特权
  public operation 或 parallel capability path；
- [x] dataset/assessment/lineage/evidence identity、六类 completeness/uniqueness、PIT、授权与
  UNKNOWN fail-closed 具有正常和失败路径测试；
- [x] DataQualityAgent 只经 typed `invoke` 完成固定两步读取，不能直连 Data Platform 或执行写入/修复，
  且异常后不重试或继续；
- [x] 输出可审计、无敏感 trace、diagnostic-only/non-tradable，architecture/public contract 均证明
  无交易、风险、broker、配置、DB、FS、网络或进程越权；
- [x] 文档、主计划、Ruff、mypy baseline、secret scan 与完整 pytest 同步通过。

```yaml
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >
    Expanded the same closed TypedResearchToolApi with one explicit read-only
    inspect_dataset_quality capability, then delivered a diagnostic-only DataQualityAgent.
    It performs exactly search_datasets followed by inspection for one identity-bound immutable
    dataset, preserves all six frozen evidence-backed categories including UNKNOWN, records a
    two-entry secret-free hash trace, and consumes its run identity before the first call. Neither
    agent nor facade can access raw data, the Data Platform, storage, configuration, network,
    remediation, publication, approval, trading, target, order, or execution capability.
  verification:
    - "P7 data-quality focused suite — 92 passed"
    - "uv run pytest -q — 1230 passed, 21 skipped"
    - "uv run ruff check ."
    - "uv run python scripts/ci/check_mypy_baseline.py check"
    - "uv run python scripts/ci/check_secrets.py"
    - "git diff --check"
```

## P7-WP05 — Ops Agent

**Status:** DONE

允许：
- health
- log summary
- deployment diagnosis
- backup status

禁止：
- 绕过 kill switch
- HALT 自动恢复 NORMAL

实现边界：

- `OpsAgent` 只能注入独立的 `TypedOpsToolApi`；不得扩大或重命名
  `TypedResearchToolApi`，以保证研究/情报/数据质量 Agent 永远不获得运维能力。
- 运维工具 allowlist 只有一个只读、原子化的 `inspect_ops_snapshot`。它通过显式注入的
  `OpsSnapshotCatalog` 返回同一受权 scope 的 frozen snapshot，包含 health、已验证脱敏的 log
  summary、deployment diagnosis 与 backup status，避免四次读取间出现时间或 scope 漂移。
- 请求只允许带时区 `as_of`；不得接受 hostname、IP、endpoint、path、service、shell、日志查询、
  命令、部署目标、账户或 broker 输入。所有 section 必须带 `scope_hash`、不可变 evidence hash 与
  `observed_at <= available_at <= as_of`；未来、未授权、scope 不一致、缺证据、未验证脱敏或未知类型
  均 fail closed。
- snapshot 仅允许 enum、bounded count、stable reason code 和 hash。不得泄漏日志正文、exception、
  stack、URL、文件路径、环境、凭据、主机、账户、订单、仓位、原始 health dict、`details_json`、
  backup artifact 或 release journal 内容。
- `OpsAgent` 固定只调用一次 `invoke(inspect_ops_snapshot, ...)`，在首次调用前消费 run/request
  identity；任何失败、畸形响应或未知副作用都停止且不可自动 replay。输出固定为
  `DIAGNOSTIC_ONLY` / `eligible_for_trading=False`，只附一条 secret-free hash trace。
- kill switch 和风险状态仅为只读观察；`ENABLED`、`HALT`、`MANUAL_RECOVERY` 与 `UNKNOWN` 必须原样
  保留，Agent 不拥有 resume、transition、approval、deploy、restart、rollback、migrate、restore、
  broker、target、order 或 execution 能力。
- 既有 health、远程 ops 脚本、backup/readiness、release journal 与 ORM latest 查询都不是安全的 Agent
  输入：它们可能读取配置/数据库/文件/SSH/进程或返回自由文本，且不能证明历史 PIT。可信、持久化的
  snapshot adapter 留待独立集成 WP；P7-WP05 不以进程内 registry 或当前 latest 状态伪装审计证据。

验收标准：

- [x] 独立 `TypedOpsToolApi` 的 closed allowlist 恰为一个 `inspect_ops_snapshot`，无 HTTP、CLI、
  subprocess、SSH、配置、数据库、文件系统、网络或动态 dispatch 能力；`TypedResearchToolApi` 保持
  九项 research-only allowlist 不变；
- [x] immutable typed snapshot 精确覆盖 health、redacted log summary、deployment diagnosis、backup
  status，并对 scope、authorization、PIT、evidence、时间序、redaction 与 UNKNOWN 实施 fail-closed；
- [x] `OpsAgent(TypedOpsToolApi)` 只公开一次只读 run，固定单步 trace，不能直达 platform/ops 脚本/
  live service/risk/trading，也不能发出恢复、部署、备份、交易或写入操作；
- [x] HALT、kill switch ENABLED 和 UNKNOWN 在输出中只读保留，结果始终 diagnostic-only/non-tradable，
  不含 command、action、recommendation、recovery、target、order 或 trade 语义；
- [x] 文档、主计划、Ruff、mypy baseline、secret scan 与完整 pytest 同步通过。

```yaml
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >
    Delivered a separate, single-operation TypedOpsToolApi so research-only agents cannot acquire
    operational privilege. Its injected catalog returns one authorized, immutable, point-in-time,
    same-scope snapshot of health, redacted log counts, deployment diagnosis, and backup status.
    OpsAgent makes exactly one typed read, consumes run identity before the call, records a secret-free
    hash trace, and is permanently DIAGNOSTIC_ONLY/non-tradable. Kill-switch and risk states including
    ENABLED, HALT, MANUAL_RECOVERY, and UNKNOWN remain observations only; no deploy, restore, resume,
    transition, broker, target, order, or execution path exists. Existing live health/SSH/log/backup/
    release readers remain deliberately outside this facade until a future immutable trusted snapshot
    adapter can prove both redaction and historical point-in-time semantics.
  verification:
    - "P7 ops focused suite — 49 passed"
    - "uv run pytest -q — 1275 passed, 21 skipped"
    - "uv run ruff check ."
    - "uv run python scripts/ci/check_mypy_baseline.py check"
    - "uv run python scripts/ci/check_secrets.py"
    - "git diff --check"
```

---

# 17. P8 — Integrated Production Candidate

**Status:** DONE

全部必须通过：

- [x] Data PIT correctness
- [x] Research reproducibility
- [x] Intelligence evidence/lineage
- [x] Portfolio/risk gate
- [x] CTP sim full loop
- [x] Deployment repeatable
- [x] Monitoring usable
- [x] Backup/restore verified
- [x] Architecture tests clean
- [x] Full pytest green
- [x] Ruff green
- [x] mypy baseline no regression
- [x] no uncontrolled live path

## P8-WP01 — Integrated Candidate Acceptance Harness

**Status:** DONE

P8 的首个工作包建立跨 P1—P7 的可复验集成候选验收，而不是将各领域已通过的局部测试误写成完整闭环。
它只允许 offline / paper / `ctp_sim` 证据，绝不启用真实 broker、真实凭据或 live trading。

初始目标：

- 盘点已有 Data PIT、Intelligence evidence/lineage、Research reproducibility、Portfolio/Risk、
  Execution/Reconciliation、Deployment、Monitoring 与 Backup/Restore 的可执行证据；
- 建立一个 deterministic integration harness 和显式 evidence matrix，证明端到端候选路径的每一段都
  使用已冻结的上游身份、时间和安全状态；
- 缺少连接证据、PIT 证明、人工审批、风险门禁、broker/account 状态或 observability 证据时必须失败关闭；
- 不创建 production profile、不接入真实 CTP、不自动恢复 HALT，也不把 research candidate 自动升级。

已完成：

- `application.candidate_acceptance.CandidateAcceptanceVerifier` 提供纯 stdlib、hash-only、冻结的验收回执。
  它精确要求九条证据车道和四条显式 seam，拒绝未来可见、缺失/重复、身份不匹配或不可能的 VERIFIED 证据；
  输出只能是 `BLOCKED` 或 `CANDIDATE_EVIDENCE_ONLY`，并永远保持 `eligible_for_trading=False`；
- `tests/e2e/test_integrated_candidate_acceptance.py` 在 WP01 验收时明确记录四条生产连接均为 BLOCKED：Data PIT→Research、
  Intelligence→Research、Research→Portfolio/Risk、Portfolio/Risk→CTP sim。独立车道测试通过绝不被误写为
  Research→Order 因果闭环；
- `scripts/ci/check_integrated_candidate.py` 固定重放 P1/P4/P2/P3/P5、P6、P7 的安全证据路径。它在调用 pytest
  前拒绝 live、production、未知环境和非 `paper` / `ctp_sim` broker；没有 shell、SSH、部署、恢复、网络或
  控制面能力。`just candidate-acceptance` 已加入 Linux CI。

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >-
    交付可复验但不授予交易权限的候选证据矩阵。它在初始状态故意把四条领域连接维持为 BLOCKED，
    防止将独立的 P1—P7 验收测试伪装成生产研究到订单闭环。
  verification:
    - pytest P8 evaluator / E2E / architecture / contract / CI-runner / just-contract focused suite: 75 passed
    - python scripts/ci/check_integrated_candidate.py: 245 passed, 7 skipped
    - pytest full suite: 1335 passed, 21 skipped
    - ruff check .；mypy baseline check；secret scan；git diff --check：通过
```

## P8-WP02 — Intelligence-to-Research Feature Projection

**Status:** DONE

依赖：P8-WP01。

将 P4 的受控 Event / mechanism / impact / market-context 证据投影为 P2 可消费的、版本化的
Research Feature 输入，而不是让 Research 直接读取原始 Document/Event，也不是让情报直接产出 target。

验收要求：

- 投影必须绑定精确的 Event、evidence、ontology、Feature 定义/版本、Dataset/市场上下文和 `available_at`；
  任一身份、PIT、授权或语义类型未知时 fail closed；
- 只能形成可研究的 Feature input / lineage，不生成 BUY/SELL、StrategyTarget、PortfolioTarget、ExecutionPlan
  或 BrokerOrder，也不自动批准或提升 Research Decision；
- 提供正常、future/mismatch/unknown 失败路径、PIT/lineage contract 及架构测试，并更新文档和 P8 证据矩阵中
  `INTELLIGENCE_TO_RESEARCH` seam 的真实状态。

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >-
    Delivered the `intelligence_feature_projection_v3` immutable Intelligence-to-Research seam.
    Every Event evidence record is
    replayed against its exact P1 raw artifact, publication receipt, document ID, content SHA-256,
    and UTF-8 span.  Every MarketContext is replayed from exactly one immutable normalized P1
    artifact, its DatasetVersion/receipt, and a closed full-row content commitment.  Only the
    narrowed hash-only provenance, bounded metric inputs, and explicit missing-data codes reach
    P2 FeatureLineage; no raw document or context payload, target, order, or trading authority
    crosses the boundary.
  verification:
    - "P8 projection focused unit / application / PIT / P2 / golden / E2E / architecture suite: 74 passed"
    - "python scripts/ci/check_integrated_candidate.py: 314 passed, 7 skipped"
    - "pytest full suite: 1402 passed, 21 skipped"
    - "pytest candidate / documentation contract suite: 42 passed"
    - "ruff check .; mypy baseline check; secret scan; git diff --check: passed"
```

## P8-WP03 — Research Decision-to-Strategy Target Manual Activation Boundary

**Status:** DONE

依赖：P8-WP02。建立显式人工、可审计的 Research candidate→StrategyTarget 激活边界；禁止自动晋升，所有
Research Card / Decision / Dataset / Feature / Strategy version 身份均须保留并在未知时失败关闭。

验收要求：

- 只能由 application composition root 接收已具 PASS admission evidence、具名人工批准为 `CANDIDATE` 的
  Research Card/Decision；ResearchAgent、P3 或 broker 不获得自动提升能力；
- 必须精确重放 ResearchCard、Validation、ExperimentSpec、ExperimentRun、Dataset、Feature、Backtest 与
  StrategyVersion 身份，并将完整 hash-only provenance 保存进回执；
- 与 Research Decision approval 分离的 `HumanStrategyTargetActivationApproval` 必须具名、带时间和理由，并精确
  绑定 StrategyTarget proposal、card、decision、experiment spec 与 strategy version；
- 强制 `input_as_of ≤ candidate approval ≤ proposal generated_at ≤ manual approval < effective_at < expires_at`，
  不得将静态研究提升为 decision-time-safe；
- `StrategyTarget` v2 必须带 `StrategyTargetActivationRef`，且同一变更更新所有调用者、测试与公开契约；不保留 v1
  兼容路径；
- receipt 固定 non-tradable，不能创建 PortfolioTarget、风险批准、ExecutionPlan、broker order 或任何交易权限。

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >-
    `application.research_strategy_activation.ResearchStrategyTargetActivator` 现在是唯一的
    Research candidate 到 P3 StrategyTarget 组合边界。它只接受具 PASS evidence、具名 CANDIDATE
    approval 的 Research Card/Decision，重放并交叉验证 Card/Validation/ExperimentSpec/Run/
    Dataset/Feature/Backtest/StrategyVersion，再要求独立的具名
    HumanStrategyTargetActivationApproval 精确绑定 target proposal、card、decision、spec 与
    strategy。输出 `StrategyTarget` v2 + hash-only activation receipt，完整保留
    STATIC_REPRODUCIBILITY_ONLY / decision_time_safe=false，且永远 eligible_for_trading=false。
    `StrategyTargetActivationRef` 与 receipt 都不是可信存储或认证边界；P8-WP04 已从原始请求重放该
    接缝并精确比较回执、拒绝手写 hash / synthetic target，才可构造 non-submitting execution preflight
    evidence。最终 durable-submit 强制接入仍留给 P8-WP05。
    `RESEARCH_TO_PORTFOLIO_RISK` 因此仅作为独立、non-tradable 的候选 seam 被验证。
  verification:
    - "P8-WP03 activation / E2E / architecture / documentation focused suite: 31 passed"
    - "python scripts/ci/check_integrated_candidate.py: 336 passed, 7 skipped"
    - "pytest full suite: 1423 passed, 21 skipped"
    - "ruff check .; mypy baseline check; secret scan; git diff --check: passed"
```

## P8-WP04 — Point-in-Time Execution Provenance Preflight

**Status:** DONE

依赖：P8-WP03。将已批准的研究/目标来源、PIT、账户/持仓/价格/保证金/risk 证据绑定到 execution preflight，
消除仅靠手写哈希或 synthetic target 的路径；不改变 live 默认关闭的安全边界。

验收要求：

- application-owned verifier 必须重放每个 P8-WP03 activation request，精确比较 claimed receipt，且要求
  activation 产生的 StrategyTarget source set 与 P3 PortfolioTarget 完全一致；直接构造的 P3 target、
  hash-only activation ref 或 hand-written risk hash 必须 fail closed；
- 结构化 P3 risk evidence、PIT data、完整 account/position、fresh `ctp_sim_market_data` quote、真实 CTP-sim
  contract rule、runtime risk、ExecutionPlan 与 P5 preflight 必须在 verifier 内部重新构造/检查，调用方不得
  注入 ExecutionPlan、PreflightResult 或 BrokerOrder；
- 严格约束 Research/P3/P5 chronology、有效期、来源环境、账户归属、实际可执行期货合约与 quote/rule freshness；
  live、paper、未知环境、kill switch、stale/future/incomplete state 一律 fail closed；
- 只输出 hash-bound、短时有效、`eligible_for_ctp_sim=false`、`eligible_for_trading=false`、
  `eligible_for_live=false` 的 non-submitting evidence receipt；不得调用 adapter/router、持久化或 broker，
  也不得把候选升级为交易授权；
- 更新 P8 candidate gate、E2E、architecture/documentation contracts。最终 CTP-sim durable-submit gate、
  旧 synthetic execution path 的移除、receipt 的一次性消费与 reconciliation 闭环仍留给 P8-WP05，
  因而 `PORTFOLIO_RISK_TO_EXECUTION_SIMULATION` seam 仍显式 BLOCKED。

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >-
    `ExecutionProvenancePreflight` 仅在 application composition root 内重放 P2 candidate 到
    P8-WP03 activation 的输入/回执，精确绑定 P3 target/risk evidence、PIT market evidence、账户归属、
    CTP-sim contract rule 与内部 P5 plan/runtime-risk/preflight。它签发短时、hash-bound、严格 non-tradable
    的 CTP-sim evidence receipt；没有 broker/router/adapter/durable submission 能力，也没有将 static
    research 语义改写为 decision-time-safe 或 live 授权。P8-WP05 仍必须将 receipt 接入最终 submit gate 并
    移除旧 synthetic execution E2E 路径。
  verification:
    - "P8-WP04 focused application / P5 preflight / architecture suite: 17 passed"
    - "P8-WP04 documentation / E2E / contract suite: 48 passed"
    - "python scripts/ci/check_integrated_candidate.py: 350 passed, 7 skipped"
    - "pytest full suite: 1435 passed, 21 skipped"
    - "ruff check .; mypy baseline check; secret scan; git diff --check: passed"
```

## P8-WP05 — Candidate CTP-Sim Integrated E2E

**Status:** DONE

依赖：P8-WP04。用已实现的真实桥接在隔离 PostgreSQL 和 `ctp_sim` 中完成候选验收；保留手动审批、
reconciliation/HALT fail-closed 与非交易授权回执，绝不接入真实 CTP 或真实资金。

验收：

- [x] `CtpSimCandidateExecutor` 只接收原始 P2→P3 provenance request，并从真实 simulator state/quotes
  重放后在内部派生 exact canonical order；调用方不能注入 plan、preflight、receipt 或 BrokerOrder 作为授权；
- [x] 每一 commitment 都与 durable intent 在同一 PostgreSQL 事务一次性消费；新 migration
  `0007_provenance_consumption` 只追加 provenance-consumption facts，且 forward-only；
- [x] 只有 application composition root 可签发不透明 `CtpSimSubmissionAuthority`。结构相同的 no-op object、
  raw adapter、direct durable 和 legacy `live execute` CTP-sim submit 全部在状态写入前失败关闭；
- [x] adapter 在自身状态锁内重新检查 exact consumption、真实 broker state 与 quote 基线；批次只能在前一
  leg 成功提交后推进基线，崩溃、外部 state/quote 变化或未知结果一律拒绝；
- [x] candidate reconciliation 不接受调用方 snapshot；它强制读取 simulator，并在任一未知 order/fill 或缺失
  consumption 时写入粘性 `HALT`；
- [x] 隔离 PostgreSQL E2E 覆盖真实 P2 candidate→人工 activation→P3 risk→provenance→durable→CTP-sim
  fill→position→reconciliation，以及双合约候选 batch；candidate matrix 的
  `PORTFOLIO_RISK_TO_EXECUTION_SIMULATION` seam 为 VERIFIED，但所有 eligibility 仍为 false；
- [x] 文档、CI matrix、architecture/public API boundaries、Ruff、mypy baseline、secret scan 与完整 pytest
  同步通过。

```yaml
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >-
    Replaced the former synthetic execution path with one CTP-sim-only application composition
    boundary. It replays the original candidate/provenance chain, derives exact canonical orders,
    records one-time PostgreSQL consumption atomically with durable intent, then permits the
    simulator only through an opaque authority. The final adapter check runs under the simulator
    state lock against the actual state/quote baseline; each successful batch leg advances that
    baseline, while unexplained state, stale/mutated data, expired receipt, direct durable/raw
    attempts and recovery blockers fail closed. Reconciliation reads the simulator itself and
    writes sticky HALT for any order/fill without consumption. The verified seam remains strictly
    non-tradable candidate evidence and cannot enable real CTP, live trading, HALT recovery, or
    automatic strategy promotion.
  verification:
    - "P8 candidate executor / CTP-sim / dual-order E2E / architecture / documentation suite: 39 passed"
    - "python scripts/ci/check_integrated_candidate.py: 362 passed, 7 skipped"
    - "pytest full suite: 1452 passed, 21 skipped"
    - "ruff check .; mypy baseline check; secret scan; git diff --check: passed"
```

---

# 18. P9 — Hardening / Performance / Security

## 性能优化准入

只有同时满足才允许引入 C++/Rust：

1. profiler 已证明瓶颈；
2. Python/SQL 优化不足；
3. 算法语义稳定；
4. benchmark 可证明收益；
5. FFI 复杂度合理。

禁止“因为以后可能慢”提前下沉。

Benchmark：

```text
benchmarks/
├── data_ingestion/
├── feature_engine/
├── event_merge/
├── backtest/
├── portfolio/
└── execution_sim/
```

## Security hardening

- dependency scan
- secret scan
- least privilege
- production credential isolation
- DB role separation
- immutable audit where appropriate
- manual approval trace

## P9-WP01 — Offline Supply-Chain & Credential Gate

**Status:** DONE

依赖：P8-WP05。先在不依赖外部 advisory/CVE 服务、不读取或创建真实凭据、不改变 broker/数据库权限的前提下，
把本地依赖完整性和秘密扫描收紧为可复现、fail-closed 的工程门禁。它是 offline integrity/policy scan，
不是对联网漏洞情报的替代声明。

实施范围：

- 为 `pyproject.toml` / `uv.lock` 建立 deterministic dependency policy：项目/锁文件不一致、未 allowlist registry、
  direct URL/VCS/path/editable source、缺少 SHA-256 artifact hash 或无效 artifact metadata 必须拒绝；输出仅含
  name/version/source/lock digest 的稳定 inventory，绝不联网或打印机密；
- 将标准库 policy gate、`uv lock --check --offline` 与 secret scan 以该顺序接入 `just check` 和 Windows/Linux CI，
  并确保它们均先于 `uv sync`；
- 收紧 `check_secrets.py`：扫描 tracked test 文本；`secret-scan: allow` 只能用于带理由、canonical path 的 disposable
  test/CI fixture，source/config/deploy/docs 中一律不接受豁免；未知二进制、非 UTF-8、不可读文件与符号链接均失败关闭；
- 为正常和所有拒绝路径提供 unit/contract tests，更新 security audit、脚本说明和主计划。

验收标准：

- [x] policy parser 对 lock/project mismatch、source、hash、artifact metadata、local dependency 异常均 fail closed，
  且成功 inventory/digest 跨运行稳定；
- [x] secret allow marker 的 scope/reason 均严格验证，tests 不再是隐式扫描盲区；
- [x] `just check` 与两个 Tier-1 CI job 均先执行 dependency policy，再执行 `uv lock --check --offline` 和 secret scan；
- [x] 不接入外部服务、不输出 token/credential、不修改真实数据库、账户、CTP 或 live trading 路径；
- [x] focused/full pytest、Ruff、mypy baseline、secret scan 与文档/contract tests 通过。

```yaml
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >-
    Added a standard-library-only, fail-closed review of the project and uv lock before any uv
    resolution, synchronization, or build. It permits exactly one editable root, allowlisted PyPI
    artifacts with complete SHA-256 metadata, and a minimal constrained PEP 517 declaration; all
    locked third-party inventory evidence is stable and secret-free. The repository secret gate now
    resolves the Git root and NUL-delimited tracked paths, scans test text too, accepts only explicit
    reasoned fixture allowances at canonical paths, and fails closed on unsafe paths, unreadable text,
    unknown binary data, and symlinks. PEP 517 bootstrap artifacts not represented in uv.lock are
    deliberately not claimed as hash-bound; P9-WP02 owns that remaining boundary.
  verification:
    - "P9 focused dependency/secret/contract/documentation suite: 69 passed, 1 skipped"
    - "just check: passed (policy -> offline lock -> secret scan -> Ruff -> mypy baseline)"
    - "pytest full suite: 1505 passed, 22 skipped"
    - "red-team adversarial scanner/policy/CI-order audit: approved"
```

## P9-WP02 — Hermetic PEP 517 Build Bootstrap

**Status:** DONE

依赖：P9-WP01。`uv.lock` 已保护所有被其记录的第三方制品，但 PEP 517 build isolation 的
`setuptools` / `wheel` bootstrap 尚未作为 lock artifact 表示。此 WP 将在不放宽 registry、hash、
离线 preflight 或 Windows/Linux Tier-1 支持的前提下，建立可从 fresh virtual environment 重放的
两阶段 bootstrap/install 边界。

实施范围：

- 将精确 build requirements 及其 artifact provenance 纳入可审查、受锁定的输入；不允许临时 index、
  direct URL、VCS/path source、ambient site-package 或未记录 build backend；
- 将 CI/开发同步拆为可验证的 bootstrap 与 project-install 阶段；后续质量命令不得隐式重新同步、
  解析或以默认 build isolation 绕过已验证输入；
- 覆盖 sdist-only transitive dependency、fresh venv、失败关闭、Windows/Linux command contract 与
  project/build metadata drift；同步更新 policy、lock、just、CI、docs 与 tests；
- 不改变业务、数据库 schema、broker、账户、CTP 或 live trading 路径。

验收标准：

- [x] fresh venv 下所有 PEP 517 build inputs 都能追溯到审核过的 lock/provenance；未知或漂移的 bootstrap
  requirement、artifact 或 backend 在下载/构建前失败关闭；
- [x] 两个 Tier-1 CI job 与本地统一入口均以显式阶段执行，post-sync test/lint/typecheck 不会隐式 sync；
- [x] unit/contract tests 覆盖 bootstrap success、sdist dependency、metadata/source/hash drift 与 command ordering；
- [x] focused/full pytest、Ruff、mypy baseline、secret scan 与 docs/contract tests 通过。

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >-
    将 setuptools==80.9.0、wheel==0.45.1 和唯一允许的 source-only jsonpath==0.82.2
    收敛为精确、hash-bound 的 PEP 517 bootstrap 输入。stdlib-only runner 在 fresh non-seed venv
    中以锁定 wheel 阶段和受限 sdist 阶段构建；development 先完成 sibling staging venv 后才原子切换，
    release 则以 root-owned managed Python 和非特权服务身份重放同一边界。所有后续 uv run 均为
    offline/no-sync；policy、制品白名单、CI、just、部署归档、Windows/Linux 文档与 contract tests 同步。
  verification:
    - "P9 focused bootstrap/policy/contract/deployment suite: 128 passed, 3 skipped"
    - "full pytest: 1528 passed, 23 skipped"
    - "just check in a fresh external P9 CI venv: passed (policy -> offline lock -> secret scan -> Ruff -> mypy baseline)"
    - "Windows fresh release bootstrap: staged install/import plus final offline --inexact wheel check passed"
    - "Linux Docker read-only release bootstrap: HERMETIC_PEP517_BOOTSTRAP_OK; bash -n install-release.sh passed"
```

---

# 19. P10 — Mature v1 总验收

## P10-WP01 — Mature v1 Acceptance Evidence Baseline

**Status:** DONE

依赖：P9-WP02。逐项审计 P10 成熟度定义，将已有 offline/paper/ctp_sim 证据、代码和测试精确映射到
验收项；把真正缺少的功能与必须由人工/外部提供的权限、数据、账户或生产条件明确区分。该包只建立
证据基线与后续依赖图，不得把 candidate、simulation 或文档声明误写为真实生产/实盘能力。

实施范围：

- 建立版本受控的 P10 acceptance evidence register，逐项给出实现路径、测试/命令证据和状态；
- 对重复、陈旧或仅部分实现的 checklist 进行证据化对账，保留 fail-closed 与 non-tradable 语义；
- 将缺口拆分为可离线完成的后续 WP 与外部/人工 BLOCKED 项，按依赖排序；
- 不访问真实 broker/账户，不启用 live trading，不采购数据或生产凭据，不修改数据库内容。

逐项状态、实现路径、测试证据及外部阻塞项见
[`P10 Mature v1 Acceptance Evidence Register`](P10_MATURE_V1_ACCEPTANCE_EVIDENCE.md)。

验收标准：

- [x] 每个 P10 checklist 项都具有精确代码/测试证据，或具有明确 owner、外部前提和 fail-closed 影响的 BLOCKED 记录；
- [x] evidence register 能区分已验证的 offline/paper/ctp_sim candidate 能力与未获授权的 real-money production 能力；
- [x] 主计划状态、完成矩阵和后续 WP 依赖与 register 一致，并由 contract tests 保护；
- [x] 审计本身可在本地离线重放，且不放宽任何既有风险或供应链门禁。

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >-
    新增 48 项 P10 evidence register，以 VERIFIED_OFFLINE, VERIFIED_SIMULATION,
    SAFE_BOUNDARY, PARTIAL, INCOMPLETE, BLOCKED_EXTERNAL 和 HOSTED_EVIDENCE_PENDING
    明确区分代码证据、模拟边界与外部条件。同步修复 P5-WP08、Risk Stress 和 Trading Ledger 的
    陈旧主计划状态；将 P10 的重复 Portfolio/Risk 清单收敛为真正缺失的组合级验收工作。
  verification:
    - "isolated Data/Intelligence/Research audit suite: 432 passed, 4 skipped"
    - "P10 evidence/documentation/master-plan contract suite: 17 passed"
    - "git diff --check: passed"
```

## Data

- [ ] 权威合约 / 日历 / 规则链（`BLOCKED_EXTERNAL`；缺失时 `NO NEW RISK`）
- [x] 受控 ArtifactStore 数据版本不可覆盖（`VERIFIED_OFFLINE`）
- [x] 受控数据 PIT correctness（`VERIFIED_OFFLINE`；真实生产 PIT 依赖权威制品）
- [x] 质量 failure 可阻断发布与下游重放（`VERIFIED_OFFLINE`）

## Intelligence

- [x] 6 个核心商品事件链（`VERIFIED_OFFLINE`；fixture-only）
- [x] 受控 Document→Event→Feature Evidence 可追溯（`VERIFIED_OFFLINE`）
- [x] Ontology versioned（`VERIFIED_OFFLINE`）
- [x] Event merge golden corpus（`VERIFIED_OFFLINE`；fixture-only）
- [x] six-commodity fixture-only typed Impact graph 可解释（`VERIFIED_OFFLINE`；非权威映射）
- [x] Event features 的 PIT→Backtest→Validation 可回测闭环（`VERIFIED_OFFLINE`；P10-WP03 fixture-only synthetic replay）

## Research

- [x] Feature Registry（`VERIFIED_OFFLINE`）
- [x] Canonical Feature Families（仅 static PIT / synthetic fixture）
- [x] Experiment Registry（仅 static reproducibility；不可回测、不可准入）
- [x] IS/OOS/Walk-forward、rolling/stress/bootstrap/Monte Carlo/regime validation（报告不可准入）
- [x] Lookahead guard（逐 checkpoint replay；回执仍不可准入/交易）
- [x] Research Decision State（具名人工批准、完整准入证据；不可直接交易）
- [x] Research Card reproducible（冻结 Experiment/Backtest/Validation/Decision 哈希链；不可交易）

## Portfolio / Risk

- [x] Canonical StrategyTarget → PortfolioTarget → ApprovedPortfolioTarget contracts（`VERIFIED_OFFLINE`）
- [x] Research candidate → named manual activation receipt → StrategyTarget v2（完整 P2 identity/PIT 顺序；非交易）
- [x] Allocation core 已由 strict canonical multi-strategy PortfolioTarget v2 composition 受控重放（`VERIFIED_OFFLINE`；P10-WP04）
- [x] Exposure core 已由 canonical composition、账户和 `ctp_sim` broker state 派生并 hash-bind 至组合级 review（`VERIFIED_SIMULATION`；P10-WP05）
- [x] Typed limits core 已由受控组合输入派生；UNKNOWN/WARN/BLOCK 无 approval、receipt、intent 或 broker mutation（`VERIFIED_SIMULATION`；P10-WP05）
- [x] Audited account-scoped risk state 已成为 canonical portfolio approval input；HALT/MANUAL_RECOVERY/UNKNOWN 失败关闭（`VERIFIED_SIMULATION`；P10-WP05）
- [x] Seven deterministic stress scenarios 已进入 portfolio approval gate，并将 WARN/BLOCK 拒绝在 P3/P8 之前（`VERIFIED_SIMULATION`；P10-WP05）
- [x] P3 multi-strategy portfolio-wide risk E2E 与 BLOCK exit 已验证；只在本地 `ctp_sim` 边界内（`VERIFIED_SIMULATION`；P10-WP04/P10-WP05）
- [x] Canonical multi-strategy portfolio composition（`VERIFIED_OFFLINE`；P10-WP04）
- [x] Portfolio-wide exposure / limits / stress / risk-state evidence and approval gate（`VERIFIED_SIMULATION`；P10-WP05；非真实 CTP/实盘）

## Trading

- [x] paper
- [x] ctp_sim
- [x] reconciliation（身份、账户与订单账本对账；差异 HALT，具名人工恢复）
- [x] ledger（订单/成交/撤单、账户与持仓快照、归因、不可变结算事实和具名受控调整）
- [x] failure matrix（`VERIFIED_SIMULATION`；P10-WP07；`docs/planning/P10_TRADING_FAILURE_MATRIX.md` 固定 P5 故障测试索引与 P3 `BLOCK` 无 mutation contract）
- [x] real CTP adapter 默认不能真实下单（`SAFE_BOUNDARY`）
- [x] production enable 需要人工确认（`SAFE_BOUNDARY`，不代表真实 CTP 可用）

## Platform

- [x] Windows/Linux 开发（`VERIFIED_OFFLINE`）
- [x] Windows/Linux deployment control（`VERIFIED_OFFLINE`；Linux target 另行验收）
- [ ] Linux production host acceptance（`BLOCKED_EXTERNAL`）
- [x] health/logs（`VERIFIED_OFFLINE`）
- [ ] backup/restore：local bundle + isolated drill 已验证；production DR/PITR 仍缺失（P10-WP08）
- [x] pre-migration safe rollback / post-migration manual recovery（`SAFE_BOUNDARY`）
- [ ] hosted CI final-commit evidence（`HOSTED_EVIDENCE_PENDING`）

## AI

- [x] Agent 无生产交易越权（`SAFE_BOUNDARY`）
- [x] constrained AI conclusion 有 evidence（`VERIFIED_OFFLINE`）
- [x] Research Agent 跨进程 hash-only durable audit（`VERIFIED_OFFLINE`；P10-WP06；不新增 Agent capability）
- [x] AI 无法绕过风险门禁（`SAFE_BOUNDARY`）

## P10-WP02 — Six-Commodity Intelligence Evidence Corpus

**Status:** DONE

依赖：P10-WP01。以清晰标为 fixture-only 的离线证据构建六个核心商品的 Document→Event→merge→
Mechanism→Impact→Feature-definition golden corpus，并为每条链提供严格的 typed crosswalk。不得把 fixture
identity、ContractRef 或 synthetic market context 误报为授权 market-data、日历、规则或真实可交易合约。

验收标准：

- [x] 六商品各有可复现、证据 span/hash 完整的 golden 链，并覆盖一个不匹配/未知 failure path；
- [x] merge golden 覆盖 multi-source、duplicate、out-of-order、lifecycle 和 retraction；
- [x] impact 解释保留 Event、Mechanism、Entity、Commodity、Market、Instrument、Contract 的 typed lineage，
  fixture-only crosswalk 缺失或不一致时 fail-closed；
- [x] 全部输出继续是 research-only，不能创建 target、approval、ExecutionPlan 或订单。

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >-
    六商品 fixture-only golden corpus 以 Document content/evidence span、Event、ImpactPath 与
    Feature-definition handoff 的稳定 hash 重放 Document→Event→merge→Mechanism→Impact。
    Canonical merge 保留迟到来源的完整 extraction lineage，并拒绝 retraction 后生命周期复活；
    typed Event→Mechanism→Entity→Commodity→Market→Instrument→Contract crosswalk 缺失或漂移
    均 fail-closed。handoff 仅绑定已注册的 intelligence.* Feature 定义，不能构造 P1/PIT
    FeatureValue、授权数据/合约/规则/日历、target、approval、ExecutionPlan 或订单。
  verification:
    - "P10-WP02 focused golden/contract/architecture suite: 13 passed"
    - "tests/intelligence -q: 45 passed"
    - "full pytest -q: 1543 passed, 23 skipped"
    - "ruff check . / mypy baseline / dependency policy / offline lock / secret gate: passed"
    - "git diff --check: passed"
```

## P10-WP03 — Intelligence Feature Research Backtest Evidence

**Status:** DONE

依赖：P10-WP02。将受控 intelligence Feature 输入放入逐决策 PIT 的 research-only
Backtest→Validation→Research Card E2E；不自动晋级、不改变任何交易权限。

验收标准：

- [x] 六个 WP02 `FixtureOnlyFeatureDefinitionHandoff` 与完整 corpus SHA-256 都被 hash-bind 到每个
  fixture-only replay plan；feature/version/handoff drift 均 fail-closed；
- [x] 每个决策 checkpoint 只消费 `available_at <= decision_at` 的六商品 observation；晚到 copper
  source 与 retracted gold 都有显式 failure/suppression 证据；
- [x] synthetic holdout outcome 严格晚于对应决策，确定性生成 research-only alignment result、Validation
  与 Research Card golden hash；它不是授权市场收益或真实合约回测；
- [x] `FIXTURE_ONLY_INTELLIGENCE_REPLAY` evidence、manifest 和 Card 只能保持 `RESEARCH_ONLY`，不能
  含 DatasetVersion、candidate approval、StrategyTarget、PortfolioTarget、ExecutionPlan、订单或 broker
  路径；P3 activator 在 target 构造前明确拒绝该类别。

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >-
    新增纯 fixture-only 的 six-commodity research replay boundary。它直接绑定 P10-WP02 的
    Event→Feature-definition handoff 和 corpus hash，以 collected/available 时间重放每个决策点，
    将 gold retraction 表示为显式缺失而非复用旧分数，并在事后 synthetic outcome 可用后才计算
    alignment statistic。该结果进入 Validation 和 ResearchCard 的专用 manifest，但不伪造
    FeatureValue、P1/PIT DatasetVersion、市场收益、合约、日历、规则或交易输入。新的 validation
    input kind 强制此类 Card 仅为 RESEARCH_ONLY，P3 activation 明确拒绝它。
  verification:
    - "P10-WP03 focused E2E/failure/architecture suite: 10 passed"
    - "tests/research -q: 234 passed"
    - "tests/intelligence plus fixture architecture: 49 passed"
    - "full pytest -q: 1553 passed, 23 skipped"
    - "ruff / mypy baseline: passed"
```

## P10-WP04 — Canonical Multi-Strategy Portfolio Composition

**Status:** DONE

依赖：P10-WP01。以多个已激活的 StrategyTarget 严格、确定性地派生 PortfolioTarget；拒绝 silent
truncation、重复来源、身份/PIT/有效期不一致，且输出仍不直接下单。

验收要求：

- 仅 `PortfolioCompositionRequest` 可进入纯 P3 `CanonicalPortfolioComposer`；请求必须含至少两个 exact
  `StrategyAllocationInput`，组合器在内部调用 `allocate()`，不接受 caller-supplied aggregate、positions、
  `AllocationResult` 或 PortfolioTarget；
- 每个 StrategyTarget/activation/allocation input 都重放；target hash、target id、source strategy、activation
  id/hash 任何重复均失败关闭。组合窗口必须显式且严格，所有 source 已在组合生成时生效，并完整覆盖输出有效期；
- allocation source 集与输入 source 集必须精确相等。按 source target hash 与 instrument id 规范排序，保留
  unallocated cash、zero allocation 和净额为零 instrument，绝不重新归一化、静默截断或丢弃 cancellation；
- `PortfolioTarget v2` 的 `target_hash` 绑定 replayable `composition_hash`；完整
  `PortfolioCompositionEvidence` 进一步绑定 policy、source snapshots、allocation、pre-net contribution 和
  output positions，因而不同 allocation 即使形成相同 net positions 也不会共享 target identity；
- 新路径不得导入 legacy Polars/DataFrame `portfolio.multi_strategy`、application、Research、P5、DB、broker、
  approval 或 order。输出只是不审批、不可执行、不可下单的 P3 evidence；P10-WP05 才能消费它进行风险审批。

完成记录：

```yaml
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >-
    新增 structured CanonicalPortfolioComposer、PortfolioCompositionRequest、StrategyTargetContribution 与
    PortfolioCompositionEvidence；升级 PortfolioTarget 到 v2 并令 target identity 显式绑定
    composition_hash。组合器从 exact activated StrategyTarget + AllocationPolicy/Input 内部重放 allocation，
    保留 source contribution / 未配置现金 / 显式 net-zero position，拒绝任何 replay、identity、window 或
    source-set drift。旧 DataFrame multi_strategy 路径保持隔离，未被 canonical contract 引用。
  verification:
    - "P10-WP04 unit/failure/e2e/golden/architecture suite: 16 passed"
    - "tests/portfolio_risk -q: 36 passed"
    - "P8 provenance/execution regression subset: 14 passed"
    - "architecture dependency/layering suite: 9 passed"
    - "full pytest -q: 1564 passed, 23 skipped"
    - "ruff / mypy baseline: passed"
```

## P10-WP05 — Portfolio-Wide Risk Evidence & Approval Gate

**Status:** DONE

依赖：P10-WP04。由受控组合/市场/账户输入派生并 hash-bind exposure、limits、七场景 stress 和
account-scoped risk state；任一 UNKNOWN/WARN/BLOCK/HALT 均不得产生 approval、P8 receipt、durable intent
或 broker mutation。

本包状态仅为 `VERIFIED_SIMULATION`：证据来自本地 PostgreSQL 与 `ctp_sim`，不代表真实 CTP、实盘账户、
真实人工身份认证或 live trading 就绪。

验收标准：

- [x] `PortfolioRiskApprovalGate` 仅从 exact `PortfolioCompositionEvidence`、profile-owned policy、账户与
  `ctp_sim` broker/reconciliation 输入重放组合级 exposure、limits、七场景 stress 和 account-scoped risk
  state；任何 caller-supplied aggregate、hash 或时间漂移都失败关闭；
- [x] 任一 UNKNOWN/WARN/BLOCK/HALT、过期输入、scope/hash/attestation drift 或不完整 scenario 都不能构造
  `ApprovedPortfolioTarget`，也不能到达 P8 receipt、durable intent 或 broker mutation；
- [x] P8 candidate 在 prepare、逐 leg prevalidate 与 final reconciliation fence 内均重读 exact durable manual
  approval binding；该 record 仅保存 verifier receipt hash，且 `0008_portfolio_risk_approval` 为前向、非破坏性
  PostgreSQL migration；
- [x] 本地 `ctp_sim` regression 覆盖重复/乱序 state、HALT、过期/篡改 approval、重放、并发 fence 与 no-mutation
  failure path；没有真实 CTP、真实账户、真实身份认证或 live order。

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >-
    canonical P3 risk review 现在从 exact multi-strategy composition、profile policy、账户、CTP-sim
    broker snapshot 与 persisted reconciliation safety state 派生 exposure、limits、七场景 stress 和
    account-scoped state，并将全部语义 hash-bind。P8 candidate 在 prepare/prevalidate/final fence 都要求
    exact hash-bound、过期即拒绝的 durable manual approval record；其 PostgreSQL record 是 append-only、
    idempotent 且只保存 verifier receipt hash，migration 为 0008_portfolio_risk_approval。默认 production
    composition 没有可用 issuer；成功 issuance 只存在于 private test composition。认证人工批准服务、
    dedicated writer role 与 CTP-sim candidate 的 SELECT-only reader role 仍为 BLOCKED_EXTERNAL；本包不连接
    真实 CTP、不认证真实身份、不创建真实订单或开启 live trading。
  verification:
    - "full -I -m pytest -q: 1682 passed, 23 skipped"
    - "ruff check .: passed"
    - "mypy baseline: 33 diagnostics; ratchet: passed"
    - "Alembic migration chain/head: 0008_portfolio_risk_approval"
    - "git diff --check: passed"
```

## P10-WP06 — Durable Agent Evidence Audit

**Status:** DONE

依赖：P10-WP01。为受限 Agent 结果新增 append-only、hash-only、跨进程 durable audit；不存 raw prompt
或 chain-of-thought，不新增 Agent capability，不触及交易权限。

验收标准：

- [x] 独立 application composition wrapper 在任何 tool 调用前提交 `ADMITTED` reservation；同一跨进程
  `run_id` 无论完成、失败或未决均拒绝自动 replay，且 Agent 本身不接收数据库能力；
- [x] 正常返回仅原子追加 `COMPLETED` 与完整有序 hash trace；request/result/trace/record hash、tool allowlist、
  lifecycle 和 `eligible_for_trading=False` 在 repository 和 PostgreSQL 中双重验证；
- [x] generic tool/Agent 异常或终态写入不确定时保留 unresolved reservation，不伪造 `FAILED`；仅已确定的
  result-binding 拒绝可追加 allowlisted stable failure code，且不持久化异常文本；
- [x] `0009_agent_run_audit` 和前向 `0010_agent_run_audit_hardening` 建立 hash-only 表、不可变
  UPDATE/DELETE/TRUNCATE 拒绝 trigger 与非破坏性约束；raw prompt/query/Document/result/rationale/payload/CoT
  不可写入；
- [x] 仅在本地隔离 PostgreSQL 测试路径验证；不提供 CLI/scheduler/broker/risk/live surface，不连接真实 CTP
  或创建真实订单。

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >-
    ResearchAgent 仍只依赖 TypedResearchToolApi。独立 durable wrapper 使用一次已提交的
    ADMITTED reservation 包围既有 research-only Agent，并在成功时原子写入 COMPLETED 事实和
    有序 hash trace。通用 tool/Agent 异常或 terminal write 不确定性保留 unresolved reservation，
    同一 run_id 不会自动重放；只有确定的返回绑定漂移写入 allowlisted
    RESEARCH_AGENT_RESULT_INVALID。前向 migration 0009/0010 强制 hash-only shape、trace tool
    allowlist、failure-code allowlist 以及 UPDATE/DELETE/TRUNCATE 的 append-only 拒绝。没有 raw
    prompt/query/Document/result/rationale/exception/payload/CoT、没有新 Agent capability，也没有
    broker/risk/live 路径。
  verification:
    - "WP06 PostgreSQL migration/audit focused suite: 26 passed"
    - "WP06 extended unit/integration/architecture/contract/docs suite: 79 passed"
    - "full -I -m pytest -q: 1709 passed, 23 skipped"
    - "ruff check .: passed"
    - "mypy baseline: 33 recorded diagnostics; ratchet passed"
    - "Alembic single head: 0010_agent_run_audit_hardening"
    - "git diff --check: passed"
```

## P10-WP07 — Trading Acceptance Evidence Closure

**Status:** DONE

依赖：P10-WP05。把既有 failure tests 映射为完整 matrix，补 portfolio-risk BLOCK 的无 plan、无 intent、
无 broker mutation contract；保持真实 CTP 永远不可连接。

验收标准：

- [x] `docs/planning/P10_TRADING_FAILURE_MATRIX.md` 将 P5-WP10 的 disconnect、restart、duplicate/
  out-of-order callback、unknown order、stale facts、DB unavailable、timeout/network partition、identity
  mismatch、insufficient margin、price limit、cancel reject 与 trading-day rollover 映射到精确本地测试；
- [x] 精确的 `PortfolioRiskReviewStatus.BLOCK` 在 P8 candidate 路径上直接证明无 plan persistence、无
  order assembly、无 `broker.submit_order`、无 intent/order/consumption 且 simulator state 不变；
- [x] application composition、CTP adapter 和 candidate architecture 均保持真实 CTP 连接前拒绝边界；该
  行只标记 `SAFE_BOUNDARY`，绝不伪称为真实 CTP integration；
- [x] matrix、P10 evidence register 和 contract tests 已同步；全部验证仅使用本地 `ctp_sim` 与隔离
  PostgreSQL，不执行真实账户或实盘操作。

完成记录：

```yaml
status: DONE
completion:
  completed_at: 2026-08-23
  commit: null
  notes: >-
    将 P5-WP10 分散的 fail-closed tests 固定为 T05-01 至 T05-13 的可审计 matrix。真实 P3 BLOCK
    通过 plan persistence、order assembly 与 broker.submit_order 三个 sentinel，以及数据库和
    simulator-state assertions，证明在任何 candidate execution side effect 前中断。真实 CTP 仍只
    有连接前拒绝的 SAFE_BOUNDARY；没有新增 broker capability、真实连接或真实订单。
  verification:
    - "T05 focused simulation/contract suite: 101 passed"
    - "P10 documentation/plan contracts: 20 passed"
    - "full -I -m pytest -q: 1710 passed, 23 skipped"
    - "ruff check .: passed"
    - "mypy baseline: 33 recorded diagnostics; ratchet passed"
    - "Alembic single head: 0010_agent_run_audit_hardening"
    - "git diff --check: passed"
```

## P10-WP08 — Platform Production / DR Acceptance

**Status:** BLOCKED

依赖：P10-WP01。需要经授权 Linux production host、root/signer/known_hosts、hosted CI evidence、
生产 DR policy 与受控恢复演练。未提供这些外部前提时维持 `NO LIVE ACTION`，不将本地 Docker 或
loopback drill 升级为生产验收。

## P10-WP09 — Authoritative Data & Source Onboarding

**Status:** BLOCKED

依赖：P10-WP01。需要数据 license、权威合约/日历/规则制品和 source authorization；在外部前提齐备前
保持 D01 与真实 production PIT 未验收，且系统不得增加风险。

---

# 19.1 用户请求的跨阶段维护

## DOC-WP01 — Documentation Consolidation & Architecture Specification

**Status:** DONE

**Origin:** 用户明确要求清理项目文档，并整理为一份规范的架构设计文档。本工作包独立于 P10
验收计数：它只治理仓库文档、入口和文档契约，不会把 P10-WP08/P10-WP09 的外部前提误记为已完成。

**Goal:** 将重叠的编号说明文档收敛为清晰、可维护的规范文档集；以单一 `docs/ARCHITECTURE.md`
描述已实现的系统边界、依赖规则、数据/研究/风险/执行流和明确的非升级边界。

**Scope:**

- 收敛根 README 与 `docs/` 导航，删除已被合并的旧编号文档；
- 保留主实施计划、P10 验收证据和故障矩阵等控制面，不将它们改写成架构事实来源；
- 在同一变更中更新配置、脚本、测试和规划文档中的本地链接；
- 用文档契约测试验证所有本地 Markdown 链接与关键安全陈述。

**Non-goals:**

- 不修改生产代码、数据、数据库 schema、迁移、真实交易能力或外部集成；
- 不创建兼容跳转页。项目尚未发布，仓库内调用方和测试必须直接迁移到新文档路径。

**Acceptance:**

- `ARCHITECTURE.md` 是唯一的架构设计权威，明确领域边界、composition root、语义、PIT、证据与交易安全链；
- 开发、运行和治理内容各有唯一权威，不重复维护路线图；
- 无失效本地 Markdown 链接，旧文档路径没有残留引用；
- 文档契约、静态质量门禁和主计划状态同步通过。

**Completion:**

```yaml
completed_at: 2026-08-23
commit: null
notes: >-
  收敛旧编号文档与重复 README 内容为 ARCHITECTURE、DEVELOPMENT、OPERATIONS、GOVERNANCE 四份规范文档，
  保留 planning 控制面并同步所有仓库链接与文档契约。删除旧路径而不保留 compatibility redirect；
  全量 pytest 1710 passed, 23 skipped，Ruff 与 mypy baseline 通过。
```

---

# 20. Codex Work Package 标准模板

```yaml
id: P?-WP??
title: ...
status: TODO

goal:
  ...

dependencies:
  - ...

scope:
  allowed:
    - ...
  forbidden:
    - ...

inputs:
  - ...

outputs:
  - ...

requirements:
  - ...

database_changes:
  required: false

config_changes:
  required: false

tests:
  unit:
    - ...
  integration:
    - ...
  contract:
    - ...
  e2e:
    - ...

acceptance:
  - ...

verification_commands:
  - just env-bootstrap
  - uv run --offline --no-sync pytest ...
  - uv run --offline --no-sync ruff check .
  - uv run --offline --no-sync python scripts/ci/check_mypy_baseline.py check

documentation:
  - ...

completion:
  completed_at: null
  commit: null
  notes: null
```

---

# 21. Codex 每轮执行报告格式

开始：

```text
ACTIVE WP: ...
WHY READY:
- ...

PLAN:
1.
2.
3.

SAFETY:
- live trading unaffected
- no credential action
```

完成：

```text
WP RESULT: DONE / VERIFY / BLOCKED

CHANGED:
- ...

TESTS:
- command → PASS/FAIL

MIGRATION:
- ...

RISKS:
- ...

MASTER PLAN UPDATE:
- checkbox/status updated
- next_task updated
```

---

# 22. 外部阻塞项协议

合法 BLOCKED：

- 商业数据采购
- 数据 license
- CTP 真实账号
- 期货公司模拟前置
- production credentials
- 真实资金确认
- 外部付费服务
- 人工审批

遇到阻塞时 Codex 必须：

1. 完成可离线完成的接口；
2. 完成 fake/sim adapter；
3. 完成测试；
4. 完成 preflight/validation 工具；
5. 标记 BLOCKED；
6. 转去其他依赖已满足的 READY WP。

不得因此停止整个项目。

---

# 23. 禁止事项

Codex 永远不得：

- 自动开启 live trading；
- 自动填写真实 CTP 凭据；
- 为通过测试删除 preflight/risk/kill-switch；
- 用 SQLite 替代 PostgreSQL；
- 伪造授权数据；
- 用未来数据补历史数据；
- 将 LLM 判断写成确定事实；
- 创建重复领域模型；
- 未测试即标 DONE；
- 不更新本文件却宣称完成；
- 一次重构整个仓库且没有可回滚中间验收点。

---

# 24. 开发优先级规则

```text
Safety
> Correctness
> Data integrity
> Reproducibility
> Architecture
> Research capability
> Production reliability
> Performance
> UI
```

同等级时优先：

> 能解除最多下游依赖的 READY WP。

---

# 25. 推荐执行顺序

```text
P0-WP01
→ P0-WP02
→ P0-WP03
→ P0-WP04

→ P1-WP01
→ P1-WP02
→ P1-WP03
→ P1-WP04
→ P1-WP05

随后允许在依赖满足后并行：
P1-WP06~08
P2-WP01~09
P3-WP01~07
P4-WP01~15
P5-WP01~11
P6-WP01~09
P7-WP01~05

最后：
P8 → P9 → P10
```

不是按月份推进，而是按 dependency graph 推进。

---

# 26. 功能完成矩阵

| 功能域 | 子系统 | 状态 |
|---|---|---|
| Data | Artifact model | DONE |
| Data | Versioning | DONE |
| Data | Contract master | DONE |
| Data | Calendar | DONE |
| Data | Quality | DONE |
| Data | PIT semantics | DONE（静态 as-of snapshot；逐决策 replay 留待研究 look-ahead guard） |
| Intelligence | Ontology | DONE（v1 版本锁定资源；未知类型 fail-closed） |
| Intelligence | Document ingestion | DONE（离线 SourceAdapter 与可审计规范化 Document） |
| Intelligence | Entity resolution | DONE（canonical ID/alias；冲突与未知值 fail-closed） |
| Intelligence | Event extraction | DONE（Document 绑定、evidence span、schema/ontology validation） |
| Intelligence | Event merge | DONE（语义归并、生命周期和 out-of-order 保护） |
| Intelligence | Confidence | DONE（独立证据因子乘积；LLM confidence 不能单独确立事件） |
| Intelligence | Mechanism engine | DONE（11 类经济机制；本体/证据/事件语义均验证） |
| Intelligence | Impact graph | DONE（六类商品的 typed Event→Contract 路径；P10 六商品 crosswalk 仅 fixture-only/research-only，映射不一致 fail-closed，绝非授权市场数据或真实合约映射） |
| Intelligence | Context | DONE（完整市场状态 + DatasetVersion/available_at PIT 门禁） |
| Intelligence | Event study | DONE（六窗口、八指标与 available_at PIT 门禁） |
| Intelligence | Analogue | DONE（八维结构化距离；embedding 仅 15% 辅助） |
| Intelligence | Feature registry | DONE（九个 `intelligence.*` 版本化研究 Feature；P10 仅有 hash-bound fixture-only Feature-definition handoff，不构造 `FeatureValue`、PIT 或授权 projection） |
| Intelligence | Offline golden E2E | DONE（既有铜链为 Document→Event→Mechanism→Impact→Context→Feature；P10 六商品链为 fixture-only Document→Event→merge→Mechanism→Impact→Feature-definition，后者不构造 Context 或 `FeatureValue`，均交易隔离） |
| Research | Feature registry | DONE（单一静态 PIT 输入；逐决策 replay 留待 Lookahead Guard） |
| Research | Experiment registry | DONE（static reproducibility only；不构成回测/准入/交易证据） |
| Research | Backtest unification | DONE（RunManifest v4、三引擎语义保留与防篡改报告归档） |
| Research | Validation | DONE（确定性 IS/OOS、walk-forward、rolling、参数邻域与压力/统计验证；报告不可准入） |
| Research | Lookahead guard | DONE（市场、Feature、Event 与 Contract RuleBook 均按 checkpoint 从 immutable DatasetVersion 重放；回执仍不可准入/交易） |
| Research | Research decision / card / E2E | DONE（具名人工批准、完整证据链与可复现离线 Research Card；不可交易） |
| Portfolio | Canonical targets | DONE（不可变时窗、策略版本血缘、必填 StrategyTargetActivationRef 与风险证据批准；不直接下单） |
| Integration | Research candidate → StrategyTarget manual activation | DONE（具名 CANDIDATE/activation、完整 P2 hash-only replay、静态 PIT 语义；P8-WP04 已重放该来源，仍非交易） |
| Integration | Execution provenance / candidate CTP-sim gate | DONE（P2→activation→P3/PIT/account/quote/rule→内部 P5 preflight 的 CTP-sim-only hash-bound receipt，后接 opaque final authority、一次性 PostgreSQL consumption、durable intent、锁内 state/quote 重验与 simulator-only reconciliation；所有 eligibility 仍为 false，绝不授予真实交易） |
| Portfolio | Allocation | DONE（固定预算、波动缩放、风险预算、上限与现金留存；不扩大受限风险） |
| Portfolio | Exposure | DONE（分类完整的 gross/net、方向、保证金与集中度快照；P10-WP05 将 exact composition/account/`ctp_sim` 输入派生并 hash-bind 至组合级 review；未知分类失败关闭，`VERIFIED_SIMULATION`） |
| Risk | Limits | DONE（九类限额均产出 PASS/WARN/BLOCK 与证据；P10-WP05 的组合级 gate 对 UNKNOWN/WARN/BLOCK 失败关闭，`VERIFIED_SIMULATION`） |
| Risk | State machine | DONE（HALT 不自动恢复；具名人工 MANUAL_RECOVERY 与不可变审计链；P10-WP05 将 account-scoped reconciliation state 绑定至审批，`VERIFIED_SIMULATION`） |
| Risk | Stress scenarios | DONE（gap、涨跌停、波动、流动性、相关商品、保证金、FX；未知输入 BLOCK；P10-WP05 已绑定组合级 approval gate，`VERIFIED_SIMULATION`） |
| Portfolio/Risk | E2E | DONE（任一 BLOCK 无批准/执行出口；P10-WP05 的本地 `ctp_sim` 组合级 review/manual-record/P8 gate 无 plan、intent 或 broker mutation，批准目标不等于 broker order） |
| Risk | Stress | DONE（七类确定性场景与组合级 approval binding 已由 P10-WP05 验证于本地 `ctp_sim`；非真实 CTP/实盘） |
| Trading | Broker contract | DONE（typed identity/capabilities/status/error/MarketGateway；未知或断连不增风险） |
| Trading | Order state | DONE（typed canonical lifecycle、broker/client identity、幂等回调与终态回退 fail-closed） |
| Trading | Positions | DONE（多空今昨、冻结/可平、保证金与已实现/未实现 PnL；超额平仓 fail-closed） |
| Trading | Execution planning | DONE（ApprovedPortfolioTarget + account/market snapshot + contract rules 绑定；ExecutionPlan 不可直接下单） |
| Trading | Pretrade | DONE（Preflight + immutable ExecutionPlan item binding；最终 broker 边界逐项一次性 fail-closed） |
| Trading | CTP sim | DONE（异步 partial/reject/cancel、重连/恢复、幂等终态与 SHFE/INE 今昨仓均受控模拟） |
| Trading | CTP adapter skeleton | DONE（仅精确 FakeCtpFront、实际合约映射和账户匹配快照；应用层仍拒绝真实 ctp） |
| Trading | Reconciliation | DONE（订单/成交身份与账户均须可归属；不可解释差异追加粘性 HALT，具名人工恢复后才可重新提交） |
| Trading | Ledger | DONE（订单/成交/撤单、账户/持仓、结算事实与具名 controlled adjustment 均 append-only） |
| Platform | Config | DONE（统一环境/画像/数据源/研究准入/ontology 交叉校验，失败关闭） |
| Platform | Messaging | DONE（typed 进程内 FIFO、显式重试/重复消息/无订阅者失败关闭） |
| Platform | Scheduling | DONE（typed registry、统一既有作业注册、LIVE 生命周期 gate） |
| Platform | Observability | DONE（结构化日志、fail-closed operational snapshot、Prometheus metrics 与健康输出统一） |
| Platform | Security | DONE（统一密钥扫描、日志/导出脱敏、稳定审计 JSON 与服务/部署身份最小权限边界） |
| Platform | Deployment | DONE（唯一 Python 控制面、强制质量门禁、受限 SSH、互斥发布、健康回退） |
| Platform | Linux production layout | DONE（固定 FHS、root 控制祖先链、release 环境/systemd 快照与 mount-aware 特权遍历） |
| Platform | Backup/restore | DONE（六类受限 SHA-256 逻辑包、无覆盖发布、双重静默检查与隔离 PostgreSQL 恢复演练） |
| Platform | Release | DONE（固定 root gate、canonical signed manifest、环境独立签名、受限 SSH stdin 提交、不可变事务审计与 migration 后人工恢复边界） |
| Platform | Hermetic PEP 517 bootstrap | DONE（精确 builder/source provenance、fresh staging venv、offline/no-sync 后续门禁、Windows/Linux release contract） |
| AI | Typed research tool API | DONE（九项封闭 typed allowlist；新增只读质量 inspection，显式 injected research ports、PIT/证据链 fail-closed、无交易权限） |
| AI | Research agent | DONE（单一 typed-tool capability、证据绑定七步链、静态实验语义、无敏感 hash trace；独立 PostgreSQL append-only/hash-only audit 保留跨进程 `run_id` reservation、终态和有序 trace，始终 `RESEARCH_ONLY`、不可交易） |
| AI | Intelligence agent | DONE（唯一 search_events typed capability、授权 source/document/span citation、精确 Event identity、严格历史 analogue、evidence-bound Event→mechanism→commodity impact、PIT fail-closed、无交易权限） |
| AI | Data quality agent | DONE（只读六类 hash-bound diagnosis；不修复、不重发、不交易） |
| AI | Ops agent | DONE（独立单项原子快照读取、授权/PIT/scope/evidence/redaction fail-closed；只读 HALT/kill-switch，永不恢复、部署或交易） |

---

# 27. 最终完成定义

项目不是在“目录都创建出来”时完成。

完成条件是：

```text
Data is trustworthy
+
Research is reproducible
+
Intelligence is evidence-backed
+
Portfolio risk is explicit
+
Execution fails closed
+
Deployment is repeatable
+
Operations are observable
+
AI cannot bypass gates
```

Northstar Quant 最终应该能够由一个高级技术负责人配合 Codex 长期维护：任何新增数据源、Feature、策略、情报类型、组合规则或交易路径，都能够沿统一架构、统一测试与统一验收流程进入系统，而不破坏已有研究可信性和交易安全性。

---

# 28. 当前下一任务

```yaml
next_task:
  id: P10-WP08
  title: Platform Production / DR Acceptance
  status: BLOCKED
```

P10-WP08 与 P10-WP09 均由外部前提阻塞；`DOC-WP01` 已完成。在获得授权的外部条件前维持 `NO LIVE ACTION`。

---

# 29. 变更日志

| 日期 | 变更 | 状态 |
|---|---|---|
| 2026-08-19 | 创建 Codex Master Implementation Plan 初版 | ACTIVE |
| 2026-08-19 | P0-WP01：固定主计划路径、README/AGENTS 引用与 contract 保护 | DONE |
| 2026-08-19 | P0-WP02：六领域运行时依赖契约、application 组合层与领域化测试树 | DONE |
| 2026-08-19 | P0-WP03：scripts / infra / just、跨平台开发工具 bootstrap 与 Linux-only 目标端 | DONE |
| 2026-08-19 | P0-WP04：开发工具、配置与本地服务初始化幂等/失败关闭契约；数据库自动化前向保全 | DONE |
| 2026-08-19 | P1-WP01：不可变数据领域契约、PIT/质量/血缘门禁与可验证 DatasetVersion 快照 | DONE |
| 2026-08-19 | P1-WP02：追加式不可变制品库、snapshot 级 lineage、归一化唯一发布绑定与 artifacts 清理保护 | DONE |
| 2026-08-19 | P1-WP03：Contract Master、PIT 规则解析与连续研究序列执行门禁 | DONE |
| 2026-08-19 | P1-WP04：不可变交易日历、夜盘/会话/授权 PIT 门禁与最终订单 Contract Rule 会话交集 | DONE |
| 2026-08-19 | P1-WP05：canonical payload 绑定的预发布数据质量引擎、PIT/reference/revision/gap 门禁 | DONE |
| 2026-08-19 | P1-WP06：受控数据源适配器协议、授权重验、不可变质量/授权证据与离线发布链 | DONE |
| 2026-08-19 | P1-WP07：行级 PIT revision、受控 DatasetVersion snapshot 与静态回测准入阻断 | DONE |
| 2026-08-19 | P1-WP08：真实质量引擎驱动的离线 Source→Raw→Normalize→DatasetVersion→PIT→Research E2E | DONE |
| 2026-08-20 | P2-WP01：受控 Feature Registry、完整 PIT 血缘重放与确定性回填 | DONE |
| 2026-08-20 | P2-WP02：九个 canonical feature family、actual-contract scope/ID 门禁与 static PIT 回填 | DONE |
| 2026-08-20 | P2-WP03：静态可复现实验账本、完整 Feature/PIT 输入冻结与 hash-only 运行记录 | DONE |
| 2026-08-20 | P2-WP04：三引擎真实性保留的统一回测审计合同、RunManifest v4 与防篡改报告归档 | DONE |
| 2026-08-21 | P2-WP05（进行中）：连续日线 target trace 已接入 LookaheadGuard 可重算 receipt；回执精确冻结每个 checkpoint 的市场快照和 target hash，但仍不可准入、不可交易，完整 Feature/Event/Contract Rule producer 与 BacktestRequest binding 尚未完成 | IN_PROGRESS |
| 2026-08-21 | P2-WP05（进行中）：receipt 现在强制显式声明 Feature/Event/Contract/Fee-Margin 输入是已提供或冻结身份证明的未使用；隐式空证据和声明/证据冲突均 fail-closed | IN_PROGRESS |
| 2026-08-21 | P2-WP05（进行中）：receipt 增加稳定 hash，精确绑定受控 target trace 与 LookaheadCertificate，作为未来 BacktestRequest 的 hash-only 引用锚点；静态 PIT 回测合同不被误改写为逐决策 replay | IN_PROGRESS |
| 2026-08-21 | P2-WP05（进行中）：新增 `decision_replay_receipt` BacktestRequest 数据合同；完整重算 receipt 后冻结逐 checkpoint PIT 证据与 target/策略身份，当前仅构造、绝不执行 weight-return 请求 | IN_PROGRESS |
| 2026-08-21 | P2-WP05（进行中）：将 `decision_replay_receipt` 设为 construction-only；统一结果合同拒绝将任何结果绑定到该请求，防止调用方绕过受控逐决策执行编排 | IN_PROGRESS |
| 2026-08-21 | P2-WP05（进行中）：BacktestRequest code contract 新增 `strategy_identity_hash`，逐决策请求必须精确绑定 receipt 已冻结的策略代码闭包与有效参数身份 | IN_PROGRESS |
| 2026-08-21 | P2-WP05：逐 checkpoint Lookahead Guard 完成。Feature 由受控 Registry 重放并双执行、Event/Contract RuleBook 由 immutable DatasetVersion 重放；签发时重新验证来源，手工同形证据失败关闭。连续 target trace 与 construction-only BacktestRequest 仍不可准入、不可交易。 | DONE |
| 2026-08-21 | P2-WP06：确定性 Validation Framework 完成，覆盖 IS/Validation/OOS、walk-forward、rolling、参数邻域、成本/滑点/延迟压力、bootstrap、Monte Carlo 与 regime 分组；报告冻结上游证据 hash 且永远不可准入。 | DONE |
| 2026-08-21 | P2-WP07：不可变、单向 Research Decision State 完成；候选及更高研究状态均要求完整验证/准入证据、精确目标状态的具名人工批准，且不授予直接交易资格。 | DONE |
| 2026-08-21 | P2-WP08：可复现 Research Card 完成；冻结 Dataset/Feature/Strategy/Experiment/Backtest/Validation/Decision 证据链，完整呈现 IS/OOS、成本/滑点、换手、回撤、产品贡献、regime/stress 与限制，且不可交易。 | DONE |
| 2026-08-21 | P2-WP09：离线 Dataset→Feature→Experiment→Backtest→Validation→Research Card E2E 完成；独立复跑卡片 hash/JSON 一致，静态 Experiment 与 Research Card 均不可交易。P2 100% 完成。 | DONE |
| 2026-08-21 | P3-WP01：不可变 StrategyTarget、PortfolioTarget 与 ApprovedPortfolioTarget 合同完成；目标时间窗、策略版本血缘和风险批准证据均 fail-closed，且不直接产生 BrokerOrder。 | DONE |
| 2026-08-21 | P3-WP02：确定性首阶段 Allocation Engine 完成；固定预算、波动缩放、风险预算、策略上限和现金留存显式建模，受限容量不会被放大为新风险。 | DONE |
| 2026-08-21 | P3-WP03：分类完整 Exposure Engine 完成；统一计算 gross/net、商品/板块/交易所/方向/相关簇、保证金和集中度，未知分类与重复合约均 fail-closed。 | DONE |
| 2026-08-21 | P3-WP04：typed Risk Limits 完成；合约、商品、板块、交易所、策略、账户、杠杆和保证金利用率限额均有确定性 PASS/WARN/BLOCK 证据，未知值 BLOCK。 | DONE |
| 2026-08-21 | P3-WP05：不可变 Risk State Machine 完成；HALT 不能自动恢复，必须经具名 MANUAL_RECOVERY，状态与前驱证据 hash 可审计。 | DONE |
| 2026-08-21 | P3-WP06：七类确定性 Stress & Scenario 完成；gap、涨跌停、波动、流动性、相关商品、保证金和 FX 压力均覆盖，未知名义/保证金 fail-closed。 | DONE |
| 2026-08-21 | P3-WP07：Portfolio/Risk E2E 完成；全绿限额才产生 ApprovedPortfolioTarget，任一 BLOCK 无批准/执行出口，批准目标仍不等于 BrokerOrder。P3 100% 完成。 | DONE |
| 2026-08-21 | P4-WP01：Intelligence Domain 完成；Source/Document/Entity/Event/Mechanism/Impact/Evidence 不可变合同落地，Document 与 Event 严格分离，事件须带证据与 ontology version。 | DONE |
| 2026-08-21 | P4-WP02：Ontology v1 完成；五份版本锁定本体资源与 fail-closed 加载器落地，未知事件类型（含交易动作）被拒绝。 | DONE |
| 2026-08-21 | P4-WP03：离线 Document Ingestion 完成；SourceAdapter poll/stream 与规范化 Document 合同落地，时间/来源/许可/content hash 缺失或集合类型不合规均拒绝。 | DONE |
| 2026-08-22 | P4-WP04：确定性 Document Dedup 完成；canonical URL、内容 hash、标题、semantic key 与转载关系归为 DocumentCluster，不能直接产生 Event/Feature。 | DONE |
| 2026-08-22 | P4-WP05：Entity Resolution 完成；12 类 canonical entity/alias 解析落地，未知 alias、重复 canonical ID 或 alias 冲突均 fail-closed。 | DONE |
| 2026-08-22 | P4-WP06：Event Extraction 完成；ExtractedEvent 候选须绑定 Document content hash、evidence span、ontology version/type 与显式置信度，LLM 输出不构成事实。 | DONE |
| 2026-08-22 | P4-WP07：Canonical Event Merge 完成；语义键归并与生命周期落地，out-of-order 到达不能回写更晚状态，保留全部抽取身份。 | DONE |
| 2026-08-22 | P4-WP08：Confidence Model 完成；source trust、cross-source confirmation、extraction confidence 与 entity-resolution confidence 的有界乘积落地，任一独立证据为零即不能确立事件。 | DONE |
| 2026-08-22 | P4-WP09：Mechanism Engine 完成；11 类版本化经济机制与 fail-closed 事件分类落地，评估须保留证据和理由，且不能生成交易信号。 | DONE |
| 2026-08-22 | P4-WP10：Impact Graph 完成；六类商品的 Event→Mechanism→Entity→Commodity→Market→Instrument→Contract 类型路径落地，版本、商品与映射不一致均 fail-closed，且无价格/下单语义。 | DONE |
| 2026-08-22 | P4-WP11：Market Context 完成；库存、曲线、基差、持仓、波动、USD/CNY、宏观/季节性与 DatasetVersion 的 PIT 快照落地，未来可得或未知商品均拒绝。 | DONE |
| 2026-08-22 | P4-WP12：Event Study 完成；六个事件窗口、return/volatility/volume/OI/spread/basis/MFE/MAE 与 DatasetVersion 研究制品落地，窗口结束及 available_at 前均不可读取。 | DONE |
| 2026-08-22 | P4-WP13：Analogue Engine 完成；事件类型、严重度、地域、商品及库存/USD/波动/曲线状态的结构化类比与 PIT 门禁落地，embedding 只能作为受限辅助。 | DONE |
| 2026-08-22 | P4-WP14：Intelligence Features 完成；九个 `intelligence.*` canonical Feature Registry 定义、版本和受控计算器落地，显式时间、输入和有界分数合同保持研究/交易隔离。 | DONE |
| 2026-08-22 | P4-WP15：Intelligence E2E 完成；离线铜矿停产 golden corpus 覆盖 Document→Event→Mechanism→Impact→Context→Feature，且显式断言不产生 target、BrokerOrder 或 execution。P4 100% 完成。 | DONE |
| 2026-08-22 | P5-WP01：Broker Contract 完成；Paper、CTP Sim 与 durable wrapper 统一暴露 typed identity/capabilities/connection status/error/MarketGateway，未知或断连状态绝不允许新增风险。 | DONE |
| 2026-08-22 | P5-WP02：Order Model 完成；canonical typed lifecycle 与 broker/client identity 落地，durable 提交、重放和单订单回调在持久化边界归一化，重复回调幂等，终态回退与未知恢复均 fail-closed。 | DONE |
| 2026-08-22 | P5-WP03：Position Model 完成；多空今昨、冻结/可平、保证金与已实现/未实现 PnL 字段及前向 PostgreSQL migration 落地，CTP Sim 对待成交平仓预冻结并拒绝超额平仓。 | DONE |
| 2026-08-22 | P5-WP04：Execution Planning 完成；批准组合目标、账户/市场快照和合约规则被绑定为 typed ExecutionPlan，复用 CTP Sim futures planner 生成不可直接提交的计划。 | DONE |
| 2026-08-22 | P5-WP05：Pre-trade Gate 完成；通过的 PreflightResult 与不可变 ExecutionPlan 逐项绑定在最终 broker 提交边界，计划 ID 不符、字段不符、重放或任一 preflight 阻断均 fail-closed。 | DONE |
| 2026-08-22 | P5-WP06：CTP Sim Hardening 完成；异步撤单确认、前置拒单、部分成交、重连恢复、重复终态与 SHFE/INE 今昨仓语义均在本地仿真与 PostgreSQL 对账中覆盖，且绝不伪造成交。 | DONE |
| 2026-08-22 | P5-WP07：Real CTP Adapter Skeleton 完成；typed front protocol 与本地 FakeCtpFront 落地，适配器只接受该 fake、绑定已启用实际合约映射并校验账户快照；应用层仍在连接前拒绝真实 `ctp`。 | DONE |
| 2026-08-22 | P5-WP08：Reconciliation 完成；券商订单、成交、账户和持仓快照必须同内部账本在账户作用域内可解释，未知/冲突会回滚状态写入并追加 PostgreSQL 对账安全状态哈希链。HALT 不会被成功对账或 runtime-risk PASS 自动解除；非 paper 提交要求具名负责人完成 HALT → MANUAL_RECOVERY → NORMAL。 | DONE |
| 2026-08-22 | P5-WP09：Ledger 完成；订单、成交、撤单、账户/持仓和归因已有持久化账本，新增带证据的不可变券商结算事实和具名审批的 controlled adjustment。重复身份仅允许内容完全一致的幂等重放，不能覆盖历史事实。 | DONE |
| 2026-08-22 | P5-WP10：Failure Matrix 完成；断连、重启、重复/乱序状态、未知订单、陈旧行情、数据库不可用、超时/网络分区、持仓偏差、保证金、涨跌停、撤单拒绝与换日均有本地失败关闭测试。 | DONE |
| 2026-08-22 | P5-WP11：Trading E2E 完成；本地 ctp_sim 覆盖 StrategyTarget → PortfolioTarget → 风险审批 → ExecutionPlan → 计划绑定门禁 → 持久化提交 → Fill → Position → Reconciliation，未触及真实 CTP 或实盘。 | DONE |
| 2026-08-22 | P6-WP01：Config Unification 完成；统一 RuntimeConfiguration 严格绑定环境、活动 app.yaml、画像、数据源、研究准入和 ontology，所有交叉不一致均失败关闭；health 已接入该校验。 | DONE |
| 2026-08-22 | P6-WP02：Messaging Abstraction 完成；typed 进程内 FIFO 通知总线覆盖十类跨领域 topic，重复 ID、无订阅者、无效 payload 与 handler 失败均显式失败关闭；不持久化且无任何交易授权。 | DONE |
| 2026-08-22 | P6-WP03：Scheduling 完成；统一 typed job registry 覆盖六类作业，application 中的既有调度任务均经 registry 注册，LIVE 作业强制生命周期 gate。 | DONE |
| 2026-08-22 | P6-WP04：Observability 完成；结构化日志、fail-closed operational snapshot、Prometheus metrics 与健康输出统一，缺失或读取失败均为 UNKNOWN。 | DONE |
| 2026-08-22 | P6-WP05：Security 完成；密钥扫描已进入 just check/CI，日志、CLI、报告、邮件和部署审计统一脱敏，邮件导出遇到机密失败关闭，部署身份与 systemd 服务权限均受限。 | DONE |
| 2026-08-22 | P6-WP06：Cross-platform Deployment Control 完成；旧 Bash 本地控制面已移除，Python 统一编排制品、严格 SSH、Linux install/upgrade、迁移、切换后健康检查和自动回退，且没有质量门禁绕过路径。 | DONE |
| 2026-08-22 | P6-WP07：Linux Production Layout 完成；固定 FHS、版本化 release 环境/systemd 快照、root 控制目录链、制品流式特权交接、有界 root 解压、root-only 部署锁、mount-aware 特权遍历和受限 shell/ops 入口均已验收。P6-WP08 Backup / Restore 已开始。 | DONE |
| 2026-08-23 | P6-WP08：Backup / Restore 完成；六类 allowlisted PostgreSQL 逻辑包、完整树 SHA-256 校验、秘密拒绝与跨平台无覆盖发布已实现；维护创建在采集前/发布前二次确认服务静默，恢复演练仅限 loopback `northstar_test` 的 schema 事务回滚，已由 Docker PostgreSQL 和 Linux 发布原语演练验证。P6-WP09 Release Pipeline 已开始。 | DONE |
| 2026-08-23 | P6-WP09：Release Pipeline 完成；固定 root-owned release gate 仅接受 identity/submit，控制端以 SSH stdin 提交 canonical signed manifest、runtime/control bundle 和独立环境签名；root 在验证签名、大小、SHA-256、完整 archive 索引和固定入口后，才在 root-owned transaction 中执行控制代码。不可变生命周期记录 received→verified→staging→migration→health→cutover→promoted，迁移开始后失败仅允许人工恢复；未启用真实 broker、实盘或生产凭据。P6 100% 完成，P7-WP01 已开始。 | DONE |
| 2026-08-23 | P7-WP01：Typed Tool API 完成；`application.agent_tools` 固定八项 research-only typed allowlist，显式注入只读 catalog / research workflow ports，对 version/hash、PIT `available_at`、feature/dataset 绑定、受控 backtest/validation evidence、可比性与 `RESEARCH_ONLY` card 输出均 fail-closed。模块不可达交易、风险、broker、实时配置、数据库、网络、进程或文件系统，所有响应明确不可交易。P7-WP02 已就绪。 | DONE |
| 2026-08-23 | P7-WP02：Research Agent 完成；`application.research_agent` 仅依赖 Typed Tool API，使用 evidence-bound hypothesis / non-executable Feature proposal 驱动 event→dataset→feature→static experiment→trusted backtest→validation→RESEARCH_ONLY card 的七步链。所有请求固定 as-of，逐步复核身份与前驱关系，输出无敏感 hash trace；失败/未知副作用不重试，不能创建 feature/code/approval/target/order 或交易权限。P7-WP03 已就绪。 | DONE |
| 2026-08-23 | P7-WP03：Intelligence Agent 完成；`application.intelligence_agent` 只经由既有八项 ToolName 中的 `search_events` 闭合入口工作。受控 Event projection 提供授权 source/document/content-hash/span 引用、精确 Event hash、严格历史且独立证据的 analogue，以及只到 Event→mechanism→commodity 的经济 impact；任一 PIT、身份、evidence、类型或结果异常均 fail-closed。输出固定 RESEARCH_ONLY/non-tradable，无外网抓取、无领域直连、无模型/数据库/配置权限、无价格/合约/target/order/execution 语义，且没有自动 retry。P7-WP04 已就绪。 | DONE |
| 2026-08-23 | P7-WP04：Data Quality Agent 完成；同一闭合 Typed Tool API 正式扩展唯一 `inspect_dataset_quality` 只读能力，受控 DataQualityAgent 固定读取 `search_datasets → inspect_dataset_quality`。精确 Dataset/version/schema/lineage/assessment 与 PIT/evidence 绑定的六类 gap/revision/anomaly/stale-source/contract-mismatch/broken-lineage 诊断保持 DETECTED/NOT_DETECTED/UNKNOWN，未知绝不伪装为通过；输出仅 DIAGNOSTIC_ONLY/non-tradable，两段无敏感 hash trace，失败或未知副作用不重试，且无数据修复、重发、发布、交易或越权路径。P7-WP05 已开始。 | DONE |
| 2026-08-23 | P7-WP05：Ops Agent 完成；独立 `TypedOpsToolApi` 仅可原子读取经授权、PIT 安全、同一 scope 的 hash-only health/log/deployment/backup snapshot。OpsAgent 固定单次读取、调用前消费 identity、保留 ENABLED/HALT/MANUAL_RECOVERY/UNKNOWN 为只读观察，永不部署、恢复、绕过 kill switch 或交易。P7 100% 完成，P8-WP01 已开始。 | DONE |
| 2026-08-23 | P8-WP01：候选验收矩阵完成。纯 hash-only evaluator 精确固定九条独立证据车道与四条 seam，所有结果永远 non-tradable；CI 只重放既有 offline/paper/ctp_sim 安全证据，并在 live/production/真实 broker 环境先行拒绝。WP01 完成时四条生产桥接均显式 BLOCKED，因此没有将独立 P1—P7 测试伪造成 Research→Order 闭环；后续 WP02/WP03 才分别验证其中两条独立 seam。P8-WP02 已开始。 | DONE |
| 2026-08-23 | P8-WP02：Intelligence-to-Research Feature Projection 完成。`intelligence_feature_projection_v3` 将 Event 精确绑定到 P1 raw artifact、source receipt、document/content SHA-256 与 UTF-8 evidence span；将 MarketContext 精确绑定到唯一 normalized P1 artifact、DatasetVersion/receipt 与完整闭合行 commitment。应用层在同一不可变制品库回放全部证据后，才发布 P2 的窄化 hash-only Feature input/PIT/lineage；不暴露 raw payload，也不产生 target、order 或交易权限。`INTELLIGENCE_TO_RESEARCH` seam 现为 VERIFIED，P8-WP03 已就绪。 | DONE |
| 2026-08-23 | P8-WP03：Research candidate→StrategyTarget 人工激活边界完成。application 仅重放具 PASS evidence、具名 CANDIDATE approval 的完整 Research Card/Decision/Experiment/Dataset/Feature/Strategy 链，再要求独立具名 activation approval 绑定精确 target proposal，签发静态、hash-only、non-tradable receipt 与 `StrategyTarget` v2。`RESEARCH_TO_PORTFOLIO_RISK` seam 现为 VERIFIED；Data PIT→Research、Portfolio/Risk→CTP sim 仍 BLOCKED，P8-WP04 已开始以消除 hand-written hash/synthetic target 的 execution preflight 路径。 | DONE |
| 2026-08-23 | P8-WP04：Point-in-Time Execution Provenance Preflight 完成。application verifier 重放 P2 candidate→P8-WP03 activation 输入并精确比对 receipt，拒绝 direct synthetic P3 target、hand-written risk hash、错误/过期 PIT、account、quote、rule 或 runtime-risk 证据；它在内部生成 P5 plan/runtime-risk/preflight，只签发短时 hash-bound、所有 eligibility 均为 false 的 `ctp_sim` evidence receipt。无 adapter/router/broker/durable submit 能力，`PORTFOLIO_RISK_TO_EXECUTION_SIMULATION` seam 仍 BLOCKED；P8-WP05 已开始，负责最终 receipt consumption、synthetic-path 清除和 PostgreSQL CTP-sim/reconciliation E2E。 | DONE |
| 2026-08-23 | P8-WP05：Candidate CTP-Sim Integrated E2E 完成。原始 P2→P3 provenance 被 application 重新回放并内部派生订单；append-only consumption 与 durable intent 原子提交，opaque final authority、锁内 state/quote 重验、双订单 batch 和 simulator-only provenance-aware reconciliation 均已验收。raw/direct/legacy CTP-sim submit、未知 state 与未解释 order/fill 均 fail-closed/HALT；`PORTFOLIO_RISK_TO_EXECUTION_SIMULATION` 仅作为 non-tradable candidate seam VERIFIED。P8 100% 完成，P9-WP01 已开始。 | DONE |
| 2026-08-23 | P9-WP01：完成标准库离线依赖来源/lock artifact policy 与全仓密钥门禁。policy 在任何 uv 解析/同步前运行，随后强制 offline lock check；扫描器覆盖 tracked tests、canonical fixture allowance、NUL path、动态表达式、未知文本/符号链接失败关闭。PEP 517 bootstrap hash provenance 明确移交 P9-WP02。 | DONE |
| 2026-08-23 | P9-WP02：完成 hermetic PEP 517 bootstrap。精确 builder/source artifact policy、fresh staging venv 原子切换、受管 Python release boundary、offline/no-sync CI/just/部署与 Windows/Linux contract 均已验收；P9 100% 完成，P10-WP01 已开始建立 Mature v1 evidence baseline。 | DONE |
| 2026-08-23 | P10-WP01：完成 Mature v1 evidence baseline。48 项验收逐项映射为受控实现、simulation、安全拒绝、真实缺口或外部阻塞，并以 contract 保护；P5-WP08 与两项陈旧完成矩阵已对账。P10-WP02 已开始建立 six-commodity offline intelligence evidence corpus。 | DONE |
| 2026-08-23 | P10-WP02：完成 six-commodity fixture-only intelligence evidence corpus。Document/evidence span/hash、Event、multi-source lifecycle merge、typed ImpactPath 与 hash-bound Feature-definition handoff 均可重放；unknown、authority、schema、crosswalk 与 lineage drift 均失败关闭。它不构造 P1/PIT FeatureValue、授权 source/market/contract/rule/calendar、target、approval 或订单。P10-WP03 已开始建立 research-only PIT→Backtest→Validation→Research Card 证据闭环。 | DONE |
| 2026-08-23 | P10-WP03：完成六商品 fixture-only intelligence Feature 研究回测证据闭环。每个 checkpoint 明确重放 `available_at <= decision_at` 的 WP02 handoff，晚到来源与 retraction 均失败关闭/显式抑制；仅在独立 synthetic outcome 事后可用时计算 deterministic alignment statistic，并 hash-bind Validation 与 RESEARCH_ONLY Research Card。它不构造 P1 FeatureValue/DatasetVersion、市场收益、真实合约、candidate、target、approval、plan 或订单；P3 activation 在创建目标前拒绝该证据类别。P10-WP04 已开始。 | DONE |
| 2026-08-23 | P10-WP04：完成 canonical multi-strategy PortfolioTarget composition。exact activated StrategyTarget 与 allocation input 在 P3 内部重放、规范排序并 hash-bind policy/allocation/pre-net contribution/net positions；target v2 绑定 composition hash，拒绝重复/未来/窗口/source-set drift，保留 cash 与 net-zero。它不评估组合风险、不创建 approval、plan、intent 或 broker mutation。P10-WP05 已开始。 | DONE |
| 2026-08-23 | P10-WP05：完成组合级风险证据与审批 gate。exact composition、profile policy、账户、CTP-sim broker snapshot 与 reconciliation safety state 派生并 hash-bind exposure、limits、七场景 stress 和风险状态；UNKNOWN/WARN/BLOCK/HALT、scope/hash/attestation/expiry drift 均在 approval、P8 receipt、intent 与 broker mutation 之前失败关闭。hash-only durable manual record 使用前向 PostgreSQL migration `0008_portfolio_risk_approval`；issuer 成功路径只在 private test composition。认证人工批准服务与专用 DB roles 仍 `BLOCKED_EXTERNAL`，无真实 CTP、身份认证或 live order。P10-WP06 已开始。 | DONE |
| 2026-08-23 | P10-WP06：完成 Research Agent 跨进程 durable evidence audit。独立 wrapper 先写 `ADMITTED`，成功时原子追加 `COMPLETED` 和有序 hash trace；generic tool/Agent 异常与终态写入不确定性均保留 unresolved reservation，拒绝同一 `run_id` 重放。前向 `0009_agent_run_audit`/`0010_agent_run_audit_hardening` 强制 hash-only、tool/failure allowlist 和 UPDATE/DELETE/TRUNCATE 不可变性；无 raw prompt/CoT、无新增 Agent/交易能力、无真实 CTP 或实盘。P10-WP07 已开始。 | DONE |
| 2026-08-23 | P10-WP07：完成 Trading Acceptance Evidence Closure。`P10_TRADING_FAILURE_MATRIX.md` 将 P5-WP10 的 disconnect/restart、重复/乱序回调、unknown order、stale facts、DB unavailable、timeout/network partition、identity mismatch、margin、price limit、cancel reject 和 rollover 固定为 T05-01 至 T05-11，并补 T05-12 的真实 P3 `BLOCK` 无 plan/intent/broker mutation sentinel contract 与 T05-13 的真实 CTP 连接前拒绝边界。所有正向证据仅为本地 `ctp_sim` / 隔离 PostgreSQL；没有真实 CTP、账户或实盘操作。P10 剩余 P10-WP08/P10-WP09 均为外部阻塞。 | DONE |
| 2026-08-23 | DOC-WP01：完成文档治理与架构设计收敛。旧编号文档、陈旧路线图和重复 README 叙述已由 `ARCHITECTURE.md`、`DEVELOPMENT.md`、`OPERATIONS.md`、`GOVERNANCE.md` 替代；planning 保留为独立控制面。所有仓库入口、配置/脚本引用和文档契约已同步，未创建旧路径兼容页。 | DONE |

> 所有重大架构变化、阶段调整、WP 删除/新增都必须记录在这里。

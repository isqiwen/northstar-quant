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
| P2 | Research & Strategy Platform | IN_PROGRESS | 44% |
| P3 | Portfolio & Risk | TODO | 0% |
| P4 | Intelligence / EventAlpha | TODO | 0% |
| P5 | Trading & Execution Production Grade | TODO | 0% |
| P6 | Platform Foundation & Automation | TODO | 0% |
| P7 | AI-assisted Research Automation | TODO | 0% |
| P8 | Integrated Production Candidate | TODO | 0% |
| P9 | Hardening / Performance / Security | TODO | 0% |
| P10 | Mature v1 Acceptance | TODO | 0% |

```yaml
active_phase: P2
active_work_package: P2-WP05
next_task:
  id: P2-WP05
  title: Lookahead Guard
  status: IN_PROGRESS
blocked_work_packages: []
```

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
uv run pytest <focused tests>
uv run ruff check .
uv run python scripts/ci/check_mypy_baseline.py check
```

涉及公共模块、DB、config、execution、live：

```bash
uv run pytest
uv run ruff check .
uv run python scripts/ci/check_mypy_baseline.py check
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
uv run pytest tests/platform/contract tests/architecture
uv run ruff check .
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
    - bash -n scripts/deploy.sh scripts/deploy/deploy.sh scripts/deploy/install-release.sh scripts/deploy/remote/linux/*.sh scripts/ops/remote/linux/*.sh
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
    - 当前 legacy 数据与 P1 STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY 都不是逐决策 PIT replay；P2-WP04 明确拒绝将其写成候选策略准入。P2-WP05 的基础 Guard 正在实施且不会提升该路径，之后仍需受控逐决策编排、P2-WP06/P2-WP07 的验证与人工 Research Decision。
    - RunManifest v4 保存数据、目标、代码和输出 checksum，不写入裸行情、路径、凭据、数据库记录或交易对象；没有 Docker、数据库迁移、券商或订单提交改动。
```

## P2-WP05 — Lookahead Guard

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
  它不调用 BacktestRequest/回测器、Research Admission、CLI、数据库或交易入口，输出仍固定不可准入；
- 现有 static PIT、FeatureLineage/FeatureBackfill、Experiment、legacy 和普通回测路径继续
  保持 `decision_time_safe=false`，不得升级为候选策略或交易证据。

完成前置（尚未满足）：

- 受控逐 checkpoint 的 Feature、事件 producer，以及可接入 BacktestRequest 的完整策略/target producer，
  必须把实际输入、输出 target hash、StrategyVersion 和 BacktestRequest 精确绑定；空证据也必须能证明
  对应类别确实未使用。当前连续日线 target trace 只完成 hash 轨迹，尚未成为 BacktestRequest 或证书输入；
- 带 `available_at` 的 immutable Contract Master/Rule 发布器，并按每个 decision_at 重放实际合约、
  费率和保证金规则；静态 YAML 或可自由构造的领域对象不能作为历史知识根；
- Application 的逐决策策略/回测 composition root 必须在任何候选准入前调用 certificate 的完整重算。

## P2-WP06 — Validation Framework

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

## P2-WP07 — Research Decision State

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

第一阶段：

- fixed budget
- volatility target
- risk budget
- capped allocation
- cash reserve

避免一开始上复杂优化器。

## P3-WP03 — Exposure Engine

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

至少：

- gap
- limit-up/down
- volatility shock
- liquidity collapse
- correlated commodity shock
- margin increase
- FX shock

## P3-WP07 — Portfolio/Risk E2E

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

利用：

- canonical URL
- exact hash
- title similarity
- semantic similarity
- repost detection

同一事件的转载不得产生多个独立交易 Feature。

## P4-WP05 — Entity Extraction / Resolution

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

至少：

```text
SourceTrust
× CrossSourceConfirmation
× ExtractionConfidence
× EntityResolutionConfidence
```

禁止仅采用 LLM 自报 confidence。

## P4-WP09 — Mechanism Engine

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

统一：
- BrokerAdapter
- MarketGateway
- typed errors/status

## P5-WP02 — Order Model

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

支持：

- long/short
- today/yesterday
- frozen
- closable
- average price
- margin
- realized/unrealized PnL

## P5-WP04 — Execution Planning

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

持续：

```text
Broker Orders ↔ Internal Orders
Broker Positions ↔ Internal Positions
Broker Account ↔ Internal Ledger
```

无法解释差异 => HALT / MANUAL_RECOVERY。

## P5-WP09 — Ledger

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

统一调度：

- data jobs
- intelligence jobs
- feature jobs
- research jobs
- maintenance
- live loops

Scheduler 不得绕过生命周期门禁。

## P6-WP04 — Observability

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

- secrets untracked
- least privilege
- deploy user separation
- broker secret redaction
- audit
- export redaction

## P6-WP06 — Cross-platform Deployment Control

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

覆盖：

- PostgreSQL
- configs
- ontology
- run manifests
- critical runtime state
- release metadata

必须执行 restore drill。

## P6-WP09 — Release Pipeline

```text
check
→ test
→ package
→ immutable manifest
→ deploy
→ health
→ promote
```

---

# 16. P7 — AI-assisted Research Automation

AI 目标：

> 提高研究吞吐，不获得未经批准的实盘权限。

## P7-WP01 — Typed Tool API

Agent 只能经由：

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

## P7-WP02 — Research Agent

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

## P7-WP03 — Intelligence Agent

允许：
- source research
- event summary
- analogue
- impact explanation

所有结论引用 evidence。

## P7-WP04 — Data Quality Agent

允许检测：
- gap
- revision
- anomaly
- stale source
- contract mismatch
- broken lineage

不得伪造修复数据。

## P7-WP05 — Ops Agent

允许：
- health
- log summary
- deployment diagnosis
- backup status

禁止：
- 绕过 kill switch
- HALT 自动恢复 NORMAL

---

# 17. P8 — Integrated Production Candidate

全部必须通过：

- [ ] Data PIT correctness
- [ ] Research reproducibility
- [ ] Intelligence evidence/lineage
- [ ] Portfolio/risk gate
- [ ] CTP sim full loop
- [ ] Deployment repeatable
- [ ] Monitoring usable
- [ ] Backup/restore verified
- [ ] Architecture tests clean
- [ ] Full pytest green
- [ ] Ruff green
- [ ] mypy baseline no regression
- [ ] no uncontrolled live path

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

---

# 19. P10 — Mature v1 总验收

## Data

- [ ] 可信合约/日历/规则链
- [ ] 数据版本不可覆盖
- [ ] PIT correctness
- [ ] 质量 failure 可阻断下游

## Intelligence

- [ ] 6 个核心商品事件链
- [ ] Evidence 可追溯
- [ ] Ontology versioned
- [ ] Event merge golden corpus
- [ ] Impact graph 可解释
- [ ] Event features 可回测

## Research

- [x] Feature Registry
- [x] Canonical Feature Families（仅 static PIT / synthetic fixture）
- [x] Experiment Registry（仅 static reproducibility；不可回测、不可准入）
- [ ] IS/OOS/Walk-forward
- [ ] Lookahead guard
- [ ] Research Card reproducible

## Portfolio / Risk

- [ ] Multi-strategy portfolio
- [ ] Exposure
- [ ] Limits
- [ ] Stress
- [ ] Risk state machine

## Trading

- [ ] paper
- [ ] ctp_sim
- [ ] reconciliation
- [ ] ledger
- [ ] failure matrix
- [ ] real CTP adapter 默认不能真实下单
- [ ] production enable 人工确认

## Platform

- [ ] Windows/Linux 开发
- [ ] Windows/Linux deployment control
- [ ] Linux production
- [ ] health/logs
- [ ] backup/restore
- [ ] rollback
- [ ] CI

## AI

- [ ] Agent 无生产交易越权
- [ ] AI conclusion 有 evidence
- [ ] Research Agent 产物可追踪
- [ ] AI 无法绕过风险门禁
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
  - uv run pytest ...
  - uv run ruff check .
  - uv run python scripts/ci/check_mypy_baseline.py check

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
| Intelligence | Ontology | TODO |
| Intelligence | Document ingestion | TODO |
| Intelligence | Entity resolution | TODO |
| Intelligence | Event extraction | TODO |
| Intelligence | Event merge | TODO |
| Intelligence | Impact graph | TODO |
| Intelligence | Context | TODO |
| Intelligence | Event study | TODO |
| Intelligence | Analogue | TODO |
| Research | Feature registry | DONE（单一静态 PIT 输入；逐决策 replay 留待 Lookahead Guard） |
| Research | Experiment registry | DONE（static reproducibility only；不构成回测/准入/交易证据） |
| Research | Backtest unification | TODO |
| Research | Validation | TODO |
| Research | Lookahead guard | IN_PROGRESS（evidence-consistency receipt + 受控连续日线 target trace；严格 composition root 与完整 producer 未完成） |
| Portfolio | Allocation | TODO |
| Portfolio | Exposure | TODO |
| Risk | Limits | TODO |
| Risk | State machine | TODO |
| Risk | Stress | TODO |
| Trading | Broker contract | TODO |
| Trading | Order state | TODO |
| Trading | Positions | TODO |
| Trading | Execution planning | TODO |
| Trading | Pretrade | TODO |
| Trading | CTP sim | TODO |
| Trading | Reconciliation | TODO |
| Trading | Ledger | TODO |
| Platform | Config | TODO |
| Platform | Messaging | TODO |
| Platform | Scheduling | TODO |
| Platform | Observability | TODO |
| Platform | Security | TODO |
| Platform | Deployment | TODO |
| Platform | Backup/restore | TODO |
| Platform | Release | TODO |
| AI | Research agent | TODO |
| AI | Intelligence agent | TODO |
| AI | Data quality agent | TODO |
| AI | Ops agent | TODO |

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
  id: P2-WP05
  title: Lookahead Guard
  status: IN_PROGRESS
```

Codex 从此处开始执行。

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

> 所有重大架构变化、阶段调整、WP 删除/新增都必须记录在这里。

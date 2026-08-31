# Northstar Quant Agent Rules

本文件定义所有 AI Agent / Codex 在 `northstar-quant` 仓库中的最高优先级开发规则。

除用户在当前会话中明确给出的指令外，Codex 必须遵守本文件。

---

# 1. Project Intent

Northstar Quant 是一个面向中国商品期货的量化研究、情报、组合、风险和交易平台。

长期架构包含六个领域：

1. Data Platform
2. Intelligence
3. Research & Strategy
4. Portfolio & Risk
5. Trading & Execution
6. Platform Foundation

本项目属于 real-money-adjacent system。

即使当前运行在：

- offline
- paper
- ctp_sim

模式下，也必须按照未来真实资金系统的工程标准处理：

- 数据完整性
- 研究可复现性
- 风险控制
- 交易状态
- 审计
- 故障恢复

优先级始终为：

1. Safety
2. Correctness
3. Data Integrity
4. Reproducibility
5. Architecture
6. Research Capability
7. Production Reliability
8. Performance
9. UI

禁止为了开发速度牺牲前面的原则。

---

# 2. Master Implementation Plan

项目实施进度的唯一事实来源是：

`docs/planning/MASTER_IMPLEMENTATION_PLAN.md`

对于任何非 trivial 的开发任务，Codex 在修改代码前必须：

1. 读取本 `AGENTS.md`
2. 读取 `docs/planning/MASTER_IMPLEMENTATION_PLAN.md`
3. 查看其中：
   - active_phase
   - active_work_package
   - next_task
   - blocked_work_packages
   - 对应 Work Package 的验收标准
4. 执行 `git status`
5. 检查当前工作树是否存在用户未提交修改

如果用户只说：

- “继续”
- “继续开发”
- “继续按计划”
- “继续 Northstar”

默认解释为：

> 从 `MASTER_IMPLEMENTATION_PLAN.md` 中的 `next_task` 继续执行。

不得依赖聊天历史猜测当前进度。

`docs/planning/MASTER_IMPLEMENTATION_PLAN.md` is the single source of truth for implementation progress.

Do not infer implementation progress from chat history.

---

# 3. Work Package Execution Protocol

Codex 每次只处理一个 Work Package。

标准流程：

```text
读取 WP
→ 检查 dependencies
→ 标记 IN_PROGRESS
→ 实现
→ 局部测试
→ 完整验收
→ 更新文档
→ 标记 DONE
→ 更新 next_task
→ 再处理下一个 READY WP
```

一个 WP 必须作为完整变更处理。

如果该 WP 影响：

- code
- tests
- config
- database schema
- migration
- CLI
- scripts
- docs

必须在同一个 WP 中一起更新。

不得只改代码而留下：

- 旧测试
- 旧配置
- 旧 schema
- 旧文档
- 旧 CLI contract

---

# 4. Definition of Done

Work Package 只有在以下条件全部满足时才能标记为 DONE：

- 功能实现完成
- 架构边界正确
- 正常路径测试存在
- 必要失败路径测试存在
- 必要 integration / contract / golden / e2e 测试存在
- schema 变化有 Alembic migration
- config 已同步
- CLI / scripts 已同步
- docs 已同步
- pytest 规定范围通过
- Ruff 通过
- mypy baseline 没有新增诊断
- 没有削弱任何交易安全门禁
- `MASTER_IMPLEMENTATION_PLAN.md` 已更新
- `next_task` 已更新

禁止：

> “代码差不多完成”

就把 WP 标为 DONE。

---

# 5. Progress Tracking

完成一个 WP 后，Codex 必须更新：

`docs/planning/MASTER_IMPLEMENTATION_PLAN.md`

至少包括：

```yaml
status: DONE

completion:
  completed_at: YYYY-MM-DD
  commit: <sha or null>
  notes: ...
```

同时更新：

```yaml
active_work_package:
next_task:
blocked_work_packages:
```

以及对应：

- checkbox
- phase progress
- feature completion matrix

Master Plan 必须反映仓库真实状态。

不得出现：

```text
代码已完成但文档仍 TODO
```

或：

```text
文档 DONE 但代码并未通过验收
```

---

# 6. Repository Architecture

长期目标领域：

```text
src/northstar_quant/
├── application/        # 根级 composition root，不属于六个领域
├── data/
├── intelligence/
├── research/
├── portfolio_risk/
├── trading_execution/
└── foundation/
```

依赖原则：

```text
foundation
  ↑
data
  ↑
intelligence
  ↑
research
  ↑
portfolio_risk
  ↑
trading_execution
```

这表示允许使用更低层稳定 contract，
但不允许形成反向业务依赖或循环依赖。

`application/` 是唯一跨领域 composition root，可调用六个领域与 `foundation`；它不承载
领域模型，且六个领域和 `foundation` 都不得反向导入它。

强制规则：

- `foundation` 不依赖业务域
- `data` 不依赖 research / trading
- `intelligence` 不依赖 trading_execution
- `research` 不直接访问 broker
- `portfolio_risk` 不直接提交订单
- `trading_execution` 不实现策略研究逻辑

跨领域协调应发生在明确的 composition root。

---

# 7. Domain Semantics

以下概念必须保持严格区分：

```text
Document != Event
Event != Feature
Feature != Strategy
StrategyTarget != PortfolioTarget
PortfolioTarget != ExecutionPlan
ExecutionPlan != BrokerOrder
Fill != ClosedTrade
Commodity != Instrument
Instrument != Contract
```

不得为了方便把这些概念合并。

---

# 8. Point-in-Time Correctness

所有研究相关数据必须正确处理时间语义。

需要时使用：

```text
event_time
source_time
published_time
ingested_time
processed_time
available_time
```

回测只能看到：

```text
available_time <= simulation_time
```

禁止：

- look-ahead
- future contract information
- future fee/margin rule
- revised data overwrite historical state
- future event leakage

任何不确定时间语义必须：

- 明确 UNKNOWN
- 或 fail closed

不得猜测。

---

# 9. Intelligence Rules

Intelligence 的标准链：

```text
Source
→ Document
→ Entity
→ Event
→ Mechanism
→ Impact
→ Market Context
→ Feature
```

必须遵守：

- Document 与 Event 分离
- 同一事件多来源需要 merge
- Event 保存 evidence
- Ontology versioned
- LLM output 不是 ground truth
- LLM confidence 不能作为最终 confidence
- Event 不直接生成 BUY/SELL
- Event 必须先进入 Feature / Research pipeline

禁止：

```text
News
→ LLM
→ BUY
```

---

# 10. Research Rules

任何策略候选必须经过：

```text
Feature
→ Experiment
→ Backtest
→ Validation
→ OOS
→ Stress
→ Research Decision
```

不得仅因为：

- 单次 Sharpe 高
- 某个参数很好
- 短样本盈利
- 连续合约结果漂亮

就升级为生产候选。

研究必须记录：

- DatasetVersion
- FeatureVersion
- StrategyVersion
- Config
- Code revision
- Cost model
- Slippage model
- OOS period

同样输入必须可复现同样结果。

---

# 11. Trading Safety Rules

默认：

```text
NORTHSTAR_BROKER=paper
NORTHSTAR_LIVE_TRADING_ENABLED=false
```

Codex 不得：

- 自动开启 live trading
- 自动填入真实 CTP credential
- 自动创建 production profile
- 自动向真实账户报单
- 自动取消真实订单
- 自动恢复 HALT 状态
- 为测试通过绕过 preflight
- 为测试通过关闭 kill switch
- 在未知账户/持仓状态下增加风险

任何真实资金相关操作都必须获得用户明确确认：

- profile
- broker
- account
- target environment
- requested action

---

# 12. Fail-Closed Policy

以下任何状态未知时：

```text
Market Data
Contract Mapping
Trading Calendar
Account
Position
Open Orders
Risk State
Broker State
Price Freshness
Margin
Data Authorization
```

默认行为：

```text
NO NEW RISK
```

而不是估算、使用旧值或继续提交订单。

---

# 13. Order / Execution Rules

严格保持：

```text
ApprovedPortfolioTarget
→ ExecutionPlan
→ PreTradeCheck
→ BrokerOrder
```

ExecutionPlan 不是订单。

Pre-trade check 是 broker submit 前最后一道强制防线。

订单状态必须处理：

- accepted
- rejected
- partial fill
- fill
- cancel pending
- cancelled
- unknown

所有 broker callback 必须考虑：

- duplicate
- out-of-order
- reconnect
- retry
- idempotency

---

# 14. Database Rules

Northstar 的存储边界按职责划分：

- **交易状态 / Platform core**：必须使用 PostgreSQL。`NORTHSTAR_DATABASE_URL`、
  `NORTHSTAR_TEST_DATABASE_URL`、合约、订单、成交、持仓、策略运行状态、风险、审批、对账与审计都属于这一权威边界。
- **大规模历史数据**：必须以受治理的 Parquet 制品保存，包括 tick、bars、factors、features，以及可复现的研究和
  backtest 输入/结果。制品仍须有版本、manifest、内容 hash、lineage、PIT 和 license/retention 控制；不能把可变的
  当前交易状态伪装成历史文件。
- **历史分析**：使用 DuckDB 查询受治理的 Parquet 制品，服务于探索、研究和回测。DuckDB 是分析引擎而非交易权威库；
  查询、数据版本、参数和产物必须可复现，且不得直接写入核心交易/风险状态或绕过 Research → Risk → Execution 门禁。
- **本地工具集 / Local tools**：允许使用 SQLite 作为显式、隔离的本地缓存、索引或 scratch storage；它不是权威数据源，
  不得静默替代核心 PostgreSQL。

SQLite Local tools 必须：

- 使用 tool-owned 的独立路径与数据模型，不能复用核心 `NORTHSTAR_DATABASE_URL`；
- 不参与 Alembic、`init-db`、核心 repository 或 PostgreSQL integration fixture；
- 不保存或派生订单、成交、持仓、策略状态、风险状态、审批、对账或审计的权威事实；
- 显式测试其文件、并发和损坏恢复边界，不能成为核心数据库不可用时的 fallback。

任何 DuckDB adapter、查询接口或本地工具 SQLite schema 都必须在其具体 Work Package 中单独设计、测试和验收；
本规则本身不授权把它们接入核心 runtime。

`paper` 与 `ctp_sim` 的可变模拟柜台状态必须保存为 PostgreSQL 中按 broker/account 隔离的当前快照与不可变 transition
审计链；不得写入 `state.json`、SQLite 或其他文件 fallback。该 adapter-private 状态机不替代同样位于 PostgreSQL 的
durable order、fill、position snapshot、risk、approval、reconciliation 与 audit 账本。Contract Master 与 CTP mapping
必须保存为 PostgreSQL 中追加式、不可变、按时间版本化的权威发布；研究、preflight 与执行只能按决策时点重放其绑定的
publication。静态 YAML 品种卡、画像或日历材料只能提供研究/配置参考，不能作为当前可交易合约、动态规则或运行时日历的 fallback。

数据库 schema 修改必须同步：

- SQLAlchemy model
- repository
- Alembic migration
- tests
- docs

不得只改 ORM 而不迁移数据库。

数据库保全是强制安全边界：仓库自动化绝不删除或清空数据库、表、schema 或本机 PostgreSQL 数据目录。
数据库删除或清空只能由用户在仓库自动化之外手动执行。`init-db` 只能执行
`alembic upgrade head`；未来迁移的 `upgrade()` 不得包含破坏性 DDL/DML。

开发期 schema baseline（2026-08-25 用户授权）：在尚无需要兼容或保留的数据库 revision 时，
`alembic/versions/` 只保留一个显式的当前完整 baseline。每次 schema 变化仍必须同步 ORM、repository、baseline、
测试和文档；不得新增兼容旧 revision 的迁移链。旧 revision 的本地库必须由用户在仓库自动化之外手动重建，
不得通过 `stamp`、`downgrade`、drop、truncate 或自动重置绕过。若未来出现需要保留的环境或数据，必须在开始前
获得用户的新明确决定，再恢复版本化升级策略。

核心集成测试数据库必须使用隔离：

```text
northstar_test
```

或项目当前配置指定的测试 PostgreSQL。SQLite Local tools 可以有独立 unit test，但不能替代核心
PostgreSQL integration test。

---

# 15. Configuration Rules

Environment variable 前缀：

```text
NORTHSTAR_
```

配置原则：

- explicit
- typed
- validated
- safe by default

禁止：

- hidden fallback
- magic values
- live risk parameter hardcoding
- production secret 写入 tracked file

影响交易安全的配置必须在：

`.env.example`

中展示安全默认值。

---

# 16. Testing Rules

当前主要质量门禁：

```bash
python scripts/dev/run_just.py env-bootstrap
python scripts/dev/run_uv.py run --offline --no-sync pytest
python scripts/dev/run_uv.py run --offline --no-sync ruff check .
python scripts/dev/run_uv.py run --offline --no-sync python scripts/ci/check_mypy_baseline.py check
```

GitHub Actions 或其他 hosted CI 不是本仓库支持的验证入口；上述质量门禁必须由具备相应前提的本地工作站显式执行。

Codex 应先运行 focused tests：

```bash
python scripts/dev/run_uv.py run --offline --no-sync pytest <affected tests>
```

如果修改涉及：

- common
- config
- db
- execution
- live
- portfolio/risk
- shared models

则完成前必须运行完整：

```bash
python scripts/dev/run_uv.py run --offline --no-sync pytest
```

测试分类：

```text
unit
integration
contract
e2e
```

并按领域扩展：

- golden
- regression
- statistical
- scenario
- simulation
- failure

---

# 17. Architecture Tests

必须保留自动架构约束。

例如：

```text
tests/architecture/
```

应检查：

- dependency cycles
- forbidden imports
- foundation business dependency
- research → broker dependency
- data → trading dependency
- public API boundaries

如果架构测试失败：

不得通过删除 architecture test 解决。

---

# 18. Golden / Regression Tests

Intelligence：

```text
Document
→ expected Entity
→ expected Event
→ expected Impact
```

必须使用 golden fixtures 防止：

- LLM upgrade
- prompt change
- ontology change
- merge algorithm change

产生静默语义漂移。

Research 应有 regression fixture：

```text
same data
+ same config
+ same code
=
same result
```

---

# 19. Scripts and Infrastructure

仓库级工程资产：

```text
scripts/
├── dev/
├── build/
├── data/
├── db/
├── ci/
├── deploy/
├── ops/
├── release/
├── maintenance/
└── tools/
```

基础设施：

```text
infra/
├── systemd/
├── ansible/
├── monitoring/
├── backup/
└── nginx/
```

区别：

```text
scripts/
= 如何执行操作

infra/
= 系统如何被声明和部署
```

---

# 20. Platform Support

开发、研究、部署控制端与生产：

```text
Linux x86_64 only
```

Windows 本机、PowerShell、Git Bash 与 Windows 部署控制端均不受支持。若命令运行时
操作系统或架构不满足 Linux x86_64，必须在任何写入、安装、迁移、构建、SSH 或交易操作前失败关闭。

目标架构：

```text
Linux x86_64 workstation
        ↓
Python deployment orchestrator / just
        ↓
SSH
        ↓
Linux x86_64 production
```

不得为 Windows、macOS 或非 x86_64 Linux 增加兼容层、回退路径或测试成功路径。Windows 风格路径的拒绝
仍属于输入安全校验，不构成平台支持。

---

# 21. justfile Rules

`justfile` 是统一的人类操作面；调用必须经由仓库本地的 `python scripts/dev/run_just.py`，不得依赖宿主机 `PATH`。

优先提供：

```text
python scripts/dev/run_just.py env-bootstrap
python scripts/dev/run_just.py setup
python scripts/dev/run_just.py dev-postgres
python scripts/dev/run_just.py check
python scripts/dev/run_just.py test-unit
python scripts/dev/run_just.py test-backtest
python scripts/dev/run_just.py test-cli
python scripts/dev/run_just.py candidate-acceptance
python scripts/dev/run_just.py deploy-prod <signing-key>
python scripts/dev/run_just.py ops-health
```

`justfile` 负责组合命令。

复杂逻辑应进入：

```text
scripts/
```

而不是写成巨大 just recipe。

---

# 22. Deployment Rules

Production target 仅 Linux。

部署控制程序仅支持 Linux x86_64；目标端同样仅支持 Linux x86_64。

标准流程：

```text
preflight
→ package
→ upload
→ install/upgrade
→ migrate
→ restart
→ health
→ promote
```

失败时：

```text
rollback
```

Production deploy 不得自动启动真实非-paper交易，除非用户明确确认。

---

# 23. Generated Files

默认不跟踪：

```text
.env
.venv/
.pytest_cache/
.ruff_cache/
.mypy_cache/
logs/
storage/
reports/
dist/
broker snapshots
market data
database backups
production credentials
```

添加任何生成文件前必须说明为什么需要版本控制。

---

# 24. External Dependency Blocking

以下缺失可以标记 BLOCKED：

- 商业数据合同
- 数据 license
- CTP 真实账号
- 期货公司模拟前置
- production secret
- 真实资金批准
- 付费供应商

Codex 遇到 BLOCKED 时必须优先完成：

- interface
- fake adapter
- simulator
- validation
- docs
- tests

然后继续其他 READY Work Package。

不得因为一个外部项停掉整个项目。

---

# 25. AI Permission Boundary

AI 可以直接完成：

- code
- tests
- migrations
- docs
- config schemas
- simulation
- local research
- backtests
- reports
- architecture refactor

需要人工确认：

- 购买数据
- 接入真实账户
- production credentials
- live enable
- 真实订单
- 真实资金操作

禁止 AI 自行：

- 把研究策略升级到 production
- 从 HALT 恢复交易
- 绕过人工审批

---

# 26. Development Discipline

Codex 修改代码前必须：

```bash
git status
```

规则：

- 不覆盖用户修改
- 不 revert 无关文件
- 不扩大 scope
- 不做无关重构
- 不创建重复 abstraction
- 新稳定概念优先 structured model
- 避免 ad-hoc dict
- 失败路径显式
- 高风险代码避免 broad `except Exception`

项目尚未正式发布，仍处于研发阶段，因此：

> 本仓库不对任何既有实现、旧接口、旧配置、旧 CLI contract 或旧数据模型承诺兼容性。

除非用户在当前会话明确要求兼容过渡，否则禁止为了保留历史实现而新增或保留：

- compatibility alias / legacy adapter
- deprecated configuration fallback
- 旧 CLI 参数、旧 API 路径或双写数据模型
- 仅用于迁就旧调用方的 shim、wrapper 或迁移分支

应优先进行干净的 breaking change，并在同一变更中同步更新全部调用方、测试、配置、文档与 schema / migration（如适用）。

如需 breaking change，应：

```text
修改实现
+
修改调用者
+
修改测试
+
修改配置
+
修改文档
```

一次完成。

---

# 27. Codex 每轮输出要求

开始工作时输出：

```text
ACTIVE WP:
WHY READY:

PLAN:
1.
2.
3.

SAFETY:
```

完成时：

```text
WP RESULT:

CHANGED:
- ...

TESTS:
- command → PASS/FAIL

MIGRATION:
- ...

RISKS:
- ...

MASTER PLAN:
- status updated
- next_task updated
```

---

# 28. Stop Conditions

Codex 只有在以下情况下应该停止并询问用户：

1. 需要真实资金批准
2. 需要 production credential
3. 需要购买外部服务
4. 需要选择无法通过现有架构原则推导的重大业务决策
5. 多个互斥方案具有明显不同长期后果，文档中没有既定选择
6. 外部事实缺失且无法安全模拟

一般代码设计问题：

> 不要停止询问，使用现有架构原则作最佳工程决策并继续。

---

# 29. Default Continuation Behavior

如果用户只说：

```text
继续
```

Codex 应：

1. 读取 `MASTER_IMPLEMENTATION_PLAN.md`
2. 找到 `next_task`
3. 验证 dependency
4. 开始该 WP
5. 验收
6. 更新 Master Plan
7. 如果无外部阻塞，继续下一 READY WP

不需要用户重复粘贴开发指令。

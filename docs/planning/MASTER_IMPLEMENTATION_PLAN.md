# Northstar Quant — 当前实施控制面

> 本文件是仓库唯一实施进度事实来源。
>
> 按用户 2026-08-27 的决定，所有已完成工作包、历史验收记录、P10 证据登记和交易故障矩阵均已直接删除，不做归档。
> 本文件只保留未完成、待验证或外部阻塞的工作。

## 1. 使用规则

每次非 trivial 改动前，必须：

1. 读取根目录 AGENTS.md 与本文件；
2. 查看当前状态、active work package、next task 与 blocked work packages；
3. 执行 git status，不覆盖用户已有修改；
4. 一次只处理一个工作包，并同步代码、测试、配置、schema/migration、CLI、脚本和文档；
5. 先运行聚焦测试，再运行该工作包要求的完整质量门禁；
6. 只有所有验收均通过，才能变更状态与 next task。

状态含义：

- TODO：尚未开始；
- IN_PROGRESS：正在实施；
- VERIFY：实现已提交，仍待完整验收；
- BLOCKED：缺少外部条件或前置依赖。

## 2. 当前状态

~~~yaml
active_phase: P10
active_work_package:
  id: MAINT-WP02
  title: Native Linux PostgreSQL Development / Docker Removal
  status: VERIFY
next_task:
  id: P10-WP08
  title: Platform Production / DR Acceptance
  status: BLOCKED
blocked_work_packages: [P10-WP08, P10-WP09]
~~~

P10-WP08 和 P10-WP09 缺少外部前提。不得以本地 loopback、offline、paper 或 ctp_sim 结果替代这些前提，
也不得为了绕过阻塞而改变 next task。

## 3. 全局安全与质量边界

### 交易与数据

- 默认配置必须保持 NORTHSTAR_BROKER=paper 与 NORTHSTAR_LIVE_TRADING_ENABLED=false。
- offline、paper 与 ctp_sim 的证据不等于真实 broker、真实账户、真实资金或生产准入。
- 不得自动恢复 HALT、连接真实 CTP、创建真实订单或增加未知风险。
- 市场数据、合约映射、账户、持仓、订单、保证金、价格新鲜度或授权未知时，默认 NO NEW RISK。
- 数据库自动化只允许前向 upgrade；不得 delete、truncate、reset、stamp 或 downgrade 数据库、表、schema、
  角色、服务或数据目录。

### 质量门禁

工作包完成前按影响范围运行以下本地门禁：

~~~text
python scripts/dev/run_just.py env-bootstrap
python scripts/dev/run_uv.py run --offline --no-sync pytest
python scripts/dev/run_uv.py run --offline --no-sync ruff check .
python scripts/dev/run_uv.py run --offline --no-sync python scripts/ci/check_mypy_baseline.py check
~~~

## 4. 当前工作包

### P10-WP08 — Platform Production / DR Acceptance

**Status:** BLOCKED

**Dependencies and external prerequisites:**

- 经授权的 Linux production host、root/signer/known_hosts 和受控部署窗口；
- 已批准的 production DR policy、受管 Python 与 production PostgreSQL 运行前提；
- 加密异地备份、WAL/PITR、明确的 RPO/RTO 和受控恢复演练。

**Acceptance:**

- 在获授权的生产环境完成受控部署、健康检查、备份与恢复演练，并保留可审计结果；
- 恢复流程验证 RPO/RTO、加密备份、WAL/PITR 与生产数据保全边界；
- 生产验收不启用真实交易，除非用户另行明确确认 broker、账户、环境与请求动作。

**Fail-closed boundary:** 缺少任一外部前提时保持 NO LIVE ACTION。本地 northstar_test loopback restore drill
不是生产恢复验收。

### P10-WP09 — Authoritative Data & Source Onboarding

**Status:** BLOCKED

**Dependencies and external prerequisites:**

- 数据 license、source authorization 和可审计的授权范围；
- 权威合约、交易日历、费用、保证金和交易规则制品；
- 真实 production PIT/source evidence。

**Acceptance:**

- 每项受管数据和规则具备来源、许可、保留期、available_time 与可重放证据；
- 合约、日历和规则在研究、preflight 与执行入口以 PIT 语义使用；
- 未知、冲突、缺失或未来事实均被拒绝，不得放行历史研究或新风险。

**Fail-closed boundary:** 外部前提不足时保持 NO NEW RISK；fixture、synthetic data 或 ctp_sim
不得伪造数据授权。

### DEV-WP01 — Development Alembic Baseline Consolidation

**Status:** IN_PROGRESS

**Goal:** 保持单个显式 PostgreSQL 当前 schema baseline，同时不为旧开发数据库维护兼容迁移链。

**Acceptance:**

- alembic/versions 只保留无 parent 的 0001_current_schema_baseline，baseline 是静态 schema，
  不调用 ORM metadata；
- fresh isolated PostgreSQL 可 upgrade head、重复升级并通过 alembic check；
- baseline 保留当前 ORM schema、ResearchAgent audit check constraints 以及拒绝 UPDATE、DELETE、TRUNCATE
  的不可变审计触发器；
- 仓库自动化只执行 upgrade head，不增加 reset、drop、truncate、stamp 或 downgrade；
- 旧 revision 的本地库只能由操作者在仓库自动化之外手动重建；
- 不改变领域语义、broker、账户、真实 CTP 或 live trading 能力。

### DEV-WP02 — Four-Tier Storage Boundary

**Status:** VERIFY

**Goal:** 固化四层存储职责，不让任何非权威存储越过核心边界。

**Acceptance:**

- PostgreSQL 是交易、风险、审批、对账、审计和核心运行状态的唯一权威来源；
- Parquet 保存受治理的历史 tick、bars、factors、features、research 与 backtest 制品，并保留版本、
  manifest、hash、lineage、PIT、license 与 retention；
- DuckDB 只读查询受治理的 Parquet，用于可重放历史分析，不得直写核心状态；
- SQLite 仅用于显式、隔离的 local tools，不得成为核心 PostgreSQL 不可用时的 fallback，也不得保存订单、
  成交、持仓、策略状态、风险、审批、对账或审计事实；
- 不削弱 PostgreSQL integration、preflight、风险门禁或数据库保全。

### DEV-WP03 — PostgreSQL Trading-State Authority

**Status:** VERIFY

**Dependencies:** DEV-WP02。

**Acceptance:**

- PaperBrokerAdapter 与 CtpSimBrokerAdapter 不以 state.json 作为订单、成交、持仓、资金或状态机的权威来源；
- broker snapshot、durable intent、ledger、risk 与 reconciliation 保持相同 broker/account scope 的可解释性、
  幂等性与失败关闭；
- schema、repository、Alembic、配置、文档和 unit / PostgreSQL integration / candidate E2E 测试同步；
- 不连接真实 CTP、不使用真实账户、不执行实盘操作；
- 旧开发数据库仍只能由操作者在仓库自动化之外手动重建。

### DEV-WP04 — PostgreSQL Contract Authority

**Status:** TODO

**Dependencies:** DEV-WP02。

**Goal:** 将 Contract Master 与 CTP mapping 迁入具有时间版本、来源证据和 PIT 语义的 PostgreSQL 权威边界。

**Acceptance:**

- 合约、品种、instrument、mapping、费用、保证金和交易规则可按 available_time 重放；
- 旧 YAML 配置路径与新 PostgreSQL 路径不形成双写、兼容 shim 或静默 fallback，所有调用方在同一 breaking
  change 中迁移；
- migration、repository、PIT、preflight、execution integration 测试和文档同步完成；
- 历史研究、preflight 和订单放行不使用未来合约、规则或 mapping。

### MAINT-WP02 — Native Linux PostgreSQL Development / Docker Removal

**Status:** VERIFY

**Dependencies:** DOC-WP08 implementation baseline。

**Goal:** Linux 开发只使用绑定 127.0.0.1:5432 的原生 PostgreSQL，移除 Docker/Compose 与 hosted CI 依赖，
同时绝不损害现有 PostgreSQL 数据或配置。

**Acceptance:**

- 日常开发和工作站初始化没有 Docker/Compose/container PostgreSQL 依赖、安装入口或兼容回退；
- 只有 setup.py --initialize-workstation 的高层 Ubuntu/Debian 初始化入口可安装 postgresql 与
  postgresql-client、启用默认服务并执行前向迁移；
- Windows、其他 Linux、非默认端口和低层入口只验证、复用或失败关闭，不猜测服务配置；
- 客户端、loopback、认证、角色、权限或数据库状态未知时，在迁移前失败关闭；
- 自动化绝不停止、重置或删除 PostgreSQL 服务、角色、数据库、schema 或数据目录，也不覆盖已有角色密码、
  认证规则或服务配置；
- 仅在 northstar 角色缺失时才可由本机 postgres OS 身份创建最小角色；已有角色绝不 ALTER 或覆盖；
- 仓库不保留 GitHub Actions workflow 或 hosted-run 验收依赖，本地质量门禁仍可显式运行；
- 缺少客户端或服务的 Ubuntu/Debian 首次自动安装分支完成实际受控验证；在此之前只以 mock/contract 结果保持 VERIFY。

### DOC-WP08 — VS Code Daily Task Surface

**Status:** VERIFY

**Dependencies:** 工作站初始化与本地工具入口可用。

**Acceptance:**

- .vscode/tasks.json 仅保留按约定顺序的四个安全日常任务：开发初始化、完整测试、质量检查和环境诊断；
- 默认 Test Task 运行完整 test recipe，默认 Build Task 运行 check recipe；
- test-unit、test-backtest、test-cli、bootstrap 和 deploy-preview 保留受控终端入口；
- deploy-preview 始终为 dry-run，不添加 apply、SSH 或真实交易动作；
- 高层 Ubuntu/Debian 初始化可受限安装/启用 loopback PostgreSQL，低层入口仅验证、复用和前向迁移；
- 不改变数据库 schema、broker、生产部署行为或交易安全门禁。

## 5. 状态更新

完成任何工作包时，先满足其验收标准并运行相应质量门禁，再更新本文件的状态、active work package、
next task 与 blocked work packages。不得重新创建已删除的完成态规划、证据或历史归档。

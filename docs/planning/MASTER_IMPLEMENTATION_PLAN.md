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
active_phase: P12
active_work_package: BT-01.1
next_task: "BT-01.1 — pin Backtest CI and isolated build toolchain"
blocked_work_packages: [P10-WP08, P10-WP09, MAINT-WP02]
~~~

P11 已完成，且按本文件“只保留未完成、待验证或外部阻塞工作”的规则不再保留其完成态条目。

P12 本轮只授权一个跨仓库工作包：BT-01.1。未被明确列入当前工作包的项目不得在本轮开始；
其中包括 BT-02 和 FL-01。

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

## 4.## 4. 当前工作包

### BT-01.1 — Pin Backtest CI and Isolated Build Toolchain

**Status:** IN_PROGRESS

**Owning repository:** [`isqiwen/quant-backtest`](https://github.com/isqiwen/quant-backtest)

**Tracking issue:** [BT-01.1 / #16 — Pin CI and isolated build toolchain](https://github.com/isqiwen/quant-backtest/issues/16)。

**Dependencies:**

- BT-01 已通过并合并：[Backtest #15](https://github.com/isqiwen/quant-backtest/pull/15)。

**Goal:** 消除 BT-01 合并后审查发现的可复现性 P1：同一源提交的 CI 和隔离 PEP 517 构建不得
因可变 action tag、工具版本或 backend 依赖解析而静默改变。

**Scope:**

- 以完整 commit SHA 固定 CI action；
- 固定 `uv` 和 CPython 3.12 patch 版本；
- 固定 Hatchling 版本，并以完整 hash closure 约束隔离构建依赖；
- 对固定项和受约束构建路径加入回归测试、文档和完整质量验证。

**Out of scope:**

- Backtest v1 contracts、Data Portal、StrategyPort、模拟运行时、订单/成交/账本、因子逻辑、
  broker、paper/live 连接或任何交易权限；
- FL-01 或其他并行工作包。

**Acceptance:**

- CI 不再解析可变 action tag、浮动 `uv` 版本或浮动 Python patch；
- PEP 517 backend 及其 transitive build dependencies 均由 hash constraints 覆盖；
- clean locked install、format/lint、strict type check、tests 与 constrained wheel build 全部通过；
- 文档保持“未实现模拟/公共 Backtest contract”的真实状态。

**Fail-closed boundary:** 固定项、hash constraint 或受约束构建任一缺失、漂移或失效时，不得将
构建结果作为可复现 Backtest 基线，也不得开始 BT-02 的公共契约发布。

**Phase progress:** P12 当前承诺范围为 0/1；BT-01.1 是唯一 active work package。

 暂缓的外部工作包

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

### MAINT-WP02 — Native Linux PostgreSQL Development / Docker Removal

**Status:** BLOCKED

**Dependencies:** 无待办开发依赖；仍需外部首次安装验证环境。

**Goal:** Linux 开发只使用绑定 127.0.0.1:5432 的原生 PostgreSQL，移除 Docker/Compose 与 hosted CI 依赖，
同时绝不损害现有 PostgreSQL 数据或配置。

**Acceptance:**

- 日常开发和工作站初始化没有 Docker/Compose/container PostgreSQL 依赖、安装入口或兼容回退；
- 只有 setup.py --initialize-workstation 的高层 Ubuntu/Debian 初始化入口可安装 postgresql 与
  postgresql-client、启用默认服务并执行前向迁移；
- 非 Ubuntu/Debian Linux、非默认端口和低层入口只验证、复用或失败关闭，不猜测服务配置；
- 客户端、loopback、认证、角色、权限或数据库状态未知时，在迁移前失败关闭；
- 自动化绝不停止、重置或删除 PostgreSQL 服务、角色、数据库、schema 或数据目录，也不覆盖已有角色密码、
  认证规则或服务配置；
- 仅在 northstar 角色缺失时才可由本机 postgres OS 身份创建最小角色；已有角色绝不 ALTER 或覆盖；
- 仓库不保留 GitHub Actions workflow 或 hosted-run 验收依赖，本地质量门禁仍可显式运行；
- 缺少客户端或服务的 Ubuntu/Debian 首次自动安装分支完成实际受控验证。

**External prerequisite:** 需要一台经授权、可丢弃、未安装 PostgreSQL/client 的 Ubuntu/Debian 主机或 VM，
由操作者在其上执行高层初始化并确认包安装、systemd、loopback、客户端、最小角色、两个数据库和 forward migration。
不得以 Docker、hosted CI 或卸载/停止当前主机 PostgreSQL 替代该验证。

## 6. 状态更新

完成任何工作包时，先满足其验收标准并运行相应质量门禁，再更新本文件的状态、active work package、
next task 与 blocked work packages。不得重新创建已删除的完成态规划、证据或历史归档。

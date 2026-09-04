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
active_phase: P14
active_work_package: BT-02.2
next_task: "BT-02.2 — Publish BacktestResult and Artifact-Integrity Contract"
blocked_work_packages: [P10-WP08, P10-WP09, MAINT-WP02]
~~~

P14 本轮只授权一个跨仓库工作包：BT-02.2。未被明确列入当前工作包的项目不得在本轮开始；
其中包括 BT-02 的其余切片、FL-01 和其他并行工作包。

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

### BT-02.2 — Publish BacktestResult and Artifact-Integrity Contract

**Status:** IN_PROGRESS

**Owning repository:** [`isqiwen/quant-backtest`](https://github.com/isqiwen/quant-backtest)

**Parent delivery:** [BT-02 / #3 — Publish Backtest v1 and StrategyPort contracts](https://github.com/isqiwen/quant-backtest/issues/3)。

**Tracking issue:** [BT-02.2 / #20 — Publish BacktestResult v1 and artifact-integrity contract](https://github.com/isqiwen/quant-backtest/issues/20)。

**Dependencies:**

- BT-01 已通过并合并：[Backtest #15](https://github.com/isqiwen/quant-backtest/pull/15)；
- BT-01.1 的可复现工具链加固已通过并合并：[Backtest #17](https://github.com/isqiwen/quant-backtest/pull/17)；
- BT-02.1 的闭合 `BacktestRunSpec v1` 已通过并合并：[Backtest #19](https://github.com/isqiwen/quant-backtest/pull/19)；
- 已发布的 `quant-data-hub@v0.9.0` 只能作为可选兼容性夹具；本工作包不直连其数据库、也不把它写死为唯一受支持发布。

**Goal:** 将 XL 的 BT-02 拆为可独立验收的下一个契约切片。只发布可复算、不可变且不可执行的模拟结果
身份与制品完整性词汇，不执行模拟、写入制品或发布结果。

**Scope:**

- 发布随 wheel 分发、带版本的 `BacktestResult` v1 JSON Schema、严格 Python 模型、
  duplicate-key-safe JSON 解析、canonical JSON 与 SHA-256 result content hash；
- 将结果固定绑定到一个 `BacktestRunSpec` hash、engine release/semantics pin、终态 status/failure
  词汇和闭合的 artifact descriptor 集合；
- artifact descriptor 必须显式包含 kind、schema version、content/logical hash、byte/row count 与稳定排序，
  不得包含 path、URL、可变引用或存储能力；
- 提供正向、负向、不兼容 major、canonical-hash 与 clean-wheel 资源夹具，并保留禁止 participant execution、
  simulation、data/storage/network 和 broker 的源码边界测试。

**Out of scope:**

- simulation coordinator、clock、Data Portal、participant execution、订单/成交/账本/account 行为、
  artifact store、result API、网络、数据库、broker、paper/live 连接或任何交易权限；
- `StrategyPort` 定义，或 Factor Lab / Strategy Lab source import、participant execution，
  或对已发布 `FactorPackage` 的依赖；
- BT-02 父项完成宣称、BT-02 的其余切片、FL-01 或其他并行工作包。

**Acceptance:**

- 任一 semantic field 改变都会改变 result content hash；仅被 schema 明确标识为 non-semantic 的 annotation
  改变不会影响它；
- 缺失或被篡改的 RunSpec、engine、status/failure、artifact identity、schema version、hash、count 或排序全部
  以稳定 reason code fail closed；
- `SUCCEEDED` 与非成功终态遵守互斥字段规则；结果不得暗示 research promotion 或 live authority；
- clean installed wheel 可读取公开 schema 与 fixtures；锁定安装、format/lint、strict type check、tests 与受限 wheel
  build 全部通过；
- 不得执行 participant、启动 simulation、访问 data/storage/network，或暴露 broker capability。

**Fail-closed boundary:** 任一 RunSpec binding、engine pin、terminal state、failure vocabulary、artifact identity、
schema/semantic version、canonicalization rule、hash、count 或排序缺失、漂移或不兼容时，不得构造有效
BacktestResult，不得暗示成功，也不得启动模拟、制品写入或任何外部访问。

**Phase progress:** P14 当前承诺范围为 0/1；BT-02.2 是唯一 active work package。

## 5. 暂缓的外部工作包

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

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
active_phase: P13
active_work_package: BT-02.1
next_task: "BT-02.1 — Publish Closed BacktestRunSpec Contract Foundation"
blocked_work_packages: [P10-WP08, P10-WP09, MAINT-WP02]
~~~

上一项已完成工作已按本文件“只保留未完成、待验证或外部阻塞工作”的规则移除，不保留完成态历史。

P13 本轮只授权一个跨仓库工作包：BT-02.1。未被明确列入当前工作包的项目不得在本轮开始；
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

### BT-02.1 — Publish Closed BacktestRunSpec Contract Foundation

**Status:** IN_PROGRESS

**Owning repository:** [`isqiwen/quant-backtest`](https://github.com/isqiwen/quant-backtest)

**Parent delivery:** [BT-02 / #3 — Publish Backtest v1 and StrategyPort contracts](https://github.com/isqiwen/quant-backtest/issues/3)。

**Tracking issue:** [BT-02.1 / #18 — Publish closed BacktestRunSpec v1 contract](https://github.com/isqiwen/quant-backtest/issues/18)。

**Dependencies:**

- BT-01 已通过并合并：[Backtest #15](https://github.com/isqiwen/quant-backtest/pull/15)；
- BT-01.1 的可复现工具链加固已通过并合并：[Backtest #17](https://github.com/isqiwen/quant-backtest/pull/17)；
- 已发布的 `quant-data-hub@v0.9.0` 只能作为可选兼容性夹具；本工作包不直连其数据库、也不把它写死为唯一受支持发布。

**Goal:** 将 XL 的 BT-02 拆为可独立验收的首个契约切片。只发布可复算的闭合模拟输入身份，
不执行模拟，也不发布结果或策略运行时。

**Scope:**

- 发布随 wheel 分发、带版本的 `BacktestRunSpec` v1 JSON Schema、严格 Python 模型、
  duplicate-key-safe JSON 解析、canonical JSON 与 SHA-256 `run_spec_hash`；
- 固定 engine 版本/字节摘要、Data Hub snapshot/manifest/export 引用、participant/runtime/parameter
  制品、time/calendar/universe mapping、initial account profile、全部 model/parameter、random seed、
  output profile/resource bounds；
- `risk_policy` 必须是显式联合：`{ "kind": "NONE" }` 或完整固定的 policy/evaluator 引用；
- 提供正向、负向、不兼容 major、canonical-hash 与 wheel-packaging 夹具，并保留禁止网络、broker、
  wall-clock 与全局 RNG 的源码边界测试。

**Out of scope:**

- `BacktestResult`、`StrategyPort`、Data Portal、外部或本地数据读取、simulation clock/engine、
  订单/成交/账本/account runtime、网络、数据库、broker、paper/live 连接或任何交易权限；
- Factor Lab 或 Strategy Lab source import、participant execution，或对已发布 `FactorPackage` 的依赖；
- BT-02 父项完成宣称、BT-02 的其余切片、FL-01 或其他并行工作包。

**Acceptance:**

- 任一 semantic field 改变都会改变 `run_spec_hash`；仅被 schema 明确标识为 non-semantic 的 annotation
  改变不会影响它；
- `latest`、branch、短/缺失 digest、未知字段、重复 JSON key、binary float/non-finite value、非 UTC 或倒置时间、
  缺失 model pin、缺失/伪造的 risk policy 全部 fail closed；
- caller-owned inert participant reference 无需 import 或 execute lab source 即可验证；
- clean installed wheel 可读取公开 schema 与 fixtures；锁定安装、format/lint、strict type check、tests 与 wheel
  build 全部通过；
- 不得出现模拟成功、策略 promotion、live authority、外部访问或 broker capability 的暗示或实现。

**Fail-closed boundary:** 任一 identity、content hash、schema/semantic version、canonicalization rule、
model pin 或 risk policy mode 缺失、漂移或不兼容时，不得构造有效 RunSpec、不得报告成功 BacktestResult，
也不得启动模拟或任何外部访问。

**Phase progress:** P13 当前承诺范围为 0/1；BT-02.1 是唯一 active work package。

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

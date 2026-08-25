# 测试说明

测试按**领域优先、验证类型次级**组织。测试路径本身表达被验证的业务边界，避免把不相干
领域的测试混在全局 `unit/`、`integration/` 或 `contract/` 目录中。

```text
tests/
├── architecture/          依赖方向、循环、分层与公开 API 边界
├── data/
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
├── foundation/
│   ├── unit/
│   ├── integration/
│   └── contract/
├── e2e/                   跨领域业务闭环
├── fixtures/              可复用的非 golden 输入样本
├── golden/                跨领域冻结期望输出
├── helpers/               测试工厂、数据库夹具与路径工具
└── conftest.py
```

## 一键运行

Windows 与 Linux 使用相同的命令面：

```bash
just dev-bootstrap   # 仅预览 uv、just、Git 安装计划；不执行安装
just env-bootstrap   # 唯一允许 materialize 锁定依赖的边界
just dev-setup       # 创建/迁移唯一活动配置
just dev-postgres    # 仅在需要 PostgreSQL/integration 时启动本地数据库并迁移
```

Python 3.11+ 需要预先安装。没有 `uv` 或 `just` 时，先用
`python scripts/dev/setup.py --bootstrap-tools` 预览工具计划；Docker 仅在确有数据库需求时加上
`--install-docker`。两者默认不安装、不启动 Docker；实际执行必须显式提供
`--apply --confirm-tool-install YES`，Docker 还需 `--confirm-docker-install YES`。

`dev-setup` 不会启动 Docker；`dev-postgres` 才会启动并复用本地 Docker PostgreSQL，并确保
`northstar` 与 `northstar_test` 存在且只升级到 Alembic head。两者都强制 `paper`、禁用 live，不会下载
市场数据或提交订单；仓库自动化绝不删除或清空数据库、表、schema 或 Docker 数据卷。
当前开发期仅支持完整基线 `0001_current_schema_baseline`；带有旧 revision 的本地库必须由操作者在仓库自动化之外手动重建，
测试或开发脚本不会 reset、stamp 或清理它。
没有安装 `just` 时，先运行 `python scripts/ci/bootstrap_pep517.py --profile development`，再使用
`uv run --offline --no-sync python scripts/dev/setup.py --initialize-config`，并按需加入
`--with-postgres --migrate`。

## 手动命令

运行 PostgreSQL integration 或完整 pytest 前，确保 Docker 已启动，`.env` 中的 `POSTGRES_PASSWORD`
非空，并存在完整的活动应用配置 `configs/app.yaml`。开发初始化入口会自动创建它；若只运行手动命令且
该文件缺失，先执行：

```powershell
Copy-Item configs/app.example.yaml configs/app.yaml
```

Git Bash/Linux 使用 `cp configs/app.example.yaml configs/app.yaml`。示例文件本身不会被
应用读取；测试与应用都只读取活动文件。数据库测试使用隔离的 `northstar_test`。数据库删除或清空只能
由用户在仓库自动化之外手动执行。

```powershell
# 全量测试
uv run --offline --no-sync pytest

# 按通用验证类型运行
uv run --offline --no-sync pytest -m unit
uv run --offline --no-sync pytest -m integration
uv run --offline --no-sync pytest -m contract
uv run --offline --no-sync pytest -m e2e

# 按领域或专项类型运行
uv run --offline --no-sync pytest tests/research
uv run --offline --no-sync pytest -m regression
uv run --offline --no-sync pytest -m statistical
uv run --offline --no-sync pytest -m scenario
uv run --offline --no-sync pytest -m simulation
uv run --offline --no-sync pytest -m failure

# 聚焦单文件或单个用例
uv run --offline --no-sync pytest tests/research/unit/test_futures_daily_backtest.py -q
uv run --offline --no-sync pytest tests/research/integration/test_backtest_run_workflow.py::test_actual_daily_backtest_run_writes_one_auditable_report_artifact -q

# 静态质量门禁
uv run --offline --no-sync ruff check .
uv run --offline --no-sync python scripts/ci/check_mypy_baseline.py check
```

Windows 工作站的日常 unit、backtest、CLI 与开发脚本测试不依赖 Bash、Docker 服务或 systemd。
Linux CI 额外运行完整 pytest、部署 shell 契约和 PostgreSQL integration；这是 Linux 生产目标职责，
不是 Windows 开发机职责。

## 编写测试

- 将测试放入拥有被测行为的领域；跨领域闭环才使用 `e2e/`；
- 纯计算、局部配置校验放在领域 `unit/`；多个模块、文件制品或 PostgreSQL 协作放在领域 `integration/`；
- 依赖边界、CLI、迁移、文档和部署安全门禁放在 `architecture/` 或 `foundation/contract/`；
- Intelligence 语义输出使用 `golden/`；P10 六商品语料位于 `tests/intelligence/golden/`，其
  `fixture_only` 来源、crosswalk 和 Feature-definition handoff 绝不能当作授权数据、运行时配置或交易映射；
- P10-WP03 的 companion replay fixture 位于 `tests/research/golden/`：它只能以同一 handoff 的
  `available_at <= decision_at` 可见性和决策后 synthetic outcome 形成 `RESEARCH_ONLY` Card；不可伪造
  `FeatureValue`、DatasetVersion、市场收益、candidate、target 或订单；
- P10-WP04 的 `tests/portfolio_risk/golden/p10_canonical_multi_strategy_composition_v1.json` 冻结
  strict typed multi-strategy composition 的 allocation、source contribution、`PortfolioTarget v2`
  `composition_hash` 与 evidence hash；它只证明 P3 组合可重放，绝不构成风险审批、ExecutionPlan、订单或
  broker capability；
- P10-WP05 的 `tests/portfolio_risk/golden/p10_portfolio_risk_approval_v1.json` 是
  `fixture_only` 的 P3 回归样本：它冻结组合、account/instrument/risk-state/policy identity、派生 exposure、
  九项 limit checks、全部七种 stress checks、具名 attestation 与 account-scoped
  `ApprovedPortfolioTarget v2`。所有 execution/broker eligibility 固定为 false；它不代表真实账户、真实保证金、
  经授权 market data、CTP 或实盘权限。
- P10-WP05 的 authority 与失败关闭覆盖分布在 `tests/application/unit/test_portfolio_risk_authority.py`、
  `tests/application/unit/test_execution_provenance_preflight.py`、
  `tests/application/integration/test_p10_portfolio_risk_authority_candidate.py` 和
  `tests/trading_execution/integration/test_p10_portfolio_risk_authority_reconciliation.py`：非提交的 P8 receipt
  始终不携带交易能力；只有 session-backed CTP-sim candidate 路径在重放完整 simulated profile policy、CTP-sim
  state、持久化且 hash-verified 的 `NORMAL` reconciliation state 与精确 contract-rule 后，才可能把该证据推进至
  plan、durable intent 与 simulator 路径。缺失、过期、篡改、非 NORMAL 或 drift 均失败关闭。该组测试不连接真实
  CTP，也不授权交易；
- P10-WP05 的 durable manual-risk approval 覆盖位于
  `tests/application/unit/test_portfolio_risk_manual_approval.py`、
  `tests/foundation/integration/test_portfolio_risk_manual_approval_repository.py`、
  `tests/architecture/test_portfolio_risk_manual_approval_boundaries.py` 与 candidate unit tests：它验证
  `RiskApprovalAttestation` 只是 P3 claim、grant 只保存 `verifier_receipt_hash`、以及 isolated PostgreSQL
  CTP-sim candidate 会在 prepare、prevalidate 和最终 adapter fence 精确重检 scoped hashes。测试 fake issuer
  仅在 `tests/helpers/manual_risk_approval.py`，不属于 production package surface；该 record 的状态是
  `VERIFIED_SIMULATION`，并不证明人工身份。外部已认证人工 issuer 与 grant 表 PostgreSQL 读写权限分离仍为
  `BLOCKED_EXTERNAL`，因此测试和 production public composition 都不提供真实 CTP 或 live authorization；
  Research 稳定性和点时正确性分别使用 `regression/`、`statistical/`；
- 风险压力使用 `scenario/`；执行模拟与失败关闭分别使用 `simulation/`、`failure/`；
- 优先使用 `postgresql_engine` 或 `postgresql_session_factory` fixture；只有并发测试才直接使用
  `tests.helpers.database` 工厂。

不要通过 SQLite、跳过 preflight、降低风险门槛或修改真实交易开关来让测试通过。新的配置、
数据制品、回测口径或执行行为必须同步增加覆盖。

# Northstar Quant

面向中国商品期货的个人量化研究工程。项目把**数据制品、策略目标、回测、报告和执行基础设施**
放在同一条可审计链路中，但当前仍以离线研究和本地语义仿真为边界。

> 安全状态：默认 `paper`；`NORTHSTAR_LIVE_TRADING_ENABLED=false`；没有 production
> 画像，也没有真实 CTP 报单适配器。任何真实资金、供应商采购或外部账户操作均不在本仓库
> 的默认流程内。

## 六大领域与闭环

工程按六个顶层领域组织：Data Platform、Intelligence、Research & Strategy、Portfolio & Risk、
Trading & Execution，以及 Platform Foundation。市场事实经数据平台进入研究和风险决策；受约束
目标才可进入执行，订单、成交和结算回报再回流为数据事实。

完整目录、依赖方向、当前实现状态和安全边界见[架构总览](docs/01_架构总览.md)。`Intelligence`
已将受控的 Event、evidence、完整 ontology、mechanism、impact 和 market context 冻结为 P4 的
`intelligence_feature_projection_v3` 不可变静态 projection。每条 Event evidence 都绑定其精确的 P1
raw source artifact、document ID、content SHA-256 和 UTF-8 span；每个 MarketContext 都绑定恰好一个
immutable normalized P1 artifact、context `DatasetVersion`/receipt，以及完整闭合行内容的 commitment。
只有 `application/` composition root 可以在**同一**不可变制品库中重放并逐条验证这些 source receipt/
raw artifact 绑定，并验证唯一 context artifact 的 receipt、全行内容 commitment 和 PIT，再经受控 P1 发布为
闭合 schema 的 `DatasetVersion`/PIT。P2 只消费由此形成、绑定精确 `FeatureVersion` 的
`FeatureLineage`/`FeatureBackfill`、受限分数与缺失原因及狭窄 hash-only provenance；它不会取得原始
Document、URL、source/context payload 或 LLM rationale。该链路仍只供研究且不可交易：不自动提升
Research Decision，不生成 `PortfolioTarget`、`ExecutionPlan` 或 broker 行为；授权、身份、语义、receipt
或 PIT 未知时一律失败关闭。

P8-WP03 另在 `application/research_strategy_activation.py` 建立 Research candidate→`StrategyTarget` 的
人工激活边界。它只接受带完整 PASS 证据且已具名批准为 `CANDIDATE` 的 Research Card/Decision、精确的
`ExperimentSpec`/`ExperimentRun`、冻结的目标提案，以及独立具名的
`HumanStrategyTargetActivationApproval`；输出的 `StrategyTarget` v2 必带不可变
`StrategyTargetActivationRef`。该回执仍保留 `STATIC_REPRODUCIBILITY_ONLY` 与
`decision_time_safe=false`，固定 `eligible_for_trading=false`，不等于 `PortfolioTarget`、风险批准、
`ExecutionPlan` 或 broker 权限。P8-WP04 的
`application/execution_provenance_preflight.py` 现会重放原始 activation request，而不信任 receipt 或
activation hash；它精确绑定 P3 portfolio/risk evidence、PIT data manifest、账户/持仓/报价、时间有效的
实际合约规则，并在内部重建 P5 execution plan、runtime-risk 与 preflight。它只产生短时、hash-bound、
`ctp_sim` evidence receipt，所有 `eligible_for_ctp_sim` / `eligible_for_trading` / `eligible_for_live` 都固定为
`false`，自身不提交 broker 订单。P8-WP05 的
`application/ctp_sim_candidate_execution.py` 是唯一后续 CTP-sim candidate 提交组合入口：它重新取得账户/
报价并重放原始 request，只派生精确 canonical order；每个 commitment 必须与 durable order intent 同一
PostgreSQL 事务一次性消费。只有该 composition root 能签发不透明的 `CtpSimSubmissionAuthority`；结构相同的
no-op guard 不能解锁 durable 或模拟柜台。模拟柜台在自身状态锁内重验 consumption、真实 state/quote 基线，
批次每个已提交 leg 才能推进该基线；无 authority 的 raw CTP-sim、direct durable 和旧 live-service CTP-sim
submit path 都拒绝。对账不接受调用方提供的 snapshot，发现没有该消费的 simulator 订单或成交会进入 `HALT`。因此
`Portfolio/Risk→CTP sim` seam 已在隔离 PostgreSQL 的真实 candidate→activation→risk→provenance→
durable→fill→reconciliation E2E 中标记为 `VERIFIED`，但仍只是 non-tradable candidate evidence，绝不构成
真实 CTP、真实账户或自动交易授权。P10-WP05 的 durable manual-risk approval record 另有收窄的
`VERIFIED_SIMULATION` 语义：P3 `RiskApprovalAttestation` 仍只是可重放的 claim，不是人工身份凭据；只有
session-backed、隔离 PostgreSQL 的 CTP-sim candidate 才能读取精确 scope 的持久化 grant。candidate 会在
prepare、prevalidate 和 adapter 的最终锁内 fence 分别重新派生并精确匹配 profile/broker/account、P3
approval/evidence、authority/policy/reconciliation、binding 与 durable record hash。生产 public composition
没有签发 approval 的 API。外部已认证人工 issuer 与 PostgreSQL 权限分离（candidate 对该 grant 表只读、独立
issuer 才能写入）仍为 `BLOCKED_EXTERNAL`；在其凭据和部署角色就绪前不存在生产签发路径，也不构成真实 CTP 或
live authorization。

## 项目适合做什么

- 用连续合约快速研究，再用实际合约日线验证保证金、费用、换月和流动性约束；
- 在必要时用分钟数据回放订单生命周期与部分成交；
- 生成带数据、配置、目标和代码指纹的回测报告；
- 在本地 `ctp_sim` 演练期货合约、保证金、今昨仓、回报和恢复语义。

它不保证策略收益，也不代表已具备真实 CTP、期货公司模拟前置或真实账户交易资格。

## 本地开发与测试

Windows 与 Linux 都是一级开发工作站。跨平台的研究核心、CLI、开发检查、制品构建和部署控制面
都由 Python 驱动；`systemd`、服务、调度器、worker、监控目标端和未来实时交易只属于 Linux
服务器。Python 3.11+ 是唯一必须由开发者自行提供的前置条件；`uv`、[`just`](https://github.com/casey/just)
与 Git 可通过受确认的 bootstrap 计划安装。Docker Desktop（Windows）或 Docker Engine + Compose v2（Linux）
只在需要本地 PostgreSQL/integration 测试时才需要。

首次在新机器上，先只读预览缺失工具计划：

```bash
python scripts/dev/setup.py --bootstrap-tools
# 只有确实需要本地数据库时，才把 Docker 纳入计划：
python scripts/dev/setup.py --bootstrap-tools --install-docker
```

预览不会执行安装、接受许可、启动 Docker/WSL、修改用户组或下载数据。确认计划后，普通工具安装必须显式使用
`--apply --confirm-tool-install YES`；Docker 还必须追加 `--confirm-docker-install YES`。Windows 使用 `winget`；
Linux 仅正式支持 Ubuntu/Debian，其他发行版会失败关闭。`ssh`/`ssh-keygen` 仅用于部署控制面，检查会报告它们，
但 bootstrap 不会自动安装。

首次先把锁定的、受审计的构建输入 materialize 到一个全新本地虚拟环境：

```bash
just env-bootstrap
```

它先离线校验依赖策略、锁文件和机密边界；普通 wheel 只能由锁文件 materialize，唯一获准的
source-only 包会按锁定 URL、大小和 SHA-256 下载后离线构建。development 会先在同级 staging venv 完成
全部校验后才原子切换 `.venv`；若有编辑器占用旧环境，会保留旧环境并失败关闭。完成后，所有 `uv run` 命令都使用
`--offline --no-sync`，不会隐式解析、下载或构建新依赖。随后创建安全的本地活动配置：

```bash
just dev-setup
```

需要本地 PostgreSQL、迁移和隔离数据库测试时，再显式启动它：

```bash
just dev-postgres
```

没有安装 `just` 时，先依次运行 `python scripts/ci/check_dependency_policy.py`、
`uv lock --check --offline`、`python scripts/ci/check_secrets.py` 和
`python scripts/ci/bootstrap_pep517.py --profile development`，再运行
`uv run --offline --no-sync python scripts/dev/setup.py --initialize-config`；加入
`--with-postgres --migrate` 才会启动 Docker PostgreSQL。初始化会创建或结构化迁移唯一的本地 `.env` 与 `configs/app.yaml`，保持
`paper`、禁用 live；不会下载市场数据、启动调度器或调用真实交易。后续只编辑这两份活动配置，
不要把 `.env.example` 或 `configs/app.example.yaml` 当作运行时配置。已有疑似生产、非 paper、live、
kill-switch 或外部数据库 `.env` 不会被自动覆盖；完成审阅后才可显式加
`--confirm-reset-local-dev-config YES` 重置为本地开发配置。

数据库保全是硬性边界：仓库自动化绝不删除或清空数据库、表、schema 或 Docker 数据卷；
`dev-postgres` 只复用现有本地数据并升级至 Alembic head。数据库删除或清空只能由用户在仓库自动化
之外手动执行。

常用的手动质量检查：

```powershell
just test-unit
just test-backtest
just test-cli
# 需要隔离 PostgreSQL；只重放 P8 离线 / paper / ctp_sim 候选证据，拒绝 live / production。
just candidate-acceptance
just check
```

Linux CI 会另外运行完整 pytest、PostgreSQL integration、Alembic 迁移和部署契约；Windows CI
只验证单元/回测、CLI 与跨平台开发脚本，不要求 Windows 承担 Linux 服务职责。

测试层级、数据库隔离与聚焦命令见 [测试说明](tests/README.md)。

## 第一次离线回测

完成本地开发初始化后，可用公开连续合约数据跑通安全的研究闭环：

```powershell
uv run --offline --no-sync northstar data download --profile cn_futures_daily_trend_offline
uv run --offline --no-sync northstar data validate --profile cn_futures_daily_trend_offline
uv run --offline --no-sync northstar backtest run portfolio --profile cn_futures_daily_trend_offline
```

报告会写入 `reports/backtest/`，包含 `report.md`、`report.json` 和不可变的
`manifest.json`。请先按 [第一个策略与回测教程](docs/00_第一个策略与回测教程.md)
实现自己的策略；它明确说明目标权重、显式平仓、测试、数据和结果分析。

低频候选策略的正式研究路径是：连续合约探索 → 实际合约日线回测 →（仅在执行细节改变
结论时）分钟回放。具体数据契约与三类回测器见
[期货回测器说明](docs/04_期货回测器说明.md)。

## 配置与数据治理

运行时环境变量、交易画像、策略默认参数、数据源、品种池和研究准入政策职责不同，不应
互相替代。配置入口和安全默认值见 [配置说明](docs/02_配置说明.md)。

当前公开 AKShare 和本地导入数据只能用于探索或工程验收。商业供应商、授权边界、核心
品种、样本外阈值和候选策略准入结论见
[研究准入政策与数据治理](docs/09_研究准入政策与数据治理.md)。其状态是
`procurement_pending` / `pending_owner_approval`，默认失败关闭。

## 当前运行边界

- `offline` 画像只做研究，不能提交订单；
- `ctp_sim` 是本地语义仿真，不连接期货公司；
- `live` 子命令受画像、数据、preflight、风险门禁和 kill switch 保护；当前没有
  production 画像，因此调度器会失败关闭；
- Linux 部署默认只运行 `health` 服务，详见
  [Linux 一键部署](docs/07_Linux一键部署.md)。

## 文档

完整阅读路径与唯一权威文档见 [文档导航](docs/README.md)。特别是：

- [Codex 主实施计划](docs/planning/MASTER_IMPLEMENTATION_PLAN.md)
- [架构总览](docs/01_架构总览.md)
- [执行与安全边界](docs/03_执行与安全边界.md)
- [平台安全与审计](docs/platform_security_audit.md)
- [项目主规划与实施状态](docs/08_项目主规划与实施状态.md)
- [研究准入政策与数据治理](docs/09_研究准入政策与数据治理.md)

## License

仓库当前未附带单独许可证文件。如需开源发布，应先补充明确的 `LICENSE`。

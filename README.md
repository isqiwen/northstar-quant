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

完整目录、依赖方向、当前实现状态和安全边界见[架构总览](docs/01_架构总览.md)。其中
`Intelligence` 目前仅为预留领域，现有研究不会把未实现的情报推理当作可用信号。

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
Linux 仅正式支持 Ubuntu/Debian，其他发行版会失败关闭。`ssh`/`scp` 仅用于部署控制面，检查会报告它们，
但 bootstrap 不会自动安装。

首次创建安全的本地活动配置并同步锁定依赖：

```bash
just dev-setup
```

需要本地 PostgreSQL、迁移和隔离数据库测试时，再显式启动它：

```bash
just dev-postgres
```

没有安装 `just` 时，可直接运行
`uv run python scripts/dev/setup.py --initialize-config`；加入 `--with-postgres --migrate` 才会
启动 Docker PostgreSQL。初始化会创建或结构化迁移唯一的本地 `.env` 与 `configs/app.yaml`，保持
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
just check
```

Linux CI 会另外运行完整 pytest、PostgreSQL integration、Alembic 迁移和部署契约；Windows CI
只验证单元/回测、CLI 与跨平台开发脚本，不要求 Windows 承担 Linux 服务职责。

测试层级、数据库隔离与聚焦命令见 [测试说明](tests/README.md)。

## 第一次离线回测

完成本地开发初始化后，可用公开连续合约数据跑通安全的研究闭环：

```powershell
uv run northstar data download --profile cn_futures_daily_trend_offline
uv run northstar data validate --profile cn_futures_daily_trend_offline
uv run northstar backtest run portfolio --profile cn_futures_daily_trend_offline
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
- [项目主规划与实施状态](docs/08_项目主规划与实施状态.md)
- [研究准入政策与数据治理](docs/09_研究准入政策与数据治理.md)

## License

仓库当前未附带单独许可证文件。如需开源发布，应先补充明确的 `LICENSE`。

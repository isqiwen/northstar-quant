# Northstar Quant

面向中国商品期货的个人量化研究工程。项目把**数据制品、策略目标、回测、报告和执行基础设施**
放在同一条可审计链路中，但当前仍以离线研究和本地语义仿真为边界。

> 安全状态：默认 `paper`；`NORTHSTAR_LIVE_TRADING_ENABLED=false`；没有 production
> 画像，也没有真实 CTP 报单适配器。任何真实资金、供应商采购或外部账户操作均不在本仓库
> 的默认流程内。

## 项目适合做什么

- 用连续合约快速研究，再用实际合约日线验证保证金、费用、换月和流动性约束；
- 在必要时用分钟数据回放订单生命周期与部分成交；
- 生成带数据、配置、目标和代码指纹的回测报告；
- 在本地 `ctp_sim` 演练期货合约、保证金、今昨仓、回报和恢复语义。

它不保证策略收益，也不代表已具备真实 CTP、期货公司模拟前置或真实账户交易资格。

## 本地开发与测试

前置条件：`uv`、Docker Desktop（或 Docker Engine）和 Docker Compose。Windows 还应安装
Git for Windows；部署脚本契约测试会自动定位其中的 Git Bash。

macOS/Linux：

```bash
scripts/setup_dev.sh
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
# 编辑 .env，将 POSTGRES_PASSWORD 设为仅供本机开发使用的非空值
.\scripts\setup_dev.ps1
```

两个入口都会同步锁定依赖、启动本地 Docker PostgreSQL、迁移 `northstar` 与
`northstar_test`、执行健康检查、完整测试和 Ruff；不会下载市场数据、启动调度器或调用
真实交易。它们还会在首次运行时从 `configs/app.example.yaml` 创建唯一会被程序读取的
本地 `configs/app.yaml`；后续只编辑这一份活动配置。

常用的手动质量检查：

```powershell
uv run pytest
uv run ruff check .
uv run python scripts/check_mypy_baseline.py check
```

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

- [架构总览](docs/01_架构总览.md)
- [执行与安全边界](docs/03_执行与安全边界.md)
- [项目主规划与实施状态](docs/08_项目主规划与实施状态.md)
- [研究准入政策与数据治理](docs/09_研究准入政策与数据治理.md)

## License

仓库当前未附带单独许可证文件。如需开源发布，应先补充明确的 `LICENSE`。

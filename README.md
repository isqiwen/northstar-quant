# Northstar Quant

> 面向个人量化研究者的现代化中文项目骨架，覆盖研究、可信回测、实盘执行、风控、监控、报告与审计闭环。

## 项目定位

Northstar Quant 的目标不是提供“开箱即用的券商生产系统”，而是提供一套适合个人长期维护、可逐步扩展到真实交易环境的工程骨架。

它强调：

- 研究与实盘解耦
- 配置与代码解耦
- 数据与元数据分离
- 关键动作可追踪、可审计、可复盘

## 核心能力

- **研究层**：通过 canonical strategy pipeline 做研究、扫描和快速验证
- **回测层**：连续合约用于快速信号研究；实际合约画像支持无跳跃信号、显式换月、参考手续费、保证金、涨跌停和成交量约束
- **执行层**：`paper` 验证通用基础设施；`ctp_sim` 验证期货具体合约、保证金、开平仓、异步回报和恢复；真实 CTP 适配器仍未实现
- **风控层**：包含全局风控、策略风控与交易前风控
- **监控层**：包含日志、健康检查、企业微信 / Telegram 告警、Dashboard
- **报告层**：支持日报、周报、月报、年报、邮件发送、Markdown/PDF 报告归档

## 技术栈

- Python `3.11+`
- 构建与打包：`setuptools`
- 依赖声明：`pyproject.toml`
- 推荐环境管理与安装工具：`uv`
- 数据库：`PostgreSQL 17`
- ORM 与迁移：`SQLAlchemy` + `Alembic`
- 数据与分析：`polars`、`pandas`、`numpy`
- 回测：项目内置目标权重 / 事件仿真引擎
- 可视化与报告：`matplotlib`、`plotly`、`streamlit`、`reportlab`

## 依赖管理

本项目使用 `pyproject.toml` 统一声明第三方依赖：

- 运行时依赖定义在 `[project.dependencies]`
- 开发依赖定义在 `[project.optional-dependencies].dev`
- 推荐使用 `uv` 创建虚拟环境并安装 `-e ".[dev]"` 进行可编辑开发

仓库已提交 `uv.lock`，开发和验证应优先使用锁定环境：

```bash
uv sync --extra dev --locked
```

## 目录结构

```text
Northstar/
├─ .codex/                     Codex 项目级安全配置
├─ .vscode/                    VS Code 设置、扩展建议与开发任务
├─ alembic/                    数据库迁移脚本
├─ configs/                    应用、策略、风控、数据配置
├─ docs/                       架构与专题文档
├─ scripts/
│  ├─ dev/                     开发环境初始化内部模块
│  ├─ deploy/                  Linux 版本发布与 systemd 部署模块
│  ├─ README.md                脚本入口与职责说明
│  ├─ deploy.sh                Linux 一键部署入口
│  ├─ setup_dev.ps1            Windows PowerShell 开发环境入口
│  └─ setup_dev.sh             macOS/Linux 开发环境入口
├─ src/northstar_quant/
│  ├─ backtest/                日线回测器与可选分钟回放状态机
│  ├─ common/                  通用类型与路径工具
│  ├─ config/                  配置加载与设置模型
│  ├─ data/                    数据读写与样例数据
│  ├─ db/                      ORM 模型、会话与仓储
│  ├─ execution/               执行计划、订单路由与券商适配器
│  ├─ live/                    实盘编排、对账、preflight 与调度
│  ├─ monitoring/              健康检查、告警、Dashboard
│  ├─ portfolio/               组合构造与仓位分配
│  ├─ reporting/               Markdown/PDF 报告与邮件发送
│  ├─ risk/                    多层风控
│  └─ strategies/              策略实现
├─ templates/                  报告模板
├─ tests/
│  ├─ unit/                    快速单元测试
│  ├─ integration/             PostgreSQL 与跨模块集成测试
│  ├─ contract/                架构、迁移、CLI 与部署契约
│  ├─ e2e/                     完整业务闭环测试
│  └─ support/                 公共 fixture 与测试构造器
├─ pyproject.toml              项目配置与依赖声明
└─ README.md                   项目说明
```

## 快速开始

### 0. 安装 `uv`

项目开发环境支持 macOS、Linux 和 Windows。请按 `uv` 官方安装说明完成安装；macOS/Linux
可使用：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

macOS 也可以使用 Homebrew：`brew install uv`。

Windows 可使用 `winget install --id=astral-sh.uv -e`，或按 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/) 选择其他方式。

安装完成后，建议先确认命令可用：

```bash
uv --version
```

### 1. 安装 Docker

macOS 使用 Docker Desktop：

```bash
brew install --cask docker
open -a Docker
```

首次启动时需要在 Docker Desktop 界面中完成许可确认和初始化。等待 Docker 启动完成后
再执行验证命令：

```bash
docker --version
docker compose version
docker info
```

Linux 开发机可以使用 Docker 官方面向测试和开发环境的便捷安装脚本：

```bash
curl -fsSL https://get.docker.com -o /tmp/northstar-get-docker.sh
sudo sh /tmp/northstar-get-docker.sh
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

执行后需要注销并重新登录，使 `docker` 用户组生效，然后运行上面的三条验证命令。
生产服务器应按照 [Docker Engine 官方安装文档](https://docs.docker.com/engine/install/)
配置软件源和版本，不使用便捷安装脚本。

Windows 使用 Docker Desktop；完成其首次初始化后，同样执行 `docker --version`、
`docker compose version` 和 `docker info` 确认 Docker daemon 已就绪。

### 2. 创建环境并安装依赖

开发环境固定使用仓库内置 Docker PostgreSQL。在 macOS 或 Linux 上直接运行初始化脚本：

```bash
scripts/setup_dev.sh
```

该脚本会检查 `uv`、Docker 与 Docker Compose，创建本地 `.env`，为固定数据库用户
`northstar` 生成随机开发密码，启动 PostgreSQL，并执行依赖同步、数据库迁移和基础检查。
密码只保存在本地 `.env`；脚本不会提交该文件，也不会启用真实交易。

Windows PowerShell 使用独立入口。它有意不创建或修改 `.env`，先由操作者完成一次本地
初始化并设置非空数据库密码：

```powershell
Copy-Item .env.example .env
# 编辑 .env，将 POSTGRES_PASSWORD 设为仅供本机开发使用的非空值
.\scripts\setup_dev.ps1
```

该入口只在当前 PowerShell 进程中生成数据库连接串，并强制 `paper` 与
`NORTHSTAR_LIVE_TRADING_ENABLED=false`；它不会下载市场数据、启动调度器或调用真实交易。

### 3. 初始化数据库

`scripts/setup_dev.sh` 或 `scripts/setup_dev.ps1` 已经完成本地 PostgreSQL 启动和迁移。若需要手动重新迁移，可执行：

```bash
uv run northstar init-db
```

该命令内部统一执行 Alembic 迁移，不再维护独立的 `create_all` 建库路径。
也可以直接执行：

```bash
uv run alembic upgrade head
```

当前项目仍处于无历史业务数据的开发阶段，迁移基线已压缩为
`0001_initial_schema`。从产生需要保留的数据或首次正式发布开始，只追加新迁移，
不再重写该基线。

测试固定使用独立的 `northstar_test` 数据库；每个数据库测试还会创建自己的隔离
schema。PostgreSQL 健康后可直接运行：

```bash
uv run pytest -m unit
uv run pytest -m integration
uv run pytest
uv run ruff check .
uv run python scripts/check_mypy_baseline.py check
```

测试分层、marker 和公共 fixture 说明见
[`tests/README.md`](tests/README.md)。

### 4. 生成或下载数据并跑通流程

```bash
northstar data profiles
northstar data download --profile cn_futures_daily_trend_offline
northstar research futures-trend --profile cn_futures_daily_trend_offline
northstar backtest run portfolio --profile cn_futures_daily_trend_offline
```

仓库内置三个安全的离线画像：

- `cn_futures_daily_trend_offline`：连续合约快速收益研究；
- `cn_futures_daily_actual_offline`：低频策略的主要验证路径，使用实际合约逐日保证金回测；
- `cn_futures_intraday_replay_offline`：可选的分钟盘口与订单生命周期专项回放。

三个画像都不能下单，也不能直接改成 `simulated` 或 `live`。

另有 `cn_futures_daily_trend_simulated` 用于本地 CTP 语义仿真。它只接受
`NORTHSTAR_BROKER=ctp_sim`，不会连接真实交易柜台。完整演练步骤见
[`configs/profiles/simulated/README.md`](configs/profiles/simulated/README.md)。

实际合约日线可通过 AKShare 自动下载并回测：

```bash
northstar data download \
  --profile cn_futures_daily_actual_offline
northstar backtest run portfolio \
  --profile cn_futures_daily_actual_offline
```

分钟回放不是低频日常流程；只有准备验证容量、委托生命周期或上线前执行细节时，才导入
专门的分钟数据：

```bash
northstar data import-file /path/to/actual_contracts_intraday.parquet \
  --profile cn_futures_intraday_replay_offline
northstar backtest run portfolio \
  --profile cn_futures_intraday_replay_offline
```

字段契约、换月口径和剩余边界见
[`docs/12_期货回测器说明.md`](docs/12_期货回测器说明.md)。

第一次自行实现策略、创建独立画像、运行回测并分析报告，请按
[`docs/00_第一个策略与回测教程.md`](docs/00_第一个策略与回测教程.md) 操作；教程只使用
离线研究路径，不会连接券商。

数据供应商、授权边界、目标品种和候选策略研究门槛见
[`docs/16_研究准入政策与数据治理.md`](docs/16_研究准入政策与数据治理.md)。当前商业
数据采购与政策激活均为失败关闭状态；公开样本只能用于探索或工程验收。

## Linux 部署

部署脚本采用版本目录、原子切换和失败回退。先创建本地非敏感配置与生产环境文件：

```bash
cp deploy.env.example deploy.env
cp .env.production.example .env.production
```

完成配置后，首次部署执行：

```bash
UPLOAD_ENV=1 SETUP_SERVER=1 scripts/deploy.sh
```

后续发布只需：

```bash
scripts/deploy.sh
```

默认 `SERVICE_MODE=health`，只部署、迁移并运行健康检查，不启动交易调度器。完整目录结构、
回退语义和 scheduler 安全门槛见
[`docs/14_Linux一键部署.md`](docs/14_Linux一键部署.md)。

## 常用命令

### 基础命令

```bash
northstar health
northstar init-db
northstar data profiles
northstar data providers
northstar data download --profile cn_futures_daily_trend_offline
northstar data download --profile cn_futures_daily_actual_offline
northstar data import-file /path/to/actual_contracts_intraday.parquet --profile cn_futures_intraday_replay_offline
northstar data validate --profile cn_futures_daily_trend_offline
northstar data manifest --profile cn_futures_daily_trend_offline
```

### 研究与回测

```bash
northstar research futures-trend --profile cn_futures_daily_trend_offline
northstar backtest run futures_trend --profile cn_futures_daily_trend_offline
northstar backtest run futures_trend --profile cn_futures_daily_actual_offline
northstar backtest run futures_trend --profile cn_futures_intraday_replay_offline
```

### 实盘执行

```bash
northstar live signal
northstar live risk-check
northstar live execute
northstar live preview-rebalance
northstar live sync
northstar live run
northstar live poll
northstar live drift
northstar live cancel-stale
northstar live scheduler
```

低频实盘链路分为三个独立任务：`signal` 只在完整日线封盘后计算并冻结目标，
`execute` 在下一可交易时段读取冻结目标，`risk-check` 在盘中持续检查账户状态、
可用资金、保证金、持仓、挂单和实时报价。`live run` 只是人工操作时串行调用前两层；
scheduler 会分别调度三层。相同决策日的目标不能被静默覆盖，重复执行使用稳定批次和
订单幂等身份。

### 报告与监控

```bash
northstar report daily --strategy futures_trend
northstar report weekly --strategy futures_trend
northstar report monthly --strategy futures_trend
northstar report yearly --strategy futures_trend
northstar report send reports/daily/cn_futures_daily_trend_offline/futures_trend/20260730/report.md
northstar report pdf reports/daily/cn_futures_daily_trend_offline/futures_trend/20260730/report.md
northstar dashboard run
```

报告按 `类型/画像/策略/周期/` 分层保存，回测还会追加不可变的 `run_id` 目录。每个
报告目录包含 `report.md`、`report.json`，回测目录还包含 `manifest.json`，生成 PDF 后
还会包含 `report.pdf`。例如回测目录为
`reports/backtest/cn_futures_daily_trend_offline/portfolio/20150105-20260730/bt-<run_id>/`；
日报周期目录使用 `YYYYMMDD`，周报使用 `YYYY-Www`，月报使用 `YYYY-MM`，
年报使用 `YYYY`。

启动 Dashboard 后，可以直接在“数据概览”页查看某个交易画像的数据覆盖区间、标的摘要、归一化价格走势、最近 K 线以及原始数据快照。

## 配置说明

运行时 Settings、交易画像和策略 YAML 是三类独立配置源，不会按一个统一优先级互相覆盖：

- Settings：`NORTHSTAR_*` 环境变量 / `.env` / 安全默认值
- 交易画像：`configs/profiles/{offline,simulated,live}/*.yaml`
- 策略默认参数：`configs/strategy/*.yaml`，可由画像中的策略参数覆盖

常见配置项包括：

- `configs/app.yaml`：应用级配置
- `configs/profiles/`：按连接边界组织的交易画像配置；`offline/` 不连接券商，`simulated/` 连接模拟账户，`live/` 连接真实账户
- `configs/strategy/*.yaml`：策略配置
- `configs/futures/*.yaml`：连续合约研究规格；连续 symbol 明确不可交易
- `.env`：数据库地址、券商参数、告警方式（`console / wecom / telegram`）、SMTP、调度 cron 等运行时配置

数据库统一使用 PostgreSQL，连接地址由本地 `.env` 中的
`NORTHSTAR_DATABASE_URL` 提供；SQLite 不再属于支持范围。所有建库和结构升级均由
Alembic 管理。
日志系统当前也会读取 `configs/app.yaml` 里的 `logging` 段，用来控制日志级别、控制台输出、文件输出、日志目录以及按日滚动行为。
当前活动日志文件默认为 `logs/northstar.log`，历史滚动文件采用 `northstar-YYYY-MM-DD.log` 命名。控制台日志保留 `|` 风格的可读格式，主干顺序为时间、级别、`file:line`、消息；文件日志使用 JSON Lines，字段顺序为 `timestamp`、`level`、`file`、`line`、`msg`，再跟随 `command`、`strategy`、`symbol` 等顶层结构化字段。
市场数据当前按两层目录管理：`storage/downloads/<provider>/<market>/<asset_type>/<data_frequency>/` 保存下载缓存，`storage/market/<market>/<asset_type>/<data_frequency>/` 保存标准化后的策略输入数据；每个数据文件都会配套生成 `.manifest.json` 元数据文件。
当前内置的数据提供器包括：

- `akshare`：通过 AKShare 的新浪主力连续合约接口自动下载国内期货日线
- `akshare_actual_daily`：下载交易所实际合约日线，并合并前一交易日的金十主力参考规则

交易画像里的 `data.download` 段负责描述下载行为，例如下载提供器、symbol 列表、开始日期、结束日期和下载选项；`data.path` 负责描述标准化后数据集在 `storage/market` 下的目标位置。这样同一套 CLI 可以同时覆盖“在线下载、缓存落盘、标准数据集落盘、manifest 追踪、研究读取”整个流程。
连续期货日线 schema 为
`date / symbol / open / high / low / close / adjusted_close / volume`。实际合约画像使用
独立的 `actual_futures_daily_v1` schema，包含结算价、参考费率、保证金、涨跌停、
研究限仓、主力选择日期和时段完整性。提供器按日缓存行情和规则快照，公开规则缺日时
拒绝发布，不沿用旧值。分钟回放使用 `actual_futures_intraday_v1`，额外要求
`timestamp`、夜/日盘标识、买一卖一与盘口量、日终标记。可通过
`northstar data validate --profile ...` 校验。

## 架构说明

项目采用六层拆分：

1. 研究层：负责参数扫描与策略筛选
2. 可信回测层：负责更贴近真实交易约束的验证
3. 实盘层：负责目标仓位到订单执行
4. 数据层：负责行情、特征与元数据存储
5. 风控层：负责全局约束与交易前检查
6. 监控层：负责日志、健康检查、告警与报告

策略、回测、执行、报告等能力通过 CLI 统一暴露，入口位于 `src/northstar_quant/cli.py`。
三个离线画像都使用日频趋势信号。常规低频流程只需要连续合约研究画像和实际合约日线
画像；分钟回放画像的原始行情频率为 `1m`、`data.signal_frequency` 为 `1d`，仅在
执行专项验证时使用。各画像的数据契约和回测引擎不能互换。

## 实盘与报告能力

当前项目已经具备以下基础设施能力：

- 从 paper 或 ctp_sim 适配器同步持仓；真实 CTP 适配器仍是明确扩展点
- 将订单、成交、持仓快照持续落库
- 内置日线期货计划器，把连续策略信号映射为具体合约并生成明确开平仓计划
- 支持单笔市价/限价 paper 撮合与超时撤单；多轮追价目前只有配置和设计文档，未接入执行主流程
- 支持日频目标、盘中执行和实时风控的独立调度
- 实时风控结论持久化；真实订单提交前要求最新结论仍然新鲜且允许交易
- 支持企业微信 / Telegram 告警、邮件发送、Markdown/PDF 报告
- 提供基于 `Streamlit` 的本地 Dashboard

真实券商默认保持关闭和只读。订单 attempt 持久化在先、账户级 fencing 租约、
instrument registry、期货日线 planner 和 completed/cancel 恢复已经落地；内置 offline
画像使用 AKShare 主力连续合约数据并明确设置
`live_trading_eligible: false`。完成实际 CTP 合约核验、可信实盘数据切换和并发/崩溃
恢复演练，并创建经核验的 production 画像前，不应开启真实资金。

`paper` 采用现货式现金/持仓记账，只用于通用基础设施测试。期货流程使用 `ctp_sim`：
它模拟合约乘数、保证金、今昨仓和开平仓，但不包含真实柜台认证、结算确认、期货公司
费率和交易前置网络行为，不能把结果解释为真实 CTP 联调完成。

## 文档索引

- [第一个策略与回测教程](docs/00_第一个策略与回测教程.md)
- [架构总览](docs/01_架构总览.md)
- [配置说明](docs/02_配置说明.md)
- [模块设计说明](docs/03_模块设计说明.md)
- [实盘执行现状与增强说明](docs/04_实盘执行现状与增强说明.md)
- [限价执行、超时撤单、交易日历与 Dashboard](docs/05_限价执行_超时撤单_交易日历与Dashboard.md)
- [限价单追价执行器](docs/06_限价单追价执行器.md)
- [邮件发送日报、周报、月报、年报](docs/07_邮件发送日报_周报_月报_年报.md)
- [邮件附件 PDF 报告](docs/08_邮件附件PDF报告.md)
- [正式版 PDF 报告版式](docs/09_正式版PDF报告版式.md)
- [架构审核与演进路线](docs/10_架构审核与演进路线.md)
- [代码与配置注释规范](docs/11_代码注释规范.md)
- [项目主规划与实施状态](docs/15_项目主规划与实施状态.md)

## 当前状态与边界

Northstar Quant 当前更适合作为个人量化工程骨架与研究到实盘的过渡系统，而不是“无需联调即可直接生产上线”的成品。

在真实上线前，仍建议至少完成以下工作：

- `paper trading` 验证
- 实盘券商连接联调
- 数据源替换与质量校验
- 再平衡时段测试
- 对账结果与异常流程验证

## License

当前仓库未附带单独许可证文件。如需开源发布，建议补充明确的 `LICENSE`。

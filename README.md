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
- **回测层**：使用项目内置目标权重与策略仿真引擎；第三方引擎仍待独立交叉验证
- **执行层**：支持本地 `paper` 流程验证；CTP 合约映射已实现，真实 CTP 报单适配器尚未实现
- **风控层**：包含全局风控、策略风控与交易前风控
- **监控层**：包含日志、健康检查、企业微信 / Telegram 告警、Dashboard
- **报告层**：支持日报、周报、月报、邮件发送、Markdown/PDF 报告归档

## 技术栈

- Python `3.11+`
- 构建与打包：`setuptools`
- 依赖声明：`pyproject.toml`
- 推荐环境管理与安装工具：`uv`
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
uv sync --extra dev
```

## 目录结构

```text
Northstar/
├─ alembic/                    数据库迁移脚本
├─ configs/                    应用、策略、风控、数据配置
├─ docs/                       架构与专题文档
├─ src/northstar_quant/
│  ├─ backtest/                目标权重与策略仿真回测入口
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
├─ tests/                      测试
├─ pyproject.toml              项目配置与依赖声明
└─ README.md                   项目说明
```

## 快速开始

### 0. 安装 `uv`

如果你的机器上还没有 `uv`，建议先按官方方式安装。

macOS / Linux：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Windows PowerShell：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

如果你更习惯用系统包管理器，也可以使用：

- macOS（Homebrew）：`brew install uv`
- Windows（WinGet）：`winget install --id=astral-sh.uv -e`

安装完成后，建议先确认命令可用：

```bash
uv --version
```

### 1. 创建环境并安装依赖

Windows PowerShell：

```powershell
uv venv
.venv\Scripts\Activate.ps1
uv pip install -e ".[dev]"
Copy-Item .env.example .env
```

macOS / Linux：

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env
```

### 2. 初始化数据库

本地快速启动可直接执行：

```bash
northstar init-db
```

如果你希望按迁移历史管理数据库结构，使用：

```bash
alembic upgrade head
```

### 3. 生成或下载数据并跑通流程

```bash
northstar data profiles
northstar data download --profile cn_futures_daily_trend_offline
northstar research futures-trend --profile cn_futures_daily_trend_offline
northstar backtest event portfolio --profile cn_futures_daily_trend_offline
```

仓库只内置 `cn_futures_daily_trend_offline` 一个安全的离线研究画像。它以商品期货连续合约
生成研究信号，不能用于下单，并通过 AKShare 自动下载研究数据。需要接入真实期货数据时，复制该文件
并显式配置合约规格、连续合约规则、实际合约映射及经核验的数据来源；不要把连续合约研究画像直接放入
`simulated/` 或 `live/`。

## 常用命令

### 基础命令

```bash
northstar health
northstar init-db
northstar data profiles
northstar data providers
northstar data download --profile cn_futures_daily_trend_offline
northstar data validate --profile cn_futures_daily_trend_offline
northstar data manifest --profile cn_futures_daily_trend_offline
```

### 研究与回测

```bash
northstar research futures-trend --profile cn_futures_daily_trend_offline
northstar backtest event futures_trend --profile cn_futures_daily_trend_offline
```

### 实盘执行

```bash
northstar live preview-rebalance
northstar live sync
northstar live run
northstar live poll
northstar live drift
northstar live cancel-stale
northstar live scheduler
```

### 报告与监控

```bash
northstar report daily --strategy futures_trend
northstar report weekly --strategy futures_trend
northstar report monthly --strategy futures_trend
northstar report send reports/futures_trend_daily_report.md
northstar report pdf reports/futures_trend_daily_report.md
northstar dashboard run
```

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
- `configs/risk/*.yaml`、`configs/portfolio/*.yaml` 当前仍是设计样例或未来扩展点，尚未接入统一运行时加载
- `.env`：数据库地址、券商参数、告警方式（`console / wecom / telegram`）、SMTP、调度 cron 等运行时配置

默认数据库使用 `sqlite:///storage/northstar.db`，正式环境更建议切换为 PostgreSQL 并配合 `Alembic` 管理迁移。
日志系统当前也会读取 `configs/app.yaml` 里的 `logging` 段，用来控制日志级别、控制台输出、文件输出、日志目录以及按日滚动行为。
当前活动日志文件默认为 `storage/logs/northstar.log`，历史滚动文件采用 `northstar-YYYY-MM-DD.log` 命名。控制台日志保留 `|` 风格的可读格式，主干顺序为时间、级别、`file:line`、消息；文件日志使用 JSON Lines，字段顺序为 `timestamp`、`level`、`file`、`line`、`msg`，再跟随 `command`、`strategy`、`symbol` 等顶层结构化字段。
市场数据当前按两层目录管理：`storage/downloads/<provider>/<market>/<asset_type>/<data_frequency>/` 保存下载缓存，`storage/market/<market>/<asset_type>/<data_frequency>/` 保存标准化后的策略输入数据；每个数据文件都会配套生成 `.manifest.json` 元数据文件。
当前内置的数据提供器包括：

- `akshare`：通过 AKShare 的新浪主力连续合约接口自动下载国内期货日线

交易画像里的 `data.download` 段负责描述下载行为，例如下载提供器、symbol 列表、开始日期、结束日期和下载选项；`data.path` 负责描述标准化后数据集在 `storage/market` 下的目标位置。这样同一套 CLI 可以同时覆盖“在线下载、缓存落盘、标准数据集落盘、manifest 追踪、研究读取”整个流程。
国内期货日线数据的标准表 schema 为：`date / symbol / open / high / low / close / adjusted_close / volume`。连续合约研究画像使用 `close`；`adjusted_close` 仅作为数据提供器可选的连续序列调整字段，不能替代实际交割合约价格。可通过 `northstar data validate --profile ...` 校验。

## 架构说明

项目采用六层拆分：

1. 研究层：负责参数扫描与策略筛选
2. 可信回测层：负责更贴近真实交易约束的验证
3. 实盘层：负责目标仓位到订单执行
4. 数据层：负责行情、特征与元数据存储
5. 风控层：负责全局约束与交易前检查
6. 监控层：负责日志、健康检查、告警与报告

策略、回测、执行、报告等能力通过 CLI 统一暴露，入口位于 `src/northstar_quant/cli.py`。
当前唯一运行时画像为：`CN × FUTURES × 1d × 1d × trend_following`。

## 实盘与报告能力

当前项目已经具备以下实用能力：

- 从 paper 或未来的 CTP 适配器同步持仓
- 将订单、成交、持仓快照持续落库
- 基于目标权重生成再平衡计划
- 支持限价执行、追价执行、超时撤单
- 支持交易日历过滤与日频调度
- 支持企业微信 / Telegram 告警、邮件发送、Markdown/PDF 报告
- 提供基于 `Streamlit` 的本地 Dashboard

真实券商默认保持关闭和只读。订单 attempt 持久化在先、账户级 fencing 租约、
instrument registry 和 completed/cancel 恢复已经落地；但仓库内 registry
默认为空，内置 offline 画像使用 AKShare 主力连续合约数据并明确设置
`live_trading_eligible: false`。完成实际 CTP 合约核验、可信实盘数据切换和并发/崩溃
恢复演练，并创建经核验的 production 画像前，不应开启真实资金。

## 文档索引

- [架构总览](docs/01_架构总览.md)
- [配置说明](docs/02_配置说明.md)
- [模块设计说明](docs/03_模块设计说明.md)
- [实盘执行现状与增强说明](docs/04_实盘执行现状与增强说明.md)
- [限价执行、超时撤单、交易日历与 Dashboard](docs/05_限价执行_超时撤单_交易日历与Dashboard.md)
- [限价单追价执行器](docs/06_限价单追价执行器.md)
- [邮件发送日报、周报、月报](docs/07_邮件发送日报_周报_月报.md)
- [邮件附件 PDF 报告](docs/08_邮件附件PDF报告.md)
- [正式版 PDF 报告版式](docs/09_正式版PDF报告版式.md)
- [架构审核与演进路线](docs/10_架构审核与演进路线.md)
- [代码与配置注释规范](docs/11_代码注释规范.md)

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

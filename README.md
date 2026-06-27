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

- **研究层**：基于 `vectorbt` 做研究、扫描和快速验证
- **可信回测层**：基于轻量事件回测引擎与 `Backtrader` 做更贴近真实交易约束的验证
- **实盘层**：支持 `paper` 与 `IBKR` 模式，覆盖持仓同步、再平衡、订单轮询与撤单
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
- 回测：`vectorbt`、`Backtrader`
- 可视化与报告：`matplotlib`、`plotly`、`streamlit`、`reportlab`

## 依赖管理

本项目使用 `pyproject.toml` 统一声明第三方依赖：

- 运行时依赖定义在 `[project.dependencies]`
- 开发依赖定义在 `[project.optional-dependencies].dev`
- 推荐使用 `uv` 创建虚拟环境并安装 `-e ".[dev]"` 进行可编辑开发

当前仓库未提交 `uv.lock` 或其他锁文件，因此依赖解析基于版本范围而非完全锁定版本。

## 目录结构

```text
Northstar/
├─ alembic/                    数据库迁移脚本
├─ configs/                    应用、策略、风控、数据配置
├─ docs/                       架构与专题文档
├─ src/northstar_quant/
│  ├─ backtest/                回测引擎与 Backtrader 入口
│  ├─ common/                  通用类型与路径工具
│  ├─ config/                  配置加载与设置模型
│  ├─ data/                    数据读写与样例数据
│  ├─ db/                      ORM 模型、会话与仓储
│  ├─ execution/               订单路由、执行器、对账
│  ├─ live/                    实盘编排、调度与服务
│  ├─ monitoring/              健康检查、告警、Dashboard
│  ├─ portfolio/               组合构造与仓位分配
│  ├─ reporting/               Markdown/PDF 报告与邮件发送
│  ├─ research/                研究扫描入口
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
northstar data download --profile cn_etf_daily
northstar research momentum --profile cn_etf_daily
northstar backtest event etf_rotation --profile cn_etf_daily
northstar backtest bt etf_rotation --profile cn_etf_daily
northstar live preview-rebalance --profile cn_etf_daily
```

如果你要下载真实的中国 A 股 ETF 日频数据并直接进入研究流程，可以使用项目内置的 `cn_etf_daily_research12` 画像。该画像使用 Yahoo Finance 的 `.SS` / `.SZ` 符号格式：

```bash
northstar data providers
northstar data download --profile cn_etf_daily_research12
northstar data validate --profile cn_etf_daily_research12
northstar data manifest --profile cn_etf_daily_research12
northstar research momentum --profile cn_etf_daily_research12
northstar backtest event etf_rotation --profile cn_etf_daily_research12
```

## 常用命令

### 基础命令

```bash
northstar health
northstar init-db
northstar sample-data --profile cn_etf_daily
northstar data profiles
northstar data providers
northstar data download --profile cn_stock_daily
northstar data validate --profile cn_stock_daily
northstar data manifest --profile cn_stock_daily
```

### 研究与回测

```bash
northstar research momentum
northstar backtest event etf_rotation
northstar backtest bt momentum
northstar backtest bt etf_rotation
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
northstar report daily --strategy etf_rotation
northstar report weekly --strategy etf_rotation
northstar report monthly --strategy etf_rotation
northstar report send reports/etf_rotation_daily_report.md
northstar report pdf reports/etf_rotation_daily_report.md
northstar dashboard run
```

启动 Dashboard 后，可以直接在“数据概览”页查看某个交易画像的数据覆盖区间、标的摘要、归一化价格走势、最近 K 线以及原始数据快照。

## 配置说明

配置优先级如下：

1. 环境变量
2. `.env`
3. `configs/*.yaml`
4. 代码默认值

常见配置项包括：

- `configs/app.yaml`：应用级配置
- `configs/profiles/*.yaml`：交易画像配置，默认主线为中国 A 股 ETF 日频，并提供 A 股股票日频、周频、分钟级画像
- `configs/strategy/*.yaml`：策略配置
- `configs/risk/*.yaml`：风控配置
- `configs/data/*.yaml`：数据配置
- `.env`：数据库地址、券商参数、告警方式（`console / wecom / telegram`）、SMTP、调度 cron 等运行时配置

默认数据库使用 `sqlite:///storage/northstar.db`，正式环境更建议切换为 PostgreSQL 并配合 `Alembic` 管理迁移。
日志系统当前也会读取 `configs/app.yaml` 里的 `logging` 段，用来控制日志级别、控制台输出、文件输出、日志目录以及按日滚动行为。
当前活动日志文件默认为 `storage/logs/northstar.log`，历史滚动文件采用 `northstar-YYYY-MM-DD.log` 命名。控制台日志保留 `|` 风格的可读格式，主干顺序为时间、级别、`file:line`、消息；文件日志使用 JSON Lines，字段顺序为 `timestamp`、`level`、`file`、`line`、`msg`，再跟随 `command`、`strategy`、`symbol` 等顶层结构化字段。
市场数据当前按两层目录管理：`storage/downloads/<provider>/<market>/<asset_type>/<data_frequency>/` 保存下载缓存，`storage/market/<market>/<asset_type>/<data_frequency>/` 保存标准化后的策略输入数据；每个数据文件都会配套生成 `.manifest.json` 元数据文件。
当前内置的数据提供器包括：

- `demo`：生成项目自带的演示数据
- `local`：直接读取画像已指向的本地数据文件
- `yfinance`：从 Yahoo Finance 下载真实行情并规范落盘，A 股使用 `.SS` / `.SZ` 后缀

交易画像里的 `data.download` 段负责描述下载行为，例如下载提供器、symbol 列表、开始日期、结束日期和下载选项；`data.path` 负责描述标准化后数据集在 `storage/market` 下的目标位置。这样同一套 CLI 可以同时覆盖“在线下载、缓存落盘、标准数据集落盘、manifest 追踪、研究读取”整个流程。
对于中国 A 股日频/周频数据，当前标准表 schema 为：`date / symbol / open / high / low / close / adjusted_close / volume / dividend / split_factor`。其中 `close` 保留原始收盘价，`adjusted_close` 单独保存复权收盘价；研究、目标权重回测和策略信号默认读取 `data.price_field` 指定的价格列，当前日频/周频画像默认使用 `adjusted_close`，而实盘预览与下单估值仍使用原始 `close`。这套语义可以通过 `northstar data validate --profile ...` 明确校验。

## 架构说明

项目采用六层拆分：

1. 研究层：负责参数扫描与策略筛选
2. 可信回测层：负责更贴近真实交易约束的验证
3. 实盘层：负责目标仓位到订单执行
4. 数据层：负责行情、特征与元数据存储
5. 风控层：负责全局约束与交易前检查
6. 监控层：负责日志、健康检查、告警与报告

策略、回测、执行、报告等能力通过 CLI 统一暴露，入口位于 `src/northstar_quant/cli.py`。
当前交易画像已经按 `市场 × 资产类型 × 频率 × 策略类型` 四个维度抽象，例如：

- `CN × ETF × 1d × 1d × momentum_rotation`
- `CN × EQUITY × 1d × 1d × cross_sectional_selection`
- `CN × EQUITY × 1w × 1w × cross_sectional_selection`
- `CN × EQUITY × 1m × 5m × intraday_breakout`

## 实盘与报告能力

当前项目已经具备以下实用能力：

- 从 `IBKR / Paper Broker` 同步真实持仓
- 将订单、成交、持仓快照持续落库
- 基于目标权重生成再平衡计划
- 支持限价执行、追价执行、超时撤单
- 支持交易日历过滤与日频调度
- 支持企业微信 / Telegram 告警、邮件发送、Markdown/PDF 报告
- 提供基于 `Streamlit` 的本地 Dashboard

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

## 版本演进

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

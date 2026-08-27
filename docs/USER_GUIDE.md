# Northstar Quant 使用者入门指南

> 这是一条从零开始的学习路径，面向想使用 Northstar Quant 做中国商品期货研究、回测和本地安全演练的人。它说明“先做什么、为什么这样做、看到什么结果才算正常”。
>
> 本文不是实盘交易说明，也不会教你连接真实账户。当前项目已验证的正向能力仅限 offline、paper 与本地 <code>ctp_sim</code>；没有真实 CTP 连接、真实账户或真实资金交易能力。

## 先读这一页：你将学会什么

完成本指南后，你可以：

- 在自己的工作站安全地初始化项目；
- 找到并理解内置研究画像；
- 下载或导入研究数据，校验它，并运行一条完整离线回测；
- 找到报告、运行清单和本地 Dashboard；
- 分清“回测结果”“研究准入”“模拟柜台”和“真实下单”这几件完全不同的事；
- 在遇到安全阻断时知道该如何解释，而不是绕过它。

本指南把现有的专题文档串成一条学习路线。需要精确定义、配置字段或运维步骤时，请回到对应的规范文档：

| 主题 | 规范文档 |
| --- | --- |
| 系统结构、领域语义、证据流和执行边界 | [架构设计](ARCHITECTURE.md) |
| 开发环境、研究流程和回测约定 | [开发与研究工作流](DEVELOPMENT.md) |
| 配置、运行模式、报告、部署与恢复边界 | [运行、配置与部署手册](OPERATIONS.md) |
| 数据授权、研究准入、AI 和人工控制 | [数据、研究、AI 与安全治理](GOVERNANCE.md) |
| 当前尚未完成的事项和外部阻塞 | [主实施计划](planning/MASTER_IMPLEMENTATION_PLAN.md) |

所有命令均应在仓库根目录执行。示例中的 <code>python</code> 指向 Python 3.11 或更高版本。

## 1. 先建立正确预期

### 1.1 Northstar Quant 是什么

Northstar Quant 是一个面向中国商品期货的工程化研究平台。它把数据、情报、策略研究、组合风险和执行建模拆开，以便让每一步都能留下可检查的证据。

最短的理解方式是：

~~~text
受控数据
  → 特征与策略
  → 回测、验证、样本外与压力测试
  → 研究结论
  → （未来、满足严格条件后）组合风险与执行
~~~

这里的箭头不是“自动下单链”。它们表示证据逐步变得更具体。研究结论不等于风险批准；风险批准不等于执行计划；执行计划也不等于券商订单。

### 1.2 Northstar Quant 不是什么

它不是：

- 荐股、喊单或保证收益工具；
- 可以在安装后直接登录期货账户的交易终端；
- 用连续合约回测结果直接推导真实持仓、保证金或成交的工具；
- 可以因为缺少数据、日历或账户状态而“估一个值继续跑”的系统。

项目始终遵循一条保守规则：

~~~text
关键事实未知 → NO NEW RISK（不增加新风险）
~~~

关键事实包括市场数据、合约映射、交易日历、账户、持仓、未完成订单、保证金、报价新鲜度、风险状态和数据授权。

### 1.3 当前模式与能力边界

| 模式 | 现在可以学习或使用的能力 | 不能把它理解成什么 |
| --- | --- | --- |
| <code>offline</code> | 数据下载/导入、数据校验、特征研究、回测、报告 | 不会连接账户或提交订单 |
| <code>paper</code> | 受 PostgreSQL 保护的纸面券商状态与执行代码能力 | 不是开箱即用的生产交易流程 |
| <code>ctp_sim</code> | 本地 PostgreSQL 中的 CTP 语义模拟、同步、风控、预检和计划预览 | 不连接期货公司，不代表真实 CTP 已接通 |
| <code>ctp</code> / real CTP | 当前没有可用能力 | 没有真实连接、报单、回报状态机、账户或实盘资格 |

命令组名称 <code>northstar live</code> 是历史上的“运行控制”名称。看到它不表示系统可以做真实交易。当前内置模拟画像不是订单演练入口：常规执行路径会先拒绝进入 <code>ctp_sim</code> 提交路径；即使经专用 candidate 路径到达最终日历校验，也会因缺少授权的运行时交易日历制品而失败关闭。

默认安全配置是：

~~~text
NORTHSTAR_BROKER=paper
NORTHSTAR_LIVE_TRADING_ENABLED=false
~~~

除非你正在按本指南的本地模拟章节做学习实验，不要修改这些值。尤其不要尝试把真实 CTP 凭据、账户号、密码或令牌放入已跟踪的配置文件。

## 2. 认识项目里的几个核心词

第一次接触量化系统时，最容易混淆的是名字相近但意义不同的对象。下面这张表足够支撑后续操作：

| 词 | 简单解释 | 不能混同为 |
| --- | --- | --- |
| Source | 数据或信息的来源及其授权事实 | 已经可信、可交易的数据 |
| Artifact / DatasetVersion | 经过记录、哈希和版本化的数据制品 | 一个可随意覆盖的文件 |
| Document / Event | 原始文本证据 / 经证据支持的事件主张 | 直接交易信号 |
| Feature | 可供研究使用的结构化特征 | 买卖指令 |
| StrategyTarget | 某策略提出的目标 | 已获批准的组合或订单 |
| PortfolioTarget | 多策略组合后的目标 | ExecutionPlan |
| ExecutionPlan | 已规划的执行动作 | BrokerOrder |
| BrokerOrder | 真正要交给券商的订单 | 回测中的虚构成交 |

期货对象也必须区分：

| 词 | 含义 |
| --- | --- |
| Commodity | 经济品种，例如螺纹钢 |
| Instrument | 可交易标的的抽象 |
| Contract | 特定到期月份、可实际交易的合约 |
| 连续合约 | 为研究拼接出的价格序列，不是可以直接下单的合约 |

如果只记一件事，请记住：连续合约很适合快速研究，但绝不能自动变成真实合约订单。

## 3. 首次初始化：只做本地、安全的准备

### 3.1 前提条件

开发和研究工作站支持 Windows x86_64 与 Linux x86_64。生产目标仅支持 Linux x86_64。你需要：

- Python 3.11 或更高版本；
- Git；
- 一个可以访问公开数据源的网络连接（仅在你执行数据下载时需要）；
- PostgreSQL：Ubuntu/Debian 的高层初始化可以受控安装本机服务；Windows、其他 Linux 和非默认端口需要你自行准备本机 PostgreSQL。

不要把 Docker、SQLite 或远程共享数据库当成核心 PostgreSQL 的替代方案。

### 3.2 推荐的唯一首次入口

在仓库根目录运行：

~~~bash
python scripts/dev/setup.py --initialize-workstation
~~~

交互式终端中，脚本会先展示缺失工具的安装计划，并在需要执行安装时要求输入 <code>YES</code>。自动化场景必须显式确认：

~~~bash
python scripts/dev/setup.py --initialize-workstation --confirm-tool-install YES
~~~

这个入口会做哪些事：

| 项目 | 行为 |
| --- | --- |
| <code>uv</code> 与 <code>just</code> | 安装到未跟踪的 <code>.northstar/</code>，不会依赖系统 PATH |
| Python 依赖 | 从已审计的锁定输入创建或复用项目虚拟环境 |
| <code>.env</code> | 建立未跟踪的本地环境文件；秘密只应放在这里 |
| <code>configs/app.yaml</code> | 建立活动的非秘密应用配置 |
| Ubuntu/Debian PostgreSQL | 在高层入口中安装并启用默认本机服务，然后只使用 <code>127.0.0.1:5432</code> |
| 本地数据库 | 仅创建或复用 <code>northstar</code> 和隔离的 <code>northstar_test</code>，并前向迁移 |

它不会做哪些事：

- 不会下载市场数据；
- 不会启动调度器；
- 不会连接真实 CTP；
- 不会提交订单；
- 不会停止、重置、清空或删除 PostgreSQL 服务、数据库、表、schema 或数据目录；
- 不会覆盖已有 PostgreSQL 角色密码、认证规则或服务配置。

若本机已有 <code>northstar</code> 数据库角色，初始化不会替换它的密码。请在未跟踪的 <code>.env</code> 中填写与该角色匹配的密码后再重试。

### 3.3 初始化后会出现什么

下面这些是本地生成物，不应该提交到 Git：

| 路径 | 用途 |
| --- | --- |
| <code>.env</code> | 本地秘密与环境变量 |
| <code>configs/app.yaml</code> | 当前活动应用配置 |
| <code>.northstar/</code> | 仓库私有的工具、缓存和状态 |
| <code>.venv/</code> | Python 环境 |
| <code>storage/</code> | 下载数据、制品、Lake 与 Local-tools 的非权威本地数据 |
| <code>reports/</code> | 回测与周期报告 |
| <code>logs/</code> | 结构化运行日志 |

模板文件 <code>.env.example</code> 和 <code>configs/app.example.yaml</code> 只用于展示字段；运行时不会自动回退读取模板。

### 3.4 确认工作站已经就绪

先运行最常用的三项检查：

~~~bash
python scripts/dev/run_just.py test
python scripts/dev/run_just.py check
python scripts/dev/run_uv.py run --offline --no-sync northstar health
~~~

它们分别回答不同的问题：

| 命令 | 你在确认什么 |
| --- | --- |
| <code>test</code> | 项目的测试套件能否运行 |
| <code>check</code> | 依赖策略、锁文件、秘密扫描、Ruff 和类型基线是否通过；它不运行 pytest |
| <code>northstar health</code> | 当前配置、目录和运行模式的基础健康信息 |

<code>health</code> 的 <code>blocked</code> 通常表示默认画像/配置、PostgreSQL、数据制品校验或 broker 能力检查失败；尚未下载数据通常是 <code>degraded</code>。它本身不替代交易日历、账户授权或执行 preflight 检查。请先按“配置 → 画像 → 数据来源/授权 → 数据校验 → 回测”的顺序排查，而不是关闭门禁。

## 4. 第一次完整研究：连续合约日线趋势

这是最推荐的新手路径。它使用内置的 <code>cn_futures_daily_trend_offline</code> 画像，数据来自 AKShare/Sina 的公开连续合约参考数据。

这条路径适合学习研究和回测工程；它不代表真实可交易合约、逐日结算、真实手续费、保证金或实盘资格。

### 4.1 先查看系统认识哪些画像和数据来源

~~~bash
python scripts/dev/run_uv.py run --offline --no-sync northstar data profiles
python scripts/dev/run_uv.py run --offline --no-sync northstar data providers
python scripts/dev/run_uv.py run --offline --no-sync northstar data sources
~~~

三条命令的区别：

| 命令 | 看什么 | 常见误解 |
| --- | --- | --- |
| <code>data profiles</code> | 内置交易画像和路径规划 | 画像存在不等于可以交易 |
| <code>data providers</code> | 技术 adapter 是否注册 | adapter 可用不等于已经有数据合同 |
| <code>data sources</code> | 数据源的授权状态与候选研究资格 | 公开参考数据不等于生产数据 |

目前内置公开源的用途是 <code>research_only</code>。它们不能成为真实交易、再分发或外部服务的数据授权依据。

### 4.2 下载研究数据

~~~bash
python scripts/dev/run_uv.py run --offline --no-sync northstar data download --profile cn_futures_daily_trend_offline
~~~

下载器会按画像中明确声明的品种和上游代码映射获取连续合约日线，并写入受控的本地目录。公开接口可能限流、临时不可用或修订历史，因此：

- 不要把一次下载成功理解为永久、权威的数据源；
- 不要丢弃本次运行产生的 manifest；
- 不要把下载的连续序列用于真实订单、保证金计算或成交模拟；
- 如果网络失败，先等待或核对来源，再重试；不要替换为不明来源文件。

### 4.3 校验下载结果

~~~bash
python scripts/dev/run_uv.py run --offline --no-sync northstar data validate --profile cn_futures_daily_trend_offline
python scripts/dev/run_uv.py run --offline --no-sync northstar data manifest --profile cn_futures_daily_trend_offline
~~~

<code>validate</code> 会检查数据 schema 与主键一致性。<code>manifest</code> 让你查看当前画像绑定的数据集信息。

校验通过只表示“该文件在已定义的工程契约内可读取”，并不自动证明：

- 数据供应商合同有效；
- 数据没有未来函数；
- 数据适合实盘；
- 策略已经通过候选研究准入；
- 任何人拥有下单权限。

### 4.4 运行回测

~~~bash
python scripts/dev/run_uv.py run --offline --no-sync northstar backtest run portfolio --profile cn_futures_daily_trend_offline
~~~

该命令会运行当前画像中启用的策略，输出指标并生成带运行清单的报告。输出位置由 <code>configs/app.yaml</code> 中的 <code>runtime.reports_dir</code> 决定，默认是仓库内的 <code>reports/</code>。

一个可靠的回测结果至少应让你能回答：

| 问题 | 应从哪里找 |
| --- | --- |
| 用的是哪个画像和策略版本？ | 报告与运行 manifest |
| 用的是哪个数据集、哪个输入版本？ | manifest、数据制品记录 |
| 信号和成交之间有没有延迟？ | 画像的 <code>execution_delay_sessions</code> 与报告 |
| 成本、滑点和风险上限是什么？ | 画像的 <code>backtest</code> 与 <code>risk</code> 段 |
| 结果能否复现？ | 同一数据、配置和代码 revision 下的运行清单 |

内置连续合约画像的成本与滑点假设比较理想化，且连续序列不含真实可交易合约的全部细节。请把结果当成“研究起点”，而不是交易建议。

### 4.5 生成研究准入结论

回测完成后，你可以查看当前策略是否满足已配置的研究准入规则：

~~~bash
python scripts/dev/run_uv.py run --offline --no-sync northstar research assess portfolio --profile cn_futures_daily_trend_offline
~~~

若你在本地自动化中需要非 PASS 时失败，可加上：

~~~bash
python scripts/dev/run_uv.py run --offline --no-sync northstar research assess portfolio --profile cn_futures_daily_trend_offline --require-pass
~~~

这里仍有三道不同的门：

~~~text
回测有收益
≠ 研究准入 PASS
≠ 风险审批
≠ 有订单权限
~~~

特别是默认连续合约画像明确不参与候选策略准入。它的目的，是让你先学习研究流程而不是误把公开参考数据升级成生产证据。

## 5. 什么时候使用哪一个内置画像

| 画像 ID | 输入与引擎 | 适合什么 | 关键限制 |
| --- | --- | --- | --- |
| <code>cn_futures_daily_trend_offline</code> | 公开连续合约日线、权重收益回测 | 快速探索趋势策略 | 连续合约仅研究用途，成本假设偏理想化 |
| <code>cn_futures_daily_actual_offline</code> | 实际合约日线、期货日线回测 | 更接近实际合约、换月、规则与保证金语义的离线验证 | 当前公开数据仍只是研究参考，不是生产授权 |
| <code>cn_futures_intraday_replay_offline</code> | 本地导入的实际合约分钟数据、订单回放 | 专项分析分钟级执行假设 | 需要你自己提供已核验且有授权的数据文件 |
| <code>cn_futures_daily_trend_simulated</code> | 本地 CTP 语义模拟 | 学习预检、风险检查、状态同步和计划预览 | 当前缺少运行时交易日历制品，不能完成订单提交 |

关于离线画像的更细字段说明，请参见 [离线研究画像](../configs/profiles/offline/README.md)。

### 5.1 导入自己的实际合约数据

分钟回放画像不会自动下载数据。你需要先确认文件来源、合同/许可、时间语义和保留期，再导入 CSV 或 Parquet：

~~~bash
python scripts/dev/run_uv.py run --offline --no-sync northstar data import-file <已核验的数据文件> --profile cn_futures_intraday_replay_offline
python scripts/dev/run_uv.py run --offline --no-sync northstar data validate --profile cn_futures_intraday_replay_offline
python scripts/dev/run_uv.py run --offline --no-sync northstar backtest run portfolio --profile cn_futures_intraday_replay_offline
~~~

导入成功和 schema 校验成功，并不代表你获得了生产、再分发、模型训练或实盘使用许可。授权事实是数据的一部分，而不是一个事后备注。

## 6. 如何读报告、日志和 Dashboard

### 6.1 报告

<code>backtest run</code> 会生成回测报告。你也可以基于当前画像生成日、周、月、年周期视图：

~~~bash
python scripts/dev/run_uv.py run --offline --no-sync northstar report daily --profile cn_futures_daily_trend_offline
python scripts/dev/run_uv.py run --offline --no-sync northstar report weekly --profile cn_futures_daily_trend_offline
~~~

报告是研究和运行的审计产物，应重点看：

- 使用的画像、策略和数据版本；
- 收益、回撤、暴露和持仓摘要；
- 成本、滑点、执行延迟与风险限制的假设；
- 生成时间、代码 revision 和 manifest；
- 是否存在数据不足、来源不合格或风险阻断。

邮件和 PDF 是报告分发能力，不是交易控制面。只有在你自行把 SMTP 机密安全地写入未跟踪的 <code>.env</code> 后，才考虑使用 <code>--send-email</code>；不要把邮件配置提交到仓库。

### 6.2 本地 Dashboard

~~~bash
python scripts/dev/run_uv.py run --offline --no-sync northstar dashboard run
~~~

启动后，请手动在浏览器访问：

~~~text
http://127.0.0.1:8501
~~~

Dashboard 固定监听本机回环地址，不对公网开放。终端保持运行时服务才会持续存在；用 Ctrl+C 可正常停止本地 Dashboard。

### 6.3 日志

日志目录由 <code>configs/app.yaml</code> 配置，默认是 <code>logs/</code>。日志是审计信息的一部分，排障时可以提高日志级别，但不要关闭文件日志来“隐藏”问题。

## 7. 高级数据研究：Parquet Lake 与 DuckDB

这一节不是第一次回测的前置条件。只有你已经拥有通过受控数据链验证的不可变 DatasetVersion，才应使用 Historical Parquet Lake。

它的目的不是替换数据库，而是让大规模历史数据以可复现、带 hash、lineage 和 PIT 语义的形式被研究和回测读取：

~~~text
已验证 DatasetVersion
  → 不可变 Parquet Lake
  → DuckDB 只读历史查询
  → 研究输入
~~~

### 7.1 物化与校验 Lake

下面是命令形状。尖括号表示你必须替换为真实、已验证的值，不能照抄为任意文件：

~~~bash
python scripts/dev/run_uv.py run --offline --no-sync northstar data lake materialize --input <已验证的-artifact.parquet> --dataset-version <dataset-version-sha256> --artifact-snapshot <artifact-snapshot-sha256> --kind bars --event-time-column date
python scripts/dev/run_uv.py run --offline --no-sync northstar data lake verify --kind bars --dataset-id <dataset-id> --version <lake-version-sha256>
~~~

物化命令只接受与已验证 artifact canonical payload 完全一致的 Parquet。校验命令会重新计算 manifest、文件 hash、schema、分区和点时字段。

### 7.2 只读查询

先准备一个 SQL 文件。它只能包含一条 SELECT 或 WITH 查询，并且必须从受控的 <code>lake_data</code> relation 读取。例如：

~~~sql
SELECT symbol, event_time, close
FROM lake_data
WHERE symbol = ?
ORDER BY event_time
~~~

然后执行：

~~~bash
python scripts/dev/run_uv.py run --offline --no-sync northstar research lake-query --kind bars --dataset-id <dataset-id> --version <lake-version-sha256> --as-of 2026-08-27T16:00:00+08:00 --sql-file <query.sql> --parameter '"RB"'
~~~

查询必须显式带有时区的 <code>--as-of</code>。系统只允许看到 <code>available_at</code> 不晚于该时点的行，拒绝外部 I/O、写入、随机/当前时间函数和不受控的 LIMIT/OFFSET。每次查询会返回可重放 receipt；receipt 不是策略批准，更不是订单权限。

### 7.3 SQLite Local-tools 索引

项目有一个可重建的 SQLite 工具索引，用于发现 Lake manifest。它不保存订单、成交、持仓、风险、审批或审计事实，也不被交易路径信任。

~~~bash
python scripts/dev/run_uv.py run --offline --no-sync northstar local-tools lake-index rebuild
python scripts/dev/run_uv.py run --offline --no-sync northstar local-tools lake-index list --kind bars
~~~

<code>rebuild</code> 会先验证 Lake，再建立一代本地 discovery metadata。SQLite 损坏时应按文档显式重建，不能把它当作 PostgreSQL 不可用时的备用核心库。

## 8. paper、ctp_sim 和真实 CTP：不要跨越边界

### 8.1 paper

<code>paper</code> 是默认安全 broker，但它不等于“所有交易流程都对新用户开放”。项目目前没有 production YAML，也没有真实账户运行条件。把 <code>paper</code> 视为代码与状态机的本地学习环境，而不是策略上线按钮。

### 8.2 ctp_sim

<code>ctp_sim</code> 是本地持久化的 CTP 语义模拟。它的模拟 broker 状态保存在本机 PostgreSQL 中，绝不连接期货公司前置。

如果你想观察预检和风险链如何工作，可以在单独的终端会话临时设置环境变量：

~~~bash
export NORTHSTAR_BROKER=ctp_sim
export NORTHSTAR_DEFAULT_PROFILE_ID=cn_futures_daily_trend_simulated
~~~

PowerShell 对应写法是：

~~~powershell
$env:NORTHSTAR_BROKER = "ctp_sim"
$env:NORTHSTAR_DEFAULT_PROFILE_ID = "cn_futures_daily_trend_simulated"
~~~

随后可以按以下顺序做本地预演：

~~~bash
python scripts/dev/run_uv.py run --offline --no-sync northstar data download --profile cn_futures_daily_trend_simulated
python scripts/dev/run_uv.py run --offline --no-sync northstar live signal --profile cn_futures_daily_trend_simulated
python scripts/dev/run_uv.py run --offline --no-sync northstar live sync
python scripts/dev/run_uv.py run --offline --no-sync northstar live risk-check --profile cn_futures_daily_trend_simulated
python scripts/dev/run_uv.py run --offline --no-sync northstar live preflight --profile cn_futures_daily_trend_simulated
python scripts/dev/run_uv.py run --offline --no-sync northstar live preview-rebalance --profile cn_futures_daily_trend_simulated
~~~

每一步都会要求相应的事实存在且新鲜，例如数据、Contract Authority、账户、持仓、风险和对账状态。因此干净环境里出现阻断是正常的学习结果。

当前内置模拟画像不是订单演练入口。常规 <code>northstar live execute</code> 会先以 <code>P8_CTP_SIM_CANDIDATE_GATE_REQUIRED</code> 拒绝进入 <code>ctp_sim</code> 提交路径；即使经专用 candidate 路径到达最终日历校验，也会因 <code>futures.calendar_artifact_snapshot_hashes</code> 为空而以 <code>TRADING_CALENDAR_ARTIFACT_REQUIRED</code> 失败关闭。两种情况都不会写入仿真订单。不要用工作日、<code>XSHG</code> 或测试 fixture 替代中国商品期货的夜盘/休市事实。

### 8.3 真实 CTP

把 <code>NORTHSTAR_BROKER</code> 设为 <code>ctp</code> 会被拒绝，因为真实 CTP execution adapter 尚未实现。当前没有 production profile，也没有真实 CTP 登录、报单、回报、重连、账户同步或真实资金操作能力。

不要尝试：

- 设置 <code>NORTHSTAR_LIVE_TRADING_ENABLED=true</code>；
- 创建占位 production YAML；
- 填入真实账户或 CTP 凭据；
- 用测试 fixture、连续合约或公开参考数据绕过日历、规则和 preflight。

这些不是“缺一个参数”的问题，而是当前项目明确保持的安全边界。

## 9. 常见情况：现象、原因与正确处理

| 现象 | 通常意味着什么 | 正确处理 |
| --- | --- | --- |
| 回测提示找不到数据文件 | 尚未下载或导入该画像的数据 | 先运行对应的 <code>data download</code> 或 <code>data import-file</code>，再 <code>data validate</code> |
| <code>health</code> 显示 blocked | 某项运行事实缺失或不安全 | 阅读输出中缺失项；不要关闭风险开关 |
| <code>TRADING_CALENDAR_ARTIFACT_REQUIRED</code> | 缺少授权的期货运行时日历 | 这是正确的订单阻断；不能用工作日或测试数据代替 |
| <code>CTP_EXECUTION_ADAPTER_REQUIRED</code> | 试图走尚未实现的真实 CTP 路径 | 保持 offline、paper 或本地 <code>ctp_sim</code> 学习范围 |
| 数据校验通过但研究准入不是 PASS | 数据工程契约与研究治理是不同门槛 | 检查数据源授权、PIT、样本、成本、验证与研究准入规则 |
| <code>check</code> 通过但测试没有运行 | <code>check</code> 不含 pytest | 运行 <code>python scripts/dev/run_just.py test</code> 或相应 focused test |
| PostgreSQL 密码认证失败 | 本机已有角色密码与 <code>.env</code> 不一致 | 更新未跟踪 <code>.env</code>；初始化不会覆盖已有密码 |
| 旧数据库迁移失败 | 当前只有一个完整 schema baseline，旧 schema 不兼容 | 由操作者按本机数据保全流程、在仓库自动化之外手动重建可丢弃的开发库；不要用仓库自动化、stamp、downgrade 或自动重置绕过该边界。若数据库含需保留数据，先备份并取得管理员确认 |

<code>northstar data cleanup --apply</code> 是明确的删除操作。它默认只预览，而且还需要保留策略显式启用；初学阶段不应把它作为日常命令。

## 10. 你下一步该读什么

| 你的目标 | 下一份文档 |
| --- | --- |
| 我想读代码、改策略或贡献功能 | [开发者指南](DEVELOPER_GUIDE.md) |
| 我想了解六个领域、PIT 与订单链 | [架构设计](ARCHITECTURE.md) |
| 我想创建或修改研究画像 | [开发与研究工作流](DEVELOPMENT.md) 与 [离线画像说明](../configs/profiles/offline/README.md) |
| 我想理解配置、报告、Dashboard、部署与恢复 | [运行、配置与部署手册](OPERATIONS.md) |
| 我想判断某个数据源或研究结果能否升级 | [数据、研究、AI 与安全治理](GOVERNANCE.md) |
| 我想知道什么仍被外部条件阻塞 | [主实施计划](planning/MASTER_IMPLEMENTATION_PLAN.md) |

如果你刚完成本指南，最好的下一步不是尝试“开交易”，而是用另一个离线画像复现实验、检查 manifest，学习如何区分连续合约探索和实际合约验证。

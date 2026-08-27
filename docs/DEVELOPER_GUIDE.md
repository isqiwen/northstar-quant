# Northstar Quant 开发者指南

> 本文面向第一次参与 Northstar Quant 的开发者。它给出一条从“能安全运行项目”到“能完成一个符合架构和安全要求的改动”的学习路径。
>
> 它是上手路线，不替代专题规范：设计理由以 [架构设计](ARCHITECTURE.md) 为准，开发与研究操作以 [开发与研究工作流](DEVELOPMENT.md) 为准，运行/部署以 [运行、配置与部署手册](OPERATIONS.md) 为准，数据和权限以 [治理与安全](GOVERNANCE.md) 为准。

## 1. 先理解你正在参与什么

Northstar Quant 是面向中国商品期货的数据、研究、风险和执行平台。它是 real-money-adjacent 系统：即使你只运行 offline、paper 或本地 <code>ctp_sim</code>，也必须像将来可能接近真实资金一样处理正确性、可追溯性和失败场景。

当前已验证的正向能力只限：

- offline 研究、回测和受控数据工作流；
- paper 及本地 PostgreSQL 持久化的模拟状态能力；
- 本地 <code>ctp_sim</code> 的受控 CTP 语义演练。

当前没有：

- 真实 CTP 连接、认证、报单、回报、重连或账户状态机；
- production YAML；
- 真实账户、真实资金或实盘交易能力；
- 已授权的生产数据、交易日历、合约/规则制品；
- 已批准的生产灾备环境。

因此，下面两项必须保持为默认：

~~~text
NORTHSTAR_BROKER=paper
NORTHSTAR_LIVE_TRADING_ENABLED=false
~~~

任何市场数据、合约、日历、账户、持仓、订单、保证金、报价、风险或授权状态未知时，正确行为都是 <code>NO NEW RISK</code>。不要用 fallback、旧值、fixture 或“合理猜测”替代缺失事实。

## 2. 第 0 天：建立开发环境

### 2.1 支持范围与前提

| 项目 | 要求 |
| --- | --- |
| Python | 3.11 或更高版本 |
| 开发工作站 | Windows x86_64 或 Linux x86_64 |
| 生产目标 | 仅 Linux x86_64 |
| 核心运行数据库 | PostgreSQL |
| 历史分析 | Parquet + DuckDB |
| Local tools | 独立、非权威的 SQLite |

不要以 SQLite 替代核心 PostgreSQL integration test，也不要把 Docker/Compose、远程共享数据库或本地文件当作交易权威状态的 fallback。

### 2.2 唯一推荐的首次入口

从仓库根目录运行：

~~~bash
python scripts/dev/setup.py --initialize-workstation
~~~

首次入口会先展示缺失工具的安装计划。交互式运行需要输入 <code>YES</code>；非交互环境必须显式确认：

~~~bash
python scripts/dev/setup.py --initialize-workstation --confirm-tool-install YES
~~~

它会：

- 在未跟踪的 <code>.northstar/</code> 中安装和固定调用 <code>uv</code>、<code>just</code>；
- 依据已审计的锁定输入 materialize Python 环境；
- 建立安全默认的 <code>.env</code> 与 <code>configs/app.yaml</code>；
- 在 Ubuntu/Debian 上受控安装并启用默认本机 PostgreSQL/client；
- 创建或复用本地 <code>northstar</code>、隔离的 <code>northstar_test</code>，并只执行 Alembic 前向迁移。

它不会：

- 覆盖已有 PostgreSQL 角色密码、认证规则或服务配置；
- 删除、清空、重置、truncate、stamp 或 downgrade 数据库；
- 下载市场数据、启动调度器、启用实盘或连接 CTP。

Windows、其他 Linux、非默认端口，以及所有涉及数据库的低层命令要求操作者自行 provision 本机 PostgreSQL。若已有 <code>northstar</code> 角色，请把正确密码填入未跟踪的 <code>.env</code>；初始化不会改写它。

### 2.3 日常命令面的两条硬规则

不要依赖宿主机 PATH 中的 <code>just</code> 或 <code>uv</code>。在本仓库中，始终使用：

~~~bash
python scripts/dev/run_just.py <recipe>
python scripts/dev/run_uv.py run --offline --no-sync <command>
~~~

常用命令如下：

| 目标 | 命令 | 说明 |
| --- | --- | --- |
| 首次/工作站初始化 | <code>python scripts/dev/setup.py --initialize-workstation</code> | 高层、安全的唯一初始化入口 |
| 只读环境诊断 | <code>python scripts/dev/run_just.py dev-check</code> | 检查工具、配置和本机 PostgreSQL 前提 |
| 环境 bootstrap | <code>python scripts/dev/run_just.py env-bootstrap</code> | 依赖策略、离线 lock、秘密扫描和依赖环境 |
| 强制重建环境 | <code>python scripts/dev/run_just.py env-bootstrap-refresh</code> | 仅在确有需要时使用 |
| 初始化/迁移本机 PostgreSQL | <code>python scripts/dev/run_just.py dev-postgres</code> | 只复用已有服务并前向迁移 |
| 单元测试 | <code>python scripts/dev/run_just.py test-unit</code> | 快速领域 unit 测试 |
| 完整测试 | <code>python scripts/dev/run_just.py test</code> | 完整 pytest |
| 静态质量门禁 | <code>python scripts/dev/run_just.py check</code> | 不包含 pytest |
| 浏览 CLI | <code>python scripts/dev/run_uv.py run --offline --no-sync northstar --help</code> | 使用实际注册的命令作为事实来源 |

<code>check</code> 包含依赖策略、锁文件、秘密扫描、Ruff 和 mypy baseline；它不运行 pytest。功能变更不能因为 <code>check</code> 通过就跳过测试。

### 2.4 第一次成功标准

建议按以下顺序运行：

~~~bash
python scripts/dev/run_just.py dev-check
python scripts/dev/run_just.py test-unit
python scripts/dev/run_just.py check
python scripts/dev/run_uv.py run --offline --no-sync northstar --help
~~~

如果你运行在具备本机 PostgreSQL/client 的 Linux 工作站，再运行完整测试：

~~~bash
python scripts/dev/run_just.py test
~~~

完整 Linux 测试包含 PostgreSQL integration 和 restore drill。它要求可用且与服务端版本匹配的 <code>pg_isready</code>、<code>psql</code>、<code>createdb</code>、<code>pg_dump</code> 和 <code>pg_restore</code>。不要通过跳过或降级这类测试来掩盖环境问题。

## 3. 配置、生成物与秘密：先知道哪些文件可以改

### 3.1 配置地图

| 路径 | 是否提交 | 作用 | 关键规则 |
| --- | --- | --- | --- |
| <code>.env</code> | 否 | 密码、令牌、环境变量 | 秘密只放这里或受控系统环境 |
| <code>.env.example</code> | 是 | 完整环境变量模板 | 安全默认值的文档，不是运行时 fallback |
| <code>configs/app.yaml</code> | 否 | 唯一活动的非秘密应用配置 | 管理运行目录和日志 |
| <code>configs/app.example.yaml</code> | 是 | 活动应用配置模板 | 程序不会自动读取它 |
| <code>configs/profiles/offline/</code> | 是 | 离线研究画像 | 只能是 research/experimental 语义 |
| <code>configs/profiles/simulated/</code> | 是 | 本地模拟画像 | 仅可配合 <code>ctp_sim</code> |
| <code>configs/profiles/live/</code> | 是 | 真实账户画像目录说明 | 当前没有 production YAML |
| <code>configs/data/sources.yaml</code> | 是 | adapter、授权和用途状态 | adapter 存在不等于数据获授权 |
| <code>configs/research/admission/</code> | 是 | 研究准入策略 | 回测不是自动准入 |
| <code>configs/maintenance/</code> | 是 | 无秘密的维护政策 | 不保存生产机密 |

已经废弃的本地配置、旧环境变量、旧 CLI 参数和兼容 fallback 不应重新引入。项目尚未发布；当模型、配置或调用方式改变时，应在同一变更中迁移全部调用方、测试和文档，而不是保留双轨路径。

### 3.2 运行目录的职责

<code>configs/app.yaml</code> 的 <code>runtime</code> 段是这些目录的唯一事实来源：

| 默认目录 | 保存内容 | 不能保存什么 |
| --- | --- | --- |
| <code>storage/</code> | 下载数据、不可变制品、Lake 与 Local-tools 的非权威本地数据 | 生产凭据、可随意覆盖的交易权威替代品 |
| <code>reports/</code> | 回测和周期报告 | 运行时秘密 |
| <code>logs/</code> | JSON Lines 审计日志 | 通过关闭日志来掩盖问题 |
| <code>.northstar/</code> | 仓库私有工具、缓存、bootstrap 状态 | 业务数据或秘密的共享位置 |

所有这些都是生成物；不要因本地调试方便而纳入版本控制。

### 3.3 画像是受控运行合同

画像不只是“策略参数文件”。一个期货画像同时绑定：

- 生命周期角色（research、simulated 或未来 production）；
- 数据 source、dataset、频率和品种池；
- 合约权威 ID、连续/实际合约语义和日历要求；
- 策略、回测成本、滑点与执行延迟；
- 风险限制、版本锚点和调度意图。

创建新画像时，从最接近的目录模板复制，并确保文件名、<code>profile_id</code> 和生命周期后缀匹配。不要通过把 offline 文件移动到 simulated/live，或把连续合约转换成“下单代码”来绕过边界。

## 4. 架构地图：先找对代码落点

### 4.1 目录与依赖方向

~~~text
src/northstar_quant/
├── foundation/          基础类型、配置、PostgreSQL、日志、安全、报告、调度
├── data/                来源、制品、质量、合约、日历、市场数据和 Lake
├── intelligence/        Document、Event、实体、机制、影响和特征投影
├── research/            特征、实验、策略、回测、验证和统计
├── portfolio_risk/      组合、暴露、限制、审批和风险
├── trading_execution/   执行计划、broker、订单、持仓、对账和结算
└── application/         唯一跨领域 composition root 与 CLI
~~~

长期依赖方向为：

~~~text
foundation
  ↑
data
  ↑
intelligence
  ↑
research
  ↑
portfolio_risk
  ↑
trading_execution
~~~

<code>application</code> 可以组合所有领域，但任何领域都不得反向导入它。遇到跨领域需求时，先问“能否用稳定 typed contract 在 application 中组合”，而不是直接在低层领域 import 高层业务代码。

### 4.2 每个领域的职责和禁止项

| 模块 | 应负责 | 不应负责 |
| --- | --- | --- |
| <code>foundation</code> | 配置、时间、身份、DB、日志、安全、报告等通用能力 | 业务策略、订单或领域决策 |
| <code>data</code> | 来源、授权、Artifact、DatasetVersion、日历、合约和 PIT | 生成策略或提交订单 |
| <code>intelligence</code> | Document → Event → Feature 的证据链 | 由新闻直接生成 BUY/SELL |
| <code>research</code> | 特征、实验、回测、验证、OOS、压力和研究结论 | 直接访问 broker |
| <code>portfolio_risk</code> | 组合、暴露、限制、审批和风险状态 | 直接提交订单 |
| <code>trading_execution</code> | ExecutionPlan、订单、broker、持仓和对账 | 策略研究逻辑 |
| <code>application</code> | 把已存在的领域契约安全组合起来 | 承载新的领域模型 |

架构测试会检查循环、禁止 import、反向依赖和公共 API 边界。架构测试失败时，修复实现；不要删除、弱化或绕过测试。

### 4.3 三条不能跳跃的业务链

~~~text
Source → Document → Entity → Event → Mechanism → Impact → Market Context → Feature

Feature → Experiment → Backtest → Validation → OOS / Stress → Research Decision

ApprovedPortfolioTarget → ExecutionPlan → PreTradeCheck → BrokerOrder
~~~

这些对象不是同义词。尤其不要把 Feature、StrategyTarget、PortfolioTarget、ExecutionPlan 和 BrokerOrder 合并为一个“方便传递的 dict”。

### 4.4 Point-in-Time 是硬约束

研究输入在回测时必须满足：

~~~text
available_time <= simulation_time
~~~

数据可能具有 <code>event_time</code>、<code>source_time</code>、<code>published_time</code>、<code>ingested_time</code>、<code>processed_time</code> 和 <code>available_time</code>。选择字段必须由事实语义决定，不能为了跑通回测选一个看起来方便的时间戳。

未知时间语义必须显式标为 UNKNOWN 或失败关闭。修订数据不能覆盖历史可见状态，未来合约/规则/费用/保证金也不能进入过去的回测。

## 5. 数据与存储：不同系统做不同的事

### 5.1 PostgreSQL、Parquet、DuckDB、SQLite 的边界

| 技术 | 正确职责 | 禁止用途 |
| --- | --- | --- |
| PostgreSQL | 合约、订单、成交、持仓、策略状态、风险、审批、对账与审计的权威状态 | 被 SQLite 或文件临时替代 |
| Parquet | 大规模、版本化历史制品：tick、bars、factors、features、research/backtest 输入输出 | 伪装成可变交易状态 |
| DuckDB | 对已验证 Parquet 的历史研究和回测分析 | 写核心风险/订单状态，绕过门禁 |
| SQLite | tool-owned、本地、可重建的 cache/index/scratch | 核心数据库 fallback 或交易权威状态 |

<code>PaperBrokerAdapter</code> 与 <code>CtpSimBrokerAdapter</code> 的可变模拟柜台状态也在 PostgreSQL 中按 broker/account 隔离，并维护 transition 审计链；它不应回退到 <code>state.json</code> 或 SQLite。

### 5.2 合约与日历

静态品种卡、研究配置和 YAML 不等于动态运行时权威。合约主数据与 CTP registry 是 PostgreSQL 中按时间发布、不可变且可重放的 Contract Authority。

同样，商品期货交易日历不能用工作日、<code>XSHG</code> 或测试 fixture 猜测。可执行画像需要绑定经过验证的 calendar ArtifactSnapshot hash；缺失时订单路径必须以 <code>TRADING_CALENDAR_ARTIFACT_REQUIRED</code> 失败关闭。

### 5.3 数据源、授权和研究准入

在 <code>configs/data/sources.yaml</code> 中：

- <code>adapter_id</code> 只说明技术连接器；
- source 状态、license、允许用途、保留期和授权环境才说明可否使用；
- <code>public_reference_unverified</code> 与 <code>procurement_pending</code> 都不能通过候选策略研究准入；
- fixture 或 synthetic 输入只能用于测试，不能伪造市场、FeatureValue、真实合约、target 或订单证据。

实现数据功能时，必须同时考虑数据 bytes、schema、hash、lineage、授权、保留期与 <code>available_time</code>，而不是只把表格读进 DataFrame。

## 6. 测试策略：先小后大，失败路径与正常路径一起写

### 6.1 测试目录

~~~text
tests/
├── architecture/                  依赖、循环、公共 API 与安全边界
├── application/{unit,integration}
├── data/{unit,integration,contract}
├── intelligence/{unit,integration,contract,golden}
├── research/{unit,integration,regression,statistical}
├── portfolio_risk/{unit,integration,scenario}
├── trading_execution/{unit,integration,simulation,failure}
├── foundation/{unit,integration,contract}
├── e2e/
├── fixtures/  golden/  helpers/
└── conftest.py
~~~

把测试放到拥有被测行为的领域。跨领域闭环才放进 <code>e2e/</code>；不要把纯计算测试放在模糊的全局目录。

### 6.2 选择测试类型

| 变更 | 至少应考虑的测试 |
| --- | --- |
| 纯领域计算或配置校验 | 所属领域 unit |
| 多模块、文件制品或 PostgreSQL 协作 | 所属领域 integration |
| CLI、迁移、文档、部署或安全接口 | foundation contract / architecture |
| Intelligence 语义输出 | golden |
| 同一输入的研究稳定性 | regression / statistical |
| 风险极端情景 | scenario |
| broker 语义、状态机或失败关闭 | simulation / failure |
| 跨领域业务闭环 | e2e |

示例：

~~~bash
python scripts/dev/run_uv.py run --offline --no-sync pytest tests/research/unit -q
python scripts/dev/run_uv.py run --offline --no-sync pytest -m unit
python scripts/dev/run_uv.py run --offline --no-sync pytest -m integration
python scripts/dev/run_uv.py run --offline --no-sync pytest -m contract
python scripts/dev/run_uv.py run --offline --no-sync pytest -m failure
~~~

### 6.3 每个改动的验证阶梯

1. 先写或更新最接近改动的 focused test。
2. 运行受影响领域的 unit/integration/contract 测试。
3. 若改动触及 common、config、DB、execution、portfolio/risk、shared model 或跨领域边界，运行完整 pytest。
4. 运行静态门禁。
5. 检查文档、CLI help、配置和 schema 是否同步。

完整质量门禁是：

~~~bash
python scripts/dev/run_just.py env-bootstrap
python scripts/dev/run_uv.py run --offline --no-sync pytest
python scripts/dev/run_uv.py run --offline --no-sync ruff check .
python scripts/dev/run_uv.py run --offline --no-sync python scripts/ci/check_mypy_baseline.py check
~~~

<code>candidate-acceptance</code> 只重放固定的 offline/paper/ctp_sim 安全证据，不是实盘准入，也不能拿它代替生产、真实 broker、真实账户或真实数据验收。

## 7. 数据库与 migration：保全优先于便利

### 7.1 测试数据库的严格边界

| URL | 用途 | 规则 |
| --- | --- | --- |
| <code>NORTHSTAR_DATABASE_URL</code> | 本地运行数据库 | 必须使用 <code>postgresql+psycopg://</code> |
| <code>NORTHSTAR_TEST_DATABASE_URL</code> | PostgreSQL integration 测试 | 必须是隔离、loopback 的 <code>northstar_test</code> |

核心 integration test 不可改用 SQLite。测试框架会创建隔离 schema，而且设计上不自动删除它们；这是为了避免自动化删除不属于自身的数据。不要把测试清理改成 broad drop 或 reset。

### 7.2 当前单一 baseline 规则

开发期只保留一个完整的 schema baseline：

~~~text
alembic/versions/0001_current_schema_baseline.py
~~~

本地数据库即使记录的 revision 名称相同，只要 schema 早于当前完整 baseline，也不受支持。正确的处理是由操作者在仓库自动化之外手动重建本地开发库，然后重新运行初始化。

严禁用以下方式绕过：

- <code>alembic stamp</code>；
- downgrade；
- 自动 reset、drop 或 truncate；
- 删除本机 PostgreSQL 数据目录；
- 给 migration 增加破坏性 DDL/DML。

### 7.3 Schema 改动是一个完整改动

当你改变核心持久化模型时，必须在同一个改动中同步：

~~~text
SQLAlchemy model
+ repository / typed contract
+ 0001 当前完整 baseline
+ migration contract 与 PostgreSQL integration tests
+ 调用方、配置、CLI（若受影响）
+ 文档
~~~

不要只修改 ORM；也不要只重写 baseline 而没有运行 fresh isolated migration 验证。

## 8. 如何完成一个高质量改动

### 8.1 开始前：先读事实而不是猜历史

任何非 trivial 改动开始前，执行：

~~~bash
git status --short
~~~

然后阅读：

1. 根目录 [AGENTS.md](../AGENTS.md)；
2. [主实施计划](planning/MASTER_IMPLEMENTATION_PLAN.md)；
3. 即将影响的领域代码、测试、配置和规范文档。

主实施计划是实施进度的唯一事实来源。若你的工作属于一个 Work Package，还要检查它的依赖、验收条件、当前状态与阻塞项；不可把本地 fixture、paper 或 <code>ctp_sim</code> 结果写成生产验收。

若工作树有用户未提交的修改，不要覆盖、revert 或顺手重构它们。

### 8.2 实现时的设计规则

- 把稳定概念建模为 typed、不可变或明确可变的结构化对象；避免跨领域 ad-hoc <code>dict</code>。
- 失败路径要显式，尤其是授权、PIT、账户、日历、合约、保证金、报价、风险和 broker 状态。
- 避免宽泛的 <code>except Exception</code>；要捕获你能够正确处理的失败类型。
- 研究逻辑放在 <code>research</code>，不得直接调用 broker。
- 组合/风险输出不得直接提交订单。
- 将 ExecutionPlan 与 BrokerOrder 保持分离；pre-trade check 是提交前最后的硬门禁。
- 不新增 compatibility alias、legacy adapter、双写模型、旧 CLI 参数或隐藏 fallback。

### 8.3 典型改动清单

| 改动类型 | 除代码外必须同步的地方 |
| --- | --- |
| 新策略/特征 | 研究测试、PIT/lineage、回测输入、画像、报告/manifest、研究文档 |
| 新数据来源 | 授权与保留期事实、source config、artifact/quality、PIT、失败关闭测试 |
| 新风险限制 | typed policy、scenario/failure 覆盖、配置、preflight、文档 |
| 新执行语义 | order state、idempotency、out-of-order/reconnect、reconciliation、simulation/failure 测试 |
| 新持久化模型 | ORM、repository、baseline、PostgreSQL integration、文档 |
| 新 CLI 命令 | Typer help、stdout/audit contract、CLI tests、用户文档 |
| 新运维动作 | dry-run/permission boundary、脚本测试、infra/ops 文档、不可破坏数据库规则 |

### 8.4 结束前：Definition of Done

一个改动完成，不是“本地手动跑了一次”。至少确认：

- 正常路径和必要失败路径均有测试；
- 所有被影响的调用方、配置、schema、CLI、脚本和文档已经同步；
- 没有降低交易安全门禁；
- focused tests、完整要求范围的测试与静态检查通过；
- Markdown 本地链接可解析；
- 若属于 Work Package，主实施计划已经真实反映状态、完成证据与 next task。

## 9. 从一个安全研究改动开始

新开发者最适合从 offline research 领域开始，而不是 broker 或 production 路径。

建议顺序：

1. 运行 [使用者入门指南](USER_GUIDE.md)中的连续合约研究流程。
2. 阅读 <code>configs/profiles/offline/cn_futures_daily_trend_offline.yaml</code>。
3. 定位对应的 strategy、backtest 与 unit/integration tests。
4. 只改变一个可解释的策略参数或特征行为。
5. 为正常与失败/PIT 情况写测试。
6. 运行该领域 focused tests，再运行回测和完整门禁。
7. 记录为什么数据、成本、滑点、延迟和样本口径仍然可重放。

不要把连续合约探索的结果直接迁到 <code>ctp_sim</code> 或未来 live 画像。实际合约、逐日动态规则、日历、风险审批、账户状态和执行预检都需要各自的证据。

## 10. CLI、报告和可观测性改动

CLI 位于 <code>src/northstar_quant/application/cli.py</code>，它是 application composition root 的一部分。新增命令时：

- 将领域逻辑留在所属领域或 application service，不要把业务逻辑塞进 Typer 回调；
- 为命令提供中文 help，并在 <code>northstar --help</code> 路径中保持发现性；
- 需要机器读取的结果要稳定地写到标准输出；审计日志不能是唯一输出通道；
- 对命令输入使用 typed validation，拒绝不安全/未知值；
- 同步 CLI contract test 和使用者文档。

报告、邮件、PDF、Dashboard 和告警是可观测性/分发能力，不是交易授权通道。Dashboard 固定绑定 loopback；邮件/ntfy 凭据只能存在于未跟踪的秘密存储中。

## 11. 高频问题与正确处理

| 现象 | 常见原因 | 正确处理 |
| --- | --- | --- |
| 找不到 <code>uv</code> 或 <code>just</code> | 尚未完成仓库本地 bootstrap | 运行首次初始化；不要改用全局工具或裸 <code>just</code> |
| PostgreSQL client/service 不可用 | 本机服务未 provision 或工具版本不匹配 | Ubuntu/Debian 用高层初始化；其他平台准备 loopback 服务；不要用 SQLite fallback |
| 密码认证失败 | <code>.env</code> 与已有角色密码不匹配 | 更新未跟踪 <code>.env</code>，不要覆盖角色 |
| <code>configs/app.yaml</code> 缺失 | 只存在 example 模板 | 运行初始化或建立完整活动配置；模板不是 fallback |
| migration 检查失败 | 旧完整 baseline 的 schema 不兼容 | 仓库自动化外手动重建开发数据库；禁止 stamp/reset |
| integration test 拒绝 DB URL | URL 不是 loopback <code>northstar_test</code> 或 driver 不符 | 修正 <code>NORTHSTAR_TEST_DATABASE_URL</code>，不要指向运行库 |
| <code>ctp</code> 报 adapter 未实现 | 真实 CTP 路径尚未落地 | 使用 offline/paper/本地 <code>ctp_sim</code> 学习范围 |
| <code>check</code> 通过但行为仍有问题 | <code>check</code> 不跑 pytest | 运行 focused test 或完整 <code>test</code> |
| mypy baseline 不一致 | 新增了类型诊断 | 修正类型问题；不要为了通过检查随意重写 baseline |
| 文档测试失败 | 链接损坏，或命令没有安全前缀 | 确保本地链接存在；所有 <code>run_uv.py run</code> 带 <code>--offline --no-sync</code>，所有 just 调用经过 <code>run_just.py</code> |

## 12. 开发者的阅读顺序

| 阶段 | 建议阅读 |
| --- | --- |
| 安全运行一遍项目 | [使用者入门指南](USER_GUIDE.md) |
| 理解依赖、对象和证据流 | [架构设计](ARCHITECTURE.md) |
| 修改研究/画像/回测 | [开发与研究工作流](DEVELOPMENT.md) 与 [测试说明](../tests/README.md) |
| 修改配置、报告、Dashboard 或运行程序 | [运行、配置与部署手册](OPERATIONS.md) |
| 触及数据、AI、授权或策略升级 | [数据、研究、AI 与安全治理](GOVERNANCE.md) |
| 触及脚本、部署或运维 | [脚本说明](../scripts/README.md) 与 [基础设施说明](../infra/README.md) |
| 判断当前是否允许推进某项工作 | [主实施计划](planning/MASTER_IMPLEMENTATION_PLAN.md) |

当不确定一个改动应放在哪里时，宁可先读对应领域的测试和架构约束。Northstar 的质量来自边界清晰、事实可回放和未知时失败关闭，而不是从“能尽快跑出一个订单”获得。

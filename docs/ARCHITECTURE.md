# Northstar Quant 架构设计

> 本文定义 Northstar Quant 的长期软件架构：模块职责、依赖方向、领域语义、证据流和安全控制边界。

## 1. 目的、状态与原则

Northstar Quant 是面向中国商品期货的量化研究、情报、组合、风险和交易平台。它是
**real-money-adjacent** 系统：即使在 offline、paper 或 `ctp_sim` 模式，也按未来真实资金系统的安全和可审计标准构建。

优先级固定为：Safety、Correctness、Data Integrity、Reproducibility、Architecture、Research Capability、
Production Reliability、Performance、UI。

安全默认值：

```text
NORTHSTAR_BROKER=paper
NORTHSTAR_LIVE_TRADING_ENABLED=false
```

市场数据、合约映射、日历、账户、持仓、未完成订单、风险、券商、报价新鲜度、保证金或数据授权任一状态未知时，
默认结论是 **NO NEW RISK**。系统不会用猜测、旧值或“估算可用”替代证据。

## 2. 系统拓扑与依赖规则

```mermaid
flowchart LR
    F[foundation]
    D[data]
    I[intelligence]
    R[research]
    PR[portfolio_risk]
    T[trading_execution]
    A[application\ncomposition root]

    F --> D --> I --> R --> PR --> T
    A -. composes .-> F
    A -. composes .-> D
    A -. composes .-> I
    A -. composes .-> R
    A -. composes .-> PR
    A -. composes .-> T
```

```text
src/northstar_quant/
├── application/        # 唯一跨领域 composition root
├── data/
├── intelligence/
├── research/
├── portfolio_risk/
├── trading_execution/
└── foundation/
```

`application/` 不是第七个业务领域；它只能组合稳定的领域契约，不承载领域模型。业务领域和 `foundation`
均不得反向导入 `application`。依赖只能从较高层使用较低层的稳定契约：

| 模块 | 可以依赖 | 禁止承担的职责 |
|---|---|---|
| `foundation` | 自身 | 任何业务领域语义 |
| `data` | `foundation` | 研究、风险、交易决策 |
| `intelligence` | `data`、`foundation` | 提交订单或生成 BUY/SELL |
| `research` | `intelligence`、`data`、`foundation` | 访问 broker 或升级生产策略 |
| `portfolio_risk` | `research`、`foundation` | 直接提交订单 |
| `trading_execution` | `portfolio_risk`、`foundation` | 策略研究逻辑 |
| `application` | 所有领域 | 反向成为领域依赖 |

### 存储职责

存储按数据的权威性和访问模式分工，而不是由某一种数据库包办：

```mermaid
flowchart TB
    ctp[CTP]
    ingestion[Data Ingestion]
    postgres[(PostgreSQL<br/>合约、订单、成交、持仓、<br/>策略状态、风险状态)]
    parquet[(Parquet<br/>tick、bars、factors、features、<br/>research、backtest)]
    duckdb[DuckDB<br/>历史分析]
    research[Strategy / Research]
    execution[Execution / Risk]
    local_tools[本地工具集]
    sqlite[(SQLite<br/>tool-owned cache / index / scratch)]

    ctp --> ingestion
    ingestion --> postgres
    ingestion --> parquet
    parquet --> duckdb
    postgres --> research
    duckdb --> research
    research --> execution
    local_tools --> sqlite
```

- **PostgreSQL** 是核心交易和运行状态的唯一权威来源：合约、订单、成交、持仓、策略状态、风险、审批、对账和审计。
  `NORTHSTAR_DATABASE_URL`、Alembic、core repository 与 PostgreSQL integration test 都在此边界内。
- **Parquet** 是大规模、版本化历史数据制品格式。`data lake materialize` 只会把已验证的 immutable
  `DatasetVersion` 中、与 canonical payload 完全一致的 tabular artifact 物化为不可覆盖的分区 Parquet；每个版本都保存
  manifest、逐文件 hash、schema、lineage、冻结授权（合同、有效期、用途）和保留期审计事实，以及逐行 `available_at`
  PIT 语义。Lake root 是服务用户私有目录，读取拒绝符号链接；可覆盖的 `storage/market` 当前投影不能直接进入历史 Lake。
- **DuckDB** 只承担已验证 Parquet 上的历史查询、探索、研究与回测分析。`research lake-query` 使用内存 DuckDB，强制
  `available_at <= as_of`：先把刚重验 hash 的分区复制为私有 query snapshot，再只暴露受控 `lake_data` relation。查询
  只能是单条 SELECT/WITH，DuckDB physical plan 的所有 base scan 必须是 `lake_data`，外部访问、写入、随机/时间/顺序敏感
  函数和用户自定义 limit/offset 均被拒绝；系统在最外层稳定排序和限制行数。每次都生成包含输入版本、manifest hash、参数、
  as-of、引擎版本和结果 hash 的可回放收据。它不是 broker、订单或风险状态库；分析产物只有经过既有
  Research → Portfolio/Risk → Execution 链才可能影响核心状态。
- **SQLite** 只属于 Local tools 的独立缓存、索引或 scratch storage。它不使用核心数据库 URL，不参与 Alembic 或
  `init-db`，也不保存任何交易/风险权威事实。当前已落地的 tool 是
  `<storage_dir>/local-tools/lake-manifest-index.sqlite3`：`local-tools lake-index rebuild` 逐份验证 Lake 后追加一代
  可重建 discovery metadata，`list` 只展示最新一代。DuckDB 与任何实际 Lake 消费路径都不读取或信任这个 index。

这不是“真实 CTP 已接通”的声明。Parquet Lake、DuckDB 历史分析 adapter 与 SQLite Local-tools manifest index 均已实现并有
独立测试；SQLite 仍只是隔离、非权威工具，不能借此绕过 core database 或交易门禁。

`PaperBrokerAdapter` 与 `CtpSimBrokerAdapter` 的可变模拟柜台状态已保存于 PostgreSQL：每个 broker/account scope 有
当前受控快照和不可变 hash-chained transition 审计链，且状态变更与 durable CTP-sim 提交确认可处于同一 PostgreSQL
事务。它不写入 `state.json`，也不把 Local-tools SQLite 当 fallback；现有 durable order、fill、position snapshot、risk、
approval、reconciliation 与 audit 账本仍是独立的 PostgreSQL 权威事实。当前 Contract Master 与 CTP mapping 仍为版本受控
YAML 配置，尚未成为 PostgreSQL 的时间版本化合约权威库。完整历史 Parquet Lake 和 DuckDB 查询 adapter 已经落地，
但现有可覆盖的 profile market 投影尚未自动迁入 Lake：它必须先经过 immutable `DatasetVersion` 入口验证。

### Application：跨领域 composition root

```mermaid
classDiagram
    class ResearchStrategyActivationRequest
    class ResearchStrategyTargetActivator
    class ResearchStrategyActivationReceipt
    class PortfolioRiskApprovalAuthority
    class ExecutionProvenanceRequest
    class ExecutionProvenancePreflight
    class ExecutionProvenancePreflightReceipt
    class CtpSimCandidateExecutor
    class CtpSimCandidateExecutionBundle

    ResearchStrategyTargetActivator ..> ResearchStrategyActivationRequest : activate
    ResearchStrategyTargetActivator --> ResearchStrategyActivationReceipt : creates
    ExecutionProvenanceRequest o-- ResearchStrategyActivationReceipt : activation_receipts
    ExecutionProvenanceRequest o-- PortfolioRiskApprovalAuthority : authority
    ExecutionProvenancePreflight ..> ExecutionProvenanceRequest : verify
    ExecutionProvenancePreflight --> ExecutionProvenancePreflightReceipt : creates
    CtpSimCandidateExecutor ..> ExecutionProvenanceRequest : prepare
    CtpSimCandidateExecutor --> CtpSimCandidateExecutionBundle : creates
    CtpSimCandidateExecutionBundle *-- ExecutionProvenancePreflightReceipt : receipt
```

Application 只协调各领域已存在的契约：activation receipt、风险 authority 和 provenance request 被重放后，
`ExecutionProvenancePreflight` 只返回 eligibility 全为 false 的证据 receipt。`CtpSimCandidateExecutor` 只能从完整请求
准备隔离的 CTP-sim batch；它不是通用 broker client，也不能连接真实账户或提升任何 receipt 的交易资格。

`tests/architecture/` 强制检查无循环、无动态导入绕过、无反向业务依赖和公共 API 边界；失败时修复实现，不能删除测试。

## 3. 六个领域

以下每个领域说明后的“核心类型关系图”只展示该模块中最稳定、最能说明边界的关系，而不是把全部实现类、ORM
记录或私有 helper 画成难以维护的全量 UML。图中的类名可直接在 `src/northstar_quant/` 对应模块中找到；跨阶段协作
仍由 `application` 通过显式 hash、版本和 typed contract 完成。

图例：`*--` 表示对象字段或集合的拥有关系，`o--` 表示稳定绑定，`..>` 表示受控调用或协议依赖，
`<|--` 表示实现中的继承。虚线不会把数据或证据对象变成隐式可变共享状态。

### Foundation

Foundation 提供类型、时间、订单身份与状态、Pydantic 配置、PostgreSQL session/models/repositories、消息、调度、
可观测性、报告、安全、备份和部署基础。核心 `NORTHSTAR_DATABASE_URL` 必须是
`postgresql+psycopg://`，用于权威运行状态；完整的 PostgreSQL、Parquet、DuckDB 和 SQLite 职责见
[存储职责](#存储职责)。

数据库是保全边界：迁移只前进，自动化不会删除、清空或截断 database、schema 或 table。
`init-db` 只执行 `alembic upgrade head`。模型、repository、Alembic migration、测试和文档必须一并变更。

#### 核心类型关系图

```mermaid
classDiagram
    class BaseSettings
    class Settings
    class AppConfig
    class TradingProfile
    class DataSourceConfig
    class ResearchAdmissionPolicy
    class RuntimeConfiguration

    BaseSettings <|-- Settings
    RuntimeConfiguration o-- Settings : settings
    RuntimeConfiguration o-- AppConfig : app
    RuntimeConfiguration o-- TradingProfile : profile
    RuntimeConfiguration o-- DataSourceConfig : data_source
    RuntimeConfiguration o-- ResearchAdmissionPolicy : optional policy
```

`RuntimeConfiguration` 是 Foundation 的受控组合根：它解析并验证运行设置、应用配置、交易画像、
数据源配置与可选研究准入策略。`DataSourceConfig` 留在 Foundation 是因为它是运行时受管配置，
不是 Data 领域发布的事实。消息总线、调度与 SQLAlchemy 记录是独立基础设施子图，不应混入此图而
伪装成领域对象拥有关系。

### Data

Data 发布受治理的事实，而不是策略或交易行为：

- source protocol、授权收据和 provider adapter；
- append-only raw / normalized artifact、内容 hash、fingerprint、lineage 与 `ArtifactSnapshot`；
- Contract Master、product/instrument/contract、规则快照、品种池和交易日历；
- 数据质量、版本、发布授权和 point-in-time（PIT）快照；
- 标准化市场数据及其可用时点。

大规模历史数据以受治理的 Parquet 制品发布；DuckDB 可在其上进行历史分析，但不能以分析结果直接替换 PIT、
Research admission 或任何交易前事实。

`Commodity` 是经济品种，`Instrument` 是可交易标的，`Contract` 是具体可交易合约；三者不互换，
规则、日历与可用性必须按其正确层级绑定。

所有研究数据需要明确 `event_time`、`source_time`、`published_time`、`ingested_time`、`processed_time` 和
`available_time`（按适用性取用）。回测只能消费：

```text
available_time <= simulation_time
```

修订后的数据不能覆盖历史状态；时间语义不明即标记 `UNKNOWN` 或失败关闭。

`Trading Calendar` 也是订单前事实，而不是工作日猜测。当前仓库配置中仅有 `test_only` 日历材料，
没有可运行的授权日历制品；任何将来可执行画像必须显式绑定
`futures.calendar_artifact_snapshot_hashes`。缺失时将以
`TRADING_CALENDAR_ARTIFACT_REQUIRED` 拒绝新订单。

#### 核心类型关系图

```mermaid
classDiagram
    class Artifact
    class ArtifactMetadata
    class RawArtifact
    class NormalizedArtifact
    class DerivedArtifact
    class DataQualityResult
    class ArtifactSnapshot
    class DatasetVersion

    RawArtifact *-- ArtifactMetadata : metadata
    NormalizedArtifact *-- ArtifactMetadata : metadata
    DerivedArtifact *-- ArtifactMetadata : metadata
    RawArtifact ..> Artifact : structural conformance
    NormalizedArtifact ..> Artifact : structural conformance
    DerivedArtifact ..> Artifact : structural conformance
    NormalizedArtifact *-- RawArtifact : raw_artifact
    DerivedArtifact --> Artifact : input_artifacts
    DataQualityResult --> Artifact : evaluates
    ArtifactSnapshot ..> Artifact : freezes
    DatasetVersion *-- ArtifactSnapshot : artifact_snapshots
```

`RawArtifact`、`NormalizedArtifact` 与 `DerivedArtifact` 通过结构契约满足 `Artifact`，而不是名义继承它；
因此图中使用依赖虚线。`DatasetVersion` 只拥有不可变 `ArtifactSnapshot`，绝不直接持有可变制品；
质量、lineage 和 `available_at` 是版本可研究回放的前置事实。

### Intelligence

规范链为：

```text
Source → Document → Entity → Event → Mechanism → Impact → Market Context → Feature 定义
```

Document 是原始证据，Event 是经生命周期和多来源 merge 后的事实主张；两者必须分离。事件保留 evidence，
ontology 必须有版本；LLM 仅可提出受验证的结构化输出，不能作为 ground truth，也不能直接产生交易信号。

未授权 source、fixture 或 synthetic input 只能用于隔离测试；它们不能构造可交易的市场 `FeatureValue`、
真实合约、target、approval、plan 或订单。

#### 核心类型关系图

```mermaid
classDiagram
    class Evidence
    class Mechanism
    class Impact
    class Event
    class Ontology
    class IntelligenceFeatureProjectionRequest
    class IntelligenceFeatureProjector
    class VersionedIntelligenceFeatureProjection

    Event *-- Evidence : evidence
    Event *-- Mechanism : mechanism
    Event *-- Impact : impacts
    IntelligenceFeatureProjectionRequest --> Ontology : ontology
    IntelligenceFeatureProjectionRequest --> Event : event
    IntelligenceFeatureProjectionRequest --> Mechanism : mechanism
    IntelligenceFeatureProjectionRequest --> Impact : selected_impact
    IntelligenceFeatureProjector ..> IntelligenceFeatureProjectionRequest : project
    IntelligenceFeatureProjector --> VersionedIntelligenceFeatureProjection : creates
```

`Event` 保存可审计 evidence、mechanism 与 impact；`Document` 通过 evidence 的身份和内容 hash 被引用，
并非被 Event 直接拥有。投影请求在精确 ontology、event、selected impact、时间和授权市场上下文绑定后才可
生成 `VersionedIntelligenceFeatureProjection`，其输出始终 non-tradable，不能直接形成 target 或订单。

### Research & Strategy

Research 将受控事实转换为可复现实验：

```text
Feature → Experiment → Backtest → Validation → OOS / Stress → Research Decision
```

每次实验记录 DatasetVersion、FeatureVersion、StrategyVersion、配置、代码 revision、成本模型、滑点模型和 OOS 区间。
同一输入必须产生同一结果。单次 Sharpe、短样本盈利、漂亮连续合约或参数搜索结果都不足以升级策略。

Research 不依赖 broker。应用层的 `ResearchStrategyTargetActivator` 只重放具名人工 activation 审批、Research Card 和验证链，
产生含 `StrategyTargetActivationRef` 的 hash-bound、non-tradable `StrategyTarget` 收据；它没有订单、运行时或 broker 权限。

#### 核心类型关系图

```mermaid
classDiagram
    class FeatureSpec
    class FeatureVersion
    class FeatureLineage
    class FeatureBackfill
    class StrategyVersionReference
    class ExperimentFeatureInput
    class ExperimentSpec
    class ExperimentRun

    FeatureVersion ..> FeatureSpec : from_spec
    FeatureLineage ..> FeatureVersion : create
    FeatureBackfill ..> FeatureLineage : from_values
    ExperimentFeatureInput ..> FeatureBackfill : binds hashes only
    ExperimentSpec --> StrategyVersionReference : strategy
    ExperimentSpec --> ExperimentFeatureInput : feature_inputs
    ExperimentRun ..> ExperimentSpec : from_spec
```

Research 将 feature、lineage、backfill、strategy 和实验输入作为独立、hash-bound 的证据对象；
`ExperimentFeatureInput` 冻结 lineage/backfill hash，而不把可变回填对象嵌入实验。回测、验证、
Research Decision 与 Research Card 继续以显式输入/输出和 hash 连接，不能被本图误读为 broker 依赖或生产升级。

### Portfolio & Risk

Portfolio/Risk 合并多个 `StrategyTarget`，计算 allocation、exposure、limits、stress 和风险状态。规范组合器
`CanonicalPortfolioComposer` 产出 `PortfolioCompositionEvidence` 和 `PortfolioTarget v2`，并把策略集、allocation 与
净头寸的精确 hash 绑定。该输出不是批准，也不是可执行授权：

```text
eligible_for_execution=false
eligible_for_broker_order=false
```

组合审批必须以完整、未过期、同 scope 的市场/账户/风险/对账证据为条件。`UNKNOWN`、`WARN`、`BLOCK`、`HALT`、
人工恢复要求或 drift 任一存在时，均不得产生 approval、execution-provenance receipt、intent 或 broker mutation。认证的人类审批者与
最小权限数据库角色仍是外部前提；测试 issuer 不得成为生产接口。

#### 核心类型关系图

```mermaid
classDiagram
    class StrategyTarget
    class StrategyAllocationInput
    class PortfolioCompositionRequest
    class PortfolioTarget
    class PortfolioCompositionEvidence
    class CanonicalPortfolioComposer
    class PortfolioRiskReviewRequest
    class PortfolioRiskReview
    class ApprovedPortfolioTarget

    StrategyAllocationInput --> StrategyTarget : strategy_target
    PortfolioCompositionRequest *-- StrategyAllocationInput : allocation_inputs
    CanonicalPortfolioComposer ..> PortfolioCompositionRequest : compose
    CanonicalPortfolioComposer --> PortfolioCompositionEvidence : creates
    PortfolioCompositionEvidence *-- PortfolioCompositionRequest : request
    PortfolioCompositionEvidence *-- PortfolioTarget : portfolio_target
    PortfolioRiskReviewRequest *-- PortfolioCompositionEvidence : composition
    PortfolioRiskReview *-- PortfolioRiskReviewRequest : request
    ApprovedPortfolioTarget *-- PortfolioRiskReview : review
```

`StrategyTarget` 与 `PortfolioTarget` 都包含 `TargetPosition`，但前者属于单策略意图、后者是规范组合后的净目标。
`CanonicalPortfolioComposer` 只产生组合证据；`PortfolioRiskApprovalGate` 复核完整的风险输入并生成批准证据，
但不具备订单或 broker 提交能力。

### Trading & Execution

Trading/Execution 负责 execution plan、定价、router、paper / `ctp_sim` / CTP contract、持久化订单状态、
pre-trade gate、reconciliation、持仓、ledger 和 settlement。其强制链为：

```text
ApprovedPortfolioTarget
→ ExecutionPlan
→ PreTradeCheck
→ BrokerOrder
→ Fill
→ reconciliation / ledger / settlement
```

`Fill` 是外部成交事实，不等同于 `ClosedTrade` 或收益结论；后者只能由完整的持仓、账本与结算事实推导。

持久化 intent 必须在 broker 调用之前；callback 必须能处理 duplicate、out-of-order、reconnect、retry 和 idempotency。
未知订单、无法解释的成交或不一致账户状态导致 sticky `HALT`，且不能自动恢复。

`ctp_sim` 是本地、持久化的 CTP 语义模拟，不连接期货公司。`CtpSimCandidateExecutor` 是受控副作用边界：
它在锁内重验状态和报价，一次性消费 durable provenance，写入 intent 后才提交到隔离的 `ctp_sim`。它无法访问真实
CTP、Agent、CLI、scheduler、paper broker 或网络/进程面。真实 CTP front 在连接前拒绝
`CTP_REAL_FRONT_DISABLED`；这只是连接前的安全拒绝边界，不是适配器或集成完成声明。

#### 核心类型关系图

```mermaid
classDiagram
    class ApprovedPortfolioTarget
    class ExecutionPlan
    class RebalanceOrderPlan
    class PreflightResult
    class PlanPreTradeGate
    class OrderRequest
    class OrderResult
    class BrokerAdapter
    class DurableBrokerAdapter

    ExecutionPlan o-- ApprovedPortfolioTarget : approved_target
    ExecutionPlan *-- RebalanceOrderPlan : orders
    PlanPreTradeGate o-- ExecutionPlan : plan
    PlanPreTradeGate o-- PreflightResult : preflight
    PlanPreTradeGate ..> OrderRequest : validates once
    DurableBrokerAdapter --|> BrokerAdapter
    DurableBrokerAdapter o-- BrokerAdapter : delegate
    BrokerAdapter ..> OrderRequest : prepare and submit
    BrokerAdapter --> OrderResult : returns
```

`ExecutionPlan`、`RebalanceOrderPlan`、`OrderRequest`、broker 返回结果和成交事实均不是同一类型。
`PlanPreTradeGate` 要求匹配计划与通过的 preflight，并且每个计划项只允许消费一次；
`DurableBrokerAdapter` 在调用底层适配器前增加持久化、幂等和租约边界。图中没有计划直达真实 CTP 的路径：
不透明的 CTP-sim authority 只存在于隔离模拟提交边界，真实 CTP front 仍在连接前失败关闭。

## 4. 跨领域证据流

### 数据与研究

```text
provider
→ immutable artifact + authorization + quality
→ DatasetVersion / PIT snapshot
→ FeatureVersion
→ Experiment / Backtest / Validation
→ Research Card
```

每一跳都必须可按 hash、版本和 `available_time` 重放。任何授权、质量、lineage、PIT 或契约映射缺失都会中断流转。

### 情报到研究

```text
Document evidence
→ Event lifecycle / merge
→ mechanism / impact / context
→ feature-definition projection
```

投影只提供窄化、静态、hash-bound 的研究输入；它不读取风险或交易路径，更不会直接形成 BUY/SELL。

### 研究到仿真候选执行

```text
Research Card + named activation
→ StrategyTarget
→ CanonicalPortfolioComposer
→ PortfolioTarget v2 / composition_hash
→ portfolio-wide risk evidence
→ app-bound manual approval
→ ExecutionProvenancePreflight
→ ctp_sim guarded plan / intent / order
→ callbacks / reconciliation / ledger / settlement
```

`ExecutionProvenancePreflight` 只是应用层纯验证器：它重放 activation、组合、风险和执行证据并返回所有 eligibility 都为 false 的
短时 receipt，不能 submit 或控制 broker。只有最终、opaque 的 `ctp_sim` authority 可以消费收据，且必须在每次副作用前
完成新鲜状态和报价检查。任何直写 synthetic target、手工 hash、过期 quote 或 scope 漂移都被拒绝。

### 模拟盘：paper 与 `ctp_sim` 的完整执行数据流

`paper` 与 `ctp_sim` 都是隔离运行模式，却不是可互换的同一提交路径。`paper` 走 application 的常规本地纸面账户闭环；
`ctp_sim` 只接受 `CtpSimCandidateExecutor` 从完整 provenance 请求重放出的实际合约批次。两条路径的订单、成交、
持仓、账本和对账安全状态都会反馈到下一轮风控与 preflight；普通 `live.execute` 入口不能直接向 `ctp_sim` 提交订单。

#### `paper`：本地纸面账户闭环

```mermaid
flowchart LR
    subgraph inputs["受控输入"]
        paperSettings["Settings: broker=paper"]
        marketData["受控 market / signal 数据"]
        frozenTarget["冻结策略目标"]
    end

    subgraph paperExecution["application 与 paper 执行"]
        paperService["live_service<br/>execute_latest_targets_once"]
        paperConnect["PaperBrokerAdapter.connect"]
        paperSnapshot["PaperBrokerAdapter.sync_state<br/>BrokerStateSnapshot 与 paper_state quotes"]
        runtimeRisk["assess_runtime_risk<br/>安全开关、行情、资金、仓位与挂单"]
        paperPreflight["build_preflight_result<br/>PIT、合约、日历、账户与报价"]
        paperPlan["ExecutionPlan / RebalanceOrderPlan<br/>OrderRequest 不是 BrokerOrder"]
        executionLease["账户 execution lease"]
        paperRouter["OrderRouter<br/>准备前后重验风险"]
        paperDurable["DurableBrokerAdapter<br/>持久化 intent 与幂等身份"]
        paperBroker["PaperBrokerAdapter.submit_order"]
        pollPaper["poll_orders_and_fills_once<br/>或下一次同步"]
    end

    paperState[("PostgreSQL simulated broker state<br/>paper current snapshot + immutable transitions<br/>cash、positions、orders、fills、last_prices")]
    executionRecords[("PostgreSQL<br/>plan、order intent、ledger、<br/>reconciliation safety state")]
    paperReconciliation["reconcile_broker_state<br/>reconciliation / ledger / settlement"]
    noPaperRisk["NO NEW RISK<br/>风险或 preflight 未通过"]
    paperHalt["sticky HALT<br/>快照、订单或成交不可解释"]

    paperSettings --> paperService
    marketData --> paperService
    frozenTarget --> paperService
    paperService --> executionLease
    executionLease --> paperConnect
    paperConnect --> paperSnapshot
    paperState --> paperSnapshot
    paperSnapshot --> paperReconciliation
    paperSnapshot --> runtimeRisk
    marketData --> runtimeRisk
    frozenTarget --> runtimeRisk
    paperSnapshot --> paperPreflight
    marketData --> paperPreflight
    frozenTarget --> paperPreflight
    runtimeRisk --> paperPreflight
    paperPreflight -->|"can trade"| paperPlan
    paperPlan -->|"保存 ExecutionPlan"| executionRecords
    paperPlan --> paperRouter
    paperRouter --> paperDurable
    executionLease -.->|"提交期持有"| paperDurable
    paperDurable -->|"先写入 durable intent"| executionRecords
    paperDurable --> paperBroker
    paperBroker --> paperState
    paperState --> pollPaper
    pollPaper --> paperSnapshot
    paperReconciliation -->|"写入账本与安全状态"| executionRecords
    executionRecords -.->|"下一轮状态"| runtimeRisk
    executionRecords -.->|"下一轮状态"| paperPreflight
    runtimeRisk -.->|"阻断"| noPaperRisk
    paperPreflight -.->|"阻断"| noPaperRisk
    paperReconciliation -.->|"未知或冲突"| paperHalt
```

#### `ctp_sim`：受证据、人工审批和一次性授权约束的本地 CTP 语义模拟器

```mermaid
flowchart LR
    subgraph provenance["可验证的候选执行证据"]
        simSettings["Settings: broker=ctp_sim<br/>live disabled、kill switch disabled"]
        activation["具名 activation 与组合目标"]
        riskApproval["账户绑定的人工风险批准"]
        simFacts["账户快照、实际合约、日历、规则、<br/>fresh ctp_sim quotes 与 NORMAL 对账状态"]
        provenanceRequest["ExecutionProvenanceRequest"]
    end

    subgraph ctpSimExecution["受控 ctp_sim 提交边界"]
        simExecutor["CtpSimCandidateExecutor.prepare"]
        provenancePreflight["ExecutionProvenancePreflight<br/>重放数据、组合、风险与执行证据"]
        evidenceReceipt["短时 hash-bound receipt<br/>全部 eligibility 均为 false"]
        simBundle["CtpSimCandidateExecutionBundle<br/>规范化 OrderRequest"]
        simSubmit["bundle.submit<br/>提交前新鲜对账与预校验"]
        simLease["账户 execution lease"]
        simGate["不透明一次性 authority<br/>重验 expiry、approval、state 与 quote hash"]
        simDurable["DurableBrokerAdapter / OrderRouter<br/>持久化 intent、幂等与风险重验"]
        simBroker["CtpSimBrokerAdapter.submit_order"]
        simSync["sync_state_checked<br/>推进 partial / fill / cancel"]
    end

    simState[("PostgreSQL simulated broker state<br/>ctp_sim current snapshot + immutable transitions<br/>quotes、positions、orders、fills、balance、margin")]
    simRecords[("PostgreSQL 原子边界<br/>plan、durable order intent、<br/>provenance consumption、ledger、safety state")]
    simReconciliation["reconcile_broker_state<br/>所有 order_ref / fill 必须可解释"]
    noSimRisk["NO NEW RISK<br/>证据、审批、报价、账户或对账异常"]
    simHalt["sticky HALT<br/>漂移、未知订单或未知 fill"]

    activation --> provenanceRequest
    provenanceRequest --> simExecutor
    simSettings --> simExecutor
    riskApproval --> simExecutor
    simFacts --> simExecutor
    simExecutor --> provenancePreflight
    provenancePreflight --> evidenceReceipt
    simExecutor --> simBundle
    evidenceReceipt -.->|"证据，不是提交能力"| simBundle
    simBundle --> simReconciliation
    simState --> simSync
    simSync --> simReconciliation
    simReconciliation -->|"NORMAL"| simLease
    simLease --> simSubmit
    simSubmit --> simGate
    simGate --> simDurable
    simDurable -->|"原子写入记录"| simRecords
    simDurable --> simBroker
    simBroker --> simState
    simReconciliation -->|"写入账本与安全状态"| simRecords
    simRecords -.->|"解释 order / fill"| simReconciliation
    provenancePreflight -.->|"任一验证失败"| noSimRisk
    simSubmit -.->|"状态、报价、审批或租约异常"| noSimRisk
    simGate -.->|"过期、漂移或重验失败"| noSimRisk
    simReconciliation -.->|"不可解释"| simHalt
```

`ExecutionProvenancePreflight` 的 receipt 仅是证据，不能授权提交；`CtpSimCandidateExecutor` 自行重放请求，
再由私有 authority、fresh quotes、正常对账状态、执行租约和一次性 consumption 共同约束 `ctp_sim` 副作用。
当前默认运行环境没有可用的人工批准签发器，因此上图的正向提交支路只有在已提供经验证批准的隔离 composition
中可达；缺少任一证据时立即 `NO NEW RISK`。`PaperBrokerAdapter` 与 `CtpSimBrokerAdapter` 都只写本地状态文件，
绝不连接期货公司前置。

### 真实 CTP / 实盘：当前可达的数据流以拒绝结束

真实 CTP 不能被画成一条已接通的交易链，因为仓库当前不存在这样的路径。下面展示的是所有当前可达的输入、
安全门和拒绝点：授权数据、画像、账户、日历、报价和审批是未来实盘所需的证据，却不足以绕过 application 或
adapter 的 fail-closed 边界。图中没有“真实 CTP 前置已连接”或“真实 BrokerOrder 已提交”的节点。

```mermaid
flowchart LR
    subgraph futureInputs["未来实盘所需但当前不足的输入"]
        liveConfig["Settings: broker=ctp<br/>即使显式 live enabled"]
        liveEvidence["授权数据、production profile、账户、<br/>实际合约、日历、报价与风险审批"]
        liveRequest["请求 CTP 执行"]
    end

    safetyGate{"画像治理、<br/>safety switch 与运行时证据通过？"}
    stopSafety["NO NEW RISK<br/>默认开关、画像或证据失败"]
    liveService["application / live_service"]
    pickBroker["_pick_broker"]
    applicationRefusal["CTP_EXECUTION_ADAPTER_REQUIRED<br/>未实现连接、报单和回报状态机"]
    directFront["直接提供真实 CTP front / SDK / credentials"]
    ctpAdapter["CtpBrokerAdapter.connect"]
    adapterRefusal["CTP_REAL_FRONT_DISABLED<br/>front.connect 前拒绝"]
    fakeFront["FakeCtpFront<br/>仅 unit-test double"]
    fakeOnly["fake-only contract test"]
    noMutation["无真实连接、无 BrokerOrder、<br/>无账户变更、无真实回报"]

    liveConfig --> safetyGate
    liveEvidence --> safetyGate
    liveRequest --> safetyGate
    safetyGate -->|"否或未知"| stopSafety
    safetyGate -->|"即使假设未来通过"| liveService
    liveService --> pickBroker
    pickBroker --> applicationRefusal
    applicationRefusal --> noMutation
    directFront --> ctpAdapter
    ctpAdapter --> adapterRefusal
    adapterRefusal --> noMutation
    fakeFront --> ctpAdapter
    ctpAdapter -->|"仅精确 fake"| fakeOnly
```

`live_service._pick_broker()` 在 `broker=ctp` 时即拒绝连接、报单和回报状态机；即便绕过该 composition root，
`CtpBrokerAdapter` 也只允许精确的 `FakeCtpFront`，任何真实 front 都在连接前以 `CTP_REAL_FRONT_DISABLED` 失败。
所以这张图描述的是当前实盘的完整安全数据流，而不是生产接入完成声明。真正的后续链还需要受授权的实时数据、
production profile 和运行时制品、真实 CTP SDK/front/credential/callback 实现，以及独立人工审批与生产运维前提。

## 5. AI 边界

AI 只能经封闭、typed 的 `TypedResearchToolApi` 访问 research-only 工具：它不可达 portfolio/risk/trading/live、
broker、配置、数据库、网络、进程或文件系统。Research、Intelligence 与 Data Quality Agent 的输出均为 non-tradable；
Ops Agent 只能读取单项 typed diagnostic snapshot。

`DurableResearchAgentRunner` 记录 hash-only 的 audit event 和 trace，用于可追溯性，但它不是 Agent tool，也不是控制路径。
不得持久化 raw prompt、chain-of-thought、原始查询、文档、结果、rationale 或异常 payload。Agent 无权 approve、
enable-live、resume-risk、submit 或连接 broker。

## 6. 配置、运行与部署边界

运行设置是显式、typed、validated 的：活动配置仅为 `configs/app.yaml` 与 `.env`，`configs/app.local.yaml`
已经废弃，发现它即失败关闭并要求完整迁移至 `app.yaml`；tracked 示例永远保持 paper / live-disabled。画像、source、
研究准入、Contract Master、instrument、calendar 和策略配置各有职责，不能以一个 YAML 暗中替代另一个。

生产目标仅 Linux x86_64；Windows/Linux 均为开发和部署控制端。可信部署路径是：

```text
Windows/Linux workstation
→ just / Python deployment controller
→ SSH stdin
→ Linux root-owned signed release gate
→ migrate → restart → health → promote
```

release gate 在 root-owned transaction 内验证签名、manifest、控制/运行 bundle 和固定入口。迁移开始后的失败只能人工恢复；
自动化不降级、不重试迁移、不绕过 health gate。systemd 服务采用 root-owned release/env snapshot、最小可写路径、
`ProtectSystem=strict` 和 loopback-only dashboard。备份、恢复演练和生产 DR 的详细操作及限制见[运行手册](OPERATIONS.md)。

## 7. 架构约束的执行

架构不是只靠文字维护。`tests/architecture/` 检查分层、循环、公共 API 和特殊候选执行 seam；领域 contract、integration、
simulation、golden、regression 与 failure tests 共同约束实现不得越过本文的依赖和安全边界。

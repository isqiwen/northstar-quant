# AI 自动因子挖掘与回测架构

本文定义 Northstar Quant 中 AI 辅助因子挖掘的长期架构与当前已落地的最小安全边界。它不让
LLM 直接“找一个能交易的策略”，而是把 AI 限制为一次性提出受约束的研究候选，再由确定性、可重放的
因子研究流水线独立验证。

本专题依赖并不替代：

- [连续日线 PIT 因子研究流水线](FACTOR_RESEARCH_PIPELINE.md)：严格 PIT 因子计算、回测、成本和 OOS；
- [架构设计](../ARCHITECTURE.md)：领域依赖、存储职责和执行边界；
- [数据、研究、AI 与安全治理](../GOVERNANCE.md)：授权、人工审批与 AI 权限政策。

## 1. 不可变的安全结论

AI 因子挖掘的任何结果固定具有：

```text
research_only = true
candidate_admission_eligible = false
simnow_handoff_allowed = false
```

所以它不能创建或变成：

```text
FactorCandidateProposal
!= StrategyTarget
!= PortfolioTarget / ApprovedPortfolioTarget
!= ExecutionPlan
!= BrokerOrder
```

高 IC、较好分位收益、低换手、正向回测或 OOS 收益都只是一份研究证据。后续仍必须经过：

```text
Feature
→ Experiment
→ Backtest
→ Validation
→ OOS / Stress
→ Research Decision
→ explicit HumanResearchApproval
→ separate Research → Portfolio/Risk → Execution gates
```

AI 不得批准 Research Decision、修改风险限制、解封 live、提交订单、连接 broker，或以结果“看起来很好”为由重试/
扩展搜索。

## 2. 总体拓扑

```text
future provider adapter
    │  only sealed metadata request
    ▼
FactorCandidateGenerator
    │  structured candidate batch + hash-only receipt
    ▼
application/ai_factor_mining.py
    │  one generation, no retry, no OOS feedback loop
    ▼
application/factor_mining_tools.py
    │  one closed typed capability
    ▼
application/factor_mining_campaign.py
    │  trusted composition: ArtifactStore + sealed DecisionReplayPlan
    ▼
application/factor_research.py
    │
    ▼
strict Feature materialization → development-only PIT replay
    │
    ▼
FactorMiningDiscoveryResult (AI-visible; IS + validation only)
    │  trusted local researcher only
    ▼
FactorMiningSelectionCommitment
    │  explicit, one-shot release
    ▼
FactorMiningOOSRelease (research-only)
```

这里有两个故意分开的能力域：

1. **AI-facing 域**只拥有候选生成器和封闭 typed tool。它看不到 `ArtifactStore`、`DecisionReplayPlan` 对象、
   DataFrame、FeatureRegistry、数据库、配置、文件系统、网络、账户或交易能力。
2. **可信 campaign 域**才持有不可变数据版本和 PIT 回放计划，并且只能调用现有
   `FactorResearchPipeline`。它不接受代码、SQL、文件路径或由 AI 提供的数据选择器。

现有静态 `ResearchAgent` 继续服务于 `STATIC_AS_OF_VIEW_NOT_DECISION_REPLAY` 工作流；它不会因自动化因子
挖掘而被放宽或复用为逐 checkpoint PIT 执行器。

## 3. 模块归属

| 模块 | 职责 | 禁止事项 |
|---|---|---|
| `research/factor_mining/models.py` | 纯领域模型：campaign、预算、参数域、候选、receipt、成本情景和选择 policy | 访问 application、broker、数据库、文件或网络。 |
| `research/factor_mining/protocol.py` | discovery、selection commitment 和 OOS release 的不可变研究合同 | 容纳 OOS 进入 discovery，或生成任何交易对象。 |
| `research/factor_mining/evaluation.py` | 按冻结阶段生成证据、成本情景与边界归属 | 访问 ArtifactStore、provider、文件、数据库或 execution。 |
| `research/factor_mining/validator.py` | 将候选与冻结 policy 做纯验证，生成受控 `FactorDefinition` | 注册 Feature、读取数据、执行回测。 |
| `application/ai_factor_mining.py` | 生成一次候选并经封闭工具提交；仅限单进程本地防重放 | 直接导入 campaign runner、数据/风险/执行能力，或自动重试。 |
| `application/factor_mining_tools.py` | 唯一 AI → 可信研究的 typed capability | 连接 provider、数据库、broker 或动态 dispatch。 |
| `application/factor_mining_campaign.py` | 组合 `ArtifactStore`、冻结 `DecisionReplayPlan`、development replay、commitment 与显式 OOS release | 暴露 researcher-only commitment/release 给 AI、升级策略或持久化交易状态。 |
| `application/durable_factor_mining_campaign.py` | PostgreSQL reservation/audit、声明验证、资源预算、一次性 local research composition 与 explicit replay authorization | 向 AI tool、broad CLI、scheduler、portfolio/risk、broker 或 execution 泄漏数据库或恢复能力。 |
| `application/factor_mining_worker_supervisor.py` | DB-free Linux worker 的累计 CPU/wall-clock、地址空间与守卫可用性边界 | 接受数据库/broker/CLI 权限，放宽既有宿主 resource limit，或将 timeout/cancellation 伪造为可重试成功。 |
| `application/factor_mining_campaign_cli.py` | 单独的 hash/ID-only durable campaign 操作面：run、inspect、authorize-replay | 接受 raw prompt/response、文件、dataset/config/profile/provider/secret 参数，或调用 trading runtime。 |
| `application/factor_research.py` | 严格 PIT 特征、development 截断 replay、完整回测和固定 OOS 执行器 | 接受 AI 原始文本、DataFrame 或交易对象。 |

`FactorMiningCampaignRunner` 是从 AI 概念到真实研究运行的唯一深接口。可信 composition 必须先构造 runner，再把它
绑定到 `FactorMiningToolApi`；runner 构造时预检 canonical feature、参数 schema 与输入契约，故无效的宿主 policy
会在任何 provider 调用之前失败。未来加入 provider、审计、队列或 scheduler 时，必须保持这一层的输入仍为已验证的
结构化 receipt，而不是把新能力散布到 feature、backtest 或 execution 模块。

## 4. 冻结的 campaign 控制面

`FactorMiningCampaignSpec` 在生成候选之前固定以下事实并计算 `campaign_hash`：

- 精确的 `DecisionReplayPlan` hash 和全部 `DatasetVersion` hashes；
- `selection_at`：必须严格早于冻结 OOS folds 的第一个决策时间；
- 完整 `FactorPipelineTemplate`：成本、滑点、持有期、目标波动、权重限制、初始资金、风险模型因子与冻结
  walk-forward OOS folds，以及冻结的 `FactorRobustnessPlan` 模板；
- `FactorMiningSelectionPolicy`：IS/validation 最小样本、方向归一化的指标阈值、Bonferroni sign-test、固定
  candidate budget、成本情景、选择上限与 `FLAT_START_FORCED_CLOSE` 边界模式；
- 允许的 canonical feature primitives、离散参数网格和方向；

`FactorPipelineTemplate.robustness_plan` 使用唯一的 `candidate_alpha` 占位 factor 身份。可信 host 只有在候选已经通过
primitive/参数域验证后，才会把这个占位符确定性地绑定到 host 生成的 `alpha_<candidate_id>`；它不会接受 AI 提供的
factor ID、额外参数点或阈值。一个 campaign 的 robustness 模板因此必须覆盖该 campaign 暴露的同一参数 schema；具有
不同参数 schema 的 canonical primitives（例如 `lookback_bars` 与 `window_bars`）应声明为各自独立、可审计的 campaign，
且每个冻结邻域点都必须落在该 campaign 中每个 primitive 的有限可信参数域内；不满足此条件的 campaign 在候选生成前
即失败关闭。
而不是在运行时把参数语义混在一起。
- 候选数预算；
- generator identity、model revision 与 prompt template hashes。

AI 不拥有上述字段的写入权。它不可以改：

```text
dataset version
DecisionReplayPlan
available_at rule
cost / slippage
holding period
OOS boundary
risk model factor
position / gross limits
```

可信 runner 在启动时再次比对 campaign 与实际 `DecisionReplayPlan`：

```text
campaign.decision_replay_plan_hash == plan.schedule_hash
campaign.dataset_version_hashes == all plan checkpoint dataset hashes
all folds share the exact IS and validation periods
all OOS folds are ordered, non-overlapping, and after validation
all retained IS/validation outcomes evaluate strictly before selection_at
campaign.selection_at < earliest frozen OOS decision_at
```

这里的 `selection_at` 是 **冻结 discovery 证据与选择的时间**，不是回测的数据可见上界。development replay 只重放
`decision_at <= selection_at` 的 checkpoint；它不物化 OOS 特征、proposal、backtest 或 manifest。当前切片尚未支持把市场、基本面或
情报上下文给 AI；未来若需要，必须先增加一个可验证、availability-bound 的 immutable context/universe artifact，
并在 runner 中绑定和过滤，不能只附加一个不透明 hash。任一不一致即失败关闭，不能以 `latest`、当前数据或静态缓存补全。

## 5. 第一阶段候选语言

第一阶段故意不是自由 Formula DSL。AI 只能从可信宿主创建的 `FactorPrimitive` 中选择：

```text
primitive_id
→ canonical feature_id
→ allowed direction {-1, +1}
→ finite, enumerated parameter values
```

例如一个被允许的 primitive 可以是：

```text
momentum_roc
→ momentum.roc
→ lookback_bars ∈ {1, 2, 3}
→ direction ∈ {-1, +1}
```

`FactorCandidateProposal` 不包含下列字段，构造时也会拒绝它们：

- Python、notebook、shell、SQL 或表达式字符串；
- callable、动态 import、`eval` / `exec`、UDF 或 FeatureComputer；
- DataFrame、raw market data、文件路径、URL、secret 或当前时间；
- `latest` / 未钉住 dataset 或 feature version；
- order、signal、portfolio、position、risk budget、cost 或 OOS 参数。

validator 必须同时满足：

```text
candidate.campaign_id matches campaign
primitive exists in campaign policy
direction is allowed
parameter names exactly match the primitive
every parameter value is in the finite trusted grid
```

通过后，系统而非 AI 生成单一 `FactorDefinition`，并固定 `role=alpha`、`risk_budget=1.0`。风险模型因子、配置其余部分
与 factor ID 均由 `FactorPipelineTemplate` 生成。未知 primitive、越界参数、重复因子定义或任意未经声明的语义都必须
reason-code reject。

自由组合 AST、跨特征运算、正交化、因子合成和自动 feature invention 不是当前能力。它们只能在分别定义 PIT 语义、
资源预算、golden fixture、回归测试、可解释 manifest 和人工审阅路径之后，作为新的、版本化的 DSL 扩展。

## 6. 一次性生成、OOS 隔离与防过拟合

`AIFactorMiningAgent` 的流程是严格的一次性：

```text
sealed campaign
→ one FactorCandidateGenerationRequest
→ one FactorCandidateGenerationReceipt
→ validation
→ one trusted development-only discovery evaluation
→ FactorMiningDiscoveryResult
```

AI-facing agent 仍只提供单进程、一次性的 typed capability；它本身不持有数据库或跨进程互斥。自动 research 入口必须改经
独立的 durable local campaign runner：先验证 receipt-free declaration 和数据授权，再在 PostgreSQL 中事务性 reserve 一个
request identity，最后才可调用 generator 或启动研究 computation。receipt 只保留 provider output、model 与 prompt 的
hashes，不保存 raw prompt、raw response、chain-of-thought、rationale 或 confidence。

生成器没有 OOS 结果输入，也没有“拿到本次回测结果再提下一组参数”的回调；候选上限在生成前冻结。封闭 tool 还会
验证 discovery 返回结果与 receipt 中每个 candidate ID/hash **一一对应**，不接受遗漏或替换的结果。`FactorMiningToolApi` 和
`AIFactorMiningAgent` 不拥有 `commit_selection` 或 `release_oos` 方法。想继续搜索必须创建新的
campaign，重新冻结 policy、输入和预算，并经过独立的研究治理决定；不得在一次 OOS 后由 agent 静默扩展网格。

durable campaign ledger 已以 PostgreSQL append-only/hash-linked campaign/request/event 记录提供跨进程边界。campaign
root 绑定 receipt-free declaration hash/snapshot，request event 链绑定 initiating actor。其状态机
将 crash、timeout、cancellation、partial failure、connection/write ambiguity 和 restart 都保留为 `UNRESOLVED`，不把
它们伪造为失败或成功，也不会自动 resume/retry。只有受信 verifier 确认 verifier-issued approval reference、approver 与旧
request 精确绑定后的显式人工 `ReplayAuthorization` 才能创建一个绑定旧 request 的新 request identity；它将原 request 以
`REPLAY_AUTHORIZED` 终结，而不恢复或修改原 reservation。CLI 只提交 reference 与 source hash，不接受 caller-supplied
approver/evidence；当前默认 verifier 不可用时拒绝写入。Foundation 的 raw replay DTO/writer 是该 verifier bridge 的私有
实现，architecture contract 禁止其他 application surface 导入；未来外部 verifier 还需要独立 database role，direct DB writer
不能被视为人类 approval authority。内存级去重仍不是
durable 账本的替代品。

## 7. 阶段 ledger、选择冻结与一次性 OOS release

首版协议只支持一个全局隔离的时序：所有 fold 必须共享同一段 IS 和 validation，之后才是至少两个有序、不重叠的
OOS fold。它明确拒绝“一个 fold 的 OOS 变成下一 fold 的 IS”的滚动训练布局；那是另一个需要逐 fold commitment 的协议，
不能伪装成单次 OOS release。

```text
shared IS → shared validation → maturity / embargo → selection_at → OOS 1 → OOS 2 → …
```

每个 `FactorMiningStageEvidence` 都绑定 candidate、campaign、fold、时间段、因子分析、outcome、成本情景及边界模式的
hash。其规则是：

- 只有 origin 与 evaluation checkpoint 都属于同一 declared stage 的 forward outcome 才能进入该 stage；跨 IS/validation/OOS
  边界的 outcome 一律 purge，绝不重新分配给相邻阶段；
- 发现阶段每一个保留 outcome 的 `evaluation_at` 必须严格早于 `selection_at`；任何更晚、未知或缺失的 development outcome
  均失败关闭；
- 因子原始 `analysis_hash` 保留 feature 的原始关系；筛选用指标依 `FactorDefinition.direction` 归一化，使 momentum 与
  contrarian candidate 都以“正值更好”的同一预声明阈值和 sign-test 评估；
- 每个 stage 按 `FLAT_START_FORCED_CLOSE` 独立回测：起点无继承仓位，目标延迟只在 stage 内生效，最后有效权重的平仓佣金、
  最低佣金、滑点与换手都计入该 stage；这是连续研究序列的成本近似，不是订单/成交模拟；
- selection 对 development ledger 应用冻结阈值和 `campaign.budget.max_candidates` 的 Bonferroni one-sided sign-test，并按
  validation Rank IC、quantile spread、turnover、candidate ID 作确定性排序。

可信本地研究者随后显式调用：

```text
FactorMiningDiscoveryResult
→ FactorMiningSelectionCommitment
→ FactorMiningOOSRelease
```

commitment 可为空；空 commitment 不能释放 OOS。一个 non-empty commitment 在同一 runner 内只能 release 一次，且 release
只返回 selected candidate 的 OOS stage evidence。它仍是 `research_only`，不会形成 `ResearchDecision`、SimNow handoff、
portfolio target 或订单。若进程重启、崩溃或局部失败，durable ledger 将 request 状态保留为 `UNRESOLVED` 并拒绝重放；
它绝不靠内存状态把未知 release 当作可继续执行。

## 8. PIT 数据与确定性回测

每个验证通过的候选先以 `run_discovery(plan, selection_at)` 取得 development replay。只有 researcher 创建了 selection
commitment 后，selected subset 才通过完整：

```text
FactorResearchPipeline(ArtifactStore, FactorPipelineConfig).run(DecisionReplayPlan)
```

执行。既有流水线仍负责：

- 根据每个 checkpoint 重新选择 immutable `DatasetVersion`；
- 强制 `available_at <= decision_at`；
- 只接受受授权的连续日线 schema；
- 注册 canonical feature 并严格 materialize；
- 产生显式 warm-up 零权重，而不是沿用旧目标；
- 固定 commission、slippage、weight-return 回测；
- 产生 IC / Rank IC、quantile return、turnover、stability、walk-forward OOS 与 look-ahead certificate；
- 对每个候选的受限参数网格重跑冻结的 robust 参数点，并从 immutable PIT snapshot 以 canonical feature 和精确参数重新
  materialize 输入；
- 将数据、特征、配置、代码、分析、回测、OOS、robustness plan/result 和证书 hashes 写入
  `FactorResearchRunManifest`。

discovery 不含完整 run manifest、总回测或 walk-forward/OOS hash；这些只有在 release 后的 OOS result 中出现。相同
campaign、receipt、artifact、PIT plan、代码与模板必须得到相同 discovery / commitment / release hash。输入修订、参数、
成本或任何 frozen hash 变化都必须是新的运行，不能复用旧结论。

## 9. 结果与存储职责

确定性 research protocol 返回不可变内存对象，供显式本地离线研究和测试使用：

```text
FactorCandidateDiscoveryResult / FactorMiningDiscoveryResult
FactorMiningSelectionCommitment
FactorMiningOOSReleaseResult / FactorMiningOOSRelease
```

它们仅描述：

- `REJECTED_INPUT` 或 `REJECTED_DISCOVERY`：输入或 development stage 失败关闭；
- `DISCOVERY_EVALUATED`：只含 IS / validation ledger；
- `SELECTED_FOR_OOS_RELEASE` / reason-coded non-selection：冻结的研究审阅范围；
- OOS release：只含已选候选的 OOS evidence。

持久化 campaign boundary 已提供 durable reservation、跨进程重复 request 拒绝、restart 后的 unresolved 语义和有限并发
控制；它仍不是 scheduler、队列服务或交易 worker。进程重启后不会猜测在途 generator/compute 的结果，因此不能将未知
状态视为可以重试。

当前 durable 实现遵守以下存储边界：

| 数据 | 目标存储 | 规则 |
|---|---|---|
| 大型 factor exposure、features、backtest/OOS outputs、run manifest | 受治理的 immutable Parquet artifacts | 内容 hash、lineage、license/retention、PIT 语义与可重放 manifest。 |
| campaign registration、request reservation、receipt/selection/OOS/result/resource hashes、失败 reason code、replay authorization、审计链 | PostgreSQL | append-only / hash-chained；不保存 raw prompt、response、chain-of-thought、secret、异常原文或交易状态替代品。 |
| 临时查询/索引 | DuckDB / tool-owned SQLite | 只能是可重建的研究分析或本地工具数据；不能成为 PostgreSQL 或 artifact 的 fallback。 |

durable runner 先建立 PostgreSQL reservation，再调用 generator/compute；discovery/selection/OOS reservation 必须在
实际 OOS release 前写入。崩溃、超时、取消、资源计量或状态未知时保持 `UNRESOLVED`，禁止自动重跑、发布、OOS release
或新回测。它也不能把 raw LLM 文本写进数据库、日志、邮件、报告或 CLI 输出。
每个 DB-free worker stage 都在 Linux 主线程中接受同一 attempt-start 的累计 `ITIMER_REAL` / `ITIMER_PROF` deadline 和
不放宽既有宿主 cap 的 `RLIMIT_AS` guard；因此生成、discovery、OOS 或 publication 都不能通过分阶段重置 CPU/wall-clock
预算。guard 不可用或被触发是非确定性 worker outcome，已预留 request 只可人工 inspect/replay。

## 10. 本地 Research Run Bundle 与独立 CLI

P11 将“可以在 Python 里临时拼一个 runner”的能力收束为一个小而深的本地操作面：先由可信的本地
composition 构造**receipt-free typed campaign declaration**，再发布为一个不可变 derived artifact。durable ledger
transactionally reserves that declaration before it receives any generation receipt; only then can the host construct the full
receipt-bound run bundle. 独立
`northstar-research` CLI 只接受该 artifact 的 SHA-256 snapshot，不读取 CSV/Parquet、JSON 文件、DataFrame、
notebook 状态、`latest` 选择器、profile 或 live 配置。

```text
verified DatasetVersion hashes + exact DecisionReplayPlan
 + sealed declarative campaign + fixed LocalFactorMiningRunConfig
 + FactorMiningRunnerResourceBudget
          │ publish with exact DatasetVersion parents
          ▼
LocalFactorMiningCampaignDeclaration (typed, receipt-free, hash-bound)
          │ PostgreSQL reserve request before generator/compute
          ▼
hash-only generation receipt → LocalFactorMiningRunBundle
          │ trusted research composition
          ▼
discovery → selection commitment → explicit OOS release
          │
          ▼
exposures / weights / analyses / discovery / selection / OOS / report artifacts
          │
          ▼
LocalFactorMiningRunManifest artifact
```

每一个输出均为 `DerivedArtifact`：content hash、直接及递归 lineage、源授权、PIT input 和 retention snapshot 都由
`ArtifactStore` 校验。loader 还要求固定的有向证据图：definition → exposures → weights/analyses → discovery →
selection → optional OOS → report → manifest；OOS 存在时还要解码 sealed typed proof，重新构造 config、experiment、analysis、
robustness、walk-forward 与 run manifest，并验证它们与 selected record、release 和报告投影的一致性。`inspect` 是静态完整性
检查；只有 `replay` 会从 sealed bundle 重新执行确定性 PIT computation 并要求精确重现 manifest。空 selection 不得伪造 OOS
或 robustness 结论。任意未知 role、错误直接 parent、bundle/retention 不匹配或 manifest reference 不匹配都拒绝。固定 config 强制
`automatic_cleanup=false`，记录 retention policy ID 和 retention days；它不让
自动清理删除研究证据。

固定 config 同时记录人类可读的 `code_revision`（必须匹配 sealed campaign template）和不可变的
`code_revision_hash`。后者必须来自可信的本地 build/registration evidence，而不是 `HEAD`、branch 或 moving tag；CLI
不能填写或替换它。时间戳、UUID、临时路径和当前时钟不进入 bundle/result identity，因此同一 bundle、数据、固定代码
attestation 和 config 可验证地引用同一 manifest/result identity。bundle 与 manifest 的 wire payload 在解码后还必须逐字节
回序列化为同一 typed declaration；`false` 伪装的 optional hash、时区别名或任何会被解析器静默规范化的第二种表示都会拒绝。

本地命令为：

```text
northstar-research factor run --bundle-snapshot <definition-snapshot-sha256>
northstar-research factor replay --bundle-snapshot <definition-snapshot-sha256> --expected-manifest-snapshot <manifest-snapshot-sha256>
northstar-research factor inspect --artifact-snapshot <definition-or-evidence-or-manifest-snapshot-sha256>
```

`replay` 先完整验证 expected manifest 和全部受治理输出，再在内存中重建完整 evidence graph（payload、snapshot、
lineage、manifest/result identity 均包含在内）；它**不发布第二套 evidence artifacts**。任何 bundle、决策 identity、
证据制品身份或 expected manifest 不匹配都会在写入前失败关闭。任一 DatasetVersion、
artifact blob/lineage、authorization、plan/campaign binding、PIT 证据或 manifest output 未知/损坏时同样失败关闭。CLI
不会给 AI 额外权限；typed declaration 的发布仍是可信的 composition boundary。独立 durable campaign CLI 才是
PostgreSQL-backed、并发安全、资源受限的自动化入口，并且只接受 stable request IDs 或 SHA-256 references，不能接收
raw input、prompt、dataset、config、profile、provider 或 secret。

独立 CLI 模块不导入 broad `northstar` CLI、broker、live service、交易 scheduler、portfolio approval、
`portfolio_risk`、`trading_execution` 或数据库 runtime。它只读取活动 runtime 配置中本地 artifact root；这不是策略、
风险或 live profile 配置。

## 11. Bounded runner 与后续 scheduler 的边界

当前 local durable runner 已执行 frozen candidate、并发、CPU、内存、wall-clock、数据行数和 artifact byte budget；
取消或任一资源计量未知时失败关闭，不能发布、release OOS 或开始新 request。将来若由单独的 research job 调度，仍不得
复用 live/paper/ctp_sim 交易 scheduler，也不得获得 broker capability。一个 research job 至少需要：

- campaign hash、操作人/服务身份、离线环境和明确 source authorization；
- 最大候选数、最大并发、CPU、内存、wall-clock、数据行数与 artifact 字节预算；
- queue isolation、取消语义、重复 receipt 拒绝和 hash-only audit；
- 每候选独立失败证据与全 campaign fail-closed 状态；
- OOS 仅在封存策略允许的唯一 release 后暴露给人工研究审阅。

任何资源、数据授权、PIT、模型身份、prompt identity、artifact integrity 或审计状态未知时，job 不应生成新候选或运行新回测。

## 12. 与六个领域的关系

```text
foundation
  └─ hashes, time, audit infrastructure
data
  └─ authorized immutable DatasetVersion / ArtifactStore / PIT facts
intelligence
  └─ optional evidence-bound research context, never trade direction
research
  └─ factor_mining policy + validator + factors + validation + backtest
application
  └─ AI tool boundary and trusted campaign composition
portfolio_risk
  └─ no dependency from AI mining path
trading_execution
  └─ no dependency from AI mining path
```

Intelligence 可在未来提供已授权、evidence-bound 的背景假设，但 LLM 输出和 event confidence 仍不是 factor truth；
它们不能自动产生 BUY/SELL 或绕过因子研究。`research` 绝不导入 broker，`portfolio_risk` 与
`trading_execution` 也不得反向依赖此候选路径。

## 13. 当前实现与后续扩展边界

当前已提供：

- 受限 primitive/参数网格、冻结 template/budget/campaign、hash-only generation receipt；
- 在 provider 之前进行 canonical feature/schema/input-contract 预检的可信 campaign runner；
- 单一 discovery-only typed tool、严格 PIT development runner，以及 receipt → discovery result 的精确一一覆盖检查；
- 冻结 selection policy、stage ledger、deterministic selection commitment、显式一次性 OOS release 与 terminal flatten 成本；
- hash-addressed local research run bundle、独立 `northstar-research` run/replay/inspect CLI，以及 content/lineage/
  retention-bound 的 exposure、weight、analysis、selection/OOS evidence、report 和 manifest artifacts；
- receipt-free campaign declaration、PostgreSQL transactional request reservation、append-only/hash-linked campaign audit，
  以及对 duplicate/concurrent/restart/crash/timeout/cancellation/partial failure 的 `UNRESOLVED` fail-closed 语义；
- 独立 hash/ID-only durable campaign CLI（run / inspect / authorize-replay）、verifier-backed 显式人工 replay authorization、候选/并发/
  CPU/内存/wall-clock/data-row/artifact-byte budget 与取消失败关闭；
- unit、architecture 与真实 PIT factor pipeline e2e 覆盖。

当前没有提供：

- 真实 LLM/provider adapter、网络调用、API key 或 prompt 存储；
- 自动 scheduler、队列、分布式 worker 编排或任何交易 scheduler 复用；
- 可供 AI 消费的 immutable context/universe artifact 或把它绑定到实际回测标的的机制；
- 多因子组合搜索、自由 DSL、auto-feature coding、自动调参循环；
- fundamental/carry/basis/inventory/seasonality 的新 PIT 数据模型；
- SimNow、paper、ctp_sim 或 live handoff。

这些后续能力都必须以新的、独立的 data authorization、PIT 契约、resource policy、artifact/audit 实现和测试工作包进入，
不能通过向候选文本增加“更多权限”来实现。

# 连续日线 PIT 因子研究流水线

本文定义首个受限、离线因子研究闭环。它研究的是**连续日线横截面**上的历史因子表现；
所有产物均为 `research_only`，并带有
`candidate_admission_eligible=false`。它不是策略激活、组合审批、SimNow、paper、`ctp_sim` 或实盘交易入口。

规范研究链为：

```text
immutable DatasetVersion + DecisionReplayPlan
→ 每个 checkpoint 的 PIT 日线截面
→ strict feature materialization
→ FactorResearchExperiment
→ FactorExposure
→ ex-post factor analysis
→ FactorPortfolioProposal
→ weight_return（显式成本/滑点）
→ frozen walk-forward OOS
→ frozen robustness study
→ research-only handoff
```

## 1. 输入与点时边界

### 1.1 唯一允许的市场输入

首个切片只消费不可变 `DatasetVersion` 中的连续日线价格与成交量行。每行至少须能稳定标识研究标的、
交易日/事件时间、价格、成交量及行级 `available_at`；数据集、快照和其内容 hash 必须可审计。输入授权只能是
`historical_backtest` 或 `internal_research`。

研究运行必须提供 `DecisionReplayPlan`。对每个决策 checkpoint，运行器从该 immutable
`DatasetVersion` **重新选择**当时可见的行，而不是复用前一个 checkpoint 的 DataFrame 或取当前最新版：

```text
row.available_at <= checkpoint.decision_time
```

`latest`、当前时钟、可变本地文件、裸 DataFrame、未绑定 snapshot 的缓存，以及静态
`FeatureBackfill` 都不是本流水线的决策输入。数据修订也不可以回写历史可见状态；checkpoint 只能看到在其
决策时点已经可见的那个版本。

### 1.2 失败关闭

以下任一情况使该 checkpoint 无效，不能生成暴露或建议权重：DatasetVersion 或 snapshot 不可验证、
授权不匹配、`available_at` 缺失/晚于 checkpoint、同一 PIT 键无法唯一选择、日线历史不足、价格或成交量无效、
或因子配置/代码身份缺失。不得以插入未来数据、前向填充未知值、猜测可用时间或回退到当前数据来继续。
在连续研究回测中，此类 checkpoint 必须显式形成零权重目标，以免稀疏目标面板的前向填充沿用已经失效的建议。

连续价格仅是研究信号载体。它不代表可成交合约，也不携带实际合约、换月、保证金、费率或交易日历的事实。

## 2. 严格特征与因子暴露

每个有效 checkpoint 独立物化严格特征。特征历史只包含该 checkpoint 已可见的连续日线；窗口、最少观察数、
缺失值处理、横截面筛选及任何归一化规则必须写入冻结配置。

第一阶段只定义下列四类 canonical price/volume 因子：

| 因子 | 只允许使用的历史 | 研究语义 |
|---|---|---|
| Momentum | 截止 checkpoint 的较长回看窗口日线价格收益 | 过去累计收益的横截面排序/分数。 |
| Reversal | 截止 checkpoint 的较短回看窗口日线价格收益 | 短期过去收益的反向横截面分数；反向符号是因子定义的一部分。 |
| Volume ratio | 截止 checkpoint 的连续日线成交量 | 当前成交量相对此前 `window_bars` 个已见成交量均值的比例；当前 bar 不进入分母。 |
| Realized volatility | 截止 checkpoint 的连续日收益序列 | 已实现日收益波动度；窗口、年化与最少样本规则均显式冻结。 |

因子不是裸数值。`FactorResearchExperiment` 冻结每次运行的配置、DecisionReplayPlan、数据/特征版本与代码身份，
并且每个 `FactorExposure` 必须绑定其 checkpoint、选中的市场 snapshot、严格特征物化、
因子定义与参数、配置 hash 和代码身份。未来的价格、未来 revision、未来 outcome 或静态 backfill 一律不能
成为同一期 exposure 的输入。

## 3. 事后分析，非决策输入

因子分析只评估已经到期的结果，且与产生 exposure 的决策路径分离：

- **IC / Rank IC**：在同一决策截面上，将当期分数与其后、已实现的 forward return 分别做 Pearson 与秩相关；
- **Quantile return**：按当期因子分数分组后，汇总各组后续已实现收益；
- **Turnover**：比较相邻 checkpoint 的分数或建议权重变化，量化因子组合的换手；
- **Stability**：以按期 IC、分位差、覆盖率和暴露/权重序列的时间统计量检查持续性，而不是只报告单一总样本数。

forward return 只在其持有期结束后用于这些诊断和 OOS 评分。它不能参与同一 checkpoint 的特征、暴露、排序、
权重、参数选择或风险预算；这样可防止 IC、分位收益或回测结果反哺当期目标。

## 4. 研究用组合建议

`FactorPortfolioProposal` 将当期 `FactorExposure` 转换为可审计的连续研究权重建议。第一阶段的 sizing
必须明确包含：inverse-volatility 缩放、目标波动率、风险预算、单标的绝对权重限制和组合毛敞口限制。其输入、
中间缩放和最终权重均须与同一 PIT checkpoint 绑定。

该对象的边界严格如下：

```text
FactorExposure → FactorPortfolioProposal

≠ StrategyTarget
≠ ApprovedPortfolioTarget / PortfolioTarget
≠ ExecutionPlan
≠ BrokerOrder
```

因此建议权重只能供离线 `weight_return` 研究回放使用，不能越过 Research → Risk → Execution 的人工与系统门禁。
完整协方差估计和 risk parity 不属于此切片。

## 5. 连续研究回测与成本

回测仅使用既有 `weight_return` 连续研究引擎。它以因子建议权重和连续日收益计算组合收益，并且在运行前显式固定：

- commission 假设；
- slippage 假设；
- 初始资金、再平衡/延迟和其他适用的连续研究参数；
- 因子、组合限制及权重生成配置。

成本和滑点不是事后可选项，零值也必须在 manifest 中明确记录。此回测不声称实际期货合约可成交性，不能替代实际
合约日线验证、撮合、margin/fee/price-limit 检查或 execution simulation。

### 5.1 冻结的稳健性研究

每个 `FactorPipelineConfig` 必须绑定一个不可变 `FactorRobustnessPlan`，并将其 hash 写入配置和最终
`FactorResearchRunManifest`。计划在运行前精确固定：

- 至少两个不重叠的子样本区间；跨越子样本边界的 forward outcome 会 purge，不能借相邻区间补样本；
- 每个子样本的显式连续研究 symbol 剔除集合；未知 symbol 或剔除后横截面不足时失败关闭；
- 每个 alpha 的有限参数邻域；除了该 factor 的既有参数，feature、方向、角色和风险预算均不可变化；
- `baseline` 和 `adverse` 两个成本/滑点/延迟情景，其中 baseline 必须与主回测精确一致；
- 分析样本数、Rank IC、正 IC 比例、分位差、IC 波动、换手、情景通过比例及成本回测的预声明阈值。

运行器会以独立的 strict-PIT replay 重跑每个参数邻域点，不能把一次结果重命名为邻域结果。评估器会逐项核对
replay plan、checkpoint、DatasetVersion、feature version、code revision、配置、proposal 与 forward outcome；它还会从
immutable store 重选每个 PIT snapshot，并以当前 canonical feature、精确参数和 replay checkpoint 重新物化每个输入。
随后才从该重放的原始证据重新计算邻域 analysis。Look-ahead certificate 必须同时绑定本次 replay plan、每个 checkpoint
的 market evidence，以及对应的 `FactorPortfolioProposal`，不能借用其他有效运行的证书。子样本/品种剔除用于成熟后的
factor-analysis 稳定性诊断；它们不会在同一次
运行中重写历史 `FactorPortfolioProposal` 或产生新的可执行目标。成本情景则对完整的连续研究权重面板重新运行
`weight_return`。`FactorRobustnessResult` 必须精确覆盖计划中的 subperiod × alpha、参数邻域和成本情景；各层及总体
通过/失败标签均由冻结阈值派生后以 hash 写入。标签只用于研究报告，绝不自动升级 candidate 或交易权限。

## 6. 冻结的 walk-forward 验证

一次研究运行至少包含两个预先声明、按时间排序且互不重叠的 OOS folds。每个 fold 在其开始前冻结训练区间、
OOS 区间、因子参数、组合参数、成本/滑点和评价规则；不得观察 OOS 结果后移动边界、修改窗口、重新选择因子或
删除不利日期。

运行 manifest 至少冻结并关联下列证据：

```text
DatasetVersion / artifact snapshot / DecisionReplayPlan hashes
checkpoint selection and strict-feature materialization hashes
factor and portfolio configuration hashes
code identity / revision
commission and slippage assumptions
ordered walk-forward fold definitions
frozen robustness-plan and robustness-result hashes
factor-analysis, proposal, backtest and final-result hashes
```

任何一个输入、配置、代码或 fold 定义改变，都是新运行，必须产生新的 manifest 和结果 hash；不得用旧结果宣称
可复现或 OOS 通过。

### 6.1 AI factor-mining 的 development / OOS 子协议

一般 `FactorResearchPipeline.run()` 可以按冻结配置计算完整 walk-forward 研究结果。P11 的 AI factor-mining 协议
不会把这个完整结果交给 discovery：它要求所有 fold 共享一段 IS 与 validation，两个或更多 OOS fold 必须位于 validation
之后、彼此有序且不重叠。

```text
IS → validation → maturity / embargo → selection_at → OOS fold 1 → OOS fold 2
```

`run_discovery()` 只重放 `decision_at <= selection_at` 的 strict PIT checkpoint，并且 discovery ledger 只保留
`origin ∈ stage AND evaluation ∈ same stage AND evaluation_at < selection_at` 的 forward outcome。跨 stage 的 outcome
被 purge；未知、缺失或较晚 outcome 不能被填入相邻阶段，也不能产生选择结论。

每个 discovery/OOS stage 使用 `FLAT_START_FORCED_CLOSE`：从零持仓开始，执行延迟只在本 stage 内适用，最后一个
有效权重会以显式佣金、最低佣金与滑点成本强制平仓，相关换手也归入该 stage。这是连续价格上的研究成本归属约定，
不是实际合约订单、成交或持仓继承模拟。

选择时只读取 IS/validation ledger，并应用选择前冻结的成本情景、样本量、指标阈值和多重比较控制。OOS evidence 只能在
researcher 创建 immutable selection commitment 后，由一次显式 release 生成；该 release 仍完全处于 research-only 边界。

## 7. 可复现性与研究交接

相同 immutable inputs、checkpoint 选择、冻结配置、代码身份和成本假设必须产生相同 exposure、分析、建议、
回测与结果 hash。实现必须保持确定性的标的、日期、分位和输出排序，并记录 no-target/warm-up 或拒绝原因，
不能把它们静默丢弃。

研究交接只发布可追溯的结果和失败证据，不发布交易权限。高 IC、漂亮分位收益、低换手或某一 fold 的盈利都不能
把产物升级为 candidate，更不能激活策略、创建订单或改变 broker 状态。

### 7.1 本地因子挖掘运行包

对于 P11 factor-mining，生成前与研究运行输入都不是 notebook 或调用方临时持有的对象。先由可信 composition 发布
receipt-free 的 `LocalFactorMiningCampaignDeclaration`：它精确绑定所有 `DatasetVersion` hashes、
`DecisionReplayPlan`、sealed campaign、固定 `LocalFactorMiningRunConfig` 和 `FactorMiningRunnerResourceBudget`。
durable runner 只可在 PostgreSQL 中事务性 reserve 此 declaration 后生成候选；receipt 被记录为 hash-only commitment 后，
才由系统构造 `LocalFactorMiningRunBundle`，把同一 declaration 与 hash-only generation receipt 绑定。两类 declaration
都必须以所有输入数据制品为 parents 发布为 immutable derived artifact，运行入口只接收相应 artifact 的 SHA-256 snapshot。

一次成功的本地运行会以受治理 `DerivedArtifact` 分别写入：exposures、weights、analyses、development discovery
evidence、selection commitment、已选择候选的 OOS evidence（如有）、research-only report，最后写入
`LocalFactorMiningRunManifest`。每项均携带 content hash、lineage、源授权与 fixed retention metadata；配置强制
`automatic_cleanup=false`。config 还固定 campaign-matching `code_revision` 及来自可信 build/registration evidence 的
immutable `code_revision_hash`；它不是可变 branch、`HEAD` 或 CLI 参数。manifest/result hash 不包含机器路径、墙钟时间或
UUID，因此相同已验证输入、固定代码 attestation 和配置具有精确的重放身份。bundle/manifest 解码必须回到完全相同的
canonical typed bytes；不能用 `false`、时间格式别名或其他被静默归一化的 JSON 表示创建第二个身份。

读取 manifest/evidence 时还会验证完整 direct-lineage 图：definition → exposures → weights/analyses → discovery →
selection → optional OOS → report → manifest。对存在 OOS 的候选，loader 会从 sealed typed OOS proof 重新构造其
config、experiment、analysis、robustness、walk-forward 与 run manifest，并将其与 selected record、release 和报告投影逐项
绑定；报告中的 robustness 投影不能独立声明结论。空 selection 不产生 OOS proof，报告必须保持空 robustness 投影。
`inspect` 只做这种 immutable graph 与 typed-proof 完整性检查；只有 `replay` 才会从 sealed bundle 重新执行确定性的
PIT research computation，并要求重建结果与既有 manifest 完全相同。role、parent、bundle/retention envelope、manifest
reference、source、PIT 或授权任一不匹配均不是可检查的研究制品。

唯一的人类 CLI 是独立的、研究专用的 `northstar-research`，而不是带 live import closure 的 broad `northstar` CLI：

```text
northstar-research factor run --bundle-snapshot <sha256>
northstar-research factor replay --bundle-snapshot <sha256> --expected-manifest-snapshot <sha256>
northstar-research factor inspect --artifact-snapshot <sha256>
```

它没有 `--input`、`--dataset-path`、`--config` 或 `--profile` 参数。路径、裸 DataFrame、未验证 JSON、`latest`、
artifact/authorization/PIT/replay identity 缺失均失败关闭。typed declaration 的首次发布是可信 composition 的工作，不是一个
允许任意文件灌入的 CLI 子命令。`replay` 在内存中重建并验证完整 evidence graph 的 payload、snapshot、
lineage 与 manifest/result identity，成功时返回既有的 immutable manifest；它不会为验证动作重新发布 evidence graph。

## 8. 明确不在范围内

本工作包不建设或暗示下列能力：

- Contract Master、contract chain、实际合约映射、roll、动态 fee/margin、价格限制或可交易日历；
- fundamental 数据，以及 carry、basis、spread、inventory、seasonality 等需要独立 PIT schema、source evidence
  和数据授权的因子；
- 完整协方差 risk parity、实际合约执行保真回测或分钟级成交模拟；
- SimNow、paper broker、`ctp_sim`、small-live、真实账户连接、真实订单或任何 live enable。

这些能力必须在各自的工作包中以独立数据授权、PIT 契约、风险验证和人工确认处理；连续日线因子研究的成功结果
不能充当它们的替代证据。

当未来由 AI 生成因子候选时，候选必须经过受限 primitive/参数网格、冻结 campaign 和一次性 typed research
路径，详情见[AI 自动因子挖掘与回测架构](AI_FACTOR_MINING_ARCHITECTURE.md)；AI 不能直接调用本流水线、
提供原始数据/代码或获得任何交易权限。

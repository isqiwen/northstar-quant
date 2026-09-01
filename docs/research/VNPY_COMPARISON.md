# vn.py（VeighNa）与 Northstar Quant：采用边界

**结论（2026-08-31）**：不以 vn.py 替换 Northstar，也不在当前本地研究阶段引入它作为运行时依赖。vn.py 是很成熟且值得尊重的
事件驱动交易框架；它尤其适合中国期货的 gateway、CTA 策略运行、回测和前端风控。Northstar 当前要解决的则是一个更上游、
更受治理的问题：可审计的数据与 PIT 研究、受限 AI 因子挖掘，以及研究到交易之间不可绕过的证据/审批边界。两者可以在未来
通过一个窄的执行适配边界协作，但不是同一层的替代品。

## vn.py 已提供什么

- vn.py 的核心是 `EventEngine`、`MainEngine`、gateway 与 app 的事件驱动交易平台；`MainEngine` 管理 gateway、功能
  engine 和 app，而 gateway 负责把 tick、订单、成交、持仓、账户和合约回调送入事件总线。[^engine][^gateway]
- 它提供国内期货 CTP 等交易接口，以及 CTA、组合策略、算法交易、数据管理/录制、模拟账户和前端风控模块。CTA 模块声明覆盖
  策略开发、历史回测、参数优化和自动交易；组合策略模块面向多合约策略的回测、优化和实盘。[^vnpy][^cta][^portfolio]
- 4.x 的 `vnpy.alpha` 已包括多因子特征工程、Lasso/LightGBM/MLP 模型、单标的/横截面策略和研究流程管理。它值得在将来
  做独立 PoC，不能因为项目要做因子研究就忽略它。[^vnpy]
- vn.py 的 datafeed 列出了 Wind、iFinD、TuShare、RQData 等适配器；这是**接入接口**，不是随框架取得数据授权、历史保留
  权或 PIT 证据。数据供应商合同仍须由 Northstar 按数据源治理流程核验。[^vnpy]

## 关键差异

| 维度 | vn.py 的重心 | Northstar 的既定边界 |
| --- | --- | --- |
| 主问题 | 用事件、gateway 和 app 快速构建/运行交易策略 | 中国商品期货从数据、情报、研究、组合风险到执行的受治理平台 |
| 回测输入 | CTA `BacktestingEngine` 接受标的、时间、bar/tick 模式、费率、滑点、合约乘数、最小变动价位、资金等参数，并将历史数据装入内存回放。[^backtest] | 只接受 immutable `DatasetVersion` 与 `DecisionReplayPlan`；每行必须满足 `available_at <= decision_time`，并冻结授权、hash、lineage、成本、代码版本和 OOS。[^factor] |
| 研究到交易 | CTA/组合 app 可自然连接到自动交易；其风控模块在下单时执行规则。[^cta][^risk] | `Research` 不可访问 broker；`StrategyTarget → Portfolio/Risk → ExecutionPlan → BrokerOrder` 是分离对象，未知事实一律 `NO NEW RISK`。[^architecture] |
| AI | 官方描述的是 ML 因子、模型和研究工作流。[^vnpy] | AI 只能提交有限、typed 的因子候选；无原始数据、文件、数据库、网络、风险或 broker 权限，OOS 受隔离且结果永远 `research_only`。[^ai] |
| 权威状态 | 交易框架提供数据库适配器，默认选项为 SQLite。[^vnpy] | PostgreSQL 是订单/成交/持仓/风险/审批/审计的唯一权威库；Parquet + DuckDB 用于带 hash 与 PIT 的历史制品和研究。[^architecture] |

## 为什么当前不直接使用它

这不是对 vn.py 能力的否定，而是架构责任不同。其标准 CTA 回测接口已经暴露了费率、滑点和合约参数，但**该接口本身没有**
Northstar 所要求的 `DatasetVersion`、逐行 `available_at`、来源授权、lineage、冻结 OOS 和 code-revision 证明字段。[^backtest]
因此，直接把 vn.py 回测结果当作 Northstar 的候选准入证据，不能证明本项目的 PIT 与可复现性要求已满足；若要使用，仍需在
外层补齐并验证这些证据，而不是放宽项目规则。

同理，vn.py 的前端下单风控（活动委托、日内报撤、重复报单、单笔数量、订单合法性）对执行很有价值，[^risk] 但它不替代
Northstar 对授权数据、合约/日历 PIT、人工审批、组合风险、对账和不可变审计链的端到端责任。将 `MainEngine`/CTA app 直接
导入 research 或 portfolio/risk，会违反本仓库禁止 `research → broker` 和 `portfolio_risk → order submit` 的依赖边界。[^architecture]

当前优先级还是本地连续日线 PIT 回测与受限 AI 因子挖掘；真实数据授权、权威 Contract Master 和真实 CTP 接入仍是外部
阻塞项。此时加入交易框架不会解决这些阻塞，反而会扩大运行时和安全面。[^plan]

## 未来的正确复用方式

当 P10 的数据授权与真实执行前提得到人工批准后，再做一个**隔离 PoC**：把 vn.py（优先 CTP gateway）放在
`trading_execution` 侧或独立进程中，且只允许单向接收已经通过 Northstar 最终 preflight 的 `ExecutionPlan`。返回的订单、
成交和账户事件必须转换为 Northstar 的 typed 事件并写入其 PostgreSQL 审计链；vn.py 不得成为 Contract Master、数据/PIT
权威来源、研究引擎、风险批准者或绕过 preflight 的直连入口。PoC 需单独验证重连、重复/乱序回调、idempotency、状态对账和
失败关闭，且不能启用真实账户。

这条路线保留 vn.py 最有价值的执行生态，同时避免把一套面向交易者的 app 框架误当成数据治理、AI 研究治理和 real-money
审计平台。

## 来源

所有外部事实均来自 vn.py 官方 GitHub 组织，访问于 2026-08-31；本项目事实来自仓库内的权威架构/研究文档。

[^vnpy]: [vn.py 官方 README](https://github.com/vnpy/vnpy/blob/master/README.md)
[^engine]: [`MainEngine` 源码](https://github.com/vnpy/vnpy/blob/master/vnpy/trader/engine.py)
[^gateway]: [`BaseGateway` 源码与回调契约](https://github.com/vnpy/vnpy/blob/master/vnpy/trader/gateway.py)
[^cta]: [vnpy_ctastrategy 官方 README](https://github.com/vnpy/vnpy_ctastrategy/blob/main/README.md)
[^portfolio]: [vnpy_portfoliostrategy 官方 README](https://github.com/vnpy/vnpy_portfoliostrategy/blob/main/README.md)
[^backtest]: [`BacktestingEngine` 源码](https://github.com/vnpy/vnpy_ctastrategy/blob/main/vnpy_ctastrategy/backtesting.py)
[^risk]: [vnpy_riskmanager 官方 README](https://github.com/vnpy/vnpy_riskmanager/blob/main/README.md)
[^architecture]: [Northstar 架构设计](../ARCHITECTURE.md)
[^factor]: [连续日线 PIT 因子研究流水线](FACTOR_RESEARCH_PIPELINE.md)
[^ai]: [AI 自动因子挖掘与回测架构](AI_FACTOR_MINING_ARCHITECTURE.md)
[^plan]: [主实施计划](../planning/MASTER_IMPLEMENTATION_PLAN.md)

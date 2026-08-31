# 数据、研究、AI 与安全治理

本文是数据授权、研究准入、AI 权限、安全审计和人工控制边界的唯一政策权威。系统架构见
[架构设计](ARCHITECTURE.md)，运行操作见[运行手册](OPERATIONS.md)，实现进度和外部阻塞以
[主实施计划](planning/MASTER_IMPLEMENTATION_PLAN.md) 为准。

## 1. 数据授权与供应商状态

数据 source、数据 license、使用目的、地域、保留期、可再分发性、品种范围、质量与 PIT 可用性必须逐项可审计。
`configs/data/sources.yaml` 可登记候选 source，但登记不构成授权。

当前公共 source 与本地导入材料只能用于探索、测试和工程验收。下列状态均是 fail-closed：

```text
procurement_pending
pending_owner_approval
UNKNOWN
```

它们不能被解释为模拟交易授权、真实 CTP 授权或真实资金授权。未获得商业合同、source authorization、PostgreSQL
Contract Authority 发布、Calendar ArtifactSnapshot 与 production PIT 证据前，任何数据不得提升为生产可用，也不得推动新增风险。

每份数据制品应保留：source identity、授权事实、接收时间、内容 hash、schema、质量评估、版本、lineage、
`available_time` 与适用的 `event_time` / `source_time` / `published_time`。修订只能新增版本，不能覆盖过去的可见状态。

## 2. 研究准入

研究政策配置位于：

```text
configs/research/admission/cn_commodity_futures_research_conservative_v1.yaml
```

研究候选必须经过：

```text
Feature → Experiment → Backtest → Validation → OOS → Stress → Research Decision
```

必要记录包括 DatasetVersion、FeatureVersion、StrategyVersion、配置、代码 revision、成本模型、滑点模型、
OOS period 和比较性说明。以下情况不构成准入：单次高 Sharpe、短样本盈利、漂亮连续合约、未绑定成本/滑点、未审计的
数据修订或无法解释的参数搜索。

`RESEARCH_ONLY`、`CANDIDATE` 和人工 activation 都不是交易权限。研究证据只有在可验证的审批链中才可产生
non-tradable `StrategyTarget`；它仍需通过组合、风险、账户、日历、报价、pre-trade 与 execution provenance gates。
AI 和自动化不得自行把研究结果升级为 production candidate、批准风险或下单。

## 3. 点时正确性与数据质量

研究和回测只允许消费：

```text
available_time <= simulation_time
```

任何未知的发布日期、日历、合约映射、费用、保证金、数据修订、引用制品或授权都阻断研究升级。质量结果不是 PASS 时，
不得发布下游事实；`UNKNOWN` 从不等于 PASS。

连续合约可用于探索，但不代表可成交的实际合约。实际合约研究需要 point-in-time 的 contract、calendar、fee、margin、
price-limit 和 rollover 事实。未来规则、未来合约信息或修订值不能回填到过去的模拟时点。

## 4. AI 权限边界

AI 只能使用显式 allowlist 的 typed API：

- Research Agent：研究目录、受控实验、回测和验证证据；
- AI Factor Mining Agent：一次性、受限 feature/parameter 候选的 research-only 因子评估；
- Intelligence Agent：受授权 evidence-bound Event 搜索和分析；
- Data Quality Agent：Dataset/quality 诊断；
- Ops Agent：单项 hash-only 诊断 snapshot。

这些 API 的共同约束是只读或受控、PIT-safe、typed、hash-bound、non-tradable，并且不可访问 portfolio/risk/trading/live、
broker、真实配置、数据库、网络、进程或文件系统。AI 输出不是 ground truth，LLM confidence 不是最终 confidence，
Event 也绝不直接生成 BUY/SELL。

`DurableResearchAgentRunner` 仅保存 hash-only audit event 和 trace。不得持久化 raw prompt、chain-of-thought、
原始 query、document、result、rationale、secret 或 exception payload。Agent 不得 approve、enable-live、resume-risk、submit、连接 broker、修改数据、部署、恢复或绕过 kill switch。

AI Factor Mining Agent 只能从由系统冻结的 canonical feature primitive 与有限参数网格中提出候选；它不能提供 Python、
SQL、shell、DataFrame、`latest` 数据选择、成本/滑点、OOS 边界、风险限制、订单或目标。可信 runner 必须使用精确
`DecisionReplayPlan` 和 immutable `DatasetVersion` 重放既有 PIT 因子流水线。每个 campaign 在 OOS 前冻结
selection time 与候选预算；失败或未知状态不得自动重试或扩展搜索。自动本地入口使用独立 PostgreSQL durable
reservation/audit 状态机：它在 generator/compute 前 reserve request，append-only/hash-linked 地记录 receipt、selection、
OOS、result、resource 与 failure commitments。crash、timeout、cancellation、partial failure、restart 或写入不确定性保持
`UNRESOLVED`；只有受信 verifier 对 external approval reference 与 source request 做精确绑定确认后的显式人工 replay
authorization 可以创建新的 request identity，绝不恢复旧 request。CLI 不接收 self-attested approver/evidence，默认 verifier
不可用时拒绝写入；Foundation private replay writer 只可由 durable verifier bridge 使用。未来外部人工授权服务必须使用独立
database role，direct DB write 权限不是 approval authority。账本不保存 raw
prompt、raw response、chain-of-thought、secret、异常原文或 broker/account/portfolio/execution state。详细架构见
[AI 自动因子挖掘与回测架构](research/AI_FACTOR_MINING_ARCHITECTURE.md)。

## 5. 机密、供应链与审计

### 机密边界

真实 credential、账户号、token、数据库 URL、签名私钥、生产清单和备份访问参数必须只通过受保护的运行环境注入，
不进入 tracked 文件、日志、报告、邮件、CLI 输出或 Agent audit。密钥扫描覆盖仓库内容；任何例外都必须紧邻标注：

```text
secret-scan: allow; reason: ...
```

例外只能是无害的 test fixture，且不能扩大到业务源代码、配置、部署、文档或生产清单。脱敏无法确认时，
导出和投递必须拒绝。

### 离线依赖供应链

依赖政策、lock 校验、secret 扫描和 hermetic PEP 517 bootstrap 必须在 quality gate 的早期执行。运行期不访问网络来
获得依赖；CVE 或漏洞信息的缺失也不意味着依赖安全。由锁定 artifact、hash provenance 和本地质量门禁证据共同决定是否可引入。

### 审计与最小权限

审计事件应稳定、结构化、可关联且不携带敏感原文。服务账户采用最小权限；root-owned release gate、环境快照、
systemd 路径和备份位置与应用可写目录隔离。数据库的自动化操作永远不删除或清空数据库、表、schema 或 volume。

## 6. 人工批准与真实资金边界

以下操作始终要求经授权的人明确确认：购买数据、接入真实账户、录入 production credential、开启 live、
真实订单、真实资金动作、从 HALT 恢复、生产备份恢复和灾备演练。

确认必须明确 profile、broker、account、target environment 和 requested action。未知账户、持仓、订单、风险、broker、
margin 或授权状态下禁止增加风险。测试 issuer、fake adapter、paper 和 `ctp_sim` 只用于隔离验证，不能成为实盘提升路径。

## 7. 违例处理

任何授权、PIT、质量、审批、订单或审计边界被破坏的迹象，都应：

1. 停止新增风险与副作用；
2. 保存只读证据和 hash，不扩散敏感内容；
3. 将状态保留为 UNKNOWN、BLOCK 或 HALT，而不是自动修复；
4. 由具备相应外部权限的人审阅、恢复或重新授权。

“为了让测试通过”不是放宽安全门禁、关掉 kill switch、虚构数据授权或恢复 HALT 的理由。

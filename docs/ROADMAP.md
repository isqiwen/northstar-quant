# 开发路线

设计与边界见 [ARCHITECTURE](ARCHITECTURE.md)，参考依据见 [REFERENCES](REFERENCES.md)。
[Project](https://github.com/users/isqiwen/projects/1) 管理顺序/状态，[Issues](https://github.com/isqiwen/northstar-quant/issues) 保存可执行步骤、直接依赖和验收。
**首轮：一份真实固定数据 → 单合约跨日回测 → 同一业务规则下的 SimNow 开平仓与恢复。**
全市场下载完成、多品种、ML、云主机和真实资金都不是首轮前置。

## 第一轮：数据与研究

| 建议顺序 | Issue | 完成后得到什么 |
|---|---|---|
| 1 | [#46 响应规范化与质量版本](https://github.com/isqiwen/northstar-quant/issues/46) | 响应校验、Decimal Parquet、留存原文重处理、逐项质量与固定版本差异已通过完整 CI，已关闭 |
| 并行核实 | [#41 Tushare真实来源验收](https://github.com/isqiwen/northstar-quant/issues/41) | 已取得 RB2610 真实分钟/日线固定样本并逐项核对；请求异常隔离修复已通过 CI，已关闭 |
| 2 | [#18 合约、时段与信息时钟](https://github.com/isqiwen/northstar-quant/issues/18) | 已接通固定多时段/多交易日读取与时间顺序拒绝；待真实 Tushare 装配、日历及条款 |
| 3 | [#19 统一期货账户](https://github.com/isqiwen/northstar-quant/issues/19) | 多空今昨仓、费用、结算、保证金和权益逐项勾稽 |
| 4 | [#20 事件原子性与受约束成交](https://github.com/isqiwen/northstar-quant/issues/20) | 失败不留半状态，成交遵守量价/时段，部分成交与残量可解释 |
| 5 | [#21 可核对报告与固定评价](https://github.com/isqiwen/northstar-quant/issues/21) | 同一账本报告、固定窗口/基准和明确样本外边界 |
| 并行工程 | [#23 持久研究负载验收](https://github.com/isqiwen/northstar-quant/issues/23) | 现有SQLite/worker的并发、取消、重启与实测资源管理 |
| 6 | [#47 数据检查工作台](https://github.com/isqiwen/northstar-quant/issues/47) | 图表、覆盖、异常行和修订差异同源可查 |

已交付 #46：响应校验、Decimal Parquet、留存原文重处理、逐项质量报告与固定版本差异均已通过完整 CI 与浏览器验收；完整领域时间与条款继续 #18。
已交付 [#56 共享消息总线](https://github.com/isqiwen/northstar-quant/issues/56)：Research/Live 实际接入、事件隔离与研究整步回滚。
已交付 [#57 共享交易内核](https://github.com/isqiwen/northstar-quant/issues/57)：统一事件入口、生命周期、失败策略和行情窗口；完整账户/执行业务继续按下表推进。
#48 已将 `Environment`（BACKTEST/SANDBOX/LIVE）接入共享内核、实例绑定、配置与管理协议。SimNow 为 SANDBOX，柜台 profile 单独固定；完整材料、健康告警仍待交付。
#19 已接入 Research/Live 共用的多空持仓数量规则及显式开平、FIFO 平仓计价与恢复核验；费用调整、跨日结算和保证金仍待完成。
#20 的单事件异常回滚已随 #56 实现；完整成交模型仍需 #19。#41 的外部权限问题不阻止无凭据工程验证。

## 第一轮：SimNow Sandbox

| 建议顺序 | Issue | 完成后得到什么 |
|---|---|---|
| 并行基座 | [#26 三工作台操作者认证](https://github.com/isqiwen/northstar-quant/issues/26) | 独立登录/会话与审计；不限制IP，登录不授予交易 |
| 并行基座 | [#48 固定材料与健康告警](https://github.com/isqiwen/northstar-quant/issues/48) | 本地完整实例绑定、独立内核、可观察故障 |
| 1 | [#31 SimNow账户与条款](https://github.com/isqiwen/northstar-quant/issues/31) | 只读可信起点，缺项与差异明确 |
| 2 | [#25 持续行情与交易日](https://github.com/isqiwen/northstar-quant/issues/25) | 夜盘/日盘独立行情、陈旧/缺口与预热处理 |
| 3 | [#49 授权、订单与预占](https://github.com/isqiwen/northstar-quant/issues/49) | 先可靠保存再发送，账户唯一所有者，UNKNOWN不盲重发 |
| 4 | [#32 柜台报撤单与控制](https://github.com/isqiwen/northstar-quant/issues/32) | 仿真开平仓、确认成交/账本、暂停/撤单/请求平仓 |
| 5 | [#33 断线与联合恢复](https://github.com/isqiwen/northstar-quant/issues/33) | 本地恢复、外部核对与重新授权完整验收 |
| 联合出口 | [#50 首轮验收](https://github.com/isqiwen/northstar-quant/issues/50) | 真实跨日研究和真实柜台仿真闭环均通过 |

顺序不是依赖闭包；硬性前置以 Issue 的原生 Blocked by 为准。每次选择一个可观察结果交付。
历史 #53 跨主机固定文件与联合恢复已有实机证据，保留历史，不重开成首轮阻塞。

## 后续独立交付

| 优先级 | Issue | 范围 |
|---|---|---|
| P1 | [#43 范围查询与引用保留](https://github.com/isqiwen/northstar-quant/issues/43) | 按实测优化读取与小文件合并，不破坏固定版本 |
| P1 | [#54 Live异步备份与容量](https://github.com/isqiwen/northstar-quant/issues/54) | SQLite封存包独立传输，远端失败不阻塞内核；不增加Data Hub数据来源 |
| P2 | [#28 组合与多策略预算](https://github.com/isqiwen/northstar-quant/issues/28) | 多合约、多策略仍共享单账户风险与发送所有者 |
| P2 | [#29 真实合约换月](https://github.com/isqiwen/northstar-quant/issues/29) | 固定映射、平旧开新、成本及残余敞口 |
| P2 | [#45 有限因子实验](https://github.com/isqiwen/northstar-quant/issues/45) | 假设、候选、训练/评价与复核；复用现有任务与产物 |
| P2 | [#34 生产准入与只读核对](https://github.com/isqiwen/northstar-quant/issues/34) | 期货公司/用户明确资料与权限，仍无发送 |
| P2 | [#35 生产部署与受限实盘](https://github.com/isqiwen/northstar-quant/issues/35) | 实机/云端故障验收后，由用户本人授权并操作真实资金 |

## 后续 AI 执行规则

1. 先读架构、Issue 当前基线、直接依赖与相关参考；不要重做已交付能力或跟随旧评论中的拓扑。
2. 使用既有业务入口推进一个完整切片，修改所有受影响调用/协议；不搭第二套演示流程或兼容架构。
3. 验证金钱、因果、持久性、权限与真实集成。外部样本、柜台交易、实机和合成故障证据分别记录。
4. 提交并检查该SHA的CI，更新Issue验收与Project；外部条件未满足的任务保持开放。

本次整理保留有交付证据的关闭事项并从当前Project归档；纯计划重复内容迁入唯一所有者后删除。
旧手工导入、NAS三库和Live PostgreSQL不再成为当前开发任务；当前已维护的部署与数据不因任务整理自动删除。

## 当前本地重构批次（2026-09-11）

用户要求整批完成后统一推送。分支 `refactor/futures-system`，远端基线
`5ec856270a15eb4a28e1305d813fac2b39e7b68e`。上述已交付事项指远端证据；
下表是尚未推送的本地工作，不据此关闭 Issue 或将 Project 标为 Done。

| 范围 | 当前已实现 | 整批剩余条件 |
|---|---|---|
| #18 固定研究输入 | 多时段/交易日、结算与有效条款进入不可变清单；时间重叠、晚可得和条款缺口拒绝 | 真实 Tushare 数据装配，交易日历与历史条款核实 |
| #19 共享账户 | FIFO、多空今昨仓、日结算、费用/保证金/权益，账本重放核对与整步回滚 | 费用调整、Live 完整资金账本与外部核对 |
| #20 受约束订单 | 后续可执行 bar、量参与率、部分成交/残量、涨跌停排队不确定性、订单预算与预占检查点 | Live 持久订单、发送尝试、预占及柜台终态闭环 |
| #21 报告与评价 | 同一账本逐项核对；执行前固定窗口、现金基准；显式探索性结果，不冒充样本外或生产资格 | 真实固定输入的最终研究验收 |
| #26 操作者身份 | 三应用独立密码登录、会话/退出、CSRF；私有密码哈希与部署初始化；可信操作人进入 Live 命令回执 | 随整批交付验证 |
| #48/#49 运行边界 | 固定候选接收、账户唯一锁、独立接收端失锁停止；命令身份/终态数据库保护，UNKNOWN 不重复执行 | 完整执行授权、预占及独立告警 |
| #31/#25/#32/#33 Live Sim | 独立 SQLite/内核、既有只读柜台证据和影子流程保持可用 | 可信账户/条款、持续夜盘行情、报撤单、资金与联合恢复闭环 |
| #50 验收 | 合成数据的安装、浏览器、后台生命周期与联合恢复已多次验证 | 真实固定研究与明确授权的外部 SimNow 非空交易闭环 |

共同机制：实例内 `TradingKernel`/消息总线；`persistence` 仅复用 UTC 数据库时间、
本地文件锁和显式写事务，各业务仍拥有表与完整提交。Data Hub 不因这类机制依赖 Live/Broker。
订单计算保留完整精度，损坏金额在恢复时拒绝，不以舍入改变既有风险边界。

最近可复核证据：

- `e6484d4` 安装包与随后 `ebe6746` 的前端通过三工作台登录、退出拒绝访问与后台隔离浏览器检查，浏览器错误为零。
- `d46b1d6` 安装包通过固定研究、Paper 重启、独立应用生命周期、空数据库联合恢复和文件日志；没有连接柜台。
- 合成账本基准：现金 97370、权益 97320、空仓 5 手、费用 30。不是收益保证或真实历史验收。
- 存储机制及文件丢失保护通过后端 700 项检查（含真实 PostgreSQL 与本地 SQLite）、237 个源文件类型检查、Ruff；前端类型检查与 Live 构建通过。`5bf7008` 安装包也通过联合恢复；`d87d6bc` 的三工作台浏览器验收通过，包含实际内核的数据库容量/WAL 诊断，浏览器错误为零。整批未完成、未推送。

组合、换月、ML 与真实资金准入不在本批。真实来源样本保留在私有验收目录；
新供应商请求、柜台连接或实机操作须有明确范围。缺失事实保留为验收缺口，
不能用合成成交、假定历史条款、候选接收或源码测试替代。

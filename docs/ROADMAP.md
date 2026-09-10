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

用户要求整批重构完成后统一推送。工作分支 `refactor/futures-system`；
保留本地提交与验证记录，本批暂不推送、不以本地修改关闭远端任务。
基线 `5ec856270a15eb4a28e1305d813fac2b39e7b68e`。

- [ ] #18：真实来源到固定研究输入，明确时段、交易日、有效条款及修订语义。
- [ ] #19：共享账户的跨日结算、费用、保证金及今昨仓，检查点和账本可核对。
- [ ] #20：订单生命周期、预占与受约束部分成交，保持整步事务。
- [ ] #21：从同一账本输出可核对报告与评价。
- [ ] #31/#25/#49/#32/#33：Live Sim 起点、行情、授权、报撤单和恢复闭环。
- [ ] #50：安装/三工作台/隔离恢复及明确授权的真实样本、外部仿真验收。

组合、换月、ML 与真实资金准入不在本批；引用项目的机制只有接入实际调用
并经过验证才标记完成。任何缺失外部条件保留为验收缺口，不伪造通过。

本地进度：#19 日结算已接入固定快照 → 批量回测 / SQLite Paper → 报告。
合成跨日快照验证离线读取、逐步重启、结算步骤提交失败回滚及重试一致；
缺失结算或错误可得时间拒绝运行。尚未完成真实有效条款、Live 资金共享、保证金及完整订单生命周期，整批保持进行中。

#20 本地进度：模拟量参与率、完整后续 bar 约束、部分成交/剩余量、明确过期与替换撤销已接入回测和 Paper。
订单过程进入报告；交易所限价、有效条款和 Live 发送前预占仍未完成。

本地验证记录：`028e3bcbdb9e2b028f42d5fc195b91240ab04d55` 后端 664 项通过，
同 SHA 安装包的安装/联合恢复及三个工作台浏览器隔离检查通过；前端构建、14 项测试和类型检查通过。
均为本地工程证据，未连接柜台、未推送，也不代表真实跨日条款或 SimNow 交易验收完成。
随后补强跨日输入排序、快照结算完整性和报告到账本的逐项核对，相关 PostgreSQL/SQLite 与账户测试通过。

本地追加：固定费用/保证金/限价修订进入快照哈希与离线清单，Research 使用同一计价规则撮合、预算及报告；
Live 只读开仓预算复用绝对计价。条款缺口、晚可得和重叠拒绝，条款变更形成新身份；
跨日批量与逐步 SQLite 重启一致。完整后端 668 项、前端 14 项、类型检查及三个前端构建通过。
真实 Tushare 到此清单的生产装配、历史条款核实、费用调整、Live 预占/发送/资金核对仍未完成，不能关闭整批任务。

`50cb418` 安装包的结果重放、独立生命周期与联合恢复通过；`19abaea` 三工作台浏览器检查通过，
包括无历史条款时的明确假设说明，浏览器错误为零；未连接柜台。结算/条款外部协议随后归入
`proto/accounting.proto`，已验证 PostgreSQL 固定清单经真实 API 的 Protobuf 返回保留精确数值及完整字段。

`32eb49f` 的安装包与新协议已通过三个工作台浏览器、独立生命周期检查，浏览器错误为零，未连接柜台。
继续将 Research/Paper 的订单数量推进和状态操作收回 Execution，补齐保留订单时的新许可截止时间约束；
相关研究、模拟与策略整步回滚检查通过。Live 发送/预占尚未接通，保持本地批次进行中。

订单/估值重构的完整后端回归 671 项通过（包含 PostgreSQL 联合备份恢复），
Ruff 与 232 个源文件的 Mypy 检查通过。报告新增逐行账户估值与模拟订单历史核对，
防止中间资金行、累计成交或终态被改写后仍输出有效报告；使用线性订单审计。
当前仍没有 Live 报撤单授权/预占闭环，也没有真实历史条款验收，不推送或关闭远端事项。

`1ac4ae3` 安装包已通过结果重放、Paper 恢复、独立生命周期、文件日志与联合恢复验收，未连接柜台。
后续本地补齐多空敞口和平仓/结算盈亏分项，并将权益轨迹的金额/手数声明为明确协议字段。

`f90b498` 的敞口/盈亏分项及明确金额协议通过三个工作台浏览器验收，浏览器错误为零。
本地继续固定探索性评价：会话创建即绑定快照窗口、现金基准和非样本外语义，初始检查点保存计划；
比较核对计划，截短固定输入及恢复时改动样本用途被拒绝。相关研究/SQLite/真实 PostgreSQL 测试 35 项通过，
233 个源文件类型检查通过；可编辑训练/验证计划及真实跨日来源验收仍待完成。

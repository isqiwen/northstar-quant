# 开发路线

设计与边界见 [ARCHITECTURE](ARCHITECTURE.md)，参考依据见 [REFERENCES](REFERENCES.md)。
[Project](https://github.com/users/isqiwen/projects/1) 管理顺序/状态，[Issues](https://github.com/isqiwen/northstar-quant/issues) 保存可执行步骤、直接依赖和验收。
**首轮：一份真实固定数据 → 单合约跨日回测 → 同一业务规则下的 SimNow 开平仓与恢复。**
全市场下载完成、多品种、ML、云主机和真实资金都不是首轮前置。

## 第一轮：数据与研究

| 建议顺序 | Issue | 完成后得到什么 |
|---|---|---|
| 1 | [#46 响应规范化与质量版本](https://github.com/isqiwen/northstar-quant/issues/46) | 缺字段/坏量价不发布，原文、规则、问题和新旧版本可追溯；本轮正在交付第一步 |
| 并行核实 | [#41 Tushare真实来源验收](https://github.com/isqiwen/northstar-quant/issues/41) | 全量同步/补缺正确，明确接口权限与真实样本；不要求全市场已下载完 |
| 2 | [#18 合约、时段与信息时钟](https://github.com/isqiwen/northstar-quant/issues/18) | 夜盘/跨日固定研究清单、有效条款引用与修订语义 |
| 3 | [#19 统一期货账户](https://github.com/isqiwen/northstar-quant/issues/19) | 多空今昨仓、费用、结算、保证金和权益逐项勾稽 |
| 4 | [#20 事件原子性与受约束成交](https://github.com/isqiwen/northstar-quant/issues/20) | 失败不留半状态，成交遵守量价/时段，部分成交与残量可解释 |
| 5 | [#21 可核对报告与固定评价](https://github.com/isqiwen/northstar-quant/issues/21) | 同一账本报告、固定窗口/基准和明确样本外边界 |
| 并行工程 | [#23 持久研究负载验收](https://github.com/isqiwen/northstar-quant/issues/23) | 现有SQLite/worker的并发、取消、重启与实测资源管理 |
| 6 | [#47 数据检查工作台](https://github.com/isqiwen/northstar-quant/issues/47) | 图表、覆盖、异常行和修订差异同源可查 |

已交付 #46 的响应校验与规则身份；完整规范化仍未完成。
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

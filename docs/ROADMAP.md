# 开发顺序与交付管理

目标：国内期货，先研究与持续模拟交易。详细职责和不变量见 [ARCHITECTURE.md](ARCHITECTURE.md)。
所有开发任务都属于 [isqiwen/northstar-quant](https://github.com/isqiwen/northstar-quant)，
[Project 1](https://github.com/users/isqiwen/projects/1) 是顺序、优先级与实时状态的唯一管理入口。
本文只解释阶段和顺序，不复制动态任务状态。

## 阶段完成条件

| 阶段 | 可运行结果 | 任务 |
|---|---|---|
| [M0 单仓库交付](https://github.com/isqiwen/northstar-quant/milestone/1) | GitHub 干净检出可启动、研究、精确重放；已有本地实现形成可复核远端交付 | #16 |
| [M1 研究可复核](https://github.com/isqiwen/northstar-quant/milestone/2) | 真实数据、夜盘和连续跨日账户、成本与成交约束、样本外比较、修订可得性和持久研究任务 | #17–#23 |
| [M2 持续 Paper](https://github.com/isqiwen/northstar-quant/milestone/3) | 方案复用、独立模拟账户、真实持续行情、顺序与事务恢复、网页控制及备份恢复 | #24–#27 |
| [M3 组合增强](https://github.com/isqiwen/northstar-quant/milestone/4) | 多合约共同资金、真实换月和多策略预算，逐项提供研究结果 | #28–#30 |

M0 和 M1 是研究主线；M2 是首个研究与模拟交易产品的完成边界。
M3 不阻塞首个可用版本。没有已确认的交付日期，不以虚构季度承诺代替依赖关系。
具体真实交易接口不在这轮 Issue 清单内；确定柜台、账户与执行范围后再设里程碑。

## 建议执行顺序

| 顺序 | 用户可见结果 | 前置 Issue |
|---|---|---|
| 1 | [#16 从单仓库干净检出启动并复现完整研究闭环](https://github.com/isqiwen/northstar-quant/issues/16) | — |
| 2 | [#17 导入首份真实国内期货行情，并从已有数据继续研究](https://github.com/isqiwen/northstar-quant/issues/17) | [#16](https://github.com/isqiwen/northstar-quant/issues/16) |
| 3 | [#18 按真实交易日研究夜盘、日盘和跨日时段](https://github.com/isqiwen/northstar-quant/issues/18) | [#17](https://github.com/isqiwen/northstar-quant/issues/17) |
| 4 | [#19 用连续账户完成跨日结算、今昨仓费用和保证金核算](https://github.com/isqiwen/northstar-quant/issues/19) | [#18](https://github.com/isqiwen/northstar-quant/issues/18) |
| 5 | [#20 让模拟成交遵守时段、价格和成交量约束，并解释未成交](https://github.com/isqiwen/northstar-quant/issues/20) | [#19](https://github.com/isqiwen/northstar-quant/issues/19) |
| 6 | [#21 固定评价方案并比较策略、基准和样本外表现](https://github.com/isqiwen/northstar-quant/issues/21) | [#20](https://github.com/isqiwen/northstar-quant/issues/20) |
| 7 | [#22 按首次可得时间处理迟到与修订，保持历史决策不被改写](https://github.com/isqiwen/northstar-quant/issues/22) | [#18](https://github.com/isqiwen/northstar-quant/issues/18)、[#19](https://github.com/isqiwen/northstar-quant/issues/19) |
| 8 | [#23 运行较长研究时可关闭网页，并恢复查看进度与结果](https://github.com/isqiwen/northstar-quant/issues/23) | [#21](https://github.com/isqiwen/northstar-quant/issues/21) |
| 9 | [#24 从研究方案启动可断点恢复的增量模拟账户](https://github.com/isqiwen/northstar-quant/issues/24) | [#21](https://github.com/isqiwen/northstar-quant/issues/21)、[#22](https://github.com/isqiwen/northstar-quant/issues/22) |
| 10 | [#25 接入一个真实持续行情来源，驱动 Paper 并处理断线缺口](https://github.com/isqiwen/northstar-quant/issues/25) | [#24](https://github.com/isqiwen/northstar-quant/issues/24)、[#23](https://github.com/isqiwen/northstar-quant/issues/23) |
| 11 | [#26 从工作台暂停、恢复、请求平仓和停止 Paper，并处理运行异常](https://github.com/isqiwen/northstar-quant/issues/26) | [#25](https://github.com/isqiwen/northstar-quant/issues/25)、[#23](https://github.com/isqiwen/northstar-quant/issues/23) |
| 12 | [#27 备份后在空环境恢复研究与 Paper，并核对账户状态](https://github.com/isqiwen/northstar-quant/issues/27) | [#24](https://github.com/isqiwen/northstar-quant/issues/24)、[#23](https://github.com/isqiwen/northstar-quant/issues/23) |
| 13 | [#28 在一个账户中完成多合约组合研究与共同风险约束](https://github.com/isqiwen/northstar-quant/issues/28) | [#19](https://github.com/isqiwen/northstar-quant/issues/19)、[#20](https://github.com/isqiwen/northstar-quant/issues/20)、[#21](https://github.com/isqiwen/northstar-quant/issues/21) |
| 14 | [#29 跨到期月份执行真实合约换月，并解释价差与成本](https://github.com/isqiwen/northstar-quant/issues/29) | [#28](https://github.com/isqiwen/northstar-quant/issues/28)、[#22](https://github.com/isqiwen/northstar-quant/issues/22) |
| 15 | [#30 让两个实际策略共享账户预算，并解释目标净额与组合贡献](https://github.com/isqiwen/northstar-quant/issues/30) | [#28](https://github.com/isqiwen/northstar-quant/issues/28) |

Order 表示默认优先执行顺序，原生 Blocked by 表示真正依赖。
依赖已完成的独立任务可以调整顺序；同一时间默认只推进一个主要纵向结果。
每条 Issue 的实现范围、验收和外部条件位于 Issue 正文，不另建横向任务清单。

## Project 使用规则

- 保留必要的 Status、Priority、Milestone、Order 和原生依赖；只有缺少外部输入时用 `needs-input`。
- Backlog：尚未选择开始，或前置/外部条件未具备。Todo：依赖具备、范围明确、可以开始。
- In Progress：正在实现完整行为。Review：代码和验收证据可检查。Done：验收满足且交付证据可复核。
- 正常推进应先查看所选 Issue 和其前置，再更新状态；发现遗漏时修正同一任务或拆出实际可用行为。
- 结束开发时写明可复核提交、相关运行/验证证据和已知限制；同步 Issue 与 Project。
  本地运行、文档完成或 CI 全绿单独都不代表整个用户结果已交付。
- 架构与代码可以在同一工作中修改；无需先合并一份架构文档才允许实施。
- #25 的具体持续行情来源尚未选定，这只阻塞该接入验收，研究与 Paper 核心可以继续。
- 每项任务都从输入走到持久结果和用户可见解释。兼容层、泛化 Contract/Schema/Validator/Fixture、
  文档测试、空框架和目录建设均不作为独立任务或验收目标。

原有三个跨仓库开发事项已由当前单仓库计划取代，不再作为活跃路线。

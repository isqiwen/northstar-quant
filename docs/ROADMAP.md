# 开发顺序与交付管理

最终目标：国内期货实盘，纵向尽早打通一个受限、可核对的真实交易闭环。
研究与内部 Paper 服务于实盘验证，不先建设完整研究或模拟平台。
详细职责、真实执行语义与准入条件见 [ARCHITECTURE.md](ARCHITECTURE.md)。
所有开发任务都属于 [isqiwen/northstar-quant](https://github.com/isqiwen/northstar-quant)，
[Project 1](https://github.com/users/isqiwen/projects/1) 是顺序、优先级与实时状态的唯一管理入口。
本文只解释阶段和顺序，不复制动态任务状态。

## 阶段完成条件

| 阶段 | 可运行结果 | 任务 |
|---|---|---|
| [M0 单仓库交付](https://github.com/isqiwen/northstar-quant/milestone/1) | GitHub 干净检出可启动、研究、精确重放；已有本地实现形成可复核远端交付 | #16 |
| [M1 柜台仿真闭环](https://github.com/isqiwen/northstar-quant/milestone/2) | 已有真实数据、最小持久会话、具体柜台只读账户/行情、仿真报撤单与成交账本 | #17、#24、#25、#31、#32 |
| [M2 受限实盘闭环](https://github.com/isqiwen/northstar-quant/milestone/3) | 未知委托与断线恢复、安全控制、备份恢复、开户准入与生产只读核对、独立授权的真实开平仓 | #26、#27、#33–#35 |
| [M3 研究与组合增强](https://github.com/isqiwen/northstar-quant/milestone/4) | 完整历史跨日模拟、样本外比较、修订与长研究任务、多合约、换月、多策略 | #18–#23、#28–#30 |

M2 是第一条真实执行技术闭环，不是策略已盈利、可扩大资金或可无人值守的声明。
限定一个账户、一个真实合约、固定策略与风险配置、有人监督的日盘窗口；
最小一手超限则拒绝执行，无法平仓时保留真实风险并人工接管。
M3 不阻塞首个受限闭环，但实际交易日、今昨仓、费用保证金、待单预占、对账和执行安全不能后移。
没有已确认的交付日期，不以虚构季度承诺代替依赖关系。

## 建议执行顺序

| 顺序 | 用户可见结果 | 前置 Issue |
|---|---|---|
| 1 | [#16 从单仓库干净检出启动并复现完整研究闭环](https://github.com/isqiwen/northstar-quant/issues/16) | — |
| 2 | [#17 导入首份真实国内期货行情，并从已有数据继续研究](https://github.com/isqiwen/northstar-quant/issues/17) | [#16](https://github.com/isqiwen/northstar-quant/issues/16) |
| 3 | [#24 从固定配置启动最小持久会话，分离模拟撮合与事实入账](https://github.com/isqiwen/northstar-quant/issues/24) | #17 |
| 4 | [#31 具体柜台仿真只读连接、账户与行情核对](https://github.com/isqiwen/northstar-quant/issues/31) | —；与 #24 并行，实际连接需仿真账号 |
| 5 | [#34 开户、程序化准入、生产 API 权限与只读账户核对](https://github.com/isqiwen/northstar-quant/issues/34) | #31；外部准入与开发并行，不阻塞仿真 |
| 6 | [#25 持续行情驱动会话，处理时段、新鲜度与断线缺口](https://github.com/isqiwen/northstar-quant/issues/25) | #24、#31 |
| 7 | [#32 在外部柜台仿真完成策略报撤单、部分成交与账本核对](https://github.com/isqiwen/northstar-quant/issues/32) | #24、#25、#31 |
| 8 | [#26 从工作台安全启用、暂停、撤单、请求平仓与停止会话](https://github.com/isqiwen/northstar-quant/issues/26) | #25、#32 |
| 9 | [#33 未知发送结果、断连重启与单执行权恢复](https://github.com/isqiwen/northstar-quant/issues/33) | #24、#32 |
| 10 | [#27 恢复备份后默认暂停，核对柜台事实与旧执行者失权](https://github.com/isqiwen/northstar-quant/issues/27) | #24、#33 |
| 11 | [#35 独立授权与固定限额的首次受限真实开平仓及对账](https://github.com/isqiwen/northstar-quant/issues/35) | #26、#27、#33、#34；另需明确实盘执行授权 |
| 12 | [#18 完整历史夜盘、日盘和跨日时段研究](https://github.com/isqiwen/northstar-quant/issues/18) | #17 |
| 13 | [#19 历史连续账户、跨日结算与费用保证金模拟](https://github.com/isqiwen/northstar-quant/issues/19) | #18 |
| 14 | [#20 完善模拟成交的时段、价格和成交量约束](https://github.com/isqiwen/northstar-quant/issues/20) | #19 |
| 15 | [#21 固定评价方案与样本外比较](https://github.com/isqiwen/northstar-quant/issues/21) | #20 |
| 16 | [#22 历史迟到与修订的可得性重放](https://github.com/isqiwen/northstar-quant/issues/22) | #18、#19 |
| 17 | [#23 持久长研究任务](https://github.com/isqiwen/northstar-quant/issues/23) | #21 |
| 18 | [#28 多合约共同资金与风险约束](https://github.com/isqiwen/northstar-quant/issues/28) | #19、#20、#21 |
| 19 | [#29 真实合约换月与价差成本解释](https://github.com/isqiwen/northstar-quant/issues/29) | #28、#22 |
| 20 | [#30 多策略预算与目标净额](https://github.com/isqiwen/northstar-quant/issues/30) | #28 |

Order 表示默认优先执行顺序，原生 Blocked by 表示真正依赖。
依赖已完成的独立任务可以调整顺序；同一时间默认只推进一个主要纵向结果。
每条 Issue 的实现范围、验收和外部条件位于 Issue 正文，不另建横向任务清单。

## 外部条件与实盘门禁

用户已确认尚未开户。先核实 [CTP / SimNow 官方仿真](https://www.simnow.com.cn/product.action)
的一个具体接入实现；仿真账号、SDK 可运行平台及接口权限在 #31 核实，生产期货公司与
程序化报告确认、认证及行情权限在 #34 核实。密码、认证码和个人账户资料不进入公共 Issues。
模拟环境和生产环境分别授权，能登录不等于允许报撤单，仿真通过不等于允许动用真实资金。

没有外部账号时可先完成 #24 与接入实现中的独立开发；不得用文件或内部 Paper 替代 #31/#32
的外部柜台验收。没有生产准入和具体执行授权时，#35 保持未完成，不以目标声明授权代理下单。
开户与协议签署由用户办理；首次真实执行由用户在已验证软件中亲自启用，开发代理不代发真实投资订单。
完整历史研究后移，但上线范围内的交易日、今昨仓开平、真实费用与保证金核对、损失及限频控制、
UNKNOWN 与未决预占、单执行者、人工应急渠道和恢复后重新启用都必须先具备。

## Project 使用规则

- 保留必要的 Status、Priority、Milestone、Order 和原生依赖；只有缺少外部输入时用 `needs-input`。
- Backlog：尚未选择开始，或前置/外部条件未具备。Todo：依赖具备、范围明确、可以开始。
- In Progress：正在实现完整行为。Review：代码和验收证据可检查。Done：验收满足且交付证据可复核。
- 正常推进应先查看所选 Issue 和其前置，再更新状态；发现遗漏时修正同一任务或拆出实际可用行为。
- 结束开发时写明可复核提交、相关运行/验证证据和已知限制；同步 Issue 与 Project。
  本地运行、文档完成或 CI 全绿单独都不代表整个用户结果已交付。
- 架构与代码可以在同一工作中修改；无需先合并一份架构文档才允许实施。
- 外部条件只阻塞对应验收；开户不阻塞仿真开发，完整研究平台不阻塞受限实盘技术主线。
- 每项任务都从输入走到持久结果和用户可见解释。兼容层、泛化 Contract/Schema/Validator/Fixture、
  文档测试、空框架和目录建设均不作为独立任务或验收目标。

原有三个跨仓库开发事项已由当前单仓库计划取代，不再作为活跃路线。

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
| [M1 柜台仿真闭环](https://github.com/isqiwen/northstar-quant/milestone/2) | 真实数据、托管原文与处理发布、固定配置与持久会话、柜台只读账户/行情、仿真报撤单与账本 | #17、#24、#36、#25、#31、#32 |
| [M2 受限实盘闭环](https://github.com/isqiwen/northstar-quant/milestone/3) | 未知委托与断线恢复、安全控制、数据库与文件联合恢复、生产准入/只读核对、独立授权的真实开平仓 | #26、#27、#33–#35 |
| [M3 研究与组合增强](https://github.com/isqiwen/northstar-quant/milestone/4) | 完整历史模拟、样本外比较、实际因子计算与复用、修订/长任务、多合约、换月、多策略 | #18–#23、#37、#28–#30 |

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
| 3 | [#24 保存并选择固定策略/Risk 配置，运行最小持久会话](https://github.com/isqiwen/northstar-quant/issues/24) | #17 |
| 4 | [#36 上传原文后持久归档，追踪处理与发布并继续研究](https://github.com/isqiwen/northstar-quant/issues/36) | #17；无需外部账号，不阻塞 #24 |
| 5 | [#31 具体柜台仿真只读连接、账户与行情核对](https://github.com/isqiwen/northstar-quant/issues/31) | —；本机配置与 dev TD 登录/查询已验证，继续核对与非空账户验收 |
| 6 | [#34 开户、程序化准入、生产 API 权限与只读账户核对](https://github.com/isqiwen/northstar-quant/issues/34) | #31；外部准入与开发并行，不阻塞仿真 |
| 7 | [#25 可追溯的持续行情驱动会话，解释新鲜度与断线缺口](https://github.com/isqiwen/northstar-quant/issues/25) | #24、#31、#36 |
| 8 | [#32 在外部柜台仿真完成策略报撤单、部分成交与账本核对](https://github.com/isqiwen/northstar-quant/issues/32) | #24、#25、#31 |
| 9 | [#26 确认实际配置与限额后安全启用、暂停和控制会话](https://github.com/isqiwen/northstar-quant/issues/26) | #25、#32 |
| 10 | [#33 未知发送结果、断连重启与单执行权恢复](https://github.com/isqiwen/northstar-quant/issues/33) | #24、#32 |
| 11 | [#27 联合恢复数据库与文件，保持暂停并核对柜台](https://github.com/isqiwen/northstar-quant/issues/27) | #24、#33、#36 |
| 12 | [#35 独立授权与固定配置/限额的首次受限真实开平仓及对账](https://github.com/isqiwen/northstar-quant/issues/35) | #26、#27、#33、#34；另需明确实盘执行授权 |
| 13 | [#18 完整历史夜盘、日盘和跨日时段研究](https://github.com/isqiwen/northstar-quant/issues/18) | #17 |
| 14 | [#19 历史连续账户、跨日结算与费用保证金模拟](https://github.com/isqiwen/northstar-quant/issues/19) | #18 |
| 15 | [#20 完善模拟成交的时段、价格和成交量约束](https://github.com/isqiwen/northstar-quant/issues/20) | #19 |
| 16 | [#21 固定评价方案与样本外比较](https://github.com/isqiwen/northstar-quant/issues/21) | #20、#24 |
| 17 | [#22 历史迟到与修订的可得性重放](https://github.com/isqiwen/northstar-quant/issues/22) | #18、#19 |
| 18 | [#23 固定输入的持久长研究任务](https://github.com/isqiwen/northstar-quant/issues/23) | #21 |
| 19 | [#37 计算并复用一个实际因子，追溯输入、策略使用与评价](https://github.com/isqiwen/northstar-quant/issues/37) | #36、#21 |
| 20 | [#28 多合约共同资金与风险约束](https://github.com/isqiwen/northstar-quant/issues/28) | #19、#20、#21 |
| 21 | [#29 真实合约换月与价差成本解释](https://github.com/isqiwen/northstar-quant/issues/29) | #28、#22 |
| 22 | [#30 多策略配置、预算与目标净额](https://github.com/isqiwen/northstar-quant/issues/30) | #28 |

Order 表示默认优先执行顺序，原生 Blocked by 表示真正依赖。
依赖已完成的独立任务可以调整顺序；同一时间默认只推进一个主要纵向结果。
每条 Issue 的实现范围、验收和外部条件位于 Issue 正文，不另建横向任务清单。

#31 的真实非空持仓/待单与完整联合核对仍未完成，不妨碍独立开发 #25 的无发送接收内核。
当前切片为单账户单合约 TD/MD 逐事件留证、SHFE DAY 分钟影子目标和 Web 暂停/停止；
不运行真实账户 Risk、模拟成交或报撤单。PostgreSQL 保存的不可变 SDK 回调是该有界流的
权威来源，不是网络原文或已发布 Snapshot；#36 文件归档/加工发布联接及实际外部连续新鲜
行情验收尚待交付，不能因内核可运行就关闭 #25。原生 #24 / #31 / #36 依赖保持不变，
#32 / #26 的联合核对、账户安全与执行条件仍是硬门槛，不把独立开发解释为提前放行。

## 管理能力随纵向结果交付

#36 是来源到研究的独立切片，包含原文、失败处理、发布、引用和该范围的联合恢复；
#25 复用它接入持续来源，#27 再扩展到交易会话、柜台事实与安全恢复。
#24 提供固定策略/Risk 配置的保存、选择和运行绑定，#26/#35 负责受控生效与独立执行授权。
#21/#23 固定研究配置和任务身份，#37 按实际计算交付因子结果管理，不先建设因子平台。
这些能力都进入现有 Web 工作台；先做到可追溯、可选择、受控生效，再按真实需求扩展。

## 外部条件与实盘门禁

用户尚未开生产账户，已提供 SimNow 仿真账户及两套环境，同意先只读验收再推进受限仿真报撤单。
采用 [CTP / SimNow](https://www.simnow.com.cn/product.action) 的一个具体接入；
本机私密配置已就绪，2026-09-05 dev TD 实际认证/登录及七类查询已取得终结回包；
#31 继续完成真实核对与剩余验收，生产期货公司与
程序化报告确认、认证及行情权限在 #34 核实。密码、认证码和个人账户资料不进入公共 Issues。
模拟环境和生产环境分别授权，能登录不等于允许报撤单，仿真通过不等于允许动用真实资金。

不得用 SDK 加载、端口连通、固定返回值或内部 Paper 替代 #31/#32 的外部柜台验收。
只读是连通验收阶段，后续 #25/#32 继续打通持续行情和受限报撤单，不以只读工具作为最终交付。
没有生产准入和具体执行授权时，#35 保持未完成，不以目标声明授权代理下单。
开户与协议签署由用户办理；首次真实执行由用户在已验证软件中亲自启用，开发代理不代发真实投资订单。
首个受限实盘闭环不以完整历史研究为前置；但上线范围内的交易日、今昨仓开平、真实费用与保证金核对、损失及限频控制、
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

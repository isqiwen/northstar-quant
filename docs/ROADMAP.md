# 开发顺序与交付管理

最终目标：国内期货实盘，纵向尽早打通一个受限、可核对的真实交易闭环。
一个仓库、一套 Python 业务实现，按 Data、Research、Live、Console 四个运行角色交付。
详细设计与当前能力见 [ARCHITECTURE.md](ARCHITECTURE.md)，取舍见 [ADR](adr/0001-runtime-roles.md)。
[Project 1](https://github.com/users/isqiwen/projects/1) 是优先级、状态、Order 与原生依赖的管理入口；
Issue 拥有实施范围、验收和证据，本文只维护交付顺序及职责，不复制每轮测试/运行日志。

## 阶段完成条件

| 阶段 | 可运行结果 | 任务 |
|---|---|---|
| [M0 单仓库交付](https://github.com/isqiwen/northstar-quant/milestone/1) | 干净检出可启动、研究并核验固定结果 | #16 |
| [M1 柜台仿真闭环](https://github.com/isqiwen/northstar-quant/milestone/2) | 固定数据/配置、独立 Live/Console、持续行情、仿真报撤单与账本 | #17、#24、#36、#38、#39、#31、#25、#32 |
| [M2 受限实盘闭环](https://github.com/isqiwen/northstar-quant/milestone/3) | 云端运行、安全控制、未知结果与分域恢复、生产准入及独立授权的真实开平仓 | #40、#26、#33、#27、#34、#35 |
| [M3 研究与组合增强](https://github.com/isqiwen/northstar-quant/milestone/4) | 独立 Data/Research、归档复用、大规模读取、完整评价、因子及组合 | #23、#41–#43、#18–#22、#37、#28–#30 |

里程碑按交付结果归类，不是阶段之间全部串行的依赖。M2 中的 #40 云端无发送基座先交付，
随后 #32/#26/#33 在该部署验收交易与故障，不能让云基座反过来等待完整交易功能。
M2 完成是限定账户、合约、配置和受监督日盘窗口的技术闭环，不是盈利、扩资或无人值守许可。
最小一手超限则不交易；实际交易日、今昨仓、费用保证金、预占、执行安全与核对不能后移。

## 默认开发顺序

| Order | 用户可见结果 | 原生 Blocked by |
|---|---|---|
| 1 | [#16 单仓库干净检出与研究基线](https://github.com/isqiwen/northstar-quant/issues/16) | — |
| 2 | [#17 首份真实历史行情导入与复用](https://github.com/isqiwen/northstar-quant/issues/17) | #16 |
| 3 | [#24 固定配置与可恢复文件 Paper](https://github.com/isqiwen/northstar-quant/issues/24) | #17 |
| 4 | [#36 原文归档、失败解释、发布与研究](https://github.com/isqiwen/northstar-quant/issues/36) | #17 |
| 5 | [#38 NiceGUI 持续接收详情](https://github.com/isqiwen/northstar-quant/issues/38) | — |
| 6 | [#39 独立 Live/Console，工作台重启不影响接收](https://github.com/isqiwen/northstar-quant/issues/39) | #24、#36、#38 |
| 7 | [#40 国内云无发送运行，本地离线与失联告警](https://github.com/isqiwen/northstar-quant/issues/40) | #39；另需云资源/访问与部署许可 |
| 8 | [#31 柜台只读账户、持仓/委托与核对](https://github.com/isqiwen/northstar-quant/issues/31) | — |
| 9 | [#34 生产开户、准入与只读核对](https://github.com/isqiwen/northstar-quant/issues/34) | #31、#40；开户与机构确认并行 |
| 10 | [#25 持续真实行情、时段与缺口解释](https://github.com/isqiwen/northstar-quant/issues/25) | #24、#31、#36、#39 |
| 11 | [#32 云端仿真报撤单、成交与账本](https://github.com/isqiwen/northstar-quant/issues/32) | #24、#25、#31、#39、#40 |
| 12 | [#26 固定运行材料与远程受控启用/暂停/平仓](https://github.com/isqiwen/northstar-quant/issues/26) | #25、#32、#39、#40 |
| 13 | [#33 云端未知结果、重启与单执行权恢复](https://github.com/isqiwen/northstar-quant/issues/33) | #24、#32、#39、#40 |
| 14 | [#27 本地与 Live 分域一致备份及安全恢复](https://github.com/isqiwen/northstar-quant/issues/27) | #24、#36、#33 |
| 15 | [#35 用户独立授权的首次受限真实开平仓](https://github.com/isqiwen/northstar-quant/issues/35) | #26、#27、#33、#34 |
| 16 | [#23 独立 Research worker 与持久研究任务](https://github.com/isqiwen/northstar-quant/issues/23) | #17、#24 |
| 17 | [#41 独立 Data 持久采集、加工与发布](https://github.com/isqiwen/northstar-quant/issues/41) | #36 |
| 18 | [#42 云端市场段异步归档到 Data 并研究](https://github.com/isqiwen/northstar-quant/issues/42) | #39、#40、#41 |
| 19 | [#18 真实交易日与跨日时段研究](https://github.com/isqiwen/northstar-quant/issues/18) | #17 |
| 20 | [#19 连续历史账户、结算与保证金](https://github.com/isqiwen/northstar-quant/issues/19) | #18 |
| 21 | [#20 保守模拟成交与未成交解释](https://github.com/isqiwen/northstar-quant/issues/20) | #19 |
| 22 | [#21 固定评价方案、基准与样本外比较](https://github.com/isqiwen/northstar-quant/issues/21) | #20、#24 |
| 23 | [#22 迟到与修订的可得性重放](https://github.com/isqiwen/northstar-quant/issues/22) | #18、#19 |
| 24 | [#43 大体积行情/因子范围读取、容量与引用](https://github.com/isqiwen/northstar-quant/issues/43) | #36 |
| 25 | [#37 实际因子计算、复用与评价](https://github.com/isqiwen/northstar-quant/issues/37) | #21、#36 |
| 26 | [#28 多合约共同资金与风险](https://github.com/isqiwen/northstar-quant/issues/28) | #19、#20、#21 |
| 27 | [#29 真实合约换月与成本](https://github.com/isqiwen/northstar-quant/issues/29) | #28、#22 |
| 28 | [#30 多策略共同预算与目标净额](https://github.com/isqiwen/northstar-quant/issues/30) | #28 |

Order 是默认选择顺序，只有 Blocked by 才是硬前置。外部条件未具备时继续独立工程工作：
#39 复用 #31/#25/#32 已交付切片，不等待它们整体验收；#40 只依赖 #39 的无发送能力。
#23 使用已有固定研究，不再依赖 #21 完整评价；#41 与 #23 可分别推进，不因共用 Console 串行化。
#43 可在已有 #36 内容上开发，不需要先完成进程拆分或因子平台。
#23/#41–#43 与完整历史研究不阻塞 #35，必要账户安全和真实外部验收仍是硬门槛。

## 每个角色交付什么，不重复建设什么

- Live/Console：#39 拥有独立生命周期和真实运行状态；#40 拥有云端无发送部署、同域存储、
  受保护的基础查询/影子控制与告警。#32/#26/#33 分别拥有实际交易、执行授权与故障恢复。
  #32 首次发送前就要具备最小授权、风险预占、唯一发送者和 UNKNOWN 保护，不能留到后续验收才实现。
- Data：#41 拥有持久来源任务/采集调度与发布，#42 拥有有界异步复制，#43 拥有大规模范围读取。
  Live 是交易事实权威写入者，Data 只接获准固定副本；积压/容量受控，禁止用归档副本改写账户。
- Research：#23 拥有独立轻量接单/查询入口与任务尝试，计算 worker 按需启动，复用现有计算和结果；
  无计算 worker 时仍可接单，不把任务控制放回 Console。#21 拥有评价，#37 拥有实际因子研究。
  参数搜索和模型训练使用同一 Research 执行路径，按实际策略需要细化，不先建设训练平台。
  有实际模型时，固定输出仍需 #26 的本地材料核验和受控启用，训练完成不授权交易。
- Console：NiceGUI 逐入口改为调用拥有行为的角色，不持有柜台连接、研究 worker 或业务表写入权。
  #39/#41/#23 各自连通真实页面与结果，不另建“前端架构平台”Issue。
- 恢复：#27 按本地与云端事实所有权分别备份数据库及必要文件，核验跨域固定引用；
  本地备份只协调本域写入，不暂停 Live；涉及 Live 维护/恢复才处理新增风险与未决委托。
  不要求跨公网全局同一快照，不等 #41/#42 全平台才验证 Live 必需材料和执行权。

## 当前交付与外部条件

当前 #39 实施独立 Live + Console + PostgreSQL：Live 拥有接收与账户处理，
Console/CLI 通过认证 HTTP 使用同一 owner，CTP 原生子进程留在 Live 内部。
代码按运行服务、传输、页面及命令职责分组；Data/Research 独立进程仍属 #41/#23。
无凭据的进程/浏览器验收与实际 SimNow 连续行情分别记录；以 Issue 的可复核证据判定完成。
#31/#25/#32 已交付的只读查询、有界接收、影子目标、固定预算与保存回报自动入账继续保留；
实际持续新鲜行情、非空核对、启动查询汇合、必要资金/结算、预占和发送仍按各 Issue 的证据判断。

用户已有 SimNow 私密配置，尚未开生产账户。#31/#25 记录实际样本、时段/环境及来源许可缺口，
不索取新密码，不把工程未完成假装成缺少外部输入。
#40 另需明确云主机/预算、运维访问与部署范围、告警接收渠道；规划不授权购买资源或部署。
#34 的开户、协议与机构确认由用户办理；#35 首次真实投资执行由用户本人在已验证软件中启用，
开发代理不代发真实订单。仿真成功、只读许可和进程在线均不是生产交易授权或盈利证据。

## Project 使用规则

- 使用 Status、Priority、Milestone、Order 与原生 Blocked by；缺少外部条件标 needs-input 并写具体缺口。
- Backlog 是尚未选择或条件未满足，Todo 是下一项可实施任务；In Progress 只表示正在实施，
  Review 表示等待验收，Done 必须有可复核实现与实际验收。规划建单保持 Open，不创建虚假的已完成功能。
- 同一时间一个主要纵向结果。部分实现未整体验收的事项可以回到 Backlog，保留全部历史证据；
  选择下一任务时读取该 Issue 及其真正前置，不把 Order 的全部前项当作串行依赖。
- 架构与代码可一起修改，不以先合并文档为实施门槛；交付后记录提交、安装态/浏览器与相关真实集成证据。
- 只维护当前架构、实现、存储与协议，不兼容旧系统。泛化 contracts/schema/validator/fixture、
  文档测试、空目录和独立脚手架均不作为交付目标；验证聚焦资金、授权、因果、重复效果与故障恢复。

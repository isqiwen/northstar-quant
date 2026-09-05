# SimNow / CTP 只读接入依据

核实日期：2026-09-05。只实现一个具体 CTP Adapter，不引入网关平台或旧 SDK 兼容。
用户已在本机私密文件填写配置，应用已在授权 dev 环境实际认证、登录并查询。
秘密不进入聊天、仓库、日志或公共验收记录；没有报撤单、结算确认、转账或生产连接。
端口连通、SDK 自检及账户回包完整都不等于已完成对账。

## 选择及证据范围

选择 `ctpwrapper==6.7.13`：它直接包装 CTP，没有附带策略、账户或事件框架。
PyPI 当前提供源码包，构建使用 Cython/setuptools；随包版本文件标记
`v6.7.13_20260225`，Linux 原生库实际为 x86-64。
依据为[发布元数据](https://pypi.org/pypi/ctpwrapper/6.7.13/json)、
[维护者构建代码](https://github.com/nooperpudd/ctpwrapper/blob/f7e08c01e25359b5f4385c14388f8dfe5a1d6fd7/setup.py)
及[随包 SDK 标记](https://github.com/nooperpudd/ctpwrapper/blob/f7e08c01e25359b5f4385c14388f8dfe5a1d6fd7/ctp/version.txt)。

比较过 `openctp-ctp==6.7.11.0`：依赖少、可直接安装 Linux x86-64 wheel，
但此版本没有 macOS/arm64 wheel，所带原生版本也较旧，不保留它作为第二实现。
`vnpy_ctp==6.7.11.4` 依赖 vn.py 框架，macOS 仍需源码构建，不为一个只读入口引入整个框架。
依据：[openctp 维护者平台矩阵](https://github.com/Jedore/openctp-ctp-python#支持版本)、
[openctp 发布文件](https://pypi.org/pypi/openctp-ctp/6.7.11.0/json)、
[vnpy_ctp 构建依赖](https://github.com/vnpy/vnpy_ctp/blob/main/pyproject.toml)。

[SimNow 官方下载入口](https://www.simnow.com.cn/static/apiDownload.action)
本次访问遇到 403/浏览器挑战；[上期技术下载入口](https://www.sfit.com.cn/5_2_DocumentDown_2.htm)
超时，未绕过访问限制。因此以上是实际绑定及随包二进制的证据，
**不是独立确认“官方最新 SDK”的证据**；目标 dev 柜台接受该版本的认证、登录和查询，
已由下述实际回报单独验证，不能外推到其他环境或生产柜台。

## 实际运行平台

2026-09-05 08:31:36 UTC，在禁用网络的 Linux amd64 容器、CPython 3.12.14 中，
两个候选都完成实际导入、Trader/Md 句柄创建及释放，没有调用 `Init` 或注册前置：

| 绑定 | 实际 Trader / Md `GetApiVersion()` | 结果 |
|---|---|---|
| ctpwrapper 6.7.13 | `v6.7.13_20260225 14:16:30.12079` | 创建与释放通过 |
| openctp-ctp 6.7.11.0 | `v6.7.11_20250617 16:22:00.10369` | 创建与释放通过，仅作比较 |

采用整个应用的 `linux/amd64` 容器，不增加第二个部署。macOS arm64 原生环境可开发和运行
非柜台功能，但不能加载本次选定的原生库；Linux arm64 也没有随此包交付的原生库。
本机对 openctp 6.7.11.0 的 Python 3.12 安装解析实测因无匹配 macOS wheel 被拒绝，未降级。
ctpwrapper 构建阶段需要 C++ 编译器；本次 `ldd` 核实运行依赖为常规 glibc、libstdc++、libgcc。
不要把 openctp 的 GB18030 locale 要求误加为 ctpwrapper 的已证实要求。

真实接入暴露两处仅加载句柄不能发现的绑定调用错误：`SubscribePrivateTopic` 必须显式传
`nSeqNo=1`，CNY 资金查询必须传字符 `BizType="1"`（期货）。现在无网络自检复用实际
七种查询结构及订阅准备调用；QUICK 仍只取登录后回报，不读取旧 flow，也不增加兼容实现。
依据为同版本 [Python 订阅方法](https://github.com/nooperpudd/ctpwrapper/blob/f7e08c01e25359b5f4385c14388f8dfe5a1d6fd7/ctpwrapper/Trader.py)、
[原生类型定义](https://github.com/nooperpudd/ctpwrapper/blob/f7e08c01e25359b5f4385c14388f8dfe5a1d6fd7/ctp/header/ThostFtdcUserApiDataType.h)
以及本机断网复现，不是密码或前置网络故障。

## 只读 Interface 与失败语义

`query_account` 只做认证、登录、CNY 资金、全账户持仓/委托/成交，以及指定合约、
投机保证金率和手续费率查询；随后短暂订阅该合约，只保留首份实际行情与订阅回报。
不调用结算确认、报单、撤单、资金转账或密码更新。
真实方法名、参数及回调采用[该绑定实际使用的 Trader 头文件](https://github.com/nooperpudd/ctpwrapper/blob/f7e08c01e25359b5f4385c14388f8dfe5a1d6fd7/ctp/header/ThostFtdcTraderApi.h)、
[Md 头文件](https://github.com/nooperpudd/ctpwrapper/blob/f7e08c01e25359b5f4385c14388f8dfe5a1d6fd7/ctp/header/ThostFtdcMdApi.h)
及[Python 请求结构](https://github.com/nooperpudd/ctpwrapper/blob/f7e08c01e25359b5f4385c14388f8dfe5a1d6fd7/ctpwrapper/ApiStructure.py)。

每个 TD 请求记录实际提交返回码、请求编号、逐条回调、`bIsLast`、错误编号及本地接收顺序；
串行请求之间至少间隔 1.1 秒是本项目的保守限额，不宣称是柜台当前的固定限值。
`SubscribeMarketData` 没有调用方请求编号参数，不伪造其回调与某个 TD 请求的对应关系。
空结果与未收齐不同；查询完成不代表跨多次查询的一致截面，更不代表已建立真实账户账本。
接收窗口中的异步委托/成交与断线仍留证，缺行情不补造 tick、不声称持续来源。

原生回调立即复制白名单字段，CTP 临时指针不离开回调；错误只保存编号，不保存自由错误文本。
绑定的[回调入口持有 Python GIL](https://github.com/nooperpudd/ctpwrapper/blob/f7e08c01e25359b5f4385c14388f8dfe5a1d6fd7/ctpwrapper/cppheader/CTraderAPI.h)，
而[Release 未显式释放 GIL](https://github.com/nooperpudd/ctpwrapper/blob/f7e08c01e25359b5f4385c14388f8dfe5a1d6fd7/ctpwrapper/TraderApi.pyx)。
据此推断并防范释放与回调等待相互阻塞的风险：使用应用内部短命子进程与有界 Pipe，
不是第二个网络服务。密码不进入 argv、日志或非受控文件；45 秒采集及短释放宽限后，
仅终止本次只读子进程，已收证据和失败原因仍可保存。重连不自动重复登录或查询。

## 实际 dev 只读验收

2026-09-05 **09:10:41.500857 → 09:10:53.815776 UTC**，在用户指定的 `simnow_dev`
完成一次有界查询，应用实际加载上述 6.7.13 原生库。TD 认证、登录及七类请求的即时返回码
均为 0，匹配编号的终结回报均无错误；交易账户身份确认，柜台交易日为 `20260904`。

| 实际观察 | 结果 |
|---|---|
| CNY 资金 | 1 条账户回报；金额仅保留在本机私有记录 |
| 全账户持仓 / 委托 / 成交 | 各 0 条，均已收到各自终结回包，非超时推测空仓 |
| 目标 `rb2610` | 原始合约回包 45 条，含同前缀期权；只有 1 条精确匹配的期货合约 |
| 保证金 / 手续费 | 各 1 条指定合约回报，保留实际字段，不替代有效条款核对 |
| MD 登录 | 请求编号与回包均为 0，终结且无错误，交易日与 TD 一致；未回显账户身份，独立标记 `UNKNOWN` |
| 行情订阅 / 首份行情 | 指定合约订阅终结回报成功，实际收到一条行情；不是持续来源验收 |

首份行情的 `TradingDay=20260904`、`ActionDay=20260903`、`UpdateTime=17:18:34.500`；
原值与本机接收时刻分别保留，不能把接收成功写成当前可交易或新鲜行情。
本次修正将 TD 账户身份与 MD 登录分开解释；行情空身份不会覆盖 TD 身份，明确冲突和
交易日不符仍阻塞。这也与 [VeighNa 维护者对行情登录的处理](https://github.com/vnpy/vnpy_ctp/blob/ad76250cf87cf5b03604336fde8c7489bdc0d0d7/vnpy_ctp/gateway/ctp_gateway.py#L275)
不以账户字段回显作为行情登录依据相吻合，但本项目额外保留请求和终结证据，不继承其交易操作。

本次结果 `COMPLETE` **仅表示回包齐全**，仍为 `UNRECONCILED`：该查询本身不是本地柜台账本，
多次查询不是原子截面，交易时段未核实，非空持仓/活动委托账户及真实账本差异尚未验收。
没有报撤单或生产权限，不因缺样本主动制造交易。先前三次失败及本次成功的固定证据均私有保留，
不覆盖成成功记录；`simnow_trading` 的认证与查询尚未实测。

### 固定观察与独立后验比较

同日 **09:35:16.903897 UTC**，通过实际网页将上述完整空账户观察固定为不可变基准。
随后 **09:35:39.778492 → 09:35:52.988799 UTC** 独立发起第二次 dev 只读查询，
TD 身份确认、七类查询完整、交易日仍为 `20260904`；没有复用原查询冒充新证据。
网页比较得到 `MATCHED`：保留的 16 个资金字段无变化，全账户持仓、委托和成交仍各为空。
资金与账户标识只保留在本机私有数据库和验收文件中。

真实浏览器完成固定、比较以及进程重启后的读取，桌面与窄屏无横向溢出、无 JavaScript 错误；
本地操作没有增加查询次数，原始查询、基准和比较摘要在重启后保持一致。
随后联合备份恢复到独立空库，全部 5 批查询、1 个基准、1 次比较通过完整性校验；
无凭据的 CLI 重试返回与原比较相同的固定内容，执行状态保持暂停，原库未重置。
这只是 `BASELINE_COMPARISON_ONLY`，始终 `UNRECONCILED`：没有完整成交、资金流与结算账本，
不证明查询间持续无活动，也不把字段变化归因为损益。非空账户与完整账本差异验收仍未完成，
因此 #31 保持进行中，没有为制造样本发送任何委托。

### 确认成交与独立持仓数量核对

已保存的成交查询和异步成交可追加到同日持仓账簿，按环境、账户、交易日、交易所、
成交编号和买卖方向去重；经济字段冲突保留原事实并显示未知。该复合身份是本项目的
当前处理规则，不能把空账户验收当作真实非空成交身份唯一性的证明。
字段来源采用同一 SDK 的
[成交与持仓结构](https://github.com/nooperpudd/ctpwrapper/blob/f7e08c01e25359b5f4385c14388f8dfe5a1d6fd7/ctp/header/ThostFtdcUserApiStruct.h)
和[开平标记定义](https://github.com/nooperpudd/ctpwrapper/blob/f7e08c01e25359b5f4385c14388f8dfe5a1d6fd7/ctp/header/ThostFtdcUserApiDataType.h)。
`TradeField` 不含逐笔确认手续费，账簿不将未知费用填零，也不从观察余额推导现金变化。

当前只投影空基准起步、同交易日的 SHFE 期货投机持仓，多空和今昨分别保存；
普通平仓或其他未支持标记、缺失合约资料、历史成交消失、跨日及不完整查询均不猜测。
Data 复用已有产品及合约 UUID，不能根据回包补造物理单位。
先固定入账结果，再获取独立查询核对；比较不偷偷导入新成交改写期望数量。

2026-09-05 **13:19 UTC**，真实浏览器将先前完整空账户查询登记为首个持仓账簿记录。
随后 **13:20:20.945415 → 13:20:32.860704 UTC** 发起一次新的有界 dev 只读查询，
TD 身份确认、各查询完整、交易日仍为 `20260904`，全账户持仓、委托、成交仍为空。
网页独立持仓比较为 `MATCHED`，仅表示数量一致；原查询、观察基准及原比较保持不变，
本地入账和比较没有触发外部查询。已有其他查询一并保留，未覆盖或重置实际账户数据库。
浏览器重启后仍返回相同账簿及比较摘要，桌面和窄屏无横向溢出、无 JavaScript 错误。
联合备份恢复到独立空库后，全部 7 批查询、1 个观察基准、1 次观察比较、1 个持仓入账
和 1 次持仓比较通过完整性校验，恢复状态保持暂停。
非空实际成交、活动委托、逐笔费用、资金流、结算和持续回报均未完成验收，
所有结果保持 `UNRECONCILED`，无报撤单或生产权限；没有主动制造交易样本。

### 委托观察与逐笔成交关联

在上述固定持仓比较之上，增加仅处理本地记录的委托核对。`OrderStatus` 与
`OrderSubmitStatus` 分别解释，数量依据 `VolumeTotalOriginal`、`VolumeTraded`、
`VolumeTotal`；字段和枚举仍来自同版本 SDK 的
[Order 结构](https://github.com/nooperpudd/ctpwrapper/blob/f7e08c01e25359b5f4385c14388f8dfe5a1d6fd7/ctp/header/ThostFtdcUserApiStruct.h#L1368)
和[状态定义](https://github.com/nooperpudd/ctpwrapper/blob/f7e08c01e25359b5f4385c14388f8dfe5a1d6fd7/ctp/header/ThostFtdcUserApiDataType.h#L573)。
撤单提交、撤单被拒绝与撤销终态不能合并；插入拒绝与撤销状态组合也单独显示，
参考[维护者的处理](https://github.com/vnpy/vnpy_ctp/blob/ad76250cf87cf5b03604336fde8c7489bdc0d0d7/vnpy_ctp/gateway/ctp_gateway.py#L667)，
但不引入其发送实现或仅按 OrderRef 关联的简化身份。

关联使用同环境、账户、交易日、交易所和 `OrderSysID`，保留前导零及回包自身的
`FrontID/SessionID/OrderRef`。缺交易所委托编号、身份冲突及歧义原样保留；
同一成交只从已有去重账簿累计，当前比较查询中的新成交不能用来补齐它自己的核对依据。
核对只到固定入账序号，后续入账不改变旧结果。旧委托消失、累计量回退、终态改变保持未知。
这里没有本地发送记录，也不释放预占；仍不构成订单生命周期、完整账户或执行权限验收。

2026-09-05 **13:53 UTC**，真实浏览器复用上节已经保存的独立空账户查询与持仓比较，
生成委托核对 `MATCHED`（0 条委托、0 笔未关联成交），并保持 `UNRECONCILED`。
本轮没有新增柜台查询；全部 7 批原始查询、已有入账和持仓比较摘要保持不变。
桌面 1440 与窄屏 390 无横向溢出、JavaScript 错误为 0；应用重启后返回原固定结果。
联合备份在新空库恢复，核验 7 批查询、1 个观察基准、1 次观察比较、1 个持仓入账、
1 次持仓比较及 1 次委托核对，执行保持 `PAUSED`。实际非空委托仍未验收；
部分成交、撤单状态、负成交差、歧义与丢失等行为由合成回包测试保护，不冒充真实交易。

## 授权范围内的 TCP 实测

用户指定以下两套范围；本次未能从可访问的官方页面独立复核新端口的归属。
2026-09-05 各端口仅尝试一次 TCP 连接、立即关闭，没有发送应用层数据：

| 用户指定范围 | 地址 | UTC 开始 → 结束 | 结果 |
|---|---|---|---|
| dev / TD | 182.254.243.31:40001 | 08:20:29.205 → 08:20:29.290 | connected |
| dev / MD | 182.254.243.31:40011 | 08:20:29.210 → 08:20:29.290 | connected |
| trading / TD | 182.254.243.31:30001 | 08:20:29.211 → 08:20:29.258 | ECONNREFUSED |
| trading / MD | 182.254.243.31:30011 | 08:20:29.211 → 08:20:29.258 | ECONNREFUSED |

网络结果只描述该时刻，不证明 CTP 握手、认证、登录、账户查询或交易权限。
环境服务时段、结算能力与账户可用条件仍应以用户实际使用时的
[SimNow 官方环境说明](https://www.simnow.com.cn/product.action)和真实回报核实，不互相推定。

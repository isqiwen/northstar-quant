# SimNow / CTP 只读接入依据

核实日期：2026-09-05。只实现一个具体 CTP Adapter，不引入网关平台或旧 SDK 兼容。
本轮没有读取交易密码、登录、查询真实账户或报撤单；端口连通及 SDK 自检不等于接入验收完成。

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
**不是独立确认“官方最新 SDK”或目标柜台接受该版本的证据**；最终认证、登录和查询仍待实测。

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

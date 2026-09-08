# 数据与柜台来源

本文保留实现所需的来源依据与历史验收边界。下列外部实测发生于 2026-09-05，
字段与预算依据复核于 2026-09-06；不代表当前服务可用性，本次文档整理未重新连接外部服务。
账户、凭据与原始私有验收材料不存入文档。

## 历史行情

首份真实分钟样本来自快期 / Shinny EDB，合约为 `SHFE.rb2610`，不是主连或拼接合约。
当时使用 `GET https://edb.shinnytech.com/md/kline`、`period=60`；
查询时间采用 `Asia/Shanghai`，返回 `datetime_nano` 表示 K 线起始 Unix 纳秒时间，成交量单位为手。
接口和数据使用范围以 [EDB 官方说明](https://doc.shinnytech.com/edb/latest/md_server.html) 为依据。
个人留存与研究不等于公开再分发许可。

历史样本：

| 项目 | 留存证据 |
|---|---|
| 请求区间 | 2026-09-04 13:30–15:00，北京时间 |
| 响应 | HTTP 200，CSV，5,380 字节，90 根分钟线 |
| 下载完成 | 2026-09-05T02:54:57Z |
| SHA-256 | `7f969bd8e3db80de794edb016d867f81f811fed60b2283efab8cbaacc218bc44` |
| 当次检查 | 无缺口和重复，成交量合计 61,550 手 |

接口未提供逐条历史首次可得时间或修订轨迹。下载完成时间和 HTTP Date 不是历史发布时间；
按分钟结束即可观察价格进行研究时，必须记录这是信息时钟假设。
`examples/intraday.toml` 的工程示例也不能被当作这份真实外部样本。

原研究范围只覆盖一个日盘子时段，费用、滑点和保证金是显式模拟参数。
合约单位与时段依据 [上期所合约细则](https://www.shfe.com.cn/regulation/exchangerules/productrules/202512/t20251231_829962.html)
和 [交易时间](https://www.shfe.cn/services/calenderandholidays/tradinghours/)，不能从 OHLCV 推导成交能力或实际账户费率。

## 当前柜台适配

代码固定使用 `ctpwrapper==6.7.13`，运行依赖 Linux amd64 原生库，构建需要 C++ 编译器。
macOS arm64 可运行非柜台功能；三个前端与 Live 管理 API 不因目录划分自动取得柜台连接权限。
没有保留第二种 SDK 实现。版本依据为
[发布元数据](https://pypi.org/pypi/ctpwrapper/6.7.13/json) 与
[固定提交的构建代码](https://github.com/nooperpudd/ctpwrapper/blob/f7e08c01e25359b5f4385c14388f8dfe5a1d6fd7/setup.py)。
这不构成“官方最新 SDK”或生产柜台兼容性结论。

实现入口为 `backend/src/northstar_quant/broker/ctp.py` 和 `_ctp_worker.py`：

- 只读查询包含认证、登录、资金、持仓、委托、成交、指定合约及费率，并保留行情订阅证据。
- 每项查询保存请求与回包身份、终结标记、错误和接收顺序；空回包与未收齐分开解释。
- 原生回调复制字段后交付，有界子进程处理查询和接收。重连不自动重复提交查询。
- 当前不调用报单、撤单、结算确认或资金转账；完整查询不是跨查询的原子账户截面。

具体字段依据固定版本的
[Trader API](https://github.com/nooperpudd/ctpwrapper/blob/f7e08c01e25359b5f4385c14388f8dfe5a1d6fd7/ctp/header/ThostFtdcTraderApi.h)、
[结构定义](https://github.com/nooperpudd/ctpwrapper/blob/f7e08c01e25359b5f4385c14388f8dfe5a1d6fd7/ctp/header/ThostFtdcUserApiStruct.h) 和
[枚举定义](https://github.com/nooperpudd/ctpwrapper/blob/f7e08c01e25359b5f4385c14388f8dfe5a1d6fd7/ctp/header/ThostFtdcUserApiDataType.h)。

## 历史 SimNow 验收

以下是已有记录摘要，不代表新版本已重新做过真实柜台验收：

| UTC 时间（2026-09-05） | 实际结果 | 限制 |
|---|---|---|
| 08:31:36 | 断网容器创建并释放 Trader/Md 句柄；版本 `v6.7.13_20260225 14:16:30.12079` | 无连接自检 |
| 09:10:41–09:10:53 | `simnow_dev` 认证、登录、七类查询完整，取得一份行情 | 持仓、委托和成交均为空；非持续新鲜行情验收 |
| 09:35:16–09:35:52 | 固定空账户基线，再独立查询比较为 `MATCHED` | 仅观察比较，仍 `UNRECONCILED` |
| 13:19–13:20:32 | 保存持仓入账记录，与新查询比较数量一致 | 未验证真实非空成交 |
| 13:53 | 使用已保存查询完成空委托核对，重启和联合恢复后记录一致 | 没有新增查询或真实委托 |

第一份行情的交易日为 `20260904`，ActionDay 为 `20260903`，UpdateTime 为 `17:18:34.500`。
接收到该记录不说明它在验收时是新鲜可交易行情。MD 登录身份未回显，与已确认 TD 身份分别留证。
历史 TCP 可达或拒绝结果不作为当前部署配置，前置地址取自获准私密配置并按实际环境核实。
原记录未完成 `simnow_trading` 认证查询、非空账户与真实报撤单验收。

## 账户事实与预算边界

当前已保存成交可按固定复合身份去重并生成持仓投影；范围限定为受支持的同日 SHFE 期货投机持仓。
不支持的开平标记、缺失身份、冲突、旧成交消失与跨日情况保留为待核查。
独立核对不能用本次查询的新成交改写它自己的比较依据；委托提交与终态分别解释。

`TradeField` 没有逐笔确认手续费。`TradingAccount.Commission` 是账户累计费用观察，
不能归给某笔成交或某个策略，也不能每次全额重复扣除。累计资金观察按相同环境、账户、币种、
交易日与结算范围关联；缺项、倒序或累计量下降不得用清零或新基准掩盖。
`Available` 保留柜台原值，不能从若干余额字段拼出未经核实的完整资金公式。

离线开仓预算只覆盖满足条件的空账户、一手 SHFE 投机目标；条款必须精确匹配账户和合约，
相对保证金率、未知冻结、字段缺失或歧义不推测。当前保守计算是：

```text
保证金 = 手数 × (乘数 × 保证金价格 × 金额率 + 每手金额)
开仓手续费 = 手数 × 乘数 × 成交金额预算价格 × 手续费金额率 + 手数 × 每手手续费
新增需求 = 保证金 + 开仓手续费
```

BUY 成交金额预算使用有效限价，SELL 使用有效涨停价；保证金价格取涨停价与昨结算价的较大值。
这是当前有界模型的保守假设，不是实际柜台冻结金额保证，不覆盖组合抵扣、跨日或临时提保。
保证金与手续费分别按分向上取整。计算见
[`risk/opening_budget.py`](../backend/src/northstar_quant/risk/opening_budget.py)，条款与证据装配见
[`live/opening_budgets.py`](../backend/src/northstar_quant/live/opening_budgets.py)。
完整资金和费用账本仍需转账、费用调整及结算事实，相关边界见
[上期所结算规则](https://www.shfe.cn/regulation/exchangerules/rules/202606/t20260603_831935.html)。
预算通过、数量匹配或查询完整都不授予发送权限。

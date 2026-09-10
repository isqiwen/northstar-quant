# 数据与柜台来源

本文保留来源依据与验收边界。历史样本实测于 2026-09-05，字段与预算依据复核于 2026-09-06；
2026-09-09 另完成下述免费 EDB 小范围读取，不代表持续采集或完整市场覆盖。
账户、凭据与原始私有验收材料不存入文档。

## Tushare 历史研究数据

用户已确认期货历史分钟权限及个人研究/本地留存范围。Data Hub 只支持 Tushare 全部期货历史自动同步；
不提供手工文件导入、Tick 或实时录制。token 在网页输入后只保存到 core 的私有凭据目录，不回显、不入任务和公开仓库。

适配依据（2026-09-09 核对）：
- [合约目录](https://tushare.pro/document/2?doc_id=135)与[期货交易日历](https://tushare.pro/document/2?doc_id=467)。
- [历史分钟](https://tushare.pro/document/2?doc_id=313)：1/5/15/30/60 分钟，单次上限 8,000 行，独立权限。
- [日线](https://tushare.pro/document/2?doc_id=138)：单次上限 2,000 行，成交额单位万元；分钟成交额单位元。
- [周/月线](https://tushare.pro/document/2?doc_id=337)、[结算参数](https://tushare.pro/document/2?doc_id=141)、[涨跌停](https://tushare.pro/document/2?doc_id=368)。
- [仓单](https://tushare.pro/document/2?doc_id=140)、[持仓排名](https://tushare.pro/document/2?doc_id=139)、[主力映射](https://tushare.pro/document/2?doc_id=189)、[复权日线](https://tushare.pro/document/2?doc_id=492)。
- [南华指数](https://tushare.pro/document/2?doc_id=468)金额为千元，[品种周报](https://tushare.pro/document/2?doc_id=216)金额为亿元。
- [Tick](https://tushare.pro/document/2?doc_id=314)没有 API，只以网盘交付，因此本站不支持。

通过固定 HTTPS 地址请求，原始响应、质量和不可变 Parquet 清单保留；网络/权限异常不回显供应商原始消息。
真实小范围验收见下文；合成响应的故障验收不能证明全部接口权限、覆盖、日夜盘标签或源端就绪时间。
下载层保留供应商时间标签与 FINAL_REVISED 来源，规范研究输入仍需核对交易日、合约与有效条款。

## 2026-09-10 Tushare 固定样本

用户授权最多三次请求，使用 core 私有 token 读取 `RB2610.SHF` 的 2026-09-08–09（自然日范围）。
`ft_mins` 1min 返回 692 行、15min 返回 48 行；`fut_daily` 返回 2 行。原文私有留存在 core，未公开行情内容或凭据。
当前 `tushare-response/4` 与 `tushare-numbers/1` 在隔离验收目录生成固定 Parquet/清单，全部原始字段、数值、金额单位转换和行数逐项核对通过；哈希摘要见 #41。
验收材料在本机 `.northstar/acceptance/issue41/`，没有将验收加工伪装为生产 worker 已发布的 receipt。
分钟金额按元、日线金额按万元转换；不改变供应商数量口径。每个自然日一分钟 346 行、十五分钟 24 行，
标签范围为 09:01/09:15 至 23:00；两个分钟级别均包含 21:00 标签，不能一律直接当成固定长度 bar 的结束时刻。这些条数不是经过交易日历确认的完整覆盖。
夜盘归属、边界标签、历史首次可得及有效条款继续 #18；不按自然日将分钟金额与日线强行相等。
此次成功证明该 token 当前可以访问所测分钟接口；不能用此前 40203 推断当前未开通权限，也不证明所有其他接口权限或源端更新时刻。
全量同步策略不变，未新增下载范围控件，没有重启或更改个人部署。

## 已有外部样本证据

2026-09-09 的 EDB 工程切片曾通过 SHFE.rb2610 2026-09-08 13:30–15:00 的 90 根分钟线、5,361 字节验证原文加工，
SHA-256 `11802406e30c54bca8c654205d4c89b1563f2c50e9af8cc4cbc39185fe073a36`。
对应历史提交为 80275f9；当前 EDB 下载入口已移除。该样本不代表 Tushare 质量、权限或完整跨日研究验收，原文未公开提交。

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

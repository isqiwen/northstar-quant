# 中国商品期货研究数据来源评估

**结论（2026-08-31）**：当前 AKShare/Sina 连续合约日线**足以做价格/成交量因子的工程探索**，但不够支撑完整的
商品期货因子挖掘、候选策略准入或执行保真回测。下一阶段应采购一个可审计的实际合约市场数据主源，并按品种补充
现货/基本面数据；交易所发布物用于合约、日历、规则和交割仓单的权威核验。

本备忘录只记录已由数据所有者或供应商官方页面确认的能力。产品、字段、历史深度、使用权和保留期均以签署合同、
数据字典和样本交付复核为准，不能从营销页面推断。

## 1. 当前来源是否够用

仓库默认来源是 `AKShare/Sina` 的公开**主力连续合约日线**。注册表明确它只有 `1d`、不含实际合约数据、
不提供权威交易日历或动态规则，且许可证状态为 `public_reference_unverified`；默认画像也明确它仅供研究，不能
代表可交易合约、结算或下单数据。[数据源注册表](../../configs/data/sources.yaml)
[连续日线画像](../../configs/profiles/offline/cn_futures_daily_trend_offline.yaml)

因此它适合当前已实现的连续日线横截面研究：momentum、reversal、volume ratio、realized volatility 及其严格
PIT/OOS 回放。[研究流水线](FACTOR_RESEARCH_PIPELINE.md)

它不覆盖或不能可靠证明以下事实：

| 研究/回测需要 | 当前 AKShare/Sina 连续日线 |
| --- | --- |
| 实际合约链、换月、近远月价差与 carry | 不够；连续序列不是实际可交易合约。 |
| 逐合约日线、夜盘完整性、分钟、tick、逐笔/盘口 | 不够。 |
| 合约规格、交易日历、保证金、手续费、涨跌停和规则的历史版本 | 不够；必须以交易所权威发布物按决策时点绑定。 |
| 仓单、库存、现货价格、产量、开工、进出口、供需 | 不够。 |
| 候选研究准入、双源校验和可审计许可 | 不够；当前准入政策要求已授权的商业主源和独立验证源。 [准入政策](../../configs/research/admission/cn_commodity_futures_research_conservative_v1.yaml) |

## 2. 可用来源分层

### A. 交易所权威发布物：规则与交割事实的来源

应直接保存原始发布物、抓取时间、公告/版本号和 `available_time`，并把修订作为新版本，而非覆盖旧值。

| 来源 | 官方已展示/说明的能力 | 在 Northstar 中的正确角色 |
| --- | --- | --- |
| [SHFE 日周数据](https://www.shfe.com.cn/reports/tradedata/dailyandweeklydata/?query_params=dailystock) | 页面列出日交易快讯、成交持仓排名、结算参数、仓单日报、周行情、库存周报等。 | SHFE 合约日线/结算、仓单/库存和规则核验的权威发布物。 |
| [INE 官方市场页](https://www.ine.cn/eng/index.html) | 提供交易日历、规则入口和延时市场行情；页面明确行情图至少延时 30 分钟。 | SC、LU、NR、BC 等 INE 品种的规则、日历、交割与行情核验；延时网页不是低延迟行情许可。 |
| [DCE 官方站](https://www.dce.com.cn/dalianshangpin/) 与 [ZCE 官方站](https://www.czce.com.cn/) | 两所是其上市合约、交割规则、日行情、持仓排名和标准仓单发布物的所有者；ZCE 还公开了[行情接口规范](https://english.czce.com.cn/en/rootfiles/2023/03/31/1681344139426513-1681344139449318.pdf)。 | DCE/ZCE 品种的合约、规则、日历、日度交易统计和交割仓单的权威核验。不要把网页抓取默认为获授权的历史数据再分发。 |
| [GFEX 交易日历与历史行情入口](https://www.gfex.com.cn/gfex/jyrl/list.shtml) | 官方页面提供延时行情、历史行情和交易日历入口；交易所还发布了[行情授权申请指引](https://www.gfex.com.cn/gfex/sdhqzlxa/202212/06f979ada4364863b63497b332a5d146/files/690cbed3f4954ad1a1201df7190de6e9.pdf)。 | GFEX 品种的权威日历、规则及交割数据；授权指引意味着用作受治理历史数据前要核验许可范围。 |

交易所仓单是**可交割注册仓单**或指定仓库库存，不等于社会库存、港口库存或全产业链库存；它可以形成独立的
delivery-inventory 特征，但不能替代基本面库存口径。

### B. 许可市场数据：实际合约日线、分钟与 tick 的主候选

| 供应商 | 官方确认的能力 | 采购前必须确认 |
| --- | --- | --- |
| [Wind Server API](https://www.wind.com.cn/mobile/WDS/sapi/en.html) / [Wind 实时行情服务](https://www.wind.com.cn/portal/zh/WDS/marketdata.html) | Wind 说明其 Server API 面向量化研究/系统集成；行情服务说明可提供全球股票、指数、期货、期权的历史 tick、分钟和日线，及逐笔成交、逐笔委托、委托队列和盘后回放。 | 中国各商品交易所的精确品种/字段、历史起点、Level-1/Level-2、夜盘、内部研究/回测/模型验证、落地/保留/派生数据和交易所授权。 |
| [iFinD Quant API](https://quantapi.10jqka.com.cn/gwstatic/static/ds_web/quantapi-web/) | 官方页面列出期货、历史与实时行情、日/周/分时/秒级频率，以及 Linux Python/C++/Java 接口。 | tick/逐笔是否包含在本合同、品种覆盖、历史深度、修订政策、使用和保存权。官方概览不能单独证明 tick 授权。 |
| [Choice Quant API](https://choice.eastmoney.com/FileDownload/DOC/CFTG.pdf) | 官方资料说明接口覆盖期货等市场，并提供基本资料、历史行情、实时行情及 Linux Python/C++ 接口。 | 分钟/tick/逐笔的可得性、具体交易所、历史深度和全部许可范围。不要把“覆盖期货”扩展解释为已确认的 tick 服务。 |
| [Tushare Pro 期货目录](https://tushare.pro/document/1?doc_id=108%E3%80%82%EF%BC%9Bindex_global) / [期货 tick 说明](https://tushare.pro/document/2?doc_id=314) | 官方目录列出合约、日线、交易日历、持仓排名、仓单和结算参数；其 tick 页面说明全市场合约 tick 以 CSV 网盘交付、近十年历史、按日增量更新，非普通 API/积分权限。 | 数据合同、原始来源、适用交易所、完整性、允许的本地/派生存储和商用/模型使用权。它是可评估的研究数据候选，不是交易所权威规则源。 |

仓库已有的采购方向是 Wind 为主源、iFinD 为独立验证源，但两者均仍为 `procurement_pending`，故不能写成已经可用。
[数据源注册表](../../configs/data/sources.yaml)

### C. 现货与基本面：按产业链购买，而不是寻找单一“万能”源

| 领域 | 供应商官方描述的可用内容 | 适合的品种/用途 |
| --- | --- | --- |
| [卓创资讯 SCI 数据中心](https://digital.sci99.com/channel/datacenter) | 供应、成本利润、需求、库存、进出口、指数和价格；官方页面还说明可提供数据包及 API 定制。 | 能源、化工、塑料、橡胶、农产品、钢铁、有色等的现货/产业链因子。 |
| [CCF/华瑞数据终端](https://service.ccf.com.cn/index.html) | 价格、供给、库存、需求、产量等结构化数据，支持历史回溯与导出。 | PTA、MEG、聚酯、化纤等产业链；需逐项确认口径、频率和 API/导出许可。 |
| [国家统计局数据查询](https://data.stats.gov.cn/) 与 [海关总署统计数据](http://www.customs.gov.cn/customs/302249/302274/index.html) | 官方宏观、工业生产和进出口统计发布。 | 低频宏观/供需锚点与交叉核验；必须记录发布时间和修订，不能把统计期当作可用时间。 |

## 3. 推荐决策顺序

1. **现在不必停止 P11**：继续用 AKShare/Sina 做连续日线价格因子与 AI 挖掘的 research-only 验证。
2. **先采购而非先写 adapter**：以 Wind（主）+ iFinD（独立验证）询价并取得数据字典/样本；合同必须明确实际合约
   日线、夜盘、分钟，是否需要 tick/逐笔，以及 `internal_research`、`historical_backtest`、`model_validation`、
   本地原始/派生数据保留和审计权。Choice/Tushare 可作为报价和覆盖面的备选比较，不应凭概览页假定能力相同。
3. **用交易所发布物补权威边界**：按交易所维护合约规格、交易日历、规则/费率/保证金/涨跌停版本与仓单数据；商业
   行情供应商不能替代这些规则的决策时点证据。
4. **只为研究品种池购买基本面**：先选核心品种及其产业链（例如黑色/有色/能化），再向 SCI、CCF 等逐项索取
   字段字典、采样口径、首次发布时间、修订政策、历史深度和许可。不要把“库存”混为仓单、港口、社会或企业库存。
5. **tick 延后单独决策**：日线 actual-contract 验证和完整分钟数据是近期优先；只有策略结论对撮合、盘口或日内执行
   敏感时，才购买并接入 tick/逐笔/队列数据。tick 数据体量、许可和 PIT 版本成本显著更高。

无论选哪一源，接入前都应形成有签约主体、订单号、交易所授权（如适用）、字段/频率/品种范围、保留期、
原始与派生产物权利、修订/补数政策和 `available_time` 语义的证据包。缺一项时，数据只能保持研究参考状态，
不能用于候选准入或交易。

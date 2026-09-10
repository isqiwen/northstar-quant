# 长期参考项目

按用户指定的参考方向整理于 2026-09-10，供后续 AI 和开发者按模块查阅。
NautilusTrader 是总体架构的首要参考；其他项目补充国内期货规则、研究方法、存储和交互设计。
本表记录参考意图，不是逐项完成技术评估后的选型结论。当前实现与取舍以 [ARCHITECTURE](ARCHITECTURE.md) 和代码为准。

## 项目与适用范围

| 项目 | 最值得参考 / 复用的部分 | 对 Northstar 的主要价值 | 适用层 |
|---|---|---|---|
| **[NautilusTrader](https://nautilustrader.io/docs/latest/concepts/architecture/)** | 交易领域模型、事件驱动内核、回测与实盘共享组件、订单/执行/组合边界、Parquet Data Catalog | 总体架构、Data Hub、Live 的长期设计参考 | Architecture / Data / Execution / Live |
| **[vn.py / VeighNa](https://github.com/vnpy/vnpy/blob/master/vnpy/trader/converter.py)** | CTP 接入、CTA、组合策略、订单/成交事件、持仓转换、今昨仓、冻结仓位、无 UI 运行 | 国内期货 Live 最重要的复用候选 | Live / Broker / Execution |
| **[RQAlpha](https://rqalpha.readthedocs.io/zh-cn/latest/_modules/rqalpha/mod/rqalpha_mod_sys_accounts/position_model.html)** | 国内市场事件驱动回测、账户/持仓、模拟撮合、费用、事前风控、结果分析、Mod 扩展 | Research 回测、Accounting、Risk、Simulation 的重要候选和参考 | Research / Backtest / Accounting |
| **[Qlib](https://qlib.readthedocs.io/en/latest/component/data.html)** | Dataset/DataHandler、因子和标签、训练/验证流程、Experiment/Recorder、模型研究 | 因子研究、实验组织和 ML 工作流 | Research / Factor / Experiment |
| **[pysystemtrade](https://github.com/pst-group/pysystemtrade/blob/develop/docs/backtesting.md)** | 系统化期货、单合约数据、连续/复权价格、换月、forecast scaling、组合权重、仓位规模、成本 | 中低频多品种 CTA 的研究方法和期货业务建模 | Research / Portfolio / Futures Data |
| **[LEAN / QuantConnect LEAN](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/trade-fills/key-concepts)** | Fill、Slippage、Fee、Buying Power、Settlement 等 reality model 分层，回测和实盘统一思想 | 跨日回测、账户、费用、保证金和成交模型的补充参考 | Backtest / Accounting / Risk |
| **[ArcticDB](https://docs.arcticdb.io/latest/api/library/)** | 时间序列版本化、snapshot、time travel、范围读取、版本保留、大规模 DataFrame 存储 | 数据版本、不可变快照和生命周期设计参考 | Data Hub / Storage |
| **[MLflow](https://mlflow.org/docs/latest/ml/tracking/)** | Experiment / Run、参数、指标、标签、Artifact Store、结果比较 UI | 任务元数据与大产物分离、实验追踪 | Research / Experiment / Artifact |
| **[Freqtrade / FreqUI](https://www.freqtrade.io/en/stable/rest-api/)** | 策略运行、回测结果管理、运行监控、独立 Web UI、参数优化交互 | Research / Live 的前端交互和工作台设计 | Frontend / UX |
| **[Backtrader](https://www.backtrader.com/docu/concepts/)** | 简单事件驱动策略 API、Broker/Order/Data Feed 抽象、Analyzer | 理解经典 Python 回测框架结构 | Research / Backtest |
| **[Zipline / Zipline-reloaded](https://zipline.ml4trading.io/)** | Pipeline、事件驱动回测、资产数据组织 | 研究数据和回测框架的历史参考 | Research |
| **[vectorbt](https://vectorbt.dev/api/portfolio/base/)** | NumPy/Numba 向量化回测、大规模参数扫描、信号矩阵 | 快速因子/参数筛选和粗粒度研究 | Research / Factor Screening |
| **[bt / PyPortfolioOpt](https://github.com/pmorissette/bt)** | 组合构建、资产权重、优化 | 后期组合与风险研究 | Portfolio / Risk |
| **[QuantStats / empyrical](https://github.com/ranaroussi/quantstats)** | 回撤、Sharpe、收益统计、风险指标、报告生成 | 结果分析和报表 | Research / Reporting |
| **[PyFolio](https://pyfolio.ml4trading.io/)** | 组合表现、交易分析、风险暴露 | 研究结果诊断参考 | Research / Reporting |
| **[DuckDB](https://duckdb.org/docs/stable/data/parquet/overview)** | 直接查询 Parquet、列式分析、嵌入式 SQL | Research 行情查询层 | Research / Query |
| **[Parquet / PyArrow](https://arrow.apache.org/docs/python/dataset.html)** | 列式存储、压缩、跨工具生态 | 历史行情和研究大产物的物理格式与读写 | Data Hub / Research Artifact |
| **[PostgreSQL](https://www.postgresql.org/docs/current/tutorial-transactions.html)** | 事务、约束、任务状态、目录、元数据、审计 | Data Hub 元数据和状态管理 | Data Hub / Metadata |
| **[SQLite](https://www.sqlite.org/atomiccommit.html)** | 本机轻量事务数据库、单机可靠状态管理 | Research 本机任务和实验状态；Live 按实例持久化与恢复 | Research / Metadata；Live / State |
| **[Tushare](https://tushare.pro/document/2?doc_id=313)** | 国内期货历史行情、合约、结算、仓单、持仓排名等数据 | Data Hub 上游供应商 | Data Hub / Provider |
| **[CTP / SimNow](https://github.com/nooperpudd/ctpwrapper/blob/f7e08c01e25359b5f4385c14388f8dfe5a1d6fd7/ctp/header/ThostFtdcTraderApi.h)** | 国内期货实时行情、交易、账户查询、模拟柜台 | Live 的柜台接入；SimNow 用于仿真，实盘使用期货公司 CTP | Live / Broker |

## 使用规则

- 修改相关模块时，先查对应项目的官方文档或源码；实际采用前核实版本、维护状态、许可证与市场适用范围，并记录具体取舍。不要把本清单的整理日期当作所有项目的核验日期。
- 参考、代码复用和运行依赖是不同决策。按当前问题选择所需能力，避免引入另一套账户、执行内核、任务系统或数据真相；保持一个当前实现。
- 国内期货交易日、夜盘、今昨仓、结算、保证金、费用与持仓转换需要独立验证。股票、加密货币或海外期货模型不能直接作为国内期货规则。
- 向量化筛选、研究回测、内部 Paper 和柜台仿真证据分别记录。筛选结果不能替代因果事件回测或柜台成交验收，恢复缓存不能替代发送前持久化与柜台核对。
- 当前存储分工保持为 Data Hub PostgreSQL、Research 本机 SQLite 与 DuckDB 查询、Live 每实例本机 SQLite、固定发布 Parquet。ArcticDB 等参考项目不意味着更换现有存储。
- 组合优化、ML 和高级研究按路线图推进，不成为第一轮单策略、单真实合约跨日回测与 Live Sim 的新增前置条件。

## 2026-09-10 设计核对

以上链接是本轮查阅入口（CTP 固定头文件及既有外部证据见 [SOURCES](SOURCES.md)）；`latest`/分支页面可变化，不冒充固定发行版本。
补充入口：[PyPortfolioOpt](https://pyportfolioopt.readthedocs.io/en/latest/)、[empyrical-reloaded](https://github.com/stefan-jansen/empyrical-reloaded)、[Nautilus 恢复核对](https://nautilustrader.io/docs/latest/concepts/reconciliation/)、[LEAN Settlement](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/settlement/key-concepts)。

| 本轮采用的设计 | 明确的边界 |
|---|---|
| Nautilus 的组件职责与环境适配 | 保留 Python；独立实现实例内同步消息总线，参考类型路由，不引入其运行库；异步缓存不替代发送前可靠保存 |
| VeighNa 的开平转换与冻结思路 | 继续当前 CTP SDK；交易所/柜台规则单独验证，不直接复制整个引擎 |
| RQAlpha/LEAN 的账户与模拟模型分工 | Northstar 自己统一期货账本；LEAN 的 settlement 抽象不等于中国期货日结算 |
| Qlib/MLflow 的数据处理、训练范围与实验身份 | 复用现有研究任务/SQLite/产物，不增加另一研究服务；ML 后置 |
| ArcticDB 的版本/快照/保留语义 | 继续固定清单与 Parquet；被引用文件不可由合并清理破坏 |
| DuckDB/Arrow 范围读取 | 直接使用现有依赖；按实测优化，不先换存储 |
| FreqUI、QuantStats 等呈现与报告 | 只消费所属 API 和已核对结果，不能成为新的交易或账户权威 |
| pysystemtrade、vectorbt、bt/PyPortfolioOpt | 换月、组合、参数粗筛按后续阶段；连续价、矩阵成交不替代真实合约事件验收 |

本轮没有新增第三方运行依赖，也没有复制上述框架源码。未完成每个项目的采用级许可证/维护审计；
例如 [RQAlpha 的许可证](https://github.com/ricequant/rqalpha/blob/master/LICENSE) 另有商业用途条件，不能因为可读源码便默认可直接复用。

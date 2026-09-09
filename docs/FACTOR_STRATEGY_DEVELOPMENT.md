# 因子与策略开发

当前流程：登记算法 → 固定参数 → 因子计算 → 策略引用 → 回测与比较 → 策略版本 → 候选发布 → Live 接收。
Research 的 `/catalog` 是算法目录，`/paper` 提供文件回放账户；当前研究任务同步执行，尚无持久 worker。
Python 路径均相对于 `backend/src/northstar_quant/`。

## 当前算法

| ID | 含义 |
|---|---|
| `trend.return` | N+1 个收盘观察的窗口收益率 |
| `range.position` | 末价在 N+1 个收盘观察区间中的位置；平坦区间为 0.5 |
| `trend.momentum` | 引用收益率因子，超过阈值顺势，否则目标归零 |
| `mean_reversion.range` | 引用区间位置因子，两端反向目标，中部归零 |

这些是计算和工程验收用例，不是交易推荐。当前输入为单个真实合约价格口径的一分钟收盘观察，
只使用已完成且已可得的数据。不支持的连续/复权价格、截面、多腿、拟合变换和嵌套因子依赖须先实现语义。

## 新增算法

1. 实现放入 `factors/<分类>/` 或 `strategies/<分类>/`，品种和参数周期不各建一个源码文件。
2. 因子定义稳定 ID、计算修订、参数元信息、`requirements` 和无 I/O 的 `compute`，预热长度从参数推导。
3. 策略定义参数、明确因子槽、`validate_state` 与 `decide`；输入是因子结果，输出是账户无关目标。
4. 在所属 `registry.py` 显式登记受审核的加载位置和元信息；页面不能提交任意 Python 路径。
5. 验证已知公式结果、可得性、缺失/预热、数值边界和实例状态隔离。计算使用项目已有 Decimal 规则。

常规算法通过同一目录和计算接口接入，不在研究或 Live 中追加算法 ID 分支。
计算语义改变时更新实现 `revision`，涉及会话行为时同步更新 `research/backtesting.py` 的
`TradingSession.REVISION`。对外协议变更按 [API](API.md) 更新；Live 内核内部协议另由 `live/client.py` 管理。

## 固定配置与复用

`ResearchConfig` 装配 `StrategyConfig`、`RiskConfig`、`SimulationConfig`，完整文件示例见
[`backend/tests/data/intraday.toml`](../backend/tests/data/intraday.toml)。例如：

```python
from northstar_quant.factors.evaluation import Binding
from northstar_quant.research.configuration import ResearchConfig
from northstar_quant.strategies.configuration import StrategyConfig

configuration = ResearchConfig(strategy=StrategyConfig.create(
    "trend.momentum",
    supplied={"threshold": "0.005", "target_fraction": "0.5"},
    factors={"momentum": Binding.create("trend.return", {"window_bars": 20})},
))
```

固定引用包含稳定 ID、修订、Git 身份和规范参数，不保存隐式 `latest`。
未知参数、错误因子槽和不支持的修订被拒绝；说明和注释不改变计算身份。
`Evaluation(inputs)` 可在相同不可变输入范围内复用因子计算，不共享策略可变状态。

Research 保存执行尝试、输入身份、逐行状态与结果；只有身份一致的干净代码结果可复用持久缓存。
当前因子初步评价报告预热、有效性和覆盖率，不冒称完成因子挖掘或样本外验证。
研究比较要求相同快照、代码、Risk 和模拟成本；`-dirty` 结果仅提供开发证据。

## 发布与接收

策略版本引用实际使用同一配置的研究结果。候选 JSON 包含固定配置、代码身份和研究证据，
不含 Python 源码、动态导入路径、训练数据、密码或执行授权。
Live `/strategy-materials` 只接收与本机干净 Git 版本一致的候选，并在本地保存；
接收状态为 `RECEIVED`、`execution_authorized=false`，不创建或启用生产实例。

新增持久记录须纳入联合备份与引用核验。固定候选、部署接收和执行授权的区别见
[架构第 6 节](ARCHITECTURE.md#6-研究与结果身份)。

## 验证

运行根目录 `make verify`。安装态数据/研究/候选链使用 `scripts/acceptance/check_install.py`，
实际浏览器交互使用 `scripts/acceptance/check_browser.py`；环境与完整命令以
[CI](../.github/workflows/ci.yml) 为准。验收只使用专用可丢弃测试数据库，不连接柜台。
